using Microsoft.Extensions.Options;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Scoring;

/// <summary>
/// Scores a symbol and assigns research categories + suggested (informational)
/// buy range, stop, and targets. Pure rules over the indicator snapshot.
/// Combines the classic technical battery (RSI/MACD/Bollinger/ATR/SMA/EMA/
/// volume/support-resistance) with the 200-SMA institutional-volume screener.
/// </summary>
public sealed class ScoringEngine : IScoringEngine
{
    private readonly Sma200Options _sma200;
    private readonly StrategyOptions _strategy;

    public ScoringEngine(IOptions<BotOptions> options)
    {
        _sma200 = options.Value.Sma200;
        _strategy = options.Value.Strategy;
    }

    public Candidate Evaluate(PriceHistory history, IndicatorSet ind)
    {
        var c = new Candidate
        {
            Symbol = history.Symbol,
            AssetClass = history.AssetClass,
            Indicators = ind
        };

        double score = 0;
        decimal price = ind.Price;

        // --- Trend (SMA/EMA) ---
        if (ind.Sma50 is { } s50 && ind.Sma200 is { } s200)
        {
            if (s50 > s200) { score += 15; c.Signals.Add("Golden-cross structure (SMA50 > SMA200)"); }
            else { score -= 5; c.Signals.Add("Death-cross structure (SMA50 < SMA200)"); }
        }
        if (ind.Ema12 is { } e12 && ind.Ema26 is { } e26 && e12 > e26)
        {
            score += 8; c.Signals.Add("Short-term EMA uptrend (EMA12 > EMA26)");
        }

        // --- RSI ---
        if (ind.Rsi14 is { } rsi)
        {
            if (rsi < 30) { score += 12; c.Signals.Add($"RSI oversold ({rsi:F0})"); c.Patterns.Add("Oversold reversal"); }
            else if (rsi > 70) { score -= 8; c.Signals.Add($"RSI overbought ({rsi:F0})"); c.Categories.Add(ReportCategory.Fallout); }
            else if (rsi is >= 45 and <= 60) { score += 6; c.Signals.Add($"RSI neutral-bullish ({rsi:F0})"); }
        }

        // --- MACD ---
        if (ind.Macd is { } macd && ind.MacdSignal is { } sig)
        {
            if (macd > sig) { score += 10; c.Signals.Add("MACD bullish crossover"); c.Patterns.Add("MACD cross up"); }
            else { score -= 4; c.Signals.Add("MACD bearish"); }
        }

        // --- Bollinger Bands ---
        if (ind.BollingerLower is { } bl && ind.BollingerUpper is { } bu && ind.BollingerMiddle is { } bm)
        {
            decimal bandWidth = bu - bl;
            decimal pctWidth = bm > 0 ? bandWidth / bm * 100m : 0;
            if (price <= bl) { score += 9; c.Signals.Add("Price at/below lower Bollinger (mean-reversion)"); c.Patterns.Add("Bollinger bounce"); }
            if (price >= bu) { score += 6; c.Signals.Add("Price at/above upper Bollinger (momentum)"); c.Patterns.Add("Bollinger breakout"); }
            if (pctWidth < 6m) { score += 5; c.Signals.Add("Bollinger squeeze (low volatility)"); c.Patterns.Add("Squeeze"); c.Categories.Add(ReportCategory.Breakout); }
        }

        // --- Volume relative strength ---
        if (ind.VolumeRelativeStrength is { } vrs)
        {
            if (vrs >= _sma200.VolumeRatioMin)
            {
                score += 10; c.Signals.Add($"Volume surge ({vrs:F2}x avg)");
                c.Patterns.Add("Institutional volume");
            }
            else if (vrs < 0.6m)
            {
                score -= 3; c.Signals.Add($"Light volume ({vrs:F2}x avg)");
            }
        }

        // --- 200-SMA screener (combined Python spec) ---
        ApplySma200Screener(history, ind, c, ref score);

        // --- Advanced confirmation battery ---
        ApplyAdvancedSignals(ind, c, ref score);

        // --- Support / resistance proximity ---
        if (ind.Support is { } sup && ind.Resistance is { } res && res > sup)
        {
            decimal range = res - sup;
            decimal posInRange = (price - sup) / range; // 0 = at support, 1 = at resistance
            if (posInRange <= 0.15m) { score += 7; c.Signals.Add("Near support"); }
            if (posInRange >= 0.85m) { score += 4; c.Signals.Add("Near resistance (breakout watch)"); c.Categories.Add(ReportCategory.Breakout); }
        }

        c.Score = Math.Round(score, 2);
        AssignCategories(c, ind);
        AssignLevels(c, ind);
        ApplyStrategy(history, ind, c);
        return c;
    }

