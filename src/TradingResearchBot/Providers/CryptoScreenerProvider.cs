using System.Globalization;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using TradingResearchBot.Models;

namespace TradingResearchBot.Providers;

/// <summary>
/// Dynamically screens crypto pairs by fetching recent bars (4-hour or daily),
/// calculating % change and volume, then sorting by activity/movement.
/// Returns the top movers for the scalp strategy, filtered by quality gates.
///
/// Since Alpaca has no crypto screener endpoint, this builds one:
///   1. Fetch available crypto/USD pairs from the trading API.
///   2. Pull recent bars for each (batch request).
///   3. Rank by absolute % change (movement) or volume (activity).
///   4. Return top N passing quality filters.
/// </summary>
public sealed class CryptoScreenerProvider
{
    private readonly HttpClient _http;
    private readonly ILogger<CryptoScreenerProvider> _logger;
    private readonly BotOptions _bot;
    private readonly AlpacaOptions _alpaca;

    // Paper trading API for asset listing (works with data-API keys).
    private const string TradingApiBase = "https://paper-api.alpaca.markets";

    public CryptoScreenerProvider(
        HttpClient http,
        IOptions<BotOptions> botOptions,
        ILogger<CryptoScreenerProvider> logger)
    {
        _http = http;
        _logger = logger;
        _bot = botOptions.Value;
        _alpaca = _bot.Providers.Alpaca;

        if (!string.IsNullOrWhiteSpace(_alpaca.ApiKeyId))
            _http.DefaultRequestHeaders.TryAddWithoutValidation("APCA-API-KEY-ID", _alpaca.ApiKeyId);
        if (!string.IsNullOrWhiteSpace(_alpaca.ApiSecret))
            _http.DefaultRequestHeaders.TryAddWithoutValidation("APCA-API-SECRET-KEY", _alpaca.ApiSecret);
    }

    /// <summary>
    /// Screen cryptos by recent movement/activity and return the top movers.
    /// </summary>
    /// <param name="topN">Maximum pairs to return.</param>
    /// <param name="timeframe">Bar timeframe for screening (e.g., "4Hour", "1Day").</param>
    /// <param name="minChangePct">Minimum absolute % change to be considered a mover.</param>
    /// <param name="sortBy">"movement" (% change) or "volume".</param>
    /// <param name="ct">Cancellation token.</param>
    public async Task<IReadOnlyList<CryptoMover>> GetTopMoversAsync(
        int topN = 20,
        string timeframe = "4Hour",
        decimal minChangePct = 1.0m,
        string sortBy = "movement",
        CancellationToken ct = default)
    {
        // Step 1: Get available crypto/USD pairs.
        var pairs = await GetCryptoPairsAsync(ct);
        if (pairs.Count == 0)
        {
            _logger.LogWarning("No crypto pairs found; falling back to default list.");
            pairs = DefaultCryptoPairs();
        }

        _logger.LogInformation("Screening {Count} crypto pairs by {Timeframe} bars.", pairs.Count, timeframe);

        // Step 2: Fetch recent bars for all pairs in batches (Alpaca allows multi-symbol requests).
        var movers = new List<CryptoMover>();
        var batchSize = 20; // Alpaca supports multiple symbols per request.
        var tf = AlpacaMarketDataProvider.NormalizeTimeframe(timeframe);

        foreach (var batch in pairs.Chunk(batchSize))
        {
            ct.ThrowIfCancellationRequested();
            var batchMovers = await FetchBarsAndCalculateMoversAsync(batch, tf, ct);
            movers.AddRange(batchMovers);
        }

        // Step 3: Filter by minimum % change and sort.
        var filtered = movers
            .Where(m => Math.Abs(m.ChangePct) >= minChangePct)
            .ToList();

        var sorted = sortBy?.ToLowerInvariant() == "volume"
            ? filtered.OrderByDescending(m => m.Volume).ThenByDescending(m => Math.Abs(m.ChangePct))
            : filtered.OrderByDescending(m => Math.Abs(m.ChangePct)).ThenByDescending(m => m.Volume);

        var result = sorted.Take(topN).ToList();

        _logger.LogInformation(
            "Crypto screener: {Total} pairs → {Filtered} passed filter → returning top {TopN}.",
            movers.Count, filtered.Count, result.Count);

        return result;
    }

