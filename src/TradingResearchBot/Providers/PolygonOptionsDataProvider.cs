using System.Text.Json;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Providers;

/// <summary>
/// Real options-chain provider backed by Polygon's official options snapshot API:
/// /v3/snapshot/options/{underlyingAsset}. Uses the existing Polygon API key.
/// </summary>
public sealed class PolygonOptionsDataProvider : IOptionsDataProvider
{
    private readonly HttpClient _http;
    private readonly ILogger<PolygonOptionsDataProvider> _logger;
    private readonly PolygonOptions _options;

    public PolygonOptionsDataProvider(
        HttpClient http,
        IOptions<BotOptions> botOptions,
        ILogger<PolygonOptionsDataProvider> logger)
    {
        _http = http;
        _logger = logger;
        _options = botOptions.Value.Providers.Polygon;

        if (_http.BaseAddress is null && !string.IsNullOrWhiteSpace(_options.BaseUrl))
            _http.BaseAddress = new Uri(_options.BaseUrl);
    }

    public string Name => "Polygon";

    public async Task<OptionsChain?> GetChainAsync(
        string symbol, decimal underlyingPrice, int minDays, int maxDays, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(_options.ApiKey))
        {
            _logger.LogError("Polygon API key not configured (Bot:Providers:Polygon:ApiKey).");
            return null;
        }

        var contracts = new List<OptionContract>();
        var path = BuildPath(symbol, minDays, maxDays);

        try
        {
            while (!string.IsNullOrWhiteSpace(path))
            {
                using var resp = await _http.GetAsync(path, ct);
                if (!resp.IsSuccessStatusCode)
                {
                    _logger.LogWarning("Polygon options returned {Status} for {Symbol}.", (int)resp.StatusCode, symbol);
                    break;
                }

                var json = await resp.Content.ReadAsStringAsync(ct);
                contracts.AddRange(ParseSnapshotChain(json, symbol));
                path = NextPath(json);
            }

            return contracts.Count == 0
                ? null
                : new OptionsChain
                {
                    Symbol = symbol,
                    UnderlyingPrice = underlyingPrice,
                    Contracts = contracts
                };
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _logger.LogWarning(ex, "Polygon options request failed for {Symbol}.", symbol);
            return null;
        }
    }

    private string BuildPath(string symbol, int minDays, int maxDays)
    {
        var today = DateOnly.FromDateTime(DateTime.UtcNow);
        var minExp = today.AddDays(Math.Max(0, minDays));
        var maxExp = today.AddDays(Math.Max(minDays, maxDays));

        return $"/v3/snapshot/options/{Uri.EscapeDataString(symbol.ToUpperInvariant())}" +
               $"?limit=250&expiration_date.gte={minExp:yyyy-MM-dd}&expiration_date.lte={maxExp:yyyy-MM-dd}" +
               $"&apiKey={Uri.EscapeDataString(_options.ApiKey)}";
    }

    private string? NextPath(string json)
    {
        using var doc = JsonDocument.Parse(json);
        if (!doc.RootElement.TryGetProperty("next_url", out var next) ||
            next.ValueKind != JsonValueKind.String)
            return null;

        var url = next.GetString();
        if (string.IsNullOrWhiteSpace(url)) return null;

        var separator = url.Contains('?') ? '&' : '?';
        return $"{url}{separator}apiKey={Uri.EscapeDataString(_options.ApiKey)}";
    }

    public static List<OptionContract> ParseSnapshotChain(string json, string underlying)
    {
        var result = new List<OptionContract>();
        using var doc = JsonDocument.Parse(json);
        if (!doc.RootElement.TryGetProperty("results", out var results) ||
            results.ValueKind != JsonValueKind.Array)
            return result;

        foreach (var row in results.EnumerateArray())
        {
            var contract = MapContract(row, underlying);
            if (contract is not null) result.Add(contract);
        }

        return result;
    }

    private static OptionContract? MapContract(JsonElement row, string underlying)
    {
        if (!row.TryGetProperty("details", out var details) ||
            details.ValueKind != JsonValueKind.Object)
            return null;

        var typeText = GetString(details, "contract_type");
        var type = string.Equals(typeText, "put", StringComparison.OrdinalIgnoreCase)
            ? OptionType.Put
            : OptionType.Call;

        if (!DateOnly.TryParse(GetString(details, "expiration_date"), out var expiration))
            return null;

        var bid = GetNestedDecimal(row, "last_quote", "bid");
        var ask = GetNestedDecimal(row, "last_quote", "ask");
        var last = GetNestedDecimal(row, "last_trade", "price");
        var volume = GetNestedLong(row, "day", "volume") ?? 0;
        var openInterest = GetNullableLong(row, "open_interest") ?? 0;
        var iv = GetNullableDecimal(row, "implied_volatility");
        var delta = GetNestedDecimal(row, "greeks", "delta");

        return new OptionContract(
            UnderlyingSymbol: underlying,
            Type: type,
            Strike: GetNullableDecimal(details, "strike_price") ?? 0m,
            Expiration: expiration,
            Bid: bid,
            Ask: ask,
            Last: last,
            OpenInterest: openInterest,
            Volume: volume,
            ImpliedVolatility: iv,
            Delta: delta);
    }

    private static string? GetString(JsonElement el, string prop) =>
        el.TryGetProperty(prop, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;

    private static decimal? GetNullableDecimal(JsonElement el, string prop) =>
        el.TryGetProperty(prop, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetDecimal() : null;

    private static long? GetNullableLong(JsonElement el, string prop) =>
        el.TryGetProperty(prop, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetInt64() : null;

    private static decimal? GetNestedDecimal(JsonElement el, string parent, string prop) =>
        el.TryGetProperty(parent, out var p) && p.ValueKind == JsonValueKind.Object
            ? GetNullableDecimal(p, prop)
            : null;

    private static long? GetNestedLong(JsonElement el, string parent, string prop) =>
        el.TryGetProperty(parent, out var p) && p.ValueKind == JsonValueKind.Object
            ? GetNullableLong(p, prop)
            : null;
}