    /// <summary>
    /// Applies the active strategy's hard gates and bonuses. A symbol that fails any
    /// gate is marked <see cref="Candidate.StrategyQualified"/> = false and is later
    /// excluded from the report. Blended mode keeps every candidate (no gating).
    /// </summary>
    private void ApplyStrategy(PriceHistory history, IndicatorSet ind, Candidate c)
    {
        var mode = _strategy.Mode ?? "Blended";
        c.StrategyMode = mode;

        var t = _strategy.For(c.AssetClass);

        if (string.Equals(mode, "BreakoutVolume", StringComparison.OrdinalIgnoreCase))
        {
            bool above200 = !t.RequireAbove200Sma ||
                            (ind.Sma200 is { } s200 && ind.Price > s200);
            bool trending = ind.Adx14 is { } adx && adx >= t.MinAdx;
            bool volume = ind.VolumeRelativeStrength is { } vrs && vrs >= t.MinVolumeRatio;

            if (!(above200 && trending && volume))
            {
                c.StrategyQualified = false;
                return;
            }

            // Passed all gates → reward genuine breakout quality.
            double bonus = 0;
            if (ind.Resistance is { } res && ind.Price >= res * 0.98m)
            {
                bonus += 12;
                if (!c.Patterns.Contains("Breakout (near/above resistance)"))
                    c.Patterns.Add("Breakout (near/above resistance)");
            }
            if (c.Patterns.Contains("Squeeze")) bonus += 8;           // coiled spring releasing
            if (ind.Rsi14 is { } rsi && rsi is >= 50 and <= 70) bonus += 6;
            if (ind.ObvSlope is { } slope && slope > 0) bonus += 6;   // accumulation confirms
            if (ind.Vwap is { } vwap && ind.Price > vwap) bonus += 4;

            c.Score = Math.Round(c.Score + bonus, 2);
            c.Signals.Add(
                $"✔ Breakout-Volume gate passed (Vol {ind.VolumeRelativeStrength:F2}x ≥ {t.MinVolumeRatio:F1}x, " +
                $"ADX {ind.Adx14:F0} ≥ {t.MinAdx:F0}, above 200-SMA)");
            if (!c.Categories.Contains(ReportCategory.Breakout))
                c.Categories.Insert(0, ReportCategory.Breakout);
        }
        else if (string.Equals(mode, "Trend", StringComparison.OrdinalIgnoreCase))
        {
            bool above200 = ind.Sma200 is { } s200 && ind.Price > s200;
            bool golden = ind.Sma50 is { } s50 && ind.Sma200 is { } s2 && s50 > s2;
            bool trending = ind.Adx14 is { } adx && adx >= t.MinAdx;

            if (!(above200 && golden && trending))
            {
                c.StrategyQualified = false;
                return;
            }

            c.Signals.Add(
                $"✔ Trend gate passed (above 200-SMA, golden cross, ADX {ind.Adx14:F0} ≥ {t.MinAdx:F0})");
            if (!c.Categories.Contains(ReportCategory.LongTerm))
                c.Categories.Insert(0, ReportCategory.LongTerm);
        }
        // Blended: no gating, no bonus — keep the full ranked universe.
    }

    /// <summary>
    /// Adds confirmation/penalty points from the advanced indicator battery
    /// (Stochastic, ADX, OBV, VWAP, MFI, Williams %R, CCI) plus a conviction bonus.
    /// More independent agreeing signals → a stronger, higher-confidence buy case.
    /// </summary>
    private static void ApplyAdvancedSignals(IndicatorSet ind, Candidate c, ref double score)
    {
        if (ind.Adx14 is { } adx)
        {
            if (adx >= 25) { score += 8; c.Signals.Add($"Strong trend (ADX {adx:F0})"); c.Patterns.Add("Trending (ADX≥25)"); }
            else if (adx < 18) { score -= 2; c.Signals.Add($"Choppy/weak trend (ADX {adx:F0})"); }
        }

        if (ind.StochasticK is { } k && ind.StochasticD is { } d)
        {
            if (k < 20 && k > d) { score += 7; c.Signals.Add("Stochastic oversold turn-up"); c.Patterns.Add("Stochastic cross up"); }
            else if (k > 80) { score -= 4; c.Signals.Add("Stochastic overbought"); }
        }

        if (ind.ObvSlope is { } slope)
        {
            if (slope > 0) { score += 6; c.Signals.Add("OBV rising (accumulation)"); c.Patterns.Add("Accumulation"); }
            else { score -= 3; c.Signals.Add("OBV falling (distribution)"); }
        }

        if (ind.Vwap is { } vwap)
        {
            if (ind.Price > vwap) { score += 5; c.Signals.Add("Price above VWAP"); }
            else { score -= 2; c.Signals.Add("Price below VWAP"); }
        }

        if (ind.Mfi14 is { } mfi)
        {
            if (mfi < 20) { score += 6; c.Signals.Add($"MFI oversold ({mfi:F0})"); }
            else if (mfi > 80) { score -= 4; c.Signals.Add($"MFI overbought ({mfi:F0})"); }
        }

        if (ind.WilliamsR is { } wr && wr < -80)
        {
            score += 4; c.Signals.Add($"Williams %R oversold ({wr:F0})");
        }

        if (ind.Cci20 is { } cci)
        {
            if (cci < -100) { score += 4; c.Signals.Add($"CCI oversold ({cci:F0})"); }
            else if (cci > 100) { score += 3; c.Signals.Add($"CCI momentum ({cci:F0})"); }
        }

        // Conviction bonus: reward broad multi-indicator agreement.
        if (ind.ConvictionScore is { } conv)
        {
            score += (double)(conv / 100m * 20m); // up to +20
            if (conv >= 70) c.Patterns.Add("High multi-indicator conviction");
        }
    }

