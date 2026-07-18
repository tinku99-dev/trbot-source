namespace TradingResearchBot.Models;

/// <summary>Strongly-typed bot options bound from configuration ("Bot" section).</summary>
public sealed class BotOptions
{
    public const string SectionName = "Bot";

    public string MarketProvider { get; set; } = "Mock";
    public string TimeZone { get; set; } = "Eastern Standard Time";
    public string MarketOpenLocal { get; set; } = "08:30";
    public string MarketCloseLocal { get; set; } = "15:00";

    /// <summary>
    /// Evening cutoff for CRYPTO evaluation (in <see cref="TimeZone"/>). Crypto trades
    /// 24/7, so it keeps running after the stock close up to this time, every day.
    /// Default "22:00" ET = 9:00 PM Central.
    /// </summary>
    public string CryptoCloseLocal { get; set; } = "22:00";

    public int MaxCandidates { get; set; } = 20;

    /// <summary>When true, notifications are logged but never actually sent.</summary>
    public bool DryRun { get; set; } = true;

    public string StockUniverse { get; set; } = "";
    public string CryptoUniverse { get; set; } = "";

    public Sma200Options Sma200 { get; set; } = new();
    public OptionsResearchOptions Options { get; set; } = new();
    public AiOptionsResearchOptions AiOptions { get; set; } = new();
    public ProviderOptions Providers { get; set; } = new();
    public NotificationOptions Notifications { get; set; } = new();
    public StrategyOptions Strategy { get; set; } = new();
    public UniverseOptions Universe { get; set; } = new();

    /// <summary>Multi-timeframe crypto scalp strategy (15-minute + 4-hour).</summary>
    public ScalpOptions Scalp { get; set; } = new();

    /// <summary>Paper-trade sizing used to enrich research alerts with simulated trade plans.</summary>
    public PaperTradingOptions PaperTrading { get; set; } = new();

    /// <summary>Institutional-style research overlays. Safe default is shadow-only.</summary>
    public InstitutionalOverlayOptions Institutional { get; set; } = new();

    public IEnumerable<string> StockSymbols() => Split(StockUniverse);
    public IEnumerable<string> CryptoSymbols() => Split(CryptoUniverse);

    private static IEnumerable<string> Split(string csv) =>
        (csv ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
}

/// <summary>
/// Research-only overlays for sector-relative strength, beta-neutral pair ideas,
/// and post-earnings announcement drift. ShadowOnly prevents score/gate changes.
/// </summary>
public sealed class InstitutionalOverlayOptions
{
    public bool Enabled { get; set; } = true;
    public bool ShadowOnly { get; set; } = true;
    public int RelativeStrengthLookbackDays { get; set; } = 20;
    public decimal MinSectorExcessReturnPct { get; set; } = 1.5m;
    public bool RequireSectorStrengthForBreakouts { get; set; } = true;
    public int PairLookbackDays { get; set; } = 60;
    public decimal MinPairCorrelation { get; set; } = 0.70m;
    public int PeadMaxAgeDays { get; set; } = 30;
    public decimal MinPeadSurpriseZ { get; set; } = 1.0m;

    /// <summary>Stock ticker to liquid sector/index benchmark.</summary>
    public Dictionary<string, string> SectorBenchmarks { get; set; } = new(StringComparer.OrdinalIgnoreCase)
    {
        ["AMD"] = "SMH", ["NVDA"] = "SMH", ["AVGO"] = "SMH", ["AMAT"] = "SMH", ["SMCI"] = "SMH",
        ["AAPL"] = "XLK", ["MSFT"] = "XLK", ["CRM"] = "XLK", ["PLTR"] = "XLK",
        ["AMZN"] = "XLY", ["TSLA"] = "XLY", ["GOOGL"] = "XLC", ["META"] = "XLC", ["NFLX"] = "XLC",
        ["JPM"] = "XLF", ["XOM"] = "XLE", ["BP"] = "XLE", ["COST"] = "XLP", ["COIN"] = "ARKF"
    };

