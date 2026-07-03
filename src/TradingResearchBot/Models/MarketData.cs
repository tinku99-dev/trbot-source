namespace TradingResearchBot.Models;

/// <summary>The kind of tradable asset.</summary>
public enum AssetClass
{
    Stock,
    Crypto
}

/// <summary>A symbol selected for research, plus the asset class it belongs to.</summary>
public readonly record struct UniverseEntry(string Symbol, AssetClass AssetClass);

/// <summary>
/// Why a research run is happening, which determines how results are notified:
///   • Intraday    — frequent runs during market hours; alert only NEW qualified names.
///   • DailyDigest — one end-of-day run; email a summary of what qualifies at close.
/// </summary>
public enum RunKind
{
    Intraday,
    DailyDigest
}

/// <summary>
/// Per-trading-day state used to de-duplicate intraday alerts so the same symbol
/// isn't re-announced on every 10-minute run. Persisted between runs.
/// </summary>
public sealed class DailyAlertState
{
    /// <summary>The trading day this state belongs to (yyyy-MM-dd, market local time).</summary>
    public string Date { get; set; } = "";

    /// <summary>Symbols already alerted today (so we only post fresh breakouts).</summary>
    public HashSet<string> AlertedSymbols { get; set; } = new(StringComparer.OrdinalIgnoreCase);
}

/// <summary>A single OHLCV bar.</summary>
public sealed record Candle(
    DateTimeOffset Timestamp,
    decimal Open,
    decimal High,
    decimal Low,
    decimal Close,
    decimal Volume);

/// <summary>A symbol plus its historical bars (oldest first).</summary>
public sealed class PriceHistory
{
    public required string Symbol { get; init; }
    public required AssetClass AssetClass { get; init; }
    public required IReadOnlyList<Candle> Candles { get; init; }

    public Candle Latest => Candles[^1];
    public bool HasEnough(int bars) => Candles.Count >= bars;
}
