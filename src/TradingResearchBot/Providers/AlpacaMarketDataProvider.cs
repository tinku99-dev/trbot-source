using System.Globalization;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Providers;

/// <summary>
/// Free real-time market-data provider backed by the official
/// <see href="https://alpaca.markets">Alpaca</see> Market Data API.
/// A single key pair (APCA-API-KEY-ID / APCA-API-SECRET-KEY) covers BOTH:
///   • Stocks (IEX feed, free real-time): /v2/stocks/{symbol}/bars?timeframe=1Day
///   • Crypto (free real-time):           /v1beta3/crypto/us/bars?symbols=BTC/USD
/// Daily OHLCV is mapped into <see cref="PriceHistory"/> (oldest first).
/// </summary>
public sealed class AlpacaMarketDataProvider : IMarketDataProvider, IIntradayMarketDataProvider
{
    private readonly HttpClient _http;
    private readonly ILogger<AlpacaMarketDataProvider> _logger;
    private readonly AlpacaOptions _options;

    private readonly SemaphoreSlim _gate = new(1, 1);
    private DateTimeOffset _lastRequestUtc = DateTimeOffset.MinValue;

    public AlpacaMarketDataProvider(
        HttpClient http,
        IOptions<BotOptions> botOptions,
        ILogger<AlpacaMarketDataProvider> logger)
    {
        _http = http;
        _logger = logger;
        _options = botOptions.Value.Providers.Alpaca;

        if (_http.BaseAddress is null && !string.IsNullOrWhiteSpace(_options.BaseUrl))
            _http.BaseAddress = new Uri(_options.BaseUrl);

        if (!string.IsNullOrWhiteSpace(_options.ApiKeyId))
            _http.DefaultRequestHeaders.TryAddWithoutValidation("APCA-API-KEY-ID", _options.ApiKeyId);
        if (!string.IsNullOrWhiteSpace(_options.ApiSecret))
            _http.DefaultRequestHeaders.TryAddWithoutValidation("APCA-API-SECRET-KEY", _options.ApiSecret);
    }

    public string Name => "Alpaca";

    public async Task<PriceHistory?> GetDailyHistoryAsync(
        string symbol, AssetClass assetClass, int lookbackDays, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(_options.ApiKeyId) || string.IsNullOrWhiteSpace(_options.ApiSecret))
        {
            _logger.LogError("Alpaca credentials not configured (Bot:Providers:Alpaca:ApiKeyId/ApiSecret).");
            return null;
        }

