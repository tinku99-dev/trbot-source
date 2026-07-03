using Microsoft.Azure.Functions.Worker;
using Microsoft.Extensions.Logging;
using TradingResearchBot.Models;
using TradingResearchBot.Services;

namespace TradingResearchBot.Functions;

/// <summary>
/// Fires once per weekday after the US market close to send a single end-of-day
/// digest (typically email). Runs at 20:10 UTC, which is after 15:00 ET in both
/// EDT (16:10 ET) and EST (15:10 ET), so exactly one digest per trading day.
/// Not subject to the intraday market-hours guard by design.
/// </summary>
public sealed class DailyDigestTimerFunction
{
    private readonly ResearchService _research;
    private readonly ILogger<DailyDigestTimerFunction> _logger;

    public DailyDigestTimerFunction(ResearchService research, ILogger<DailyDigestTimerFunction> logger)
    {
        _research = research;
        _logger = logger;
    }

    [Function("DailyDigest")]
    public async Task Run(
        [TimerTrigger("0 10 20 * * 1-5")] TimerInfo timer,
        CancellationToken ct)
    {
        _logger.LogInformation("Daily digest run starting.");
        var report = await _research.RunAsync(RunKind.DailyDigest, ct);
        _logger.LogInformation("Daily digest complete: {Count} candidates.", report.Candidates.Count);
    }
}
