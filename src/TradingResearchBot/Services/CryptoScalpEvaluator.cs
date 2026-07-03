using Microsoft.Extensions.Options;
using TradingResearchBot.Models;

namespace TradingResearchBot.Services;

/// <summary>
/// Pure rules for the multi-timeframe crypto scalp setup. Given a higher-timeframe
/// (trend) indicator snapshot and an entry-timeframe (trigger) snapshot, decides
/// whether a high-quality long scalp exists and, if so, returns an informational
/// <see cref="Candidate"/> with tight stop and 10-20% targets.
///
/// Long-only, research/educational output — NOT a trade order.
/// Logic:
///   • Higher timeframe (e.g. 4H) must be trending up: price &gt; VWAP, EMA12 &gt; EMA26,
///     MACD &gt; signal, and ADX above threshold.
///   • Entry timeframe (e.g. 15m) must trigger: price &gt; VWAP, MACD cross up with a
///     rising histogram, RSI in a momentum-but-not-overbought band, and a volume surge.
///   • Stop is the tighter of a recent swing low and a small ATR multiple, capped so
///     risk never exceeds MaxStopPct. The setup is only emitted if reward:risk to T1
///     clears MinRewardRisk.
/// </summary>
public sealed class CryptoScalpEvaluator
{
    private readonly ScalpOptions _opts;
    private readonly PaperTradingOptions _paper;

    public CryptoScalpEvaluator(IOptions<BotOptions> options)
    {
        _opts = options.Value.Scalp;
        _paper = options.Value.PaperTrading;
    }

    /// <summary>Returns a scalp candidate when the setup qualifies; otherwise null.</summary>
    public Candidate? Evaluate(string symbol, PriceHistory entryHistory, IndicatorSet higher, IndicatorSet entry)
    {
        // --- Higher-timeframe trend filter ---
        bool htfAboveVwap = higher.Vwap is { } hv && higher.Price > hv;
        bool htfEmaUp = higher.Ema12 is { } he12 && higher.Ema26 is { } he26 && he12 > he26;
        bool htfMacdUp = higher.Macd is { } hm && higher.MacdSignal is { } hs && hm > hs;
        bool htfAdx = _opts.MinHigherAdx <= 0 || (higher.Adx14 is { } adx && adx >= _opts.MinHigherAdx);
        if (!(htfAboveVwap && htfEmaUp && htfMacdUp && htfAdx))
            return null;

        // --- Entry-timeframe trigger ---
        decimal price = entry.Price;
        if (price <= 0) return null;

        bool aboveVwap = entry.Vwap is { } v && price > v;
        bool macdUp = entry.Macd is { } em && entry.MacdSignal is { } es && em > es
                      && (entry.MacdHistogram ?? 0m) > 0m;
        bool rsiOk = entry.Rsi14 is { } rsi && rsi >= _opts.MinEntryRsi && rsi <= _opts.MaxEntryRsi;
        bool volOk = (entry.VolumeRelativeStrength ?? 0m) >= _opts.MinEntryVolumeRatio;
        bool obvOk = !_opts.RequireObvConfirmation
            || (entry.ObvPressurePct is { } pressure && pressure >= _opts.MinObvPressurePct
                && entry.ObvUpVolumeRatio is { } upRatio && upRatio >= _opts.MinObvUpVolumeRatio);
        if (!(aboveVwap && macdUp && rsiOk && volOk && obvOk))
            return null;

        // --- Levels: tight stop, 10-20% targets ---
        decimal atr = entry.Atr14 is { } a && a > 0 ? a : price * 0.01m;
        decimal swingLow = RecentLow(entryHistory, _opts.SwingLookbackBars);
        decimal atrStop = price - atr * _opts.StopAtrMult;

        // Prefer the structure stop (swing low) when it is tighter than the ATR stop,
        // but never risk more than MaxStopPct of the entry price.
        decimal stop = Math.Max(swingLow, atrStop);
        decimal maxRiskStop = price * (1m - _opts.MaxStopPct / 100m);
        stop = Math.Max(stop, maxRiskStop);          // cap the risk (tight stop)
        stop = Math.Min(stop, price * 0.999m);       // ensure strictly below entry

        decimal riskPct = (price - stop) / price * 100m;
        if (riskPct <= 0) return null;

        decimal rrT1 = _opts.Target1Pct / riskPct;
        if (rrT1 < _opts.MinRewardRisk)
            return null;                             // poor reward:risk → skip

        var c = new Candidate
        {
            Symbol = symbol,
            AssetClass = AssetClass.Crypto,
            Indicators = entry
        };
        c.StrategyMode = "CryptoScalp";
        c.StrategyQualified = true;
        c.Categories.Add(ReportCategory.Scalp);
        c.Patterns.Add($"Multi-timeframe scalp ({_opts.EntryTimeframe}/{_opts.HigherTimeframe})");

        c.BuyRangeLow = Math.Round(Math.Min(price, entry.Vwap ?? price), 2);
        c.BuyRangeHigh = Math.Round(price, 2);
        c.StopLoss = Math.Round(stop, 2);
        c.Target1 = Math.Round(price * (1m + _opts.Target1Pct / 100m), 2);
        c.Target2 = Math.Round(price * (1m + _opts.Target2Pct / 100m), 2);
        c.PaperTrade = BuildPaperTradePlan(price, c.StopLoss.Value, c.Target1.Value, c.Target2.Value);

        c.Signals.Add(
            $"{_opts.HigherTimeframe} trend up (price>VWAP, EMA12>EMA26, MACD>signal" +
            (higher.Adx14 is { } ha ? $", ADX {ha:F0}" : "") + ")");
        c.Signals.Add(
            $"{_opts.EntryTimeframe} trigger (>VWAP, MACD cross↑, RSI {entry.Rsi14:F0}, " +
            $"Vol {entry.VolumeRelativeStrength:F2}x)");
        if (_opts.RequireObvConfirmation)
            c.Signals.Add($"OBV accumulation {entry.ObvPressurePct:+0.0;-0.0;0.0}% pressure, {entry.ObvUpVolumeRatio:P0} up-volume");
        c.Signals.Add($"Risk {riskPct:F1}% → T1 +{_opts.Target1Pct:F0}% (R:R {rrT1:F1}:1)");

        c.Score = Math.Round(ScalpScore(higher, entry, rrT1), 2);
        return c;
    }