        var to = DateTimeOffset.UtcNow;
        // Pad calendar range so we capture enough trading days (weekends/holidays).
        var from = to.AddDays(-(int)Math.Ceiling(lookbackDays * 1.6) - 5);
        var fromStr = from.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        var toStr = to.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);

        var (path, cryptoSymbol) = assetClass == AssetClass.Crypto
            ? BuildCryptoPath(symbol, fromStr, toStr)
            : BuildStockPath(symbol, fromStr, toStr);

        try
        {
            await ThrottleAsync(ct);
            using var resp = await _http.GetAsync(path, ct);
            if (!resp.IsSuccessStatusCode)
            {
                _logger.LogWarning("Alpaca returned {Status} for {Symbol}.", (int)resp.StatusCode, symbol);
                return null;
            }

            var json = await resp.Content.ReadAsStringAsync(ct);
            var candles = assetClass == AssetClass.Crypto
                ? ParseCryptoBars(json, cryptoSymbol)
                : ParseStockBars(json);

            if (candles.Count == 0)
            {
                _logger.LogDebug("Alpaca returned no bars for {Symbol}.", symbol);
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
            _logger.LogWarning(ex, "Alpaca request failed for {Symbol}.", symbol);
            return null;
        }
    }

    public async Task<PriceHistory?> GetIntradayHistoryAsync(
        string symbol, AssetClass assetClass, string timeframe, int lookbackBars, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(_options.ApiKeyId) || string.IsNullOrWhiteSpace(_options.ApiSecret))
        {
            _logger.LogError("Alpaca credentials not configured (Bot:Providers:Alpaca:ApiKeyId/ApiSecret).");
            return null;
        }

        var tf = NormalizeTimeframe(timeframe);
        var minutes = TimeframeMinutes(tf);
        var bars = Math.Clamp(lookbackBars, 1, 10000);
        var to = DateTimeOffset.UtcNow;
        // Pad the calendar range so we reliably cover the requested number of bars.
        var from = to.AddMinutes(-minutes * bars * 1.5);
        var fromStr = from.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture);

        string path;
        string cryptoSymbol;
        if (assetClass == AssetClass.Crypto)
        {
            cryptoSymbol = ToCryptoPair(symbol); // "BTC/USD"
            path = $"/v1beta3/crypto/us/bars" +
                   $"?symbols={Uri.EscapeDataString(cryptoSymbol)}&timeframe={Uri.EscapeDataString(tf)}" +
                   $"&start={Uri.EscapeDataString(fromStr)}&limit={bars}";
        }
        else
        {
            cryptoSymbol = symbol.Trim().ToUpperInvariant();
            path = $"/v2/stocks/{Uri.EscapeDataString(cryptoSymbol)}/bars" +
                   $"?timeframe={Uri.EscapeDataString(tf)}&start={Uri.EscapeDataString(fromStr)}&limit={bars}" +
                   $"&adjustment=split&feed={Uri.EscapeDataString(_options.Feed)}";
        }

        try
        {
            await ThrottleAsync(ct);
            using var resp = await _http.GetAsync(path, ct);
            if (!resp.IsSuccessStatusCode)
            {
                _logger.LogWarning("Alpaca intraday returned {Status} for {Symbol} ({Tf}).",
                    (int)resp.StatusCode, symbol, tf);
                return null;
            }

            var json = await resp.Content.ReadAsStringAsync(ct);
            var candles = assetClass == AssetClass.Crypto
                ? ParseCryptoBars(json, cryptoSymbol)
                : ParseStockBars(json);

            if (candles.Count == 0)
            {
                _logger.LogDebug("Alpaca returned no intraday bars for {Symbol} ({Tf}).", symbol, tf);
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
            _logger.LogWarning(ex, "Alpaca intraday request failed for {Symbol} ({Tf}).", symbol, tf);
            return null;
        }
    }

    /// <summary>Map common aliases to Alpaca's expected timeframe tokens (e.g. "15m" → "15Min").</summary>
    public static string NormalizeTimeframe(string timeframe)
    {
        var t = (timeframe ?? "").Trim();
        return t.ToLowerInvariant() switch
        {
            "15min" or "15m" => "15Min",
            "5min" or "5m" => "5Min",
            "1min" or "1m" => "1Min",
            "30min" or "30m" => "30Min",
            "1hour" or "1h" or "60min" => "1Hour",
            "4hour" or "4h" or "240min" => "4Hour",
            "1day" or "1d" => "1Day",
            _ => string.IsNullOrEmpty(t) ? "15Min" : t
        };
    }

    /// <summary>Approximate number of minutes per bar for the given timeframe token.</summary>
    public static int TimeframeMinutes(string timeframe)
    {
        var t = NormalizeTimeframe(timeframe);
        int i = 0;
        while (i < t.Length && char.IsDigit(t[i])) i++;
        int qty = i > 0 && int.TryParse(t[..i], out var q) ? q : 1;
        var unit = t[i..].ToLowerInvariant();
        return unit switch
        {
            "min" => qty,
            "hour" => qty * 60,
            "day" => qty * 1440,
            _ => qty
        };
    }

    private (string path, string cryptoSymbol) BuildStockPath(string symbol, string from, string to)
    {
        var ticker = symbol.Trim().ToUpperInvariant();
        var path = $"/v2/stocks/{Uri.EscapeDataString(ticker)}/bars" +
                   $"?timeframe=1Day&start={from}&end={to}&limit=10000" +
                   $"&adjustment=split&feed={Uri.EscapeDataString(_options.Feed)}";
        return (path, ticker);
    }

    private (string path, string cryptoSymbol) BuildCryptoPath(string symbol, string from, string to)
    {
        var pair = ToCryptoPair(symbol); // "BTC/USD"
        var path = $"/v1beta3/crypto/us/bars" +
                   $"?symbols={Uri.EscapeDataString(pair)}&timeframe=1Day&start={from}&end={to}&limit=10000";
        return (path, pair);
    }

    /// <summary>"BTC-USD" / "BTCUSD" / "BTC/USD" → "BTC/USD".</summary>
    public static string ToCryptoPair(string symbol)
    {
        var s = symbol.Trim().ToUpperInvariant().Replace("-", "/");
        if (s.Contains('/')) return s;
        // "BTCUSD" → assume trailing USD/USDT quote.
        if (s.EndsWith("USDT")) return $"{s[..^4]}/USDT";
        if (s.EndsWith("USD")) return $"{s[..^3]}/USD";
        return s;
    }

    /// <summary>Parse a stocks bars payload (bars is a JSON array). Oldest first.</summary>
    public static List<Candle> ParseStockBars(string json)
    {
        var result = new List<Candle>();
        using var doc = JsonDocument.Parse(json);
        if (!doc.RootElement.TryGetProperty("bars", out var bars) || bars.ValueKind != JsonValueKind.Array)
            return result;

        foreach (var bar in bars.EnumerateArray())
            AddBar(result, bar);
        return result;
    }

    /// <summary>Parse a crypto bars payload (bars is an object keyed by symbol). Oldest first.</summary>
    public static List<Candle> ParseCryptoBars(string json, string cryptoSymbol)
    {
        var result = new List<Candle>();
        using var doc = JsonDocument.Parse(json);
        if (!doc.RootElement.TryGetProperty("bars", out var bars) || bars.ValueKind != JsonValueKind.Object)
            return result;

        if (!bars.TryGetProperty(cryptoSymbol, out var arr) || arr.ValueKind != JsonValueKind.Array)
        {
            // Fall back to the first symbol present.
            foreach (var prop in bars.EnumerateObject())
            {
                arr = prop.Value;
                break;
            }
        }

        if (arr.ValueKind != JsonValueKind.Array) return result;
        foreach (var bar in arr.EnumerateArray())
            AddBar(result, bar);
        return result;
    }

    private static void AddBar(List<Candle> result, JsonElement bar)
    {
        // Alpaca bar fields: t=RFC3339 timestamp, o/h/l/c=prices, v=volume.
        var t = bar.TryGetProperty("t", out var te) && te.ValueKind == JsonValueKind.String
            ? DateTimeOffset.Parse(te.GetString()!, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal)
            : DateTimeOffset.MinValue;
        decimal o = GetDecimal(bar, "o");
        decimal h = GetDecimal(bar, "h");
        decimal l = GetDecimal(bar, "l");
        decimal c = GetDecimal(bar, "c");
        decimal v = GetDecimal(bar, "v");
        result.Add(new Candle(t, o, h, l, c, v));
    }

    private static decimal GetDecimal(JsonElement el, string prop) =>
        el.TryGetProperty(prop, out var v) && v.ValueKind == JsonValueKind.Number
            ? v.GetDecimal()
            : 0m;

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
                await Task.Delay(wait, ct);
            _lastRequestUtc = DateTimeOffset.UtcNow;
        }
        finally
        {
            _gate.Release();
        }
    }
}
