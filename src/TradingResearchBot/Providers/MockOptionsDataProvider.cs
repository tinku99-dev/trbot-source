using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Providers;

/// <summary>
/// Deterministic synthetic options-chain provider for local development and tests.
/// Generates a plausible chain around the underlying price with a simple IV/Greeks
/// approximation. Replace with a licensed provider (Tradier/Polygon/Alpaca) for
/// production use.
/// </summary>
public sealed class MockOptionsDataProvider : IOptionsDataProvider
{
    public string Name => "Mock";

    public Task<OptionsChain?> GetChainAsync(
        string symbol, decimal underlyingPrice, int minDays, int maxDays, CancellationToken ct = default)
    {
        if (underlyingPrice <= 0)
            return Task.FromResult<OptionsChain?>(null);

        var rng = new Random(StableSeed(symbol));
        var contracts = new List<OptionContract>();

        // A couple of expirations inside the requested window.
        var today = DateOnly.FromDateTime(DateTime.UtcNow);
        int[] dteTargets = { Math.Max(minDays, 7), (minDays + maxDays) / 2, Math.Min(maxDays, 45) };

        // Strikes: roughly +/- 20% in ~2.5% steps.
        decimal step = RoundStrike(underlyingPrice * 0.025m);
        if (step <= 0) step = 0.5m;

        foreach (var dte in dteTargets.Distinct())
        {
            var expiration = today.AddDays(dte);
            decimal baseIv = 0.30m + (decimal)rng.NextDouble() * 0.40m; // 30%-70%
            double t = dte / 365.0;

            for (decimal k = RoundStrike(underlyingPrice * 0.8m); k <= underlyingPrice * 1.2m; k += step)
            {
                foreach (var type in new[] { OptionType.Call, OptionType.Put })
                {
                    decimal moneyness = (underlyingPrice - k) / underlyingPrice; // >0 ITM for calls
                    decimal iv = Math.Max(0.05m, baseIv + Math.Abs(moneyness) * 0.5m); // simple smile
                    decimal intrinsic = type == OptionType.Call
                        ? Math.Max(0, underlyingPrice - k)
                        : Math.Max(0, k - underlyingPrice);
                    decimal timeValue = underlyingPrice * iv * (decimal)Math.Sqrt(t) * 0.4m;
                    decimal mid = Math.Round(intrinsic + timeValue, 2);
                    decimal spread = Math.Max(0.02m, mid * 0.05m);

                    decimal delta = ApproxDelta(type, moneyness);
                    long oi = 50 + rng.Next(0, 5000);
                    long vol = rng.Next(0, 2000);

                    contracts.Add(new OptionContract(
                        symbol, type, Math.Round(k, 2), expiration,
                        Bid: Math.Round(Math.Max(0.01m, mid - spread / 2), 2),
                        Ask: Math.Round(mid + spread / 2, 2),
                        Last: mid,
                        OpenInterest: oi,
                        Volume: vol,
                        ImpliedVolatility: Math.Round(iv, 4),
                        Delta: Math.Round(delta, 2)));
                }
            }
        }

        var chain = new OptionsChain
        {
            Symbol = symbol,
            UnderlyingPrice = underlyingPrice,
            Contracts = contracts
        };
        return Task.FromResult<OptionsChain?>(chain);
    }

    private static decimal ApproxDelta(OptionType type, decimal moneyness)
    {
        // Smooth 0..1 mapping centered at the money; crude but monotonic.
        decimal callDelta = 0.5m + Math.Clamp(moneyness * 2.5m, -0.45m, 0.45m);
        return type == OptionType.Call ? callDelta : callDelta - 1m;
    }

    private static decimal RoundStrike(decimal value)
    {
        if (value >= 100m) return Math.Round(value / 5m) * 5m;
        if (value >= 25m) return Math.Round(value);
        return Math.Round(value * 2m) / 2m;
    }

    private static int StableSeed(string symbol)
    {
        unchecked
        {
            int hash = 23;
            foreach (var c in symbol) hash = hash * 31 + c;
            return hash;
        }
    }
}
