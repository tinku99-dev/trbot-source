using System.Globalization;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Providers;

/// <summary>
/// Compliant market-data provider backed by the official
/// <see href="https://polygon.io">Polygon.io</see> Aggregates (bars) API.
/// A single API key covers BOTH stocks and crypto:
///   • Stocks: ticker "AAPL"        → /v2/aggs/ticker/AAPL/range/1/day/...
///   • Crypto: "BTC-USD" → "X:BTCUSD" → /v2/aggs/ticker/X:BTCUSD/range/1/day/...
/// Daily OHLCV is mapped into <see cref="PriceHistory"/> (oldest first).
/// </summary>
public sealed class PolygonMarketDataProvider : IMarketDataProvider
{
    private readonly HttpClient _http;
    private readonly ILogger<PolygonMarketDataProvider> _logger;
    private readonly PolygonOptions _options;

    // Simple per-instance throttle so a full-universe scan respects the free-tier
    // rate limit. The provider is resolved once per run, so this state persists
    // across all symbols in that run.
    private readonly SemaphoreSlim _gate = new(1, 1);
    private DateTimeOffset _lastRequestUtc = DateTimeOffset.MinValue;

    public PolygonMarketDataProvider(
        HttpClient http,
        IOptions<BotOptions> botOptions,
        ILogger<PolygonMarketDataProvider> logger)
    {
        _http = http;
        _logger = logger;
        _options = botOptions.Value.Providers.Polygon;
        if (_http.BaseAddress is null && !string.IsNullOrWhiteSpace(_options.BaseUrl))
            _http.BaseAddress = new Uri(_options.BaseUrl);
    }

    public string Name => "Polygon";

    public async Task<PriceHistory?> GetDailyHistoryAsync(
        string symbol, AssetClass assetClass, int lookbackDays, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(_options.ApiKey))
        {
            _logger.LogError("Polygon API key not configured (Bot:Providers:Polygon:ApiKey).");
            return null;
        }

        var ticker = ToPolygonTicker(symbol, assetClass);
        var to = DateOnly.FromDateTime(DateTime.UtcNow);
        // Pad calendar range so we capture enough trading days (markets close on weekends/holidays).
        var from = to.AddDays(-(int)Math.Ceiling(lookbackDays * 1.6) - 5);

        var path = $"/v2/aggs/ticker/{Uri.EscapeDataString(ticker)}/range/1/day/" +
                   $"{from:yyyy-MM-dd}/{to:yyyy-MM-dd}?adjusted=true&sort=asc&limit=50000" +
                   $"&apiKey={Uri.EscapeDataString(_options.ApiKey)}";

        try
        {
            await ThrottleAsync(ct);
            using var resp = await _http.GetAsync(path, ct);
            if (!resp.IsSuccessStatusCode)
            {
                _logger.LogWarning("Polygon returned {Status} for {Ticker}.", (int)resp.StatusCode, ticker);
                return null;
            }

            var json = await resp.Content.ReadAsStringAsync(ct);
            var candles = ParseAggregates(json);
            if (candles.Count == 0)
            {
                _logger.LogDebug("Polygon returned no bars for {Ticker}.", ticker);
                return null;
            }

            return new PriceHistory
            {
                Symbol = symbol,
                AssetClass = assetClass,
                Candles = candles
            };
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _logger.LogWarning(ex, "Polygon request failed for {Ticker}.", ticker);
            return null;
        }
    }

    /// <summary>
    /// Spaces consecutive requests by at least <see cref="PolygonOptions.MinRequestIntervalMs"/>
    /// so a full-universe scan stays under the free-tier limit (5 calls/min).
    /// Serialized via a semaphore so callers queue rather than burst. Set the interval
    /// to 0 to disable throttling (e.g. on a paid plan).
    /// </summary>
    private async Task ThrottleAsync(CancellationToken ct)
    {
        var minIntervalMs = _options.MinRequestIntervalMs;
        if (minIntervalMs <= 0) return;

        await _gate.WaitAsync(ct);
        try
        {
            var sinceLast = DateTimeOffset.UtcNow - _lastRequestUtc;
            var wait = TimeSpan.FromMilliseconds(minIntervalMs) - sinceLast;
            if (wait > TimeSpan.Zero)
            {
                _logger.LogDebug("Throttling {Wait:F0}ms to respect Polygon rate limit.", wait.TotalMilliseconds);
                await Task.Delay(wait, ct);
            }
            _lastRequestUtc = DateTimeOffset.UtcNow;
        }
        finally
        {
            _gate.Release();
        }
    }

    /// <summary>Convert a config symbol to Polygon's ticker format.</summary>
    public static string ToPolygonTicker(string symbol, AssetClass assetClass)
    {
        if (assetClass != AssetClass.Crypto) return symbol.Trim().ToUpperInvariant();
        // "BTC-USD" / "BTC/USD" / "BTCUSD" → "X:BTCUSD"
        var cleaned = symbol.Trim().ToUpperInvariant().Replace("-", "").Replace("/", "");
        return cleaned.StartsWith("X:") ? cleaned : $"X:{cleaned}";
    }

    /// <summary>
    /// Parse a Polygon aggregates JSON payload into candles (oldest first).
    /// Exposed as a pure function so it can be unit-tested without network access.
    /// </summary>
    public static List<Candle> ParseAggregates(string json)
    {
        var result = new List<Candle>();
        using var doc = JsonDocument.Parse(json);
        if (!doc.RootElement.TryGetProperty("results", out var results) ||
            results.ValueKind != JsonValueKind.Array)
            return result;

        foreach (var bar in results.EnumerateArray())
        {
            // Polygon fields: t=epoch ms, o/h/l/c=prices, v=volume.
            long t = bar.TryGetProperty("t", out var te) ? te.GetInt64() : 0;
            decimal o = GetDecimal(bar, "o");
            decimal h = GetDecimal(bar, "h");
            decimal l = GetDecimal(bar, "l");
            decimal c = GetDecimal(bar, "c");
            decimal v = GetDecimal(bar, "v");

            var ts = DateTimeOffset.FromUnixTimeMilliseconds(t);
            result.Add(new Candle(ts, o, h, l, c, v));
        }

        return result;
    }

    private static decimal GetDecimal(JsonElement el, string prop) =>
        el.TryGetProperty(prop, out var v) && v.ValueKind == JsonValueKind.Number
            ? v.GetDecimal()
            : 0m;
}
