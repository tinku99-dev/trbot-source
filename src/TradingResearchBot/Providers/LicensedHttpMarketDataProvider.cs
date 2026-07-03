using Microsoft.Extensions.Logging;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Providers;

/// <summary>
/// Template for a compliant, licensed HTTP market-data provider
/// (e.g. Polygon.io, Tradier, Alpaca, Finnhub, Twelve Data).
///
/// This stub intentionally does NOT call any endpoint. Wire your licensed
/// provider's REST client + API key (from configuration / Key Vault) here and
/// map its response into <see cref="PriceHistory"/>.
///
/// Compliance note: only use official, terms-of-service-compliant data APIs.
/// Do not scrape brokerage-internal or unofficial endpoints (e.g. Robinhood).
/// </summary>
public sealed class LicensedHttpMarketDataProvider : IMarketDataProvider
{
    private readonly HttpClient _http;
    private readonly ILogger<LicensedHttpMarketDataProvider> _logger;

    public LicensedHttpMarketDataProvider(
        HttpClient http, ILogger<LicensedHttpMarketDataProvider> logger)
    {
        _http = http;
        _logger = logger;
    }

    public string Name => "LicensedHttp";

    public Task<PriceHistory?> GetDailyHistoryAsync(
        string symbol, AssetClass assetClass, int lookbackDays, CancellationToken ct = default)
    {
        _logger.LogWarning(
            "LicensedHttpMarketDataProvider is a template and returns no data. " +
            "Implement against your licensed provider before using in production.");
        return Task.FromResult<PriceHistory?>(null);
    }
}
