using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;
using TradingResearchBot.Notifications;

namespace TradingResearchBot.Services;

/// <summary>
/// Orchestrates a full research run: fetch data for the configured universe,
/// compute indicators, score/categorize, build the report, persist and notify.
/// </summary>
public sealed class ResearchService
{
    private const int LookbackDays = 260; // enough for a 200-SMA
    private const int OptionsMinDte = 7;
    private const int OptionsMaxDte = 45;

    private readonly IMarketDataProvider _market;
    private readonly IUniverseProvider _universe;
    private readonly IIndicatorEngine _indicators;
    private readonly IScoringEngine _scoring;
    private readonly IReportBuilder _reportBuilder;
    private readonly IReportStore _store;
    private readonly IAlertStateStore _alertState;
    private readonly NotificationRouter _notifications;
    private readonly MarketHoursGuard _clock;
    private readonly IOptionsDataProvider _options;
    private readonly IOptionsStrategist _optionsStrategist;
    private readonly BotOptions _optionsConfig;
    private readonly ILogger<ResearchService> _logger;

    public ResearchService(
        IMarketDataProvider market,
        IUniverseProvider universe,
        IIndicatorEngine indicators,
        IScoringEngine scoring,
        IReportBuilder reportBuilder,
        IReportStore store,
        IAlertStateStore alertState,
        NotificationRouter notifications,
        MarketHoursGuard clock,
        IOptionsDataProvider options,
        IOptionsStrategist optionsStrategist,
        IOptions<BotOptions> botOptions,
        ILogger<ResearchService> logger)
    {
        _market = market;
        _universe = universe;
        _indicators = indicators;
        _scoring = scoring;
        _reportBuilder = reportBuilder;
        _store = store;
        _alertState = alertState;
        _notifications = notifications;
        _clock = clock;
        _options = options;
        _optionsStrategist = optionsStrategist;
        _optionsConfig = botOptions.Value;
        _logger = logger;
    }

    /// <summary>Convenience overload — runs an intraday pass (used by the timer trigger).</summary>
    public Task<ResearchReport> RunAsync(CancellationToken ct = default) =>
        RunAsync(RunKind.Intraday, ct);

    /// <summary>
    /// Side-effect-free full-universe evaluation for on-demand dashboard/manual runs.
    /// Unlike the intraday pass this ignores the market-hours session filter (so the
    /// Research Summary is never empty just because the US session is closed) and does
    /// NOT dispatch notifications or persist state (so clicking "Run Now" can't spam
    /// alerts). Returns both qualifying candidates and the rejected watchlist.
    /// </summary>
    public async Task<ResearchReport> PreviewAsync(CancellationToken ct = default)
    {
        _logger.LogInformation("Research PREVIEW run started using provider '{Provider}'.", _market.Name);

        var universe = await _universe.GetUniverseAsync(ct);
        var report = await EvaluateAsync(universe, ct);

        _logger.LogInformation("Preview report built with {Count} candidate(s).", report.Candidates.Count);
        return report;
    }

    public async Task<ResearchReport> RunAsync(RunKind kind, CancellationToken ct = default)
    {
        _logger.LogInformation("Research run ({Kind}) started using provider '{Provider}'.", kind, _market.Name);

        var universe = await _universe.GetUniverseAsync(ct);

        // Intraday: only evaluate asset classes whose session is currently open.
        // Stocks run during the weekday session; crypto runs every day until the
        // configured evening cutoff (default 9 PM Central). The daily digest ignores
        // this and evaluates everything.
        if (kind == RunKind.Intraday)
        {
            var now = DateTimeOffset.UtcNow;
            bool stocksOpen = _clock.AreStocksOpen(now);
            bool cryptoOpen = _clock.IsCryptoOpen(now);

            universe = universe
                .Where(e => e.AssetClass == AssetClass.Stock ? stocksOpen : cryptoOpen)
                .ToList();

            if (universe.Count == 0)
            {
                _logger.LogInformation("No asset class is currently in-session; skipping run.");
                return _reportBuilder.Build(Array.Empty<Candidate>(), _optionsConfig.MaxCandidates);
            }

            _logger.LogInformation(
                "In-session: stocks={Stocks}, crypto={Crypto} → evaluating {Count} symbols.",
                stocksOpen, cryptoOpen, universe.Count);
        }

        var report = await EvaluateAsync(universe, ct);
        _logger.LogInformation("Report built with {Count} candidates.", report.Candidates.Count);

        await _store.SaveAsync(report, ct);
        await DispatchAsync(report, kind, ct);

        return report;
    }

