using Microsoft.Azure.Functions.Worker;
using Microsoft.Extensions.Logging;
using TradingResearchBot.Models;
using TradingResearchBot.Services;

namespace TradingResearchBot.Functions;

/// <summary>
/// Timer-triggered entry point. The cron runs every 10 minutes on weekdays over
/// a broad UTC window; an in-code Eastern-time guard enforces the precise
/// 08:30-15:00 ET trading window (DST-safe). Set WEBSITE_TIME_ZONE to change the
/// host's cron interpretation if desired.
/// </summary>
public sealed class ResearchTimerFunction
{
    private readonly ResearchService _research;
    private readonly MarketHoursGuard _guard;
    private readonly ILogger<ResearchTimerFunction> _logger;

    public ResearchTimerFunction(
        ResearchService research,
        MarketHoursGuard guard,
        ILogger<ResearchTimerFunction> logger)
    {
        _research = research;
        _guard = guard;
        _logger = logger;
    }

    // Every 10 minutes, all hours/days. The in-code MarketHoursGuard decides what to
    // actually evaluate: stocks during the weekday session, crypto every day until
    // the evening cutoff (default 9 PM Central). Cron is permissive; guard is precise.
    [Function("ResearchTimer")]
    public async Task Run(
        [TimerTrigger("0 */10 * * * *")] TimerInfo timer,
        CancellationToken ct)
    {
        var nowUtc = DateTimeOffset.UtcNow;
        if (!_guard.AreStocksOpen(nowUtc) && !_guard.IsCryptoOpen(nowUtc))
        {
            _logger.LogInformation(
                "No session open ({Tz}) at {NowUtc:O}; skipping run.",
                _guard.TimeZoneId, nowUtc);
            return;
        }

        _logger.LogInformation("Session open; starting research run at {NowUtc:O}.", nowUtc);
        var report = await _research.RunAsync(RunKind.Intraday, ct);
        _logger.LogInformation("Research run complete: {Count} candidates.", report.Candidates.Count);
    }
}
