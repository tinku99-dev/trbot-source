using System.Net.Http.Headers;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Providers;

/// <summary>
/// Compliant options-data provider backed by the official
/// <see href="https://documentation.tradier.com">Tradier</see> Market Data API.
/// Fetches available expirations, picks one inside the requested DTE window, then
/// pulls that expiration's chain (with greeks/IV) and maps it into
/// <see cref="OptionsChain"/>. Use the sandbox base URL for testing.
/// </summary>
public sealed class TradierOptionsDataProvider : IOptionsDataProvider
{
    private readonly HttpClient _http;
    private readonly ILogger<TradierOptionsDataProvider> _logger;
    private readonly TradierOptions _options;

    public TradierOptionsDataProvider(
        HttpClient http,
        IOptions<BotOptions> botOptions,
        ILogger<TradierOptionsDataProvider> logger)
    {
        _http = http;
        _logger = logger;
        _options = botOptions.Value.Providers.Tradier;

        if (_http.BaseAddress is null && !string.IsNullOrWhiteSpace(_options.BaseUrl))
            _http.BaseAddress = new Uri(_options.BaseUrl);
        _http.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        if (!string.IsNullOrWhiteSpace(_options.ApiKey))
            _http.DefaultRequestHeaders.Authorization =
                new AuthenticationHeaderValue("Bearer", _options.ApiKey);
    }

    public string Name => "Tradier";

    public async Task<OptionsChain?> GetChainAsync(
        string symbol, decimal underlyingPrice, int minDays, int maxDays, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(_options.ApiKey))
        {
            _logger.LogError("Tradier API key not configured (Bot:Providers:Tradier:ApiKey).");
            return null;
        }

        try
        {
            var expirationsJson = await GetStringAsync(
                $"/v1/markets/options/expirations?symbol={Uri.EscapeDataString(symbol)}&includeAllRoots=true",
                ct);
            if (expirationsJson is null) return null;

            var expirations = ParseExpirations(expirationsJson);
            var expiration = PickExpiration(expirations, minDays, maxDays);
            if (expiration is null)
            {
                _logger.LogDebug("No Tradier expiration in window for {Symbol}.", symbol);
                return null;
            }

            var chainJson = await GetStringAsync(
                $"/v1/markets/options/chains?symbol={Uri.EscapeDataString(symbol)}" +
                $"&expiration={expiration.Value:yyyy-MM-dd}&greeks=true",
                ct);
            if (chainJson is null) return null;

            var contracts = ParseChain(chainJson, symbol);
            if (contracts.Count == 0) return null;

            return new OptionsChain
            {
                Symbol = symbol,
                UnderlyingPrice = underlyingPrice,
                Contracts = contracts
            };
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _logger.LogWarning(ex, "Tradier options request failed for {Symbol}.", symbol);
            return null;
        }
    }

    private async Task<string?> GetStringAsync(string path, CancellationToken ct)
    {
        using var resp = await _http.GetAsync(path, ct);
        if (resp.IsSuccessStatusCode) return await resp.Content.ReadAsStringAsync(ct);
        _logger.LogWarning("Tradier returned {Status} for {Path}.", (int)resp.StatusCode, path);
        return null;
    }

    /// <summary>Choose the nearest expiration whose DTE falls inside the window.</summary>
    public static DateOnly? PickExpiration(IReadOnlyList<DateOnly> expirations, int minDays, int maxDays)
    {
        var today = DateOnly.FromDateTime(DateTime.UtcNow);
        DateOnly? best = null;
        int bestDte = int.MaxValue;
        foreach (var exp in expirations)
        {
            int dte = exp.DayNumber - today.DayNumber;
            if (dte < minDays || dte > maxDays) continue;
            if (dte < bestDte) { bestDte = dte; best = exp; }
        }
        // Fallback: closest expiration at/after minDays even if beyond maxDays.
        if (best is null)
        {
            foreach (var exp in expirations)
            {
                int dte = exp.DayNumber - today.DayNumber;
                if (dte >= minDays && dte < bestDte) { bestDte = dte; best = exp; }
            }
        }
        return best;
    }

    /// <summary>Parse Tradier expirations payload. Pure function for testing.</summary>
    public static List<DateOnly> ParseExpirations(string json)
    {
        var result = new List<DateOnly>();
        using var doc = JsonDocument.Parse(json);
        if (!doc.RootElement.TryGetProperty("expirations", out var expirations) ||
            expirations.ValueKind != JsonValueKind.Object)
            return result;

        if (!expirations.TryGetProperty("date", out var dates)) return result;

        if (dates.ValueKind == JsonValueKind.Array)
        {
            foreach (var d in dates.EnumerateArray())
                if (TryDate(d.GetString(), out var dt)) result.Add(dt);
        }
        else if (dates.ValueKind == JsonValueKind.String && TryDate(dates.GetString(), out var single))
        {
            result.Add(single);
        }

        return result;
    }

    /// <summary>Parse Tradier options chain payload. Pure function for testing.</summary>
    public static List<OptionContract> ParseChain(string json, string underlying)
    {
        var result = new List<OptionContract>();
        using var doc = JsonDocument.Parse(json);
        if (!doc.RootElement.TryGetProperty("options", out var options) ||
            options.ValueKind != JsonValueKind.Object)
            return result;

        if (!options.TryGetProperty("option", out var optionEl)) return result;

        if (optionEl.ValueKind == JsonValueKind.Array)
        {
            foreach (var o in optionEl.EnumerateArray())
            {
                var c = MapContract(o, underlying);
                if (c is not null) result.Add(c);
            }
        }
        else if (optionEl.ValueKind == JsonValueKind.Object)
        {
            var c = MapContract(optionEl, underlying);
            if (c is not null) result.Add(c);
        }

        return result;
    }

    private static OptionContract? MapContract(JsonElement o, string underlying)
    {
        var typeStr = GetString(o, "option_type");
        if (!TryDate(GetString(o, "expiration_date"), out var exp)) return null;
        var type = string.Equals(typeStr, "put", StringComparison.OrdinalIgnoreCase)
            ? OptionType.Put : OptionType.Call;

        decimal? iv = null, delta = null;
        if (o.TryGetProperty("greeks", out var greeks) && greeks.ValueKind == JsonValueKind.Object)
        {
            iv = GetNullableDecimal(greeks, "mid_iv") ?? GetNullableDecimal(greeks, "smv_vol");
            delta = GetNullableDecimal(greeks, "delta");
        }

        return new OptionContract(
            UnderlyingSymbol: underlying,
            Type: type,
            Strike: GetNullableDecimal(o, "strike") ?? 0m,
            Expiration: exp,
            Bid: GetNullableDecimal(o, "bid"),
            Ask: GetNullableDecimal(o, "ask"),
            Last: GetNullableDecimal(o, "last"),
            OpenInterest: GetNullableLong(o, "open_interest") ?? 0,
            Volume: GetNullableLong(o, "volume") ?? 0,
            ImpliedVolatility: iv,
            Delta: delta);
    }

    private static bool TryDate(string? s, out DateOnly date) =>
        DateOnly.TryParse(s, out date);

    private static string? GetString(JsonElement el, string prop) =>
        el.TryGetProperty(prop, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;

    private static decimal? GetNullableDecimal(JsonElement el, string prop) =>
        el.TryGetProperty(prop, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetDecimal() : null;

    private static long? GetNullableLong(JsonElement el, string prop) =>
        el.TryGetProperty(prop, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetInt64() : null;
}
