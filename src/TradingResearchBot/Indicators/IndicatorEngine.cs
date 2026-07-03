using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Indicators;

/// <summary>
/// Pure technical-analysis math. All methods operate on closing/volume series and
/// return values for the most recent bar. No external dependencies.
/// </summary>
public sealed class IndicatorEngine : IIndicatorEngine
{
    public IndicatorSet Compute(PriceHistory history)
    {
        var candles = history.Candles;
        var closes = candles.Select(c => c.Close).ToArray();
        var highs = candles.Select(c => c.High).ToArray();
        var lows = candles.Select(c => c.Low).ToArray();
        var volumes = candles.Select(c => c.Volume).ToArray();

        var sma200 = Sma(closes, 200);
        var price = closes[^1];

        var (macd, signal, hist) = Macd(closes, 12, 26, 9);
        var (bbUpper, bbMid, bbLower) = Bollinger(closes, 20, 2m);
        var (support, resistance) = SupportResistance(highs, lows, 30);
        var (stochK, stochD) = Stochastic(highs, lows, closes, 14, 3);
        var (obv, obvSlope) = Obv(closes, volumes, 20);
        var (obvPressurePct, obvUpVolumeRatio) = ObvAccumulation(closes, volumes, 12);

        var indicators = new IndicatorSet
        {
            Price = price,
            Sma20 = Sma(closes, 20),
            Sma50 = Sma(closes, 50),
            Sma200 = sma200,
            Ema12 = Ema(closes, 12),
            Ema26 = Ema(closes, 26),
            Rsi14 = Rsi(closes, 14),
            Macd = macd,
            MacdSignal = signal,
            MacdHistogram = hist,
            BollingerUpper = bbUpper,
            BollingerMiddle = bbMid,
            BollingerLower = bbLower,
            Atr14 = Atr(highs, lows, closes, 14),
            Support = support,
            Resistance = resistance,
            VolumeRelativeStrength = VolumeRelativeStrength(volumes, 30),
            PctFromSma200 = sma200 is > 0 ? (price - sma200.Value) / sma200.Value * 100m : null,
            StochasticK = stochK,
            StochasticD = stochD,
            Adx14 = Adx(highs, lows, closes, 14),
            Obv = obv,
            ObvSlope = obvSlope,
            ObvPressurePct = obvPressurePct,
            ObvUpVolumeRatio = obvUpVolumeRatio,
            Vwap = Vwap(highs, lows, closes, volumes, 20),
            Mfi14 = Mfi(highs, lows, closes, volumes, 14),
            WilliamsR = WilliamsR(highs, lows, closes, 14),
            Cci20 = Cci(highs, lows, closes, 20),
            HistoricalVolatility = HistoricalVolatility(closes, 20)
        };

        return indicators with { ConvictionScore = Conviction(indicators) };
    }

    public static decimal? Sma(IReadOnlyList<decimal> values, int period)
    {
        if (values.Count < period) return null;
        decimal sum = 0;
        for (int i = values.Count - period; i < values.Count; i++) sum += values[i];
        return sum / period;
    }

    public static decimal? Ema(IReadOnlyList<decimal> values, int period)
    {
        if (values.Count < period) return null;
        decimal k = 2m / (period + 1);
        // Seed with SMA of first `period` values.
        decimal ema = 0;
        for (int i = 0; i < period; i++) ema += values[i];
        ema /= period;
        for (int i = period; i < values.Count; i++)
            ema = values[i] * k + ema * (1 - k);
        return ema;
    }

    public static decimal? Rsi(IReadOnlyList<decimal> closes, int period)
    {
        if (closes.Count <= period) return null;
        decimal gain = 0, loss = 0;
        for (int i = 1; i <= period; i++)
        {
            var diff = closes[i] - closes[i - 1];
            if (diff >= 0) gain += diff; else loss -= diff;
        }
        decimal avgGain = gain / period, avgLoss = loss / period;
        for (int i = period + 1; i < closes.Count; i++)
        {
            var diff = closes[i] - closes[i - 1];
            decimal up = diff > 0 ? diff : 0, down = diff < 0 ? -diff : 0;
            avgGain = (avgGain * (period - 1) + up) / period;
            avgLoss = (avgLoss * (period - 1) + down) / period;
        }
        if (avgLoss == 0) return 100m;
        var rs = avgGain / avgLoss;
        return 100m - 100m / (1 + rs);
    }