    /// <summary>Stock ticker to economically similar peer used for shadow hedging.</summary>
    public Dictionary<string, string> PairPeers { get; set; } = new(StringComparer.OrdinalIgnoreCase)
    {
        ["AMD"] = "NVDA", ["NVDA"] = "AMD", ["AVGO"] = "QCOM", ["AMAT"] = "LRCX",
        ["AAPL"] = "MSFT", ["MSFT"] = "AAPL", ["GOOGL"] = "META", ["META"] = "GOOGL",
        ["AMZN"] = "WMT", ["TSLA"] = "GM", ["JPM"] = "BAC", ["XOM"] = "CVX", ["BP"] = "XOM",
        ["COST"] = "WMT", ["CRM"] = "NOW", ["NFLX"] = "DIS", ["COIN"] = "HOOD"
    };

    /// <summary>
    /// Licensed/provider-supplied EPS events. At least four prior surprises are
    /// required before a z-score is considered statistically usable.
    /// </summary>
    public List<EarningsEventOptions> EarningsEvents { get; set; } = new();
}

public sealed class EarningsEventOptions
{
    public string Symbol { get; set; } = "";
    public DateTimeOffset ReportedAtUtc { get; set; }
    public decimal ActualEps { get; set; }
    public decimal EstimateEps { get; set; }
}

/// <summary>Settings for the combined 200-day SMA screener.</summary>
public sealed class Sma200Options
{
    public bool Enabled { get; set; } = true;
    public decimal VolumeRatioMin { get; set; } = 1.20m;
    public decimal BounceZonePct { get; set; } = 2.0m;
    public decimal TargetPct { get; set; } = 10.0m;
    public decimal StopPct { get; set; } = 3.0m;
}

/// <summary>
/// Selects how candidates are ranked and filtered. Each mode applies hard "gates"
/// (a symbol must pass all of them to appear in the report) plus mode-specific
/// scoring bonuses. Thresholds are tuned separately per asset class because crypto
/// is structurally more volatile than equities.
/// </summary>
public sealed class StrategyOptions
{
    /// <summary>BreakoutVolume | Blended | Trend. Default Blended keeps every candidate.</summary>
    public string Mode { get; set; } = "Blended";

    public AssetThresholds Stock { get; set; } = new()
    {
        MinVolumeRatio = 1.5m,
        MinAdx = 20m,
        RequireAbove200Sma = true
    };

    public AssetThresholds Crypto { get; set; } = new()
    {
        MinVolumeRatio = 2.0m,
        MinAdx = 20m,
        RequireAbove200Sma = true
    };

    public AssetThresholds For(AssetClass assetClass) =>
        assetClass == AssetClass.Crypto ? Crypto : Stock;
}

/// <summary>Per-asset-class gating thresholds for a strategy.</summary>
public sealed class AssetThresholds
{
    /// <summary>Minimum (today's volume ÷ trailing average) to confirm participation.</summary>
    public decimal MinVolumeRatio { get; set; } = 1.5m;

    /// <summary>Minimum ADX(14) to require an actual trend (filters chop).</summary>
    public decimal MinAdx { get; set; } = 20m;

    /// <summary>When true, only research names trading above their 200-day SMA.</summary>
    public bool RequireAbove200Sma { get; set; } = true;
}

/// <summary>
/// Controls how the symbol universe is selected each run.
///   • Static  — use the fixed StockUniverse / CryptoUniverse CSV lists.
///   • Dynamic — pull a fresh list from the market each run (Alpaca screener:
///               most-actives + top gainers/losers), filtered for quality.
/// Crypto is always taken from CryptoUniverse (no free crypto screener).
/// </summary>
public sealed class UniverseOptions
{
    /// <summary>Static | Dynamic. Default Static (deterministic, no extra API calls).</summary>
    public string Mode { get; set; } = "Static";

    /// <summary>Total number of stock names to keep after filtering/dedup.</summary>
    public int TopN { get; set; } = 40;

    /// <summary>Drop anything below this price (filters penny-stock pump-and-dumps).</summary>
    public decimal MinPrice { get; set; } = 5m;

    /// <summary>Drop anything above this price (optional ceiling; 0 = no cap).</summary>
    public decimal MaxPrice { get; set; } = 0m;

    /// <summary>Minimum daily volume to keep a most-actives/movers name.</summary>
    public long MinVolume { get; set; } = 1_000_000;