    private async Task<List<string>> GetCryptoPairsAsync(CancellationToken ct)
    {
        var pairs = new List<string>();
        try
        {
            using var resp = await _http.GetAsync($"{TradingApiBase}/v2/assets?asset_class=crypto&status=active", ct);
            if (!resp.IsSuccessStatusCode)
            {
                _logger.LogWarning("Crypto assets endpoint returned {Status}.", (int)resp.StatusCode);
                return pairs;
            }

            var json = await resp.Content.ReadAsStringAsync(ct);
            using var doc = JsonDocument.Parse(json);
            if (doc.RootElement.ValueKind != JsonValueKind.Array) return pairs;

            foreach (var el in doc.RootElement.EnumerateArray())
            {
                var symbol = GetString(el, "symbol");
                // Only USD pairs for consistent pricing; skip BTC/USDC/USDT quote pairs.
                if (!string.IsNullOrEmpty(symbol) && symbol.EndsWith("/USD"))
                    pairs.Add(symbol);
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Failed to fetch crypto pairs.");
        }
        return pairs;
    }

    private async Task<List<CryptoMover>> FetchBarsAndCalculateMoversAsync(
        string[] symbols, string timeframe, CancellationToken ct)
    {
        var movers = new List<CryptoMover>();
        try
        {
            var symbolsParam = string.Join(",", symbols.Select(Uri.EscapeDataString));
            var to = DateTimeOffset.UtcNow;
            var from = to.AddHours(-48); // Enough to get several bars.
            var fromStr = from.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture);

            var path = $"https://data.alpaca.markets/v1beta3/crypto/us/bars" +
                       $"?symbols={symbolsParam}&timeframe={Uri.EscapeDataString(timeframe)}" +
                       $"&start={Uri.EscapeDataString(fromStr)}&limit=10";

            using var resp = await _http.GetAsync(path, ct);
            if (!resp.IsSuccessStatusCode)
            {
                _logger.LogDebug("Bars request failed for batch: {Status}", (int)resp.StatusCode);
                return movers;
            }

            var json = await resp.Content.ReadAsStringAsync(ct);
            using var doc = JsonDocument.Parse(json);

            if (!doc.RootElement.TryGetProperty("bars", out var bars) || bars.ValueKind != JsonValueKind.Object)
                return movers;

            foreach (var prop in bars.EnumerateObject())
            {
                var symbol = prop.Name;
                if (prop.Value.ValueKind != JsonValueKind.Array) continue;

                var barList = prop.Value.EnumerateArray().ToList();
                if (barList.Count < 2) continue;

                // Most recent bar vs previous bar.
                var latest = barList[^1];
                var prev = barList[^2];

                decimal closeNow = GetDecimal(latest, "c");
                decimal closePrev = GetDecimal(prev, "c");
                decimal volume = GetDecimal(latest, "v");

                if (closePrev <= 0) continue;

                decimal changePct = (closeNow - closePrev) / closePrev * 100m;

                movers.Add(new CryptoMover(
                    Symbol: ToStandardSymbol(symbol),
                    Price: closeNow,
                    ChangePct: Math.Round(changePct, 2),
                    Volume: volume));
            }
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Error fetching bars for crypto batch.");
        }
        return movers;
    }

    /// <summary>Convert "BTC/USD" to "BTC-USD" (the standard symbol format used elsewhere).</summary>
    private static string ToStandardSymbol(string alpacaPair) =>
        alpacaPair.Replace("/", "-");

    private static List<string> DefaultCryptoPairs() => new()
    {
        "BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "DOGE/USD", "ADA/USD",
        "AVAX/USD", "LINK/USD", "DOT/USD", "SHIB/USD", "LTC/USD", "UNI/USD",
        "BCH/USD", "ATOM/USD", "APE/USD", "CRV/USD", "AAVE/USD", "SUSHI/USD",
        "GRT/USD", "MKR/USD", "NEAR/USD", "ALGO/USD", "FIL/USD", "XLM/USD"
    };

    private static string GetString(JsonElement el, string prop) =>
        el.TryGetProperty(prop, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? "" : "";

    private static decimal GetDecimal(JsonElement el, string prop) =>
        el.TryGetProperty(prop, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetDecimal() : 0m;
}

/// <summary>A crypto pair with its recent movement/activity metrics.</summary>
public readonly record struct CryptoMover(string Symbol, decimal Price, decimal ChangePct, decimal Volume);