    /// <summary>
    /// Evaluates each symbol in the universe into a scored candidate and builds the
    /// report (with optional options enrichment). No persistence or notifications.
    /// </summary>
    private async Task<ResearchReport> EvaluateAsync(IReadOnlyList<UniverseEntry> universe, CancellationToken ct)
    {
        var candidates = new List<Candidate>(universe.Count);

        foreach (var (symbol, assetClass) in universe)
        {
            ct.ThrowIfCancellationRequested();
            try
            {
                var history = await _market.GetDailyHistoryAsync(symbol, assetClass, LookbackDays, ct);
                if (history is null || history.Candles.Count == 0)
                {
                    _logger.LogDebug("No data for {Symbol}.", symbol);
                    continue;
                }

                var ind = _indicators.Compute(history);
                var candidate = _scoring.Evaluate(history, ind);
                candidates.Add(candidate);
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "Failed to evaluate {Symbol}.", symbol);
            }
        }

        var report = _reportBuilder.Build(candidates, _optionsConfig.MaxCandidates);

        if (_optionsConfig.Options.Enabled)
            await EnrichWithOptionsAsync(report, ct);

        return report;
    }

    /// <summary>Routes the report to the right channel based on cadence (with intraday dedup).</summary>
    private async Task DispatchAsync(ResearchReport report, RunKind kind, CancellationToken ct)
    {
        if (kind == RunKind.DailyDigest)
        {
            // One end-of-day summary of whatever qualifies at close.
            await _notifications.Daily.NotifyAsync(report, ct);
            return;
        }

        // Intraday: alert only names that haven't already been announced today.
        var localDate = _clock.LocalDate(DateTimeOffset.UtcNow);
        var state = await _alertState.GetAsync(localDate, ct);

        var fresh = report.Candidates
            .Where(c => !state.AlertedSymbols.Contains(c.Symbol))
            .ToList();

        if (fresh.Count == 0)
        {
            if (_notifications.SuppressEmpty)
            {
                _logger.LogInformation("No new qualified names; suppressing intraday alert.");
                return;
            }
            await _notifications.Intraday.NotifyAsync(report, ct);
            return;
        }

        var alert = new ResearchReport
        {
            GeneratedAtUtc = report.GeneratedAtUtc,
            Candidates = fresh,
            StrategyMode = report.StrategyMode,
            Rejected = report.Rejected
        };
        await _notifications.Intraday.NotifyAsync(alert, ct);

        foreach (var c in fresh)
            state.AlertedSymbols.Add(c.Symbol);
        await _alertState.SaveAsync(state, ct);
        _logger.LogInformation("Alerted {Count} new qualified name(s) intraday.", fresh.Count);
    }

    private async Task EnrichWithOptionsAsync(ResearchReport report, CancellationToken ct)
    {
        foreach (var candidate in report.Candidates)
        {
            ct.ThrowIfCancellationRequested();

            // Options apply to stocks/ETFs; only enrich relevant ideas to limit calls.
            if (candidate.AssetClass != AssetClass.Stock) continue;
            if (!candidate.Categories.Contains(ReportCategory.OptionsWatch) &&
                !candidate.Categories.Contains(ReportCategory.Scalp) &&
                !candidate.Categories.Contains(ReportCategory.Breakout) &&
                !candidate.Categories.Contains(ReportCategory.Fallout))
                continue;

            try
            {
                var chain = await _options.GetChainAsync(
                    candidate.Symbol, candidate.Indicators.Price,
                    OptionsMinDte, OptionsMaxDte, ct);
                if (chain is null) continue;

                candidate.OptionIdea = _optionsStrategist.Suggest(candidate, chain);
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "Options enrichment failed for {Symbol}.", candidate.Symbol);
            }
        }
    }
}