    /// <summary>Include the most-actively-traded names (by volume).</summary>
    public bool IncludeMostActives { get; set; } = true;

    /// <summary>Include the day's top gainers (momentum/breakout candidates).</summary>
    public bool IncludeGainers { get; set; } = true;

    /// <summary>Include the day's top losers (fallout/reversal candidates).</summary>
    public bool IncludeLosers { get; set; } = false;

    /// <summary>
    /// Symbols always added regardless of the screener — pin specific sectors/leaders
    /// you always want researched (e.g. semis: NVDA,AMD,AVGO).
    /// </summary>
    public string AlwaysInclude { get; set; } = "";

    public IEnumerable<string> AlwaysIncludeSymbols() =>
        (AlwaysInclude ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
}

/// <summary>
/// Multi-timeframe crypto scalp strategy. A higher timeframe (default 4-hour)
/// establishes trend direction; an entry timeframe (default 15-minute) provides
/// the trigger. Targets a quick 10-20% move with a deliberately tight stop.
///
/// NOTE: a true 10-20% move on a 15-minute crypto scalp is aggressive and fires
/// far more often on volatile alt-coins than on BTC/ETH. The gates below are
/// intentionally strict so alerts stay high-quality and rare.
/// </summary>
public sealed class ScalpOptions
{
    /// <summary>Master switch. When false, the scalp timer does nothing.</summary>
    public bool Enabled { get; set; } = false;

    /// <summary>Higher (trend) timeframe token, e.g. "4Hour".</summary>
    public string HigherTimeframe { get; set; } = "4Hour";

    /// <summary>Entry (trigger) timeframe token, e.g. "15Min".</summary>
    public string EntryTimeframe { get; set; } = "15Min";

    /// <summary>Bars to pull for the higher timeframe (≥ ~30 for stable indicators).</summary>
    public int HigherBars { get; set; } = 120;

    /// <summary>Bars to pull for the entry timeframe.</summary>
    public int EntryBars { get; set; } = 120;

    /// <summary>Recent entry-timeframe bars scanned for the protective swing low.</summary>
    public int SwingLookbackBars { get; set; } = 12;

    /// <summary>First take-profit target, percent above entry.</summary>
    public decimal Target1Pct { get; set; } = 10m;

    /// <summary>Second take-profit target, percent above entry.</summary>
    public decimal Target2Pct { get; set; } = 20m;

    /// <summary>Maximum risk (stop distance) as a percent below entry — the "minimum" tight stop cap.</summary>
    public decimal MaxStopPct { get; set; } = 4m;

    /// <summary>ATR multiple (entry timeframe) used to place the stop.</summary>
    public decimal StopAtrMult { get; set; } = 1.0m;

    /// <summary>Minimum reward:risk to T1 required to emit an alert.</summary>
    public decimal MinRewardRisk { get; set; } = 2.0m;

    /// <summary>Minimum entry-timeframe volume surge (today vs trailing average).</summary>
    public decimal MinEntryVolumeRatio { get; set; } = 1.5m;

    /// <summary>Lower RSI(14) bound on the entry timeframe (momentum present).</summary>
    public decimal MinEntryRsi { get; set; } = 50m;

    /// <summary>Upper RSI(14) bound on the entry timeframe (not yet overbought).</summary>
    public decimal MaxEntryRsi { get; set; } = 72m;

    /// <summary>Minimum ADX(14) on the higher timeframe to confirm a real trend (0 disables).</summary>
    public decimal MinHigherAdx { get; set; } = 20m;

    /// <summary>When true, require normalized OBV accumulation on the entry timeframe.</summary>
    public bool RequireObvConfirmation { get; set; } = true;

    /// <summary>Minimum OBV change as a percent of trailing volume.</summary>
    public decimal MinObvPressurePct { get; set; } = 8m;

    /// <summary>Minimum share of trailing volume printed on up-closing bars.</summary>
    public decimal MinObvUpVolumeRatio { get; set; } = 0.52m;

    /// <summary>Symbols to scalp (CSV). When blank and Mode=Static, falls back to the crypto universe.</summary>
    public string Symbols { get; set; } = "";

    // --- Dynamic crypto selection (screen by movement/activity) ---

