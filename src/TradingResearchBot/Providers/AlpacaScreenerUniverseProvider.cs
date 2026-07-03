using System.Text.Json;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Providers;

/// <summary>
/// Builds a FRESH stock universe each run from Alpaca's free screener endpoints:
///   • /v1beta1/screener/stocks/most-actives  (by volume)
///   • /v1beta1/screener/stocks/movers        (top gainers + losers)
/// Results are quality-filtered (min price/volume) to avoid penny-stock noise,
/// de-duplicated, capped at TopN, then merged with any pinned AlwaysInclude names.
/// Crypto always comes from the configured CryptoUniverse (no free crypto screener).
/// </summary>
public sealed class AlpacaScreenerUniverseProvider : IUniverseProvider
{
    private readonly HttpClient _http;
    private readonly ILogger<AlpacaScreenerUniverseProvider> _logger;
    private readonly BotOptions _bot;
    private readonly AlpacaOptions _alpaca;
    private readonly UniverseOptions _universe;

    public AlpacaScreenerUniverseProvider(
        HttpClient http,
        IOptions<BotOptions> botOptions,
        ILogger<AlpacaScreenerUniverseProvider> logger)
    {
        _http = http;
        _logger = logger;
        _bot = botOptions.Value;
        _alpaca = _bot.Providers.Alpaca;
        _universe = _bot.Universe;

        if (_http.BaseAddress is null && !string.IsNullOrWhiteSpace(_alpaca.BaseUrl))
            _http.BaseAddress = new Uri(_alpaca.BaseUrl);
        if (!string.IsNullOrWhiteSpace(_alpaca.ApiKeyId))
            _http.DefaultRequestHeaders.TryAddWithoutValidation("APCA-API-KEY-ID", _alpaca.ApiKeyId);
        if (!string.IsNullOrWhiteSpace(_alpaca.ApiSecret))
            _http.DefaultRequestHeaders.TryAddWithoutValidation("APCA-API-SECRET-KEY", _alpaca.ApiSecret);
    }

    public async Task<IReadOnlyList<UniverseEntry>> GetUniverseAsync(CancellationToken ct = default)
    {
        // Use a generous request size; we filter down to TopN afterwards.
        var fetch = Math.Clamp(_universe.TopN * 2, 20, 100);
        var picked = new Dictionary<string, ScreenerRow>(StringComparer.OrdinalIgnoreCase);

        if (_universe.IncludeMostActives)
            foreach (var r in await GetMostActivesAsync(fetch, ct))
                picked.TryAdd(r.Symbol, r);

        if (_universe.IncludeGainers || _universe.IncludeLosers)
        {
            var (gainers, losers) = await GetMoversAsync(fetch, ct);
            if (_universe.IncludeGainers)
                foreach (var r in gainers) picked.TryAdd(r.Symbol, r);
            if (_universe.IncludeLosers)
                foreach (var r in losers) picked.TryAdd(r.Symbol, r);
        }

        var stocks = picked.Values
            .Where(PassesQualityFilter)
            // Rank by volume (most-actives carry volume; movers may not — push those down).
            .OrderByDescending(r => r.Volume)
            .Take(_universe.TopN)
            .Select(r => new UniverseEntry(r.Symbol, AssetClass.Stock))
            .ToList();

        // Pin always-include names (sector leaders) at the front, de-duplicated.
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var result = new List<UniverseEntry>();
        foreach (var pinned in _universe.AlwaysIncludeSymbols())
            if (seen.Add(pinned))
                result.Add(new UniverseEntry(pinned, AssetClass.Stock));
        foreach (var s in stocks)
            if (seen.Add(s.Symbol))
                result.Add(s);

        // Crypto from the configured list (Alpaca has no free crypto screener).
        foreach (var c in _bot.CryptoSymbols())
            result.Add(new UniverseEntry(c, AssetClass.Crypto));

        _logger.LogInformation(
            "Dynamic universe: {StockCount} stocks (screened) + {CryptoCount} crypto.",
            result.Count(e => e.AssetClass == AssetClass.Stock),
            result.Count(e => e.AssetClass == AssetClass.Crypto));

        if (result.Count == 0)
        {
            _logger.LogWarning("Screener returned nothing usable; falling back to static StockUniverse.");
            foreach (var s in _bot.StockSymbols())
                result.Add(new UniverseEntry(s, AssetClass.Stock));
            foreach (var c in _bot.CryptoSymbols())
                result.Add(new UniverseEntry(c, AssetClass.Crypto));
        }

        return result;
    }

