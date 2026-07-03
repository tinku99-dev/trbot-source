using TradingResearchBot.Models;

namespace TradingResearchBot.Abstractions;

/// <summary>
/// Compliant market-data abstraction. Implementations should wrap an official,
/// licensed provider (Polygon, Tradier, Alpaca, Finnhub, etc.). Do NOT implement
/// against unofficial/scraped or brokerage-internal endpoints (e.g. Robinhood).
/// </summary>
public interface IMarketDataProvider
{
    string Name { get; }

    /// <summary>Fetch daily OHLCV history (oldest first) for a symbol.</summary>
    Task<PriceHistory?> GetDailyHistoryAsync(
        string symbol,
        AssetClass assetClass,
        int lookbackDays,
        CancellationToken ct = default);
}

/// <summary>
/// Optional capability: fetch INTRADAY OHLCV bars at an arbitrary timeframe
/// (e.g. "15Min", "1Hour", "4Hour"). Used by the multi-timeframe crypto scalp
/// strategy. Only providers with a real-time intraday feed (Alpaca) implement this;
/// others can be wired to a no-op implementation that returns null.
/// </summary>
public interface IIntradayMarketDataProvider
{
    string Name { get; }

    /// <summary>
    /// Fetch the most recent <paramref name="lookbackBars"/> intraday bars (oldest first)
    /// at the given provider <paramref name="timeframe"/>. Returns null if unavailable.
    /// </summary>
    Task<PriceHistory?> GetIntradayHistoryAsync(
        string symbol,
        AssetClass assetClass,
        string timeframe,
        int lookbackBars,
        CancellationToken ct = default);
}

/// <summary>Computes a technical indicator snapshot from price history.</summary>
public interface IIndicatorEngine
{
    IndicatorSet Compute(PriceHistory history);
}

/// <summary>
/// Selects the symbol universe to research for a run. Implementations may return a
/// fixed configured list (static) or a freshly-screened list from the market (dynamic).
/// </summary>
public interface IUniverseProvider
{
    Task<IReadOnlyList<UniverseEntry>> GetUniverseAsync(CancellationToken ct = default);
}

/// <summary>
/// Compliant options-chain data abstraction. Implement against a licensed options
/// data API (e.g. Tradier, Polygon, Alpaca). Do NOT use unofficial brokerage
/// endpoints (e.g. Robinhood) — they have no official public options API.
/// </summary>
public interface IOptionsDataProvider
{
    string Name { get; }

    /// <summary>Fetch an options chain for the underlying, near the given expiration window.</summary>
    Task<OptionsChain?> GetChainAsync(
        string symbol,
        decimal underlyingPrice,
        int minDaysToExpiration,
        int maxDaysToExpiration,
        CancellationToken ct = default);
}

/// <summary>Builds an options suggestion for a candidate from a chain.</summary>
public interface IOptionsStrategist
{
    OptionSuggestion? Suggest(Candidate candidate, OptionsChain chain);
}

/// <summary>Scores and categorizes a symbol into research candidates.</summary>
public interface IScoringEngine
{
    Candidate Evaluate(PriceHistory history, IndicatorSet indicators);
}

/// <summary>Builds the final research report from scored candidates.</summary>
public interface IReportBuilder
{
    ResearchReport Build(IEnumerable<Candidate> candidates, int maxCandidates);

    /// <summary>Render a human-readable plain-text version of the report.</summary>
    string RenderText(ResearchReport report);
}

/// <summary>Persists generated reports (blob, table, file, etc.).</summary>
public interface IReportStore
{
    Task SaveAsync(ResearchReport report, CancellationToken ct = default);
}

/// <summary>
/// Stores per-trading-day alert state so intraday notifications can de-duplicate
/// (only announce a symbol the first time it qualifies each day).
/// </summary>
public interface IAlertStateStore
{
    /// <summary>Get today's state (empty if none yet, or if the stored day rolled over).</summary>
    Task<DailyAlertState> GetAsync(string localDate, CancellationToken ct = default);

    Task SaveAsync(DailyAlertState state, CancellationToken ct = default);
}

/// <summary>Delivers the report to a channel (Discord, email, ...).</summary>
public interface INotificationService
{
    Task NotifyAsync(ResearchReport report, CancellationToken ct = default);
}