    /// <summary>Static | Dynamic. Dynamic screens all crypto pairs by recent movement.</summary>
    public string Mode { get; set; } = "Static";

    /// <summary>Number of top movers to select when Mode=Dynamic.</summary>
    public int TopN { get; set; } = 15;

    /// <summary>Timeframe for screening (e.g., "4Hour", "1Day"). Used to calculate % change.</summary>
    public string ScreenerTimeframe { get; set; } = "4Hour";

    /// <summary>Minimum absolute % change to be considered a mover.</summary>
    public decimal MinChangePct { get; set; } = 2.0m;

    /// <summary>Sort by "movement" (% change) or "volume".</summary>
    public string SortBy { get; set; } = "movement";

    /// <summary>Symbols always included regardless of screening (pinned leaders).</summary>
    public string AlwaysInclude { get; set; } = "BTC-USD,ETH-USD";

    public IEnumerable<string> SymbolList() =>
        (Symbols ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

    public IEnumerable<string> AlwaysIncludeSymbols() =>
        (AlwaysInclude ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
}

public sealed class PaperTradingOptions
{
    /// <summary>When true, Discord alerts include a simulated paper-trade plan.</summary>
    public bool Enabled { get; set; } = true;

    /// <summary>Simulated USD allocation per candidate.</summary>
    public decimal CapitalPerTradeUsd { get; set; } = 1_000m;

    /// <summary>Maximum simultaneous paper positions used for alert budget context.</summary>
    public int MaxOpenPositions { get; set; } = 10;

    /// <summary>
    /// Explicit arming switch for Alpaca paper orders. Disabled by default; this
    /// code refuses any host other than paper-api.alpaca.markets.
    /// </summary>
    public bool SubmitToAlpaca { get; set; } = false;

    /// <summary>Minimum final research score required for an automated paper entry.</summary>
    public double MinCandidateScore { get; set; } = 70;

    /// <summary>Maximum new automated stock entries submitted per UTC trading day.</summary>
    public int MaxNewPositionsPerDay { get; set; } = 2;

    /// <summary>Maximum fraction of paper account equity exposed across open positions.</summary>
    public decimal MaxAccountExposurePct { get; set; } = 25m;

    /// <summary>Maximum account-equity risk to the structural stop for one entry.</summary>
    public decimal RiskPerTradePct { get; set; } = 0.5m;

    public decimal TotalCapitalUsd => CapitalPerTradeUsd * Math.Max(MaxOpenPositions, 0);
}

/// <summary>Settings for options research enrichment.</summary>
public sealed class OptionsResearchOptions
{
    public bool Enabled { get; set; } = true;
    /// <summary>Provider key: Mock | Tradier | Polygon | LicensedHttp.</summary>
    public string Provider { get; set; } = "Mock";

    /// <summary>Expiration window used for options research ideas.</summary>
    public int MinDaysToExpiration { get; set; } = 10;
    public int MaxDaysToExpiration { get; set; } = 45;

    /// <summary>Liquidity gates for contracts. These keep the bot away from wide, stale chains.</summary>
    public long MinOpenInterest { get; set; } = 300;
    public long MinVolume { get; set; } = 50;
    public decimal MaxBidAskSpreadPct { get; set; } = 12m;

    /// <summary>Directional delta targets. Scalp ideas can use a stronger, nearer-the-money contract.</summary>
    public decimal TargetDelta { get; set; } = 0.40m;
    public decimal ScalpTargetDelta { get; set; } = 0.55m;
}

/// <summary>Optional AI grading for real options ideas. The model must not invent prices.</summary>
public sealed class AiOptionsResearchOptions
{
    public bool Enabled { get; set; } = false;
    public string Provider { get; set; } = "None";
    public string Model { get; set; } = "gemini-2.5-flash-lite";
    public string ApiKey { get; set; } = "";
    public string BaseUrl { get; set; } = "https://generativelanguage.googleapis.com";
    public int MinScoreToAskAi { get; set; } = 70;
}

/// <summary>API credentials/endpoints for licensed data providers (keep keys in secrets).</summary>
public sealed class ProviderOptions
{
    public PolygonOptions Polygon { get; set; } = new();
    public TradierOptions Tradier { get; set; } = new();
    public AlpacaOptions Alpaca { get; set; } = new();
}

/// <summary>Polygon.io — covers both stocks and crypto via the aggregates API.</summary>
public sealed class PolygonOptions
{
    public string ApiKey { get; set; } = "";
    public string BaseUrl { get; set; } = "https://api.polygon.io";

    /// <summary>
    /// Minimum spacing between requests, in milliseconds, to respect provider rate
    /// limits (Polygon free tier = 5 calls/min). 13000ms ≈ 4.6 calls/min (safe).
    /// Set to 0 to disable throttling (paid tiers with no per-minute cap).
    /// </summary>
    public int MinRequestIntervalMs { get; set; } = 13000;
}

/// <summary>Tradier — licensed options chains (and equity quotes). Use sandbox for testing.</summary>
public sealed class TradierOptions
{
    public string ApiKey { get; set; } = "";
    /// <summary>https://api.tradier.com (live) or https://sandbox.tradier.com (sandbox).</summary>
    public string BaseUrl { get; set; } = "https://api.tradier.com";
}

/// <summary>
/// Alpaca — free real-time market data (IEX feed) covering BOTH stocks and crypto.
/// Sign up at https://alpaca.markets and create API keys (Paper keys work for data).
/// Stocks: https://data.alpaca.markets/v2/stocks/{symbol}/bars
/// Crypto: https://data.alpaca.markets/v1beta3/crypto/us/bars?symbols=BTC/USD
/// </summary>
public sealed class AlpacaOptions
{
    /// <summary>APCA-API-KEY-ID header value.</summary>
    public string ApiKeyId { get; set; } = "";

    /// <summary>APCA-API-SECRET-KEY header value.</summary>
    public string ApiSecret { get; set; } = "";

    /// <summary>Market data base URL (not the trading API base).</summary>
    public string BaseUrl { get; set; } = "https://data.alpaca.markets";

    /// <summary>
    /// Paper brokerage endpoint. The broker implementation validates this exact
    /// Alpaca paper host and will refuse a live trading URL.
    /// </summary>
    public string PaperTradingBaseUrl { get; set; } = "https://paper-api.alpaca.markets";

    /// <summary>Equity data feed: "iex" (free, real-time) or "sip" (paid, full market).</summary>
    public string Feed { get; set; } = "iex";

    /// <summary>
    /// Minimum spacing between requests, in ms. Alpaca free allows 200 req/min, so 0
    /// (no throttle) is fine. Set a positive value only if you hit rate limits.
    /// </summary>
    public int MinRequestIntervalMs { get; set; } = 0;
}

public sealed class NotificationOptions
{
    /// <summary>None | Discord | Email | Both</summary>
    public string Provider { get; set; } = "None";

    /// <summary>Channel for intraday breakout alerts: Discord | Email | None.</summary>
    public string IntradayChannel { get; set; } = "Discord";

    /// <summary>Channel for the once-a-day end-of-day digest: Email | Discord | None.</summary>
    public string DailyChannel { get; set; } = "Email";

    /// <summary>When true, an intraday run with no NEW qualified names sends nothing.</summary>
    public bool SuppressEmpty { get; set; } = true;

    /// <summary>Minimum candidate score required for intraday alerts. Manual research still shows the full report.</summary>
    public double MinIntradayScore { get; set; } = 75;

    /// <summary>Maximum fresh candidates to send in one intraday notification.</summary>
    public int MaxIntradayAlerts { get; set; } = 3;

    /// <summary>When true, stock intraday alerts require a concrete options idea.</summary>
    public bool RequireOptionIdeaForStockAlerts { get; set; } = true;

    public DiscordOptions Discord { get; set; } = new();
    public EmailOptions Email { get; set; } = new();
}

public sealed class DiscordOptions
{
    public string WebhookUrl { get; set; } = "";
}

public sealed class EmailOptions
{
    public string SmtpHost { get; set; } = "";
    public int SmtpPort { get; set; } = 587;
    public bool UseSsl { get; set; } = true;
    public string Username { get; set; } = "";
    public string Password { get; set; } = "";
    public string From { get; set; } = "";
    public string To { get; set; } = "";
}