    private PaperTradePlan? BuildPaperTradePlan(decimal entry, decimal stop, decimal target1, decimal target2)
    {
        if (!_paper.Enabled || _paper.CapitalPerTradeUsd <= 0 || entry <= 0)
            return null;

        var quantity = _paper.CapitalPerTradeUsd / entry;
        return new PaperTradePlan(
            AllocationUsd: Math.Round(_paper.CapitalPerTradeUsd, 2),
            EstimatedQuantity: Math.Round(quantity, 8),
            EntryPrice: Math.Round(entry, 4),
            StopPrice: Math.Round(stop, 4),
            Target1Price: Math.Round(target1, 4),
            Target2Price: Math.Round(target2, 4),
            RiskUsd: Math.Round(Math.Max(entry - stop, 0m) * quantity, 2),
            Target1ProfitUsd: Math.Round(Math.Max(target1 - entry, 0m) * quantity, 2),
            Target2ProfitUsd: Math.Round(Math.Max(target2 - entry, 0m) * quantity, 2),
            TotalBudgetUsd: Math.Round(_paper.TotalCapitalUsd, 2),
            MaxOpenPositions: Math.Max(_paper.MaxOpenPositions, 0));
    }

    /// <summary>Lowest low across the most recent <paramref name="bars"/> entry candles.</summary>
    private static decimal RecentLow(PriceHistory history, int bars)
    {
        var candles = history.Candles;
        if (candles.Count == 0) return 0m;
        int take = Math.Clamp(bars, 1, candles.Count);
        decimal low = decimal.MaxValue;
        for (int i = candles.Count - take; i < candles.Count; i++)
            low = Math.Min(low, candles[i].Low);
        return low == decimal.MaxValue ? candles[^1].Low : low;
    }

    /// <summary>0-100-ish quality score combining conviction, R:R, and momentum agreement.</summary>
    private static double ScalpScore(IndicatorSet higher, IndicatorSet entry, decimal rrT1)
    {
        double score = 40;                                   // base for passing all gates
        score += (double)(entry.ConvictionScore ?? 0m) * 0.20;
        score += (double)(higher.ConvictionScore ?? 0m) * 0.10;
        score += Math.Min((double)rrT1, 6) * 4;              // reward favorable R:R (cap)
        if (entry.MacdHistogram is { } h && h > 0) score += 4;
        if (entry.ObvSlope is { } slope && slope > 0) score += 4;
        if (higher.Adx14 is { } adx) score += Math.Min((double)adx, 40) * 0.3;
        return score;
    }
}
