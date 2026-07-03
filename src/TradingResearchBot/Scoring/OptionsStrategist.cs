using Microsoft.Extensions.Logging;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Scoring;

/// <summary>
/// Picks a single, liquid, directional options idea for a candidate (research only).
/// Bullish candidates → a slightly OTM call; Fallout candidates → a slightly OTM put.
/// Prefers contracts with reasonable open interest and ~0.30-0.55 |delta|.
/// </summary>
public sealed class OptionsStrategist : IOptionsStrategist
{
    private readonly ILogger<OptionsStrategist> _logger;

    public OptionsStrategist(ILogger<OptionsStrategist> logger) => _logger = logger;

    public OptionSuggestion? Suggest(Candidate candidate, OptionsChain chain)
    {
        if (chain.Contracts.Count == 0) return null;

        bool bearish = candidate.Categories.Contains(ReportCategory.Fallout)
                       && !candidate.Categories.Contains(ReportCategory.Breakout);
        var type = bearish ? OptionType.Put : OptionType.Call;

        decimal targetDelta = candidate.Categories.Contains(ReportCategory.Scalp) ? 0.55m : 0.40m;

        var liquid = chain.Contracts
            .Where(c => c.Type == type && c.OpenInterest >= 100 && c.Mid is > 0)
            .Where(c => c.DaysToExpiration >= 5)
            .ToList();

        if (liquid.Count == 0)
        {
            _logger.LogDebug("No liquid {Type} contracts for {Symbol}.", type, candidate.Symbol);
            return null;
        }

        // Prefer the contract whose |delta| is closest to target, tie-break on OI.
        var best = liquid
            .OrderBy(c => Math.Abs(Math.Abs(c.Delta ?? 0.5m) - targetDelta))
            .ThenByDescending(c => c.OpenInterest)
            .First();

        string rationale = bearish
            ? $"Bearish setup → {type} near {targetDelta:F2} delta for defined-risk downside research."
            : $"Bullish conviction {candidate.Conviction:F0}/100 → {type} near {targetDelta:F2} delta.";

        return new OptionSuggestion
        {
            Type = best.Type,
            Strike = best.Strike,
            Expiration = best.Expiration,
            DaysToExpiration = best.DaysToExpiration,
            EntryMid = best.Mid,
            ImpliedVolatility = best.ImpliedVolatility,
            Delta = best.Delta,
            OpenInterest = best.OpenInterest,
            Rationale = rationale
        };
    }
}
