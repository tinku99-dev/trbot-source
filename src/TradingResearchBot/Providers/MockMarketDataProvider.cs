using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Providers;

/// <summary>
/// Deterministic synthetic market-data provider for local development and tests.
/// Generates plausible OHLCV history using a seeded random walk so runs are
/// reproducible per symbol. Replace with a licensed provider in production.
/// </summary>
public sealed class MockMarketDataProvider : IMarketDataProvider
{
    public string Name => "Mock";

    public Task<PriceHistory?> GetDailyHistoryAsync(
        string symbol, AssetClass assetClass, int lookbackDays, CancellationToken ct = default)
    {
        var candles = Generate(symbol, assetClass, Math.Max(lookbackDays, 260));
        var history = new PriceHistory
        {
            Symbol = symbol,
            AssetClass = assetClass,
            Candles = candles
        };
        return Task.FromResult<PriceHistory?>(history);
    }

    private static List<Candle> Generate(string symbol, AssetClass assetClass, int days)
    {
        // Seed from symbol so each run is reproducible but symbols differ.
        var rng = new Random(StableSeed(symbol));

        decimal basePrice = assetClass == AssetClass.Crypto
            ? 50m + rng.Next(0, 60000)
            : 20m + rng.Next(0, 400);

        // Crypto is more volatile.
        double dailyVol = assetClass == AssetClass.Crypto ? 0.035 : 0.018;
        double drift = (rng.NextDouble() - 0.45) * 0.0015; // slight up/down bias

        var candles = new List<Candle>(days);
        decimal price = basePrice;
        var start = DateTimeOffset.UtcNow.Date.AddDays(-days);

        for (int i = 0; i < days; i++)
        {
            double shock = (rng.NextDouble() - 0.5) * 2 * dailyVol + drift;
            decimal open = price;
            decimal close = Math.Max(0.01m, open * (decimal)(1 + shock));
            decimal high = Math.Max(open, close) * (decimal)(1 + rng.NextDouble() * dailyVol);
            decimal low = Math.Min(open, close) * (decimal)(1 - rng.NextDouble() * dailyVol);

            decimal baseVol = assetClass == AssetClass.Crypto ? 200_000m : 1_000_000m;
            // Occasional volume spikes to trigger volume-strength signals.
            decimal volMult = rng.NextDouble() > 0.9 ? 1.3m + (decimal)rng.NextDouble() : 0.7m + (decimal)rng.NextDouble() * 0.6m;
            decimal volume = baseVol * volMult;

            candles.Add(new Candle(
                new DateTimeOffset(start.AddDays(i), TimeSpan.Zero),
                Round(open), Round(high), Round(low), Round(close), Math.Round(volume)));

            price = close;
        }

        return candles;
    }

    private static decimal Round(decimal v) => Math.Round(v, 4);

    private static int StableSeed(string symbol)
    {
        unchecked
        {
            int hash = 17;
            foreach (var c in symbol) hash = hash * 31 + c;
            return hash;
        }
    }
}