    public static (decimal? macd, decimal? signal, decimal? histogram) Macd(
        IReadOnlyList<decimal> closes, int fast, int slow, int signalPeriod)
    {
        if (closes.Count < slow + signalPeriod) return (null, null, null);

        // Build the MACD line series, then EMA it for the signal line.
        var macdSeries = new List<decimal>();
        for (int end = slow; end <= closes.Count; end++)
        {
            var slice = closes.Take(end).ToArray();
            var fastEma = Ema(slice, fast);
            var slowEma = Ema(slice, slow);
            if (fastEma is null || slowEma is null) continue;
            macdSeries.Add(fastEma.Value - slowEma.Value);
        }
        if (macdSeries.Count < signalPeriod) return (macdSeries.LastOrDefault(), null, null);

        var macd = macdSeries[^1];
        var signal = Ema(macdSeries, signalPeriod);
        var hist = signal is null ? (decimal?)null : macd - signal.Value;
        return (macd, signal, hist);
    }

    public static (decimal? upper, decimal? middle, decimal? lower) Bollinger(
        IReadOnlyList<decimal> closes, int period, decimal stdDevMult)
    {
        if (closes.Count < period) return (null, null, null);
        var window = closes.Skip(closes.Count - period).ToArray();
        decimal mean = window.Average();
        decimal variance = window.Sum(v => (v - mean) * (v - mean)) / period;
        decimal sd = (decimal)Math.Sqrt((double)variance);
        return (mean + stdDevMult * sd, mean, mean - stdDevMult * sd);
    }

    public static decimal? Atr(
        IReadOnlyList<decimal> highs, IReadOnlyList<decimal> lows,
        IReadOnlyList<decimal> closes, int period)
    {
        if (closes.Count <= period) return null;
        var trs = new List<decimal>();
        for (int i = 1; i < closes.Count; i++)
        {
            var hl = highs[i] - lows[i];
            var hc = Math.Abs(highs[i] - closes[i - 1]);
            var lc = Math.Abs(lows[i] - closes[i - 1]);
            trs.Add(Math.Max(hl, Math.Max(hc, lc)));
        }
        if (trs.Count < period) return null;
        decimal atr = trs.Take(period).Average();
        for (int i = period; i < trs.Count; i++)
            atr = (atr * (period - 1) + trs[i]) / period;
        return atr;
    }

    public static (decimal? support, decimal? resistance) SupportResistance(
        IReadOnlyList<decimal> highs, IReadOnlyList<decimal> lows, int lookback)
    {
        if (highs.Count == 0) return (null, null);
        int start = Math.Max(0, highs.Count - lookback);
        decimal support = decimal.MaxValue, resistance = decimal.MinValue;
        for (int i = start; i < highs.Count; i++)
        {
            if (lows[i] < support) support = lows[i];
            if (highs[i] > resistance) resistance = highs[i];
        }
        return (support, resistance);
    }

    public static decimal? VolumeRelativeStrength(IReadOnlyList<decimal> volumes, int period)
    {
        if (volumes.Count <= period) return null;
        decimal sum = 0;
        for (int i = volumes.Count - period - 1; i < volumes.Count - 1; i++) sum += volumes[i];
        decimal avg = sum / period;
        if (avg <= 0) return null;
        return volumes[^1] / avg;
    }

    public static (decimal? k, decimal? d) Stochastic(
        IReadOnlyList<decimal> highs, IReadOnlyList<decimal> lows,
        IReadOnlyList<decimal> closes, int period, int smoothD)
    {
        if (closes.Count < period + smoothD) return (null, null);

        var kSeries = new List<decimal>();
        for (int end = period; end <= closes.Count; end++)
        {
            decimal hh = decimal.MinValue, ll = decimal.MaxValue;
            for (int i = end - period; i < end; i++)
            {
                if (highs[i] > hh) hh = highs[i];
                if (lows[i] < ll) ll = lows[i];
            }
            decimal range = hh - ll;
            kSeries.Add(range == 0 ? 50m : (closes[end - 1] - ll) / range * 100m);
        }

        var k = kSeries[^1];
        decimal? d = kSeries.Count >= smoothD
            ? kSeries.Skip(kSeries.Count - smoothD).Average()
            : null;
        return (k, d);
    }

