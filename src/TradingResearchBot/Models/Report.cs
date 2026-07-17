namespace TradingResearchBot.Models;

/// <summary>Strategy bucket a candidate is categorized into.</summary>
public enum ReportCategory
{
    Scalp,        // very short-term, 10-20% quick move targets
    ShortTerm,
    Swing,
    LongTerm,
    Breakout,
    Fallout,      // breakdown / short-bias watch
    OptionsWatch
}

/// <summary>A single actionable research idea (NOT a trade order).</summary>
public sealed class Candidate
{
    public required string Symbol { get; init; }
    public required AssetClass AssetClass { get; init; }
    public required IndicatorSet Indicators { get; init; }

    public double Score { get; set; }
    public List<ReportCategory> Categories { get; } = new();
    public List<string> Signals { get; } = new();

    // Suggested research levels (informational only)
    public decimal? BuyRangeLow { get; set; }
    public decimal? BuyRangeHigh { get; set; }
    public decimal? StopLoss { get; set; }
    public decimal? Target1 { get; set; }
    public decimal? Target2 { get; set; }

    /// <summary>Pattern/setup labels recognized (e.g. "200-SMA crossover", "Bollinger squeeze").</summary>
    public List<string> Patterns { get; } = new();

    /// <summary>Optional concrete options idea (informational only) for OptionsWatch candidates.</summary>
    public OptionSuggestion? OptionIdea { get; set; }

    /// <summary>Optional simulated paper-trade plan for research alerts.</summary>
    public PaperTradePlan? PaperTrade { get; set; }

    /// <summary>Shadow-only institutional research measurements and hedge ideas.</summary>
    public InstitutionalOverlayResult? Institutional { get; set; }

    /// <summary>Outcome of an optional Alpaca paper-order submission.</summary>
    public AlpacaPaperOrderResult? AlpacaPaperOrder { get; set; }

    /// <summary>0-100 composite conviction from the indicator battery.</summary>
    public decimal Conviction => Indicators.ConvictionScore ?? 0;

    /// <summary>Quality tier derived from the final candidate score: S, A, B, or C.</summary>
    public string Tier => Score switch
    {
        >= 85 => "S",
        >= 70 => "A",
        >= 55 => "B",
        _ => "C"
    };

    /// <summary>Human-friendly tier label for reports and alerts.</summary>
    public string TierLabel => $"{Tier}-tier";

    /// <summary>The strategy mode that evaluated this candidate (e.g. "BreakoutVolume").</summary>
    public string? StrategyMode { get; set; }

    /// <summary>
    /// False when the active strategy's hard gates rejected this symbol. Non-qualified
    /// candidates are excluded from the final report. Always true in Blended mode.
    /// </summary>
    public bool StrategyQualified { get; set; } = true;
}

/// <summary>
/// Measurements produced by institutional-style overlays. These are observations,
/// not orders; <see cref="ShadowOnly"/> is true under the safe default configuration.
/// </summary>
public sealed class InstitutionalOverlayResult
{
    public bool ShadowOnly { get; init; } = true;
    public string? SectorBenchmark { get; set; }
    public decimal? AssetReturnPct { get; set; }
    public decimal? BenchmarkReturnPct { get; set; }
    public decimal? SectorExcessReturnPct { get; set; }
    public bool? RelativeStrengthQualified { get; set; }
    public string? HedgePeer { get; set; }
    public decimal? PairCorrelation { get; set; }
    public decimal? HedgeBeta { get; set; }
    public decimal? ShortNotionalPctOfLong { get; set; }
    public DateTimeOffset? EarningsReportedAtUtc { get; set; }
    public decimal? EarningsSurprisePct { get; set; }
    public decimal? EarningsSurpriseZ { get; set; }
    public bool? PeadQualified { get; set; }
}

public sealed record AlpacaPaperOrderResult(
    string Status,
    string? OrderId,
    string? ClientOrderId,
    decimal Quantity,
    decimal EstimatedNotionalUsd,
    decimal StopPrice,
    decimal TakeProfitPrice,
    string? Message);

public sealed record PaperTradePlan(
    decimal AllocationUsd,
    decimal EstimatedQuantity,
    decimal EntryPrice,
    decimal StopPrice,
    decimal Target1Price,
    decimal Target2Price,
    decimal RiskUsd,
    decimal Target1ProfitUsd,
    decimal Target2ProfitUsd,
    decimal TotalBudgetUsd,
    int MaxOpenPositions);

/// <summary>The full generated research report for one run.</summary>
public sealed class ResearchReport
{
    public DateTimeOffset GeneratedAtUtc { get; init; } = DateTimeOffset.UtcNow;
    public required IReadOnlyList<Candidate> Candidates { get; init; }

    /// <summary>The active strategy mode that produced this report.</summary>
    public string StrategyMode { get; init; } = "Blended";

    /// <summary>Symbols that were evaluated but rejected by the strategy gates (ranked by score).</summary>
    public IReadOnlyList<Candidate> Rejected { get; init; } = Array.Empty<Candidate>();

    public string Disclaimer { get; init; } =
        "Research/educational output only. Not financial advice. Not a live-trading system.";
}
