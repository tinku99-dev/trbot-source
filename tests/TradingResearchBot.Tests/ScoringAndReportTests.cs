using Microsoft.Extensions.Options;
using TradingResearchBot.Indicators;
using TradingResearchBot.Models;
using TradingResearchBot.Providers;
using TradingResearchBot.Reports;
using TradingResearchBot.Scoring;
using Xunit;

namespace TradingResearchBot.Tests;

public class ScoringAndReportTests
{
    private static ScoringEngine BuildScoring()
    {
        var options = Options.Create(new BotOptions());
        return new ScoringEngine(options);
    }

    [Fact]
    public async Task MockProvider_ReturnsHistory_WithEnoughBars()
    {
        var provider = new MockMarketDataProvider();
        var history = await provider.GetDailyHistoryAsync("AAPL", AssetClass.Stock, 260);
        Assert.NotNull(history);
        Assert.True(history!.Candles.Count >= 260);
        Assert.All(history.Candles, c => Assert.True(c.Close > 0));
    }

    [Fact]
    public async Task MockProvider_IsDeterministic_PerSymbol()
    {
        var provider = new MockMarketDataProvider();
        var a = await provider.GetDailyHistoryAsync("MSFT", AssetClass.Stock, 260);
        var b = await provider.GetDailyHistoryAsync("MSFT", AssetClass.Stock, 260);
        Assert.Equal(a!.Latest.Close, b!.Latest.Close);
    }

    [Fact]
    public async Task Scoring_AssignsCategoriesAndLevels()
    {
        var provider = new MockMarketDataProvider();
        var engine = new IndicatorEngine();
        var scoring = BuildScoring();

        var history = await provider.GetDailyHistoryAsync("NVDA", AssetClass.Stock, 260);
        var ind = engine.Compute(history!);
        var candidate = scoring.Evaluate(history!, ind);

        Assert.NotEmpty(candidate.Categories);
        Assert.NotNull(candidate.BuyRangeLow);
        Assert.NotNull(candidate.StopLoss);
        Assert.NotNull(candidate.Target1);
        Assert.True(candidate.Target1 > candidate.Indicators.Price);
        Assert.True(candidate.StopLoss < candidate.Indicators.Price);
    }

    [Theory]
    [InlineData(90, "S", "S-tier")]
    [InlineData(75, "A", "A-tier")]
    [InlineData(60, "B", "B-tier")]
    [InlineData(40, "C", "C-tier")]
    public void CandidateTier_ComesFromFinalScore(double score, string tier, string label)
    {
        var candidate = new Candidate
        {
            Symbol = "TEST",
            AssetClass = AssetClass.Stock,
            Indicators = new IndicatorSet { Price = 100m },
            Score = score
        };

        Assert.Equal(tier, candidate.Tier);
        Assert.Equal(label, candidate.TierLabel);
    }

    [Fact]
    public async Task ReportBuilder_RanksAndCaps()
    {
        var provider = new MockMarketDataProvider();
        var engine = new IndicatorEngine();
        var scoring = BuildScoring();
        var builder = new ReportBuilder();

        var symbols = new[] { "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "AMZN", "GOOGL", "META" };
        var candidates = new List<Candidate>();
        foreach (var s in symbols)
        {
            var h = await provider.GetDailyHistoryAsync(s, AssetClass.Stock, 260);
            candidates.Add(scoring.Evaluate(h!, engine.Compute(h!)));
        }

        var report = builder.Build(candidates, maxCandidates: 3);
        Assert.Equal(3, report.Candidates.Count);

        // Verify descending score ordering.
        for (int i = 1; i < report.Candidates.Count; i++)
            Assert.True(report.Candidates[i - 1].Score >= report.Candidates[i].Score);

        var text = builder.RenderText(report);
        Assert.Contains("CANDIDATE REPORT", text);
        Assert.Contains("Tier:", text);
        Assert.Contains("Not financial advice", text, StringComparison.OrdinalIgnoreCase);
    }
}
