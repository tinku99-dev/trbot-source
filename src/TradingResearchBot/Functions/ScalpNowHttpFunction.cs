using System.Net;
using Microsoft.Azure.Functions.Worker;
using Microsoft.Azure.Functions.Worker.Http;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using TradingResearchBot.Models;
using TradingResearchBot.Providers;
using TradingResearchBot.Services;

namespace TradingResearchBot.Functions;

/// <summary>
/// On-demand HTTP trigger to run the multi-timeframe crypto scalp scan immediately
/// (useful for manual testing/verification). Bypasses the timer cadence but still
/// respects the crypto-window guard inside <see cref="CryptoScalpService"/>.
/// Secured with the Function authorization level (requires a function key).
/// </summary>
public sealed class ScalpNowHttpFunction
{
    private readonly CryptoScalpService _scalp;
    private readonly CryptoScreenerProvider _screener;
    private readonly BotOptions _bot;
    private readonly ILogger<ScalpNowHttpFunction> _logger;

    public ScalpNowHttpFunction(
        CryptoScalpService scalp,
        CryptoScreenerProvider screener,
        IOptions<BotOptions> botOptions,
        ILogger<ScalpNowHttpFunction> logger)
    {
        _scalp = scalp;
        _screener = screener;
        _bot = botOptions.Value;
        _logger = logger;
    }

    [Function("ScalpNow")]
    public async Task<HttpResponseData> Run(
        [HttpTrigger(AuthorizationLevel.Function, "post", "get", Route = "scalp")] HttpRequestData req,
        CancellationToken ct)
    {
        _logger.LogInformation("Manual crypto scalp scan requested via HTTP.");

        // Get screener diagnostics before running the scan.
        var opts = _bot.Scalp;
        IReadOnlyList<CryptoMover>? movers = null;
        if (string.Equals(opts.Mode, "Dynamic", StringComparison.OrdinalIgnoreCase))
        {
            movers = await _screener.GetTopMoversAsync(
                opts.TopN, opts.ScreenerTimeframe, opts.MinChangePct, opts.SortBy, ct);
        }

        var report = await _scalp.RunAsync(ct);

        var response = req.CreateResponse(HttpStatusCode.OK);
        await response.WriteAsJsonAsync(new
        {
            generatedAtUtc = report.GeneratedAtUtc,
            strategy = report.StrategyMode,
            count = report.Candidates.Count,
            disclaimer = report.Disclaimer,
            screenerMode = opts.Mode,
            screenedMovers = movers?.Select(m => new
            {
                m.Symbol,
                m.Price,
                changePct = m.ChangePct,
                volume = m.Volume
            }),
            setups = report.Candidates.Select(c => new
            {
                c.Symbol,
                c.Score,
                tier = c.Tier,
                tierLabel = c.TierLabel,
                conviction = c.Conviction,
                price = c.Indicators.Price,
                buyRangeLow = c.BuyRangeLow,
                buyRangeHigh = c.BuyRangeHigh,
                stopLoss = c.StopLoss,
                target1 = c.Target1,
                target2 = c.Target2,
                rsi = c.Indicators.Rsi14,
                vwap = c.Indicators.Vwap,
                macd = c.Indicators.Macd,
                macdSignal = c.Indicators.MacdSignal,
                volumeRatio = c.Indicators.VolumeRelativeStrength,
                signals = c.Signals,
                patterns = c.Patterns.Distinct()
            })
        }, ct);

        return response;
    }
}
