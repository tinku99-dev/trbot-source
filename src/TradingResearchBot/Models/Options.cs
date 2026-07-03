namespace TradingResearchBot.Models;

public enum OptionType
{
    Call,
    Put
}

/// <summary>A single option contract quote (research data only).</summary>
public sealed record OptionContract(
    string UnderlyingSymbol,
    OptionType Type,
    decimal Strike,
    DateOnly Expiration,
    decimal? Bid,
    decimal? Ask,
    decimal? Last,
    long OpenInterest,
    long Volume,
    decimal? ImpliedVolatility,
    decimal? Delta)
{
    public decimal? Mid => Bid is { } b && Ask is { } a ? (b + a) / 2m : Last;
    public int DaysToExpiration => Math.Max(0, Expiration.DayNumber - DateOnly.FromDateTime(DateTime.UtcNow).DayNumber);
}

/// <summary>An options chain for one underlying.</summary>
public sealed class OptionsChain
{
    public required string Symbol { get; init; }
    public required decimal UnderlyingPrice { get; init; }
    public required IReadOnlyList<OptionContract> Contracts { get; init; }
}

/// <summary>A concrete options idea attached to a candidate (informational only).</summary>
public sealed class OptionSuggestion
{
    public required OptionType Type { get; init; }
    public required decimal Strike { get; init; }
    public required DateOnly Expiration { get; init; }
    public int DaysToExpiration { get; init; }
    public decimal? EntryMid { get; init; }
    public decimal? ImpliedVolatility { get; init; }
    public decimal? Delta { get; init; }
    public long OpenInterest { get; init; }
    public string Rationale { get; init; } = "";

    public string Describe() =>
        $"{Type} {Strike:F2} exp {Expiration:yyyy-MM-dd} ({DaysToExpiration}d)" +
        (EntryMid is { } m ? $" @ ~{m:F2}" : "") +
        (Delta is { } d ? $", Δ{d:F2}" : "") +
        (ImpliedVolatility is { } iv ? $", IV {iv * 100:F0}%" : "");
}
