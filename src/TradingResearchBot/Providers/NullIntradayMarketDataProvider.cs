using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Providers;

/// <summary>
/// No-op intraday provider used when the active market provider has no real-time
/// intraday feed. Always returns null, so the crypto scalp strategy simply produces
/// no candidates (degrades gracefully instead of failing).
/// </summary>
public sealed class NullIntradayMarketDataProvider : IIntradayMarketDataProvider
{
    public string Name => "NullIntraday";

    public Task<PriceHistory?> GetIntradayHistoryAsync(
        string symbol, AssetClass assetClass, string timeframe, int lookbackBars, CancellationToken ct = default)
        => Task.FromResult<PriceHistory?>(null);
}
