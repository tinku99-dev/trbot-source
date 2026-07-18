using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
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
    private readonly OptionsResearchOptions _options;
    private readonly ILogger<OptionsStrategist> _logger;

    public OptionsStrategist(IOptions<BotOptions> options, ILogger<OptionsStrategist> logger)
    {
        _options = options.Value.Options;
        _logger = logger;
    }

    public OptionSuggestion? Suggest(Candidate candidate, OptionsChain chain)
    {
        if (chain.Contracts.Count == 0) return null;

        bool bearish = candidate.Categories.Contains(ReportCategory.Fallout)
                       && !candidate.Categories.Contains(ReportCategory.Breakout);
        var type = bearish ? OptionType.Put : OptionType.Call;

        decimal targetDelta = candidate.Categories.Contains(ReportCategory.Scalp)
            ? _options.ScalpTargetDelta
            : _options.TargetDelta;

        var liquid = chain.Contracts
            .Where(c => c.Type == type && c.OpenInterest >= _options.MinOpenInterest)
            .Where(c => c.Volume >= _options.MinVolume)
            .Where(c => c.Mid is > 0)
            .Where(c => c.DaysToExpiration >= _options.MinDaysToExpiration &&
                        c.DaysToExpiration <= _options.MaxDaysToExpiration)
            .Where(c => BidAskSpreadPct(c) <= _options.MaxBidAskSpreadPct)
            .ToList();

        if (liquid.Count == 0)
        {
            _logger.LogDebug("No liquid {Type} contracts for {Symbol}.", type, candidate.Symbol);
            return null;
        }

        // Prefer liquid, tighter-spread contracts near the target delta.
        var best = liquid
            .OrderBy(c => BidAskSpreadPct(c))
            .ThenBy(c => Math.Abs(Math.Abs(c.Delta ?? targetDelta) - targetDelta))
            .ThenByDescending(c => c.Volume)
            .ThenByDescending(c => c.OpenInterest)
            .First();

        string rationale = bearish
            ? $"Bearish setup -> {type} near {targetDelta:F2} delta; liquidity and spread gates passed."
            : $"Bullish conviction {candidate.Conviction:F0}/100 -> {type} near {targetDelta:F2} delta; liquidity and spread gates passed.";

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

    private static decimal BidAskSpreadPct(OptionContract c)
    {
        if (c.Bid is not { } bid || c.Ask is not { } ask || bid <= 0 || ask <= 0 || ask < bid)
            return decimal.MaxValue;

        var mid = (bid + ask) / 2m;
        return mid <= 0 ? decimal.MaxValue : (ask - bid) / mid * 100m;
    }
}