    private void ApplySma200Screener(PriceHistory history, IndicatorSet ind, Candidate c, ref double score)
    {
        if (!_sma200.Enabled || ind.Sma200 is not { } sma200 || sma200 <= 0) return;
        if (!history.HasEnough(201)) return;

        decimal price = ind.Price;
        decimal prevClose = history.Candles[^2].Close;
        decimal pctFromSma = ind.PctFromSma200 ?? 0;
        decimal volRatio = ind.VolumeRelativeStrength ?? 0;

        bool volumeConfirmed = volRatio >= _sma200.VolumeRatioMin;
        bool isCrossover = prevClose <= sma200 && price > sma200;
        bool isBounceZone = pctFromSma >= 0 && pctFromSma <= _sma200.BounceZonePct;

        if ((isCrossover || isBounceZone) && volumeConfirmed)
        {
            score += 18;
            string kind = isCrossover ? "200-SMA crossover" : "200-SMA support bounce";
            c.Signals.Add($"{kind} with volume confirmation ({volRatio:F2}x)");
            c.Patterns.Add(kind);
            c.Categories.Add(ReportCategory.Breakout);
        }
    }

    private static void AssignCategories(Candidate c, IndicatorSet ind)
    {
        decimal atrPct = ind.Atr14 is { } atr && ind.Price > 0 ? atr / ind.Price * 100m : 0;

        // Scalp: high intraday volatility + volume surge → quick 10-20% move potential.
        if (atrPct >= 3m && (ind.VolumeRelativeStrength ?? 0) >= 1.2m)
            c.Categories.Add(ReportCategory.Scalp);

        // Short-term momentum
        if (ind.Ema12 is { } e12 && ind.Ema26 is { } e26 && e12 > e26 && (ind.Rsi14 ?? 50) is > 50 and < 70)
            c.Categories.Add(ReportCategory.ShortTerm);

        // Swing: mid trend with room to resistance
        if (ind.Sma50 is { } s50 && ind.Price > s50 && (ind.Rsi14 ?? 50) is >= 40 and <= 65)
            c.Categories.Add(ReportCategory.Swing);

        // Long-term: above 200 SMA with golden-cross structure
        if (ind.Sma200 is { } s200 && ind.Price > s200 && ind.Sma50 is { } sm50 && sm50 > s200)
            c.Categories.Add(ReportCategory.LongTerm);

        // Fallout / breakdown watch
        if (ind.Sma200 is { } s2 && ind.Price < s2 && (ind.Rsi14 ?? 50) > 55)
            c.Categories.Add(ReportCategory.Fallout);

        // Options watch: elevated volatility makes premium strategies interesting (research only)
        if (atrPct >= 2.5m)
            c.Categories.Add(ReportCategory.OptionsWatch);

        if (c.Categories.Count == 0)
            c.Categories.Add(ReportCategory.ShortTerm);

        // De-dup while preserving order.
        var seen = new HashSet<ReportCategory>();
        var deduped = c.Categories.Where(seen.Add).ToList();
        c.Categories.Clear();
        c.Categories.AddRange(deduped);
    }

    private static void AssignLevels(Candidate c, IndicatorSet ind)
    {
        decimal price = ind.Price;
        decimal atr = ind.Atr14 ?? price * 0.02m;

        // Buy range: just under price toward nearest support / 1 ATR.
        decimal buyLow = ind.Support is { } sup ? Math.Max(sup, price - atr) : price - atr;
        decimal buyHigh = price;
        c.BuyRangeLow = Math.Round(Math.Min(buyLow, buyHigh), 2);
        c.BuyRangeHigh = Math.Round(buyHigh, 2);

        // Stop: below support or 1.5 ATR, whichever is tighter but sane.
        decimal stop = ind.Support is { } s ? Math.Min(s - atr * 0.25m, price - atr * 1.5m) : price - atr * 1.5m;
        c.StopLoss = Math.Round(Math.Max(0.01m, stop), 2);

        // Targets: scalp ~ +10-20%, plus resistance-based / ATR-based second target.
        bool isScalp = c.Categories.Contains(ReportCategory.Scalp);
        decimal t1Pct = isScalp ? 0.10m : 0.06m;
        c.Target1 = Math.Round(price * (1 + t1Pct), 2);

        decimal t2 = ind.Resistance is { } res && res > price
            ? Math.Max(res, price * (1 + (isScalp ? 0.20m : 0.12m)))
            : price * (1 + (isScalp ? 0.20m : 0.12m));
        c.Target2 = Math.Round(t2, 2);
    }
}
