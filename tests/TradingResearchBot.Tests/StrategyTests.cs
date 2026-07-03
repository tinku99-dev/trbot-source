using Microsoft.Extensions.Options;
using TradingResearchBot.Indicators;
using TradingResearchBot.Models;
using TradingResearchBot.Reports;
using TradingResearchBot.Scoring;
using Xunit;

namespace TradingResearchBot.Tests;

public class StrategyTests
{
    private static ScoringEngine Scoring(string mode)
    {
        var opts = new BotOptions();
        opts.Strategy.Mode = mode;
        return new ScoringEngine(Options.Create(opts));
    }

    /// <summary>Clean uptrend (high ADX, above 200-SMA) with a final volume spike.</summary>
    private static PriceHistory QualifyingHistory(AssetClass assetClass = AssetClass.Stock)
    {
        const int n = 220;
        var candles = new List<Candle>(n);
        var start = DateTimeOffset.UtcNow.AddDays(-n);
        for (int i = 0; i < n; i++)
        {
            decimal close = 100m + i * 0.5m;       // monotonic uptrend → strong ADX, well above SMA200
            decimal open = close - 0.25m;
            decimal high = close + 1m;
            decimal low = open - 1m;
            // Baseline volume everywhere, a 3x spike on the final bar.
            decimal volume = i == n - 1 ? 300_000m : 100_000m;
            candles.Add(new Candle(start.AddDays(i), open, high, low, close, volume));
        }
        return new PriceHistory { Symbol = "QUAL", AssetClass = assetClass, Candles = candles };
    }

    /// <summary>Same uptrend but the final bar has only average volume → fails the volume gate.</summary>
    private static PriceHistory LowVolumeHistory()
    {
        const int n = 220;
        var candles = new List<Candle>(n);
        var start = DateTimeOffset.UtcNow.AddDays(-n);
        for (int i = 0; i < n; i++)
        {
            decimal close = 100m + i * 0.5m;
            decimal open = close - 0.25m;
            decimal high = close + 1m;
            decimal low = open - 1m;
            decimal volume = 100_000m;             // no spike anywhere
            candles.Add(new Candle(start.AddDays(i), open, high, low, close, volume));
        }
        return new PriceHistory { Symbol = "LOWVOL", AssetClass = AssetClass.Stock, Candles = candles };
    }

    /// <summary>Choppy, directionless series → weak ADX, fails the trend gate.</summary>
    private static PriceHistory ChoppyHistory()
    {
        const int n = 220;
        var candles = new List<Candle>(n);
        var start = DateTimeOffset.UtcNow.AddDays(-n);
        for (int i = 0; i < n; i++)
        {
            decimal close = i % 2 == 0 ? 100m : 101m;   // oscillate, no trend
            decimal open = close;
            decimal high = 101.5m;
            decimal low = 99.5m;
            decimal volume = 100_000m;
            candles.Add(new Candle(start.AddDays(i), open, high, low, close, volume));
        }
        return new PriceHistory { Symbol = "CHOP", AssetClass = AssetClass.Stock, Candles = candles };
    }

    [Fact]
    public void BreakoutVolume_QualifyingSymbol_PassesAndIsTagged()
    {
        var engine = new IndicatorEngine();
        var scoring = Scoring("BreakoutVolume");
        var history = QualifyingHistory();

        var c = scoring.Evaluate(history, engine.Compute(history));

        Assert.Equal("BreakoutVolume", c.StrategyMode);
        Assert.True(c.StrategyQualified, "Clean uptrend with volume spike should pass the gates.");
        Assert.Contains(ReportCategory.Breakout, c.Categories);
        Assert.Contains(c.Signals, s => s.Contains("Breakout-Volume gate passed"));
    }

    [Fact]
    public void BreakoutVolume_LowVolume_IsRejected()
    {
        var engine = new IndicatorEngine();
        var scoring = Scoring("BreakoutVolume");
        var history = LowVolumeHistory();

        var c = scoring.Evaluate(history, engine.Compute(history));

        Assert.False(c.StrategyQualified, "No volume confirmation should fail the breakout gate.");
    }

    [Fact]
    public void BreakoutVolume_Choppy_IsRejected()
    {
        var engine = new IndicatorEngine();
        var scoring = Scoring("BreakoutVolume");
        var history = ChoppyHistory();

        var c = scoring.Evaluate(history, engine.Compute(history));

        Assert.False(c.StrategyQualified, "A directionless series should fail the trend (ADX) gate.");
    }

    [Fact]
    public void Crypto_UsesStricterVolumeThreshold()
    {
        var engine = new IndicatorEngine();
        var opts = new BotOptions();
        opts.Strategy.Mode = "BreakoutVolume";
        opts.Strategy.Stock.MinVolumeRatio = 1.5m;
        opts.Strategy.Crypto.MinVolumeRatio = 5.0m; // deliberately very strict
        var scoring = new ScoringEngine(Options.Create(opts));

        // 3x volume spike: clears the 1.5x stock bar but not the 5.0x crypto bar.
        var asStock = scoring.Evaluate(
            QualifyingHistory(AssetClass.Stock), engine.Compute(QualifyingHistory(AssetClass.Stock)));
        var asCrypto = scoring.Evaluate(
            QualifyingHistory(AssetClass.Crypto), engine.Compute(QualifyingHistory(AssetClass.Crypto)));

        Assert.True(asStock.StrategyQualified, "3x volume should clear the 1.5x stock threshold.");
        Assert.False(asCrypto.StrategyQualified, "3x volume should not clear the 5.0x crypto threshold.");
    }

    [Fact]
    public void Blended_KeepsAllCandidates_NoGating()
    {
        var engine = new IndicatorEngine();
        var scoring = Scoring("Blended");

        var qualifies = scoring.Evaluate(LowVolumeHistory(), engine.Compute(LowVolumeHistory()));
        var choppy = scoring.Evaluate(ChoppyHistory(), engine.Compute(ChoppyHistory()));

        Assert.True(qualifies.StrategyQualified);
        Assert.True(choppy.StrategyQualified);
    }

    [Fact]
    public void ReportBuilder_DropsNonQualifiedCandidates()
    {
        var engine = new IndicatorEngine();
        var scoring = Scoring("BreakoutVolume");
        var builder = new ReportBuilder();

        var pass = scoring.Evaluate(QualifyingHistory(), engine.Compute(QualifyingHistory()));
        var fail = scoring.Evaluate(LowVolumeHistory(), engine.Compute(LowVolumeHistory()));

        var report = builder.Build(new[] { pass, fail }, maxCandidates: 20);

        Assert.Single(report.Candidates);
        Assert.Equal("QUAL", report.Candidates[0].Symbol);
    }
}
