using Microsoft.Extensions.Options;
using TradingResearchBot.Models;
using TradingResearchBot.Services;
using Xunit;

namespace TradingResearchBot.Tests;

public class CryptoScalpTests
{
    private static CryptoScalpEvaluator Evaluator(Action<ScalpOptions>? tune = null)
    {
        var opts = new BotOptions();
        opts.Scalp.Enabled = true;
        tune?.Invoke(opts.Scalp);
        return new CryptoScalpEvaluator(Options.Create(opts));
    }

    /// <summary>4-hour snapshot that is clearly trending up (passes the HTF filter).</summary>
    private static IndicatorSet HigherUptrend() => new()
    {
        Price = 100m,
        Vwap = 95m,
        Ema12 = 99m,
        Ema26 = 96m,
        Macd = 1.2m,
        MacdSignal = 0.8m,
        Adx14 = 28m,
        ConvictionScore = 60m
    };

    /// <summary>15-minute snapshot that triggers a long entry (passes the entry filter).</summary>
    private static IndicatorSet EntryTrigger() => new()
    {
        Price = 100m,
        Vwap = 98m,
        Ema12 = 99.5m,
        Ema26 = 98.5m,
        Macd = 0.5m,
        MacdSignal = 0.3m,
        MacdHistogram = 0.2m,
        Rsi14 = 62m,
        VolumeRelativeStrength = 1.8m,
        Atr14 = 2m,
        ObvSlope = 1m,
        ObvPressurePct = 10m,
        ObvUpVolumeRatio = 0.60m,
        ConvictionScore = 65m
    };

    /// <summary>A tiny entry history whose recent swing low sits just under the entry.</summary>
    private static PriceHistory EntryHistory(decimal price = 100m, decimal swingLow = 97m)
    {
        var start = DateTimeOffset.UtcNow.AddMinutes(-15 * 40);
        var candles = new List<Candle>();
        for (int i = 0; i < 40; i++)
        {
            decimal low = i == 30 ? swingLow : price - 0.5m;
            candles.Add(new Candle(start.AddMinutes(15 * i), price - 0.25m, price + 0.5m, low, price, 1000m));
        }
        return new PriceHistory { Symbol = "BTC-USD", AssetClass = AssetClass.Crypto, Candles = candles };
    }

    [Fact]
    public void QualifyingSetup_ProducesScalpCandidate_WithTightStopAndTargets()
    {
        var eval = Evaluator();

        var c = eval.Evaluate("BTC-USD", EntryHistory(), HigherUptrend(), EntryTrigger());

        Assert.NotNull(c);
        Assert.Equal("CryptoScalp", c!.StrategyMode);
        Assert.Contains(ReportCategory.Scalp, c.Categories);

        // Tight stop: ATR(2) * 1.0 below 100 = 98 (swing low 97 is looser, ATR stop wins).
        Assert.Equal(98m, c.StopLoss);
        // Targets: +10% and +20%.
        Assert.Equal(110m, c.Target1);
        Assert.Equal(120m, c.Target2);
        Assert.NotNull(c.PaperTrade);
        Assert.Equal(1_000m, c.PaperTrade!.AllocationUsd);
        // Stop is below entry, T1 below T2.
        Assert.True(c.StopLoss < c.BuyRangeHigh);
        Assert.True(c.Target1 < c.Target2);
    }

    [Fact]
    public void HigherTimeframeDowntrend_IsRejected()
    {
        var eval = Evaluator();
        var bearishHigher = HigherUptrend() with { Macd = 0.2m, MacdSignal = 0.9m }; // MACD below signal

        var c = eval.Evaluate("BTC-USD", EntryHistory(), bearishHigher, EntryTrigger());

        Assert.Null(c);
    }

    [Fact]
    public void EntryOverbought_IsRejected()
    {
        var eval = Evaluator();
        var overbought = EntryTrigger() with { Rsi14 = 80m }; // above MaxEntryRsi

        var c = eval.Evaluate("BTC-USD", EntryHistory(), HigherUptrend(), overbought);

        Assert.Null(c);
    }

    [Fact]
    public void NoVolumeSurge_IsRejected()
    {
        var eval = Evaluator();
        var quiet = EntryTrigger() with { VolumeRelativeStrength = 1.0m }; // below MinEntryVolumeRatio

        var c = eval.Evaluate("BTC-USD", EntryHistory(), HigherUptrend(), quiet);

        Assert.Null(c);
    }

    [Fact]
    public void WeakObvAccumulation_IsRejected()
    {
        var eval = Evaluator();
        var weakObv = EntryTrigger() with { ObvPressurePct = 2m, ObvUpVolumeRatio = 0.49m };

        var c = eval.Evaluate("BTC-USD", EntryHistory(), HigherUptrend(), weakObv);

        Assert.Null(c);
    }

    [Fact]
    public void PoorRewardToRisk_IsRejected()
    {
        // Require an unrealistically high R:R so the standard 2% risk / 10% target fails.
        var eval = Evaluator(s => s.MinRewardRisk = 10m);

        var c = eval.Evaluate("BTC-USD", EntryHistory(), HigherUptrend(), EntryTrigger());

        Assert.Null(c);
    }

    [Fact]
    public void StopRiskIsCappedAtMaxStopPct()
    {
        // Wide ATR would push the stop far below entry; MaxStopPct must cap the risk.
        var eval = Evaluator(s => s.MaxStopPct = 3m);
        var wideAtr = EntryTrigger() with { Atr14 = 20m }; // ATR stop would be 80 (20% risk)

        var c = eval.Evaluate("BTC-USD", EntryHistory(swingLow: 70m), HigherUptrend(), wideAtr);

        Assert.NotNull(c);
        Assert.Equal(97m, c!.StopLoss); // 100 * (1 - 3%) = 97
    }
}
