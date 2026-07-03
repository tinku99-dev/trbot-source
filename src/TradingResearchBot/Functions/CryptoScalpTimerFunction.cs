using Microsoft.Azure.Functions.Worker;
using Microsoft.Extensions.Logging;
using TradingResearchBot.Services;

namespace TradingResearchBot.Functions;

/// <summary>
/// Timer-triggered crypto scalp scan. Runs every 15 minutes to align with the
/// entry timeframe; the in-code crypto-window guard (and the Bot:Scalp:Enabled
/// switch) decide whether work actually happens. Crypto runs every day until the
/// configured evening cutoff (default 9 PM Central).
/// </summary>
public sealed class CryptoScalpTimerFunction
{
    private readonly CryptoScalpService _scalp;
    private readonly MarketHoursGuard _guard;
    private readonly ILogger<CryptoScalpTimerFunction> _logger;

    public CryptoScalpTimerFunction(
        CryptoScalpService scalp,
        MarketHoursGuard guard,
        ILogger<CryptoScalpTimerFunction> logger)
    {
        _scalp = scalp;
        _guard = guard;
        _logger = logger;
    }

    [Function("CryptoScalpTimer")]
    public async Task Run(
        [TimerTrigger("0 */15 * * * *")] TimerInfo timer,
        CancellationToken ct)
    {
        var nowUtc = DateTimeOffset.UtcNow;
        if (!_guard.IsCryptoOpen(nowUtc))
        {
            _logger.LogInformation("Crypto window closed at {NowUtc:O}; skipping scalp scan.", nowUtc);
            return;
        }

        var report = await _scalp.RunAsync(ct);
        _logger.LogInformation("Crypto scalp run complete: {Count} setup(s).", report.Candidates.Count);
    }
}
