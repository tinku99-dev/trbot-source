using System.Net;
using Microsoft.Azure.Functions.Worker;
using Microsoft.Azure.Functions.Worker.Http;
using Microsoft.Extensions.Logging;
using TradingResearchBot.Models;
using TradingResearchBot.Services;

namespace TradingResearchBot.Functions;

/// <summary>
/// On-demand HTTP trigger to run a research pass immediately (useful for local
/// testing and manual refreshes). Bypasses the market-hours guard by design.
/// Secured with the Function authorization level (requires a function key).
/// </summary>
public sealed class RunNowHttpFunction
{
    private readonly ResearchService _research;
    private readonly ILogger<RunNowHttpFunction> _logger;

    public RunNowHttpFunction(ResearchService research, ILogger<RunNowHttpFunction> logger)
    {
        _research = research;
        _logger = logger;
    }

    [Function("RunNow")]
    public async Task<HttpResponseData> Run(
        [HttpTrigger(AuthorizationLevel.Function, "post", "get", Route = "run")] HttpRequestData req,
        CancellationToken ct)
    {
        _logger.LogInformation("Manual research run requested via HTTP.");
        var report = await _research.PreviewAsync(ct);

        var response = req.CreateResponse(HttpStatusCode.OK);
        await response.WriteAsJsonAsync(new
        {
            generatedAtUtc = report.GeneratedAtUtc,
            strategy = report.StrategyMode,
            count = report.Candidates.Count,
            disclaimer = report.Disclaimer,
            candidates = report.Candidates.Select(c => new
            {
                c.Symbol,
                assetClass = c.AssetClass.ToString(),
                c.Score,
                tier = c.Tier,
                tierLabel = c.TierLabel,
                conviction = c.Conviction,
                categories = c.Categories.Select(x => x.ToString()),
                c.BuyRangeLow,
                c.BuyRangeHigh,
                c.StopLoss,
                c.Target1,
                c.Target2,
                option = c.OptionIdea is null ? null : c.OptionIdea.Describe(),
                patterns = c.Patterns.Distinct(),
                institutional = c.Institutional,
                alpacaPaperOrder = c.AlpacaPaperOrder
            }),
            rejected = report.Rejected.Select(c => new
            {
                c.Symbol,
                assetClass = c.AssetClass.ToString(),
                c.Score,
                tier = c.Tier,
                tierLabel = c.TierLabel,
                price = c.Indicators.Price,
                buyRangeLow = c.BuyRangeLow,
                buyRangeHigh = c.BuyRangeHigh,
                stopLoss = c.StopLoss,
                target1 = c.Target1,
                target2 = c.Target2,
                volumeRatio = c.Indicators.VolumeRelativeStrength,
                adx = c.Indicators.Adx14,
                aboveSma200 = c.Indicators.Sma200 is { } s && c.Indicators.Price > s,
                reason = GateReason(c)
            })
        }, ct);

        return response;
    }

    /// <summary>Explains which BreakoutVolume/Trend gate(s) a rejected symbol failed.</summary>
    private static string GateReason(Candidate c)
    {
        var ind = c.Indicators;
        var reasons = new List<string>();

        bool above200 = ind.Sma200 is { } s && ind.Price > s;
        if (!above200) reasons.Add("below 200-SMA");
        if (ind.Adx14 is { } adx && adx < 20) reasons.Add($"weak trend (ADX {adx:F0} < 20)");
        decimal minVol = c.AssetClass == AssetClass.Crypto ? 2.0m : 1.5m;
        if (ind.VolumeRelativeStrength is { } v && v < minVol) reasons.Add($"no volume ({v:F2}x < {minVol:F1}x)");

        return reasons.Count > 0 ? string.Join("; ", reasons) : "did not meet strategy gates";
    }
}
