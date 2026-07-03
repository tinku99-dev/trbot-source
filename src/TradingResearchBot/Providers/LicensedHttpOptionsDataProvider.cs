using Microsoft.Extensions.Logging;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Providers;

/// <summary>
/// Template for a compliant, licensed options-data provider
/// (e.g. Tradier `/v1/markets/options/chains`, Polygon options snapshots,
/// Alpaca options data). This stub returns no data — wire your licensed REST
/// client + API key (from configuration / Key Vault) here and map the response
/// into <see cref="OptionsChain"/>.
///
/// Compliance note: Robinhood has no official public options API. Do not scrape
/// brokerage-internal endpoints; use a licensed market-data vendor instead.
/// </summary>
public sealed class LicensedHttpOptionsDataProvider : IOptionsDataProvider
{
    private readonly HttpClient _http;
    private readonly ILogger<LicensedHttpOptionsDataProvider> _logger;

    public LicensedHttpOptionsDataProvider(
        HttpClient http, ILogger<LicensedHttpOptionsDataProvider> logger)
    {
        _http = http;
        _logger = logger;
    }

    public string Name => "LicensedHttp";

    public Task<OptionsChain?> GetChainAsync(
        string symbol, decimal underlyingPrice, int minDays, int maxDays, CancellationToken ct = default)
    {
        _logger.LogWarning(
            "LicensedHttpOptionsDataProvider is a template and returns no data. " +
            "Implement against your licensed options provider before production use.");
        return Task.FromResult<OptionsChain?>(null);
    }
}
