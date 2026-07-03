using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using TradingResearchBot.Indicators;
using TradingResearchBot.Models;
using TradingResearchBot.Providers;
using TradingResearchBot.Scoring;
using Xunit;

namespace TradingResearchBot.Tests;

public class AdvancedIndicatorTests
{
    [Fact]
    public void Stochastic_AtTopOfRange_IsHigh()
    {
        var highs = new decimal[] { 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27 };
        var lows = new decimal[] { 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26 };
        var closes = new decimal[] { 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27 };
        var (k, _) = IndicatorEngine.Stochastic(highs, lows, closes, 14, 3);
        Assert.NotNull(k);
        Assert.True(k > 90, $"Expected high %K at top of range, got {k}");
    }

    [Fact]
    public void Adx_TrendingSeries_IsElevated()
    {
        int n = 60;
        var highs = new decimal[n];
        var lows = new decimal[n];
        var closes = new decimal[n];
        for (int i = 0; i < n; i++)
        {
            decimal p = 100 + i; // strong uptrend
            highs[i] = p + 1;
            lows[i] = p - 1;
            closes[i] = p;
        }
        var adx = IndicatorEngine.Adx(highs, lows, closes, 14);
        Assert.NotNull(adx);
        Assert.True(adx > 25, $"Expected strong ADX for a clean trend, got {adx}");
    }

    [Fact]
    public void Obv_RisesWithUpCloses()
    {
        var closes = new decimal[] { 10, 11, 12, 13, 14 };
        var volumes = new decimal[] { 100, 100, 100, 100, 100 };
        var (obv, slope) = IndicatorEngine.Obv(closes, volumes, 3);
        Assert.NotNull(obv);
        Assert.True(obv > 0);
        Assert.True(slope > 0);
    }

    [Fact]
    public void ObvAccumulation_NormalizesPressureAndUpVolume()
    {
        var closes = new decimal[] { 10m, 11m, 10.5m, 11.5m, 12m };
        var volumes = new decimal[] { 50m, 100m, 50m, 150m, 100m };

        var (pressure, upRatio) = IndicatorEngine.ObvAccumulation(closes, volumes, 4);

        Assert.NotNull(pressure);
        Assert.NotNull(upRatio);
        Assert.Equal(75m, pressure.Value);
        Assert.Equal(0.875m, upRatio.Value);
    }

    [Fact]
    public async Task Mfi_StaysWithinBounds()
    {
        var provider = new MockMarketDataProvider();
        var history = (await provider.GetDailyHistoryAsync("AAPL", AssetClass.Stock, 60))!;
        var highs = history.Candles.Select(c => c.High).ToArray();
        var lows = history.Candles.Select(c => c.Low).ToArray();
        var closes = history.Candles.Select(c => c.Close).ToArray();
        var volumes = history.Candles.Select(c => c.Volume).ToArray();
        var mfi = IndicatorEngine.Mfi(highs, lows, closes, volumes, 14);
        Assert.NotNull(mfi);
        Assert.InRange(mfi!.Value, 0m, 100m);
    }

    [Fact]
    public void WilliamsR_IsNegative()
    {
        var highs = new decimal[] { 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36 };
        var lows = new decimal[] { 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31, 33, 35 };
        var closes = new decimal[] { 9.5m, 11.5m, 13.5m, 15.5m, 17.5m, 19.5m, 21.5m, 23.5m, 25.5m, 27.5m, 29.5m, 31.5m, 33.5m, 35.5m };
        var wr = IndicatorEngine.WilliamsR(highs, lows, closes, 14);
        Assert.NotNull(wr);
        Assert.InRange(wr!.Value, -100m, 0m);
    }

    [Fact]
    public async Task Compute_PopulatesConvictionAndAdvancedFields()
    {
        var provider = new MockMarketDataProvider();
        var history = (await provider.GetDailyHistoryAsync("NVDA", AssetClass.Stock, 260))!;
        var ind = new IndicatorEngine().Compute(history);

        Assert.NotNull(ind.Adx14);
        Assert.NotNull(ind.StochasticK);
        Assert.NotNull(ind.Vwap);
        Assert.NotNull(ind.Mfi14);
        Assert.NotNull(ind.ConvictionScore);
        Assert.InRange(ind.ConvictionScore!.Value, 0m, 100m);
    }
}

public class OptionsTests
{
    private static ScoringEngine BuildScoring() => new(Options.Create(new BotOptions()));

    [Fact]
    public async Task MockOptionsProvider_ReturnsChainAroundPrice()
    {
        var provider = new MockOptionsDataProvider();
        var chain = await provider.GetChainAsync("AAPL", 150m, 7, 45);
        Assert.NotNull(chain);
        Assert.NotEmpty(chain!.Contracts);
        Assert.Contains(chain.Contracts, c => c.Type == OptionType.Call);
        Assert.Contains(chain.Contracts, c => c.Type == OptionType.Put);
        Assert.All(chain.Contracts, c => Assert.True(c.Strike > 0));
    }

    [Fact]
    public async Task Strategist_SuggestsCallForBullishCandidate()
    {
        var marketProvider = new MockMarketDataProvider();
        var optionsProvider = new MockOptionsDataProvider();
        var engine = new IndicatorEngine();
        var scoring = BuildScoring();
        var strategist = new OptionsStrategist(NullLogger<OptionsStrategist>.Instance);

        var history = await marketProvider.GetDailyHistoryAsync("MSFT", AssetClass.Stock, 260);
        var ind = engine.Compute(history!);
        var candidate = scoring.Evaluate(history!, ind);

        var chain = await optionsProvider.GetChainAsync(candidate.Symbol, ind.Price, 7, 45);
        var suggestion = strategist.Suggest(candidate, chain!);

        Assert.NotNull(suggestion);
        Assert.True(suggestion!.Strike > 0);
        Assert.True(suggestion.DaysToExpiration >= 5);
        Assert.False(string.IsNullOrWhiteSpace(suggestion.Describe()));
    }
}