    public static decimal? Adx(
        IReadOnlyList<decimal> highs, IReadOnlyList<decimal> lows,
        IReadOnlyList<decimal> closes, int period)
    {
        int n = closes.Count;
        if (n <= period * 2) return null;

        var plusDm = new decimal[n];
        var minusDm = new decimal[n];
        var tr = new decimal[n];
        for (int i = 1; i < n; i++)
        {
            decimal up = highs[i] - highs[i - 1];
            decimal down = lows[i - 1] - lows[i];
            plusDm[i] = up > down && up > 0 ? up : 0;
            minusDm[i] = down > up && down > 0 ? down : 0;
            decimal hl = highs[i] - lows[i];
            decimal hc = Math.Abs(highs[i] - closes[i - 1]);
            decimal lc = Math.Abs(lows[i] - closes[i - 1]);
            tr[i] = Math.Max(hl, Math.Max(hc, lc));
        }

        // Wilder smoothing
        decimal trS = 0, plusS = 0, minusS = 0;
        for (int i = 1; i <= period; i++) { trS += tr[i]; plusS += plusDm[i]; minusS += minusDm[i]; }

        var dxValues = new List<decimal>();
        for (int i = period + 1; i < n; i++)
        {
            trS = trS - trS / period + tr[i];
            plusS = plusS - plusS / period + plusDm[i];
            minusS = minusS - minusS / period + minusDm[i];
            if (trS == 0) continue;
            decimal plusDi = plusS / trS * 100m;
            decimal minusDi = minusS / trS * 100m;
            decimal diSum = plusDi + minusDi;
            if (diSum == 0) continue;
            dxValues.Add(Math.Abs(plusDi - minusDi) / diSum * 100m);
        }

        if (dxValues.Count < period) return dxValues.Count > 0 ? dxValues.Average() : null;
        return dxValues.Skip(dxValues.Count - period).Average();
    }

    public static (decimal? obv, decimal? slope) Obv(
        IReadOnlyList<decimal> closes, IReadOnlyList<decimal> volumes, int slopeWindow)
    {
        if (closes.Count < 2) return (null, null);
        decimal obv = 0;
        var series = new List<decimal>(closes.Count) { 0 };
        for (int i = 1; i < closes.Count; i++)
        {
            if (closes[i] > closes[i - 1]) obv += volumes[i];
            else if (closes[i] < closes[i - 1]) obv -= volumes[i];
            series.Add(obv);
        }

        decimal? slope = null;
        if (series.Count > slopeWindow)
            slope = series[^1] - series[^(slopeWindow + 1)];
        return (obv, slope);
    }

    public static (decimal? pressurePct, decimal? upVolumeRatio) ObvAccumulation(
        IReadOnlyList<decimal> closes, IReadOnlyList<decimal> volumes, int lookback)
    {
        if (lookback <= 0 || closes.Count != volumes.Count || closes.Count < lookback + 1)
            return (null, null);

        decimal obv = 0;
        var series = new decimal[closes.Count];
        for (int i = 1; i < closes.Count; i++)
        {
            if (closes[i] > closes[i - 1]) obv += volumes[i];
            else if (closes[i] < closes[i - 1]) obv -= volumes[i];
            series[i] = obv;
        }

        decimal totalVolume = 0;
        decimal upVolume = 0;
        for (int i = closes.Count - lookback; i < closes.Count; i++)
        {
            totalVolume += volumes[i];
            if (closes[i] > closes[i - 1])
                upVolume += volumes[i];
        }

        if (totalVolume <= 0) return (null, null);

        var obvChange = series[^1] - series[^(lookback + 1)];
        return (obvChange / totalVolume * 100m, upVolume / totalVolume);
    }

    public static decimal? Vwap(
        IReadOnlyList<decimal> highs, IReadOnlyList<decimal> lows,
        IReadOnlyList<decimal> closes, IReadOnlyList<decimal> volumes, int period)
    {
        if (closes.Count < period) return null;
        decimal pv = 0, vol = 0;
        for (int i = closes.Count - period; i < closes.Count; i++)
        {
            decimal typical = (highs[i] + lows[i] + closes[i]) / 3m;
            pv += typical * volumes[i];
            vol += volumes[i];
        }
        return vol == 0 ? null : pv / vol;
    }

    public static decimal? Mfi(
        IReadOnlyList<decimal> highs, IReadOnlyList<decimal> lows,
        IReadOnlyList<decimal> closes, IReadOnlyList<decimal> volumes, int period)
    {
        if (closes.Count <= period) return null;
        decimal posFlow = 0, negFlow = 0;
        for (int i = closes.Count - period; i < closes.Count; i++)
        {
            decimal typical = (highs[i] + lows[i] + closes[i]) / 3m;
            decimal prevTypical = (highs[i - 1] + lows[i - 1] + closes[i - 1]) / 3m;
            decimal rawFlow = typical * volumes[i];
            if (typical > prevTypical) posFlow += rawFlow;
            else if (typical < prevTypical) negFlow += rawFlow;
        }
        if (negFlow == 0) return 100m;
        decimal ratio = posFlow / negFlow;
        return 100m - 100m / (1 + ratio);
    }

