using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;
using TradingResearchBot.Notifications;
using TradingResearchBot.Providers;

namespace TradingResearchBot.Services;

/// <summary>
/// Runs the multi-timeframe crypto scalp scan: for each crypto symbol it pulls the
/// higher (trend) and entry (trigger) intraday bars, computes indicators on each,
/// evaluates the scalp setup, and routes qualifying ideas to the intraday channel
/// (Discord) with per-day de-duplication kept separate from the breakout alerts.
/// </summary>
public sealed class CryptoScalpService
{
    private readonly IIntradayMarketDataProvider _intraday;
    private readonly IIndicatorEngine _indicators;
    private readonly CryptoScalpEvaluator _evaluator;
    private readonly CryptoScreenerProvider _screener;
    private readonly IAlertStateStore _alertState;
    private readonly NotificationRouter _notifications;
    private readonly MarketHoursGuard _clock;
    private readonly BotOptions _bot;
    private readonly ILogger<CryptoScalpService> _logger;

    public CryptoScalpService(
        IIntradayMarketDataProvider intraday,
        IIndicatorEngine indicators,
        CryptoScalpEvaluator evaluator,
        CryptoScreenerProvider screener,
        IAlertStateStore alertState,
        NotificationRouter notifications,
        MarketHoursGuard clock,
        IOptions<BotOptions> botOptions,
        ILogger<CryptoScalpService> logger)
    {
        _intraday = intraday;
        _indicators = indicators;
        _evaluator = evaluator;
        _screener = screener;
        _alertState = alertState;
        _notifications = notifications;
        _clock = clock;
        _bot = botOptions.Value;
        _logger = logger;
    }

    public async Task<ResearchReport> RunAsync(CancellationToken ct = default)
    {
        var opts = _bot.Scalp;
        var empty = new ResearchReport { Candidates = Array.Empty<Candidate>(), StrategyMode = "CryptoScalp" };

        if (!opts.Enabled)
        {
            _logger.LogInformation("Crypto scalp disabled (Bot:Scalp:Enabled=false).");
            return empty;
        }

        var now = DateTimeOffset.UtcNow;

        // Build the crypto universe: dynamic (screened movers) or static (configured list).
        var symbols = await BuildCryptoUniverseAsync(opts, ct);
        if (symbols.Count == 0)
        {
            _logger.LogInformation("No crypto symbols available for scalp scan.");
            return empty;
        }

        _logger.LogInformation(
            "Crypto scalp scan: {Count} symbols ({Entry}/{Higher}).",
            symbols.Count, opts.EntryTimeframe, opts.HigherTimeframe);

        var candidates = new List<Candidate>(symbols.Count);
        foreach (var symbol in symbols)
        {
            ct.ThrowIfCancellationRequested();
            try
            {
                var htf = await _intraday.GetIntradayHistoryAsync(
                    symbol, AssetClass.Crypto, opts.HigherTimeframe, opts.HigherBars, ct);
                var etf = await _intraday.GetIntradayHistoryAsync(
                    symbol, AssetClass.Crypto, opts.EntryTimeframe, opts.EntryBars, ct);

                if (htf is null || etf is null || !htf.HasEnough(30) || !etf.HasEnough(30))
                {
                    _logger.LogDebug("Insufficient intraday data for {Symbol}.", symbol);
                    continue;
                }

                var higher = _indicators.Compute(htf);
                var entry = _indicators.Compute(etf);

                var candidate = _evaluator.Evaluate(symbol, etf, higher, entry);
                if (candidate is not null)
                    candidates.Add(candidate);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                _logger.LogWarning(ex, "Scalp evaluation failed for {Symbol}.", symbol);
            }
        }

        var ranked = candidates
            .OrderByDescending(c => c.Score)
            .Take(_bot.MaxCandidates)
            .ToList();

        var report = new ResearchReport
        {
            GeneratedAtUtc = now,
            Candidates = ranked,
            StrategyMode = "CryptoScalp"
        };

        _logger.LogInformation("Crypto scalp scan produced {Count} setup(s).", ranked.Count);
        await DispatchAsync(report, now, ct);
        return report;
    }

    /// <summary>Alerts new scalp setups via the intraday channel, de-duplicated per day.</summary>
    private async Task DispatchAsync(ResearchReport report, DateTimeOffset now, CancellationToken ct)
    {
        // Namespace the dedup key so scalp alerts never collide with breakout alerts.
        var key = $"scalp-{_clock.LocalDate(now)}";
        var state = await _alertState.GetAsync(key, ct);

        var fresh = report.Candidates
            .Where(c => !state.AlertedSymbols.Contains(c.Symbol))
            .ToList();

        if (fresh.Count == 0)
        {
            if (_notifications.SuppressEmpty)
            {
                _logger.LogInformation("No new scalp setups; suppressing alert.");
                return;
            }
            await _notifications.Intraday.NotifyAsync(report, ct);
            return;
        }

        var alert = new ResearchReport
        {
            GeneratedAtUtc = report.GeneratedAtUtc,
            Candidates = fresh,
            StrategyMode = "CryptoScalp"
        };
        await _notifications.Intraday.NotifyAsync(alert, ct);

        foreach (var c in fresh)
            state.AlertedSymbols.Add(c.Symbol);
        await _alertState.SaveAsync(state, ct);
        _logger.LogInformation("Alerted {Count} new scalp setup(s).", fresh.Count);
    }

    /// <summary>
    /// Builds the crypto universe: dynamic (top movers from screener) or static (configured list).
    /// </summary>
    private async Task<List<string>> BuildCryptoUniverseAsync(ScalpOptions opts, CancellationToken ct)
    {
        var symbols = new List<string>();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        // Always-include symbols (pinned leaders like BTC, ETH) go first.
        foreach (var pinned in opts.AlwaysIncludeSymbols())
            if (seen.Add(pinned))
                symbols.Add(pinned);

        if (string.Equals(opts.Mode, "Dynamic", StringComparison.OrdinalIgnoreCase))
        {
            _logger.LogInformation(
                "Dynamic crypto selection: screening by {Timeframe}, top {TopN}, min {MinChg}% change.",
                opts.ScreenerTimeframe, opts.TopN, opts.MinChangePct);

            var movers = await _screener.GetTopMoversAsync(
                opts.TopN,
                opts.ScreenerTimeframe,
                opts.MinChangePct,
                opts.SortBy,
                ct);

            foreach (var m in movers)
                if (seen.Add(m.Symbol))
                    symbols.Add(m.Symbol);

            _logger.LogInformation(
                "Screened {MoverCount} movers; total universe: {Total} symbols.",
                movers.Count, symbols.Count);
        }
        else
        {
            // Static mode: use configured list or fall back to crypto universe.
            var configured = opts.SymbolList().ToList();
            if (configured.Count == 0)
                configured = _bot.CryptoSymbols().ToList();

            foreach (var s in configured)
                if (seen.Add(s))
                    symbols.Add(s);
        }

        return symbols;
    }
}