    private bool PassesQualityFilter(ScreenerRow r)
    {
        if (_universe.MinPrice > 0 && r.Price > 0 && r.Price < _universe.MinPrice) return false;
        if (_universe.MaxPrice > 0 && r.Price > _universe.MaxPrice) return false;
        if (_universe.MinVolume > 0 && r.Volume > 0 && r.Volume < _universe.MinVolume) return false;
        // Skip obvious non-common-stock tickers (warrants/units): contain '.' or '/'.
        if (r.Symbol.Contains('.') || r.Symbol.Contains('/')) return false;
        return true;
    }

    private async Task<List<ScreenerRow>> GetMostActivesAsync(int top, CancellationToken ct)
    {
        var path = $"/v1beta1/screener/stocks/most-actives?by=volume&top={top}";
        var rows = new List<ScreenerRow>();
        try
        {
            var json = await GetStringAsync(path, ct);
            if (json is null) return rows;
            using var doc = JsonDocument.Parse(json);
            if (doc.RootElement.TryGetProperty("most_actives", out var arr) && arr.ValueKind == JsonValueKind.Array)
                foreach (var el in arr.EnumerateArray())
                    rows.Add(new ScreenerRow(
                        GetString(el, "symbol"),
                        Price: 0m, // most-actives has no price; price filter applies to movers
                        Volume: GetLong(el, "volume")));
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Most-actives screener call failed.");
        }
        return rows;
    }

    private async Task<(List<ScreenerRow> Gainers, List<ScreenerRow> Losers)> GetMoversAsync(int top, CancellationToken ct)
    {
        var path = $"/v1beta1/screener/stocks/movers?top={top}";
        var gainers = new List<ScreenerRow>();
        var losers = new List<ScreenerRow>();
        try
        {
            var json = await GetStringAsync(path, ct);
            if (json is null) return (gainers, losers);
            using var doc = JsonDocument.Parse(json);
            if (doc.RootElement.TryGetProperty("gainers", out var g) && g.ValueKind == JsonValueKind.Array)
                foreach (var el in g.EnumerateArray())
                    gainers.Add(new ScreenerRow(GetString(el, "symbol"), GetDecimal(el, "price"), Volume: 0));
            if (doc.RootElement.TryGetProperty("losers", out var l) && l.ValueKind == JsonValueKind.Array)
                foreach (var el in l.EnumerateArray())
                    losers.Add(new ScreenerRow(GetString(el, "symbol"), GetDecimal(el, "price"), Volume: 0));
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Movers screener call failed.");
        }
        return (gainers, losers);
    }

    private async Task<string?> GetStringAsync(string path, CancellationToken ct)
    {
        using var resp = await _http.GetAsync(path, ct);
        if (!resp.IsSuccessStatusCode)
        {
            _logger.LogWarning("Alpaca screener {Path} returned {Status}.", path, (int)resp.StatusCode);
            return null;
        }
        return await resp.Content.ReadAsStringAsync(ct);
    }

    private static string GetString(JsonElement el, string prop) =>
        el.TryGetProperty(prop, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? "" : "";

    private static long GetLong(JsonElement el, string prop) =>
        el.TryGetProperty(prop, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetInt64() : 0;

    private static decimal GetDecimal(JsonElement el, string prop) =>
        el.TryGetProperty(prop, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetDecimal() : 0m;

    private readonly record struct ScreenerRow(string Symbol, decimal Price, long Volume);
}