    public static decimal? WilliamsR(
        IReadOnlyList<decimal> highs, IReadOnlyList<decimal> lows,
        IReadOnlyList<decimal> closes, int period)
    {
        if (closes.Count < period) return null;
        decimal hh = decimal.MinValue, ll = decimal.MaxValue;
        for (int i = closes.Count - period; i < closes.Count; i++)
        {
            if (highs[i] > hh) hh = highs[i];
            if (lows[i] < ll) ll = lows[i];
        }
        decimal range = hh - ll;
        return range == 0 ? -50m : (hh - closes[^1]) / range * -100m;
    }

    public static decimal? Cci(
        IReadOnlyList<decimal> highs, IReadOnlyList<decimal> lows,
        IReadOnlyList<decimal> closes, int period)
    {
        if (closes.Count < period) return null;
        var typical = new decimal[period];
        for (int j = 0; j < period; j++)
        {
            int i = closes.Count - period + j;
            typical[j] = (highs[i] + lows[i] + closes[i]) / 3m;
        }
        decimal mean = typical.Average();
        decimal meanDev = typical.Sum(t => Math.Abs(t - mean)) / period;
        if (meanDev == 0) return 0m;
        return (typical[^1] - mean) / (0.015m * meanDev);
    }

    public static decimal? HistoricalVolatility(IReadOnlyList<decimal> closes, int period)
    {
        if (closes.Count <= period) return null;
        var returns = new List<double>();
        for (int i = closes.Count - period; i < closes.Count; i++)
        {
            if (closes[i - 1] <= 0) continue;
            returns.Add(Math.Log((double)(closes[i] / closes[i - 1])));
        }
        if (returns.Count < 2) return null;
        double mean = returns.Average();
        double variance = returns.Sum(r => (r - mean) * (r - mean)) / (returns.Count - 1);
        double daily = Math.Sqrt(variance);
        double annualized = daily * Math.Sqrt(252);
        return (decimal)annualized;
    }

    /// <summary>
    /// Blends the indicator battery into a single 0-100 buy-side conviction score.
    /// Higher = more independent signals agree on the bullish case.
    /// </summary>
    public static decimal Conviction(IndicatorSet i)
    {
        decimal score = 0, max = 0;

        void Add(bool condition, decimal weight, bool applicable = true)
        {
            if (!applicable) return;
            max += weight;
            if (condition) score += weight;
        }

        Add(i.Sma50 is { } s50 && i.Sma200 is { } s200 && s50 > s200, 12, i.Sma200 is not null);
        Add(i.Price > (i.Sma50 ?? decimal.MaxValue), 8, i.Sma50 is not null);
        Add(i.Ema12 is { } e12 && i.Ema26 is { } e26 && e12 > e26, 8, i.Ema26 is not null);
        Add(i.Macd is { } m && i.MacdSignal is { } sig && m > sig, 10, i.MacdSignal is not null);
        Add(i.Rsi14 is { } rsi && rsi is > 45 and < 70, 8, i.Rsi14 is not null);
        Add(i.StochasticK is { } k && i.StochasticD is { } d && k > d && k < 80, 6, i.StochasticD is not null);
        Add(i.Adx14 is { } adx && adx >= 25, 10, i.Adx14 is not null);
        Add(i.Mfi14 is { } mfi && mfi is > 40 and < 80, 7, i.Mfi14 is not null);
        Add(i.ObvSlope is { } slope && slope > 0, 8, i.ObvSlope is not null);
        Add(i.Vwap is { } vwap && i.Price > vwap, 6, i.Vwap is not null);
        Add(i.VolumeRelativeStrength is { } vrs && vrs >= 1.2m, 9, i.VolumeRelativeStrength is not null);
        Add(i.Cci20 is { } cci && cci is > 0 and < 200, 4, i.Cci20 is not null);
        Add(i.WilliamsR is { } wr && wr > -80, 4, i.WilliamsR is not null);

        if (max == 0) return 0;
        return Math.Round(score / max * 100m, 1);
    }
}
