using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Services;

/// <summary>
/// Analyzes a single ticker across multiple timeframes and provides recommendations
/// for scalp, swing, and long-term trading styles.
/// </summary>
public sealed class AssetAnalyzerService
{
    private readonly IIntradayMarketDataProvider _intraday;
    private readonly IMarketDataProvider _daily;
    private readonly IIndicatorEngine _indicators;
    private readonly BotOptions _bot;
    private readonly ILogger<AssetAnalyzerService> _logger;

    public AssetAnalyzerService(
        IIntradayMarketDataProvider intraday,
        IMarketDataProvider daily,
        IIndicatorEngine indicators,
        IOptions<BotOptions> botOptions,
        ILogger<AssetAnalyzerService> logger)
    {
        _intraday = intraday;
        _daily = daily;
        _indicators = indicators;
        _bot = botOptions.Value;
        _logger = logger;
    }

    public async Task<TickerAnalysis> AnalyzeAsync(string symbol, CancellationToken ct = default)
    {
        symbol = symbol.Trim().ToUpperInvariant();
        var assetClass = DetectAssetClass(symbol);

        _logger.LogInformation("Analyzing {Symbol} ({Asset})...", symbol, assetClass);

        var analysis = new TickerAnalysis
        {
            Symbol = symbol,
            AssetClass = assetClass,
            AnalyzedAtUtc = DateTimeOffset.UtcNow
        };

        // Fetch multi-timeframe data in parallel where possible.
        var tf15m = _intraday.GetIntradayHistoryAsync(symbol, assetClass, "15Min", 120, ct);
        var tf1h = _intraday.GetIntradayHistoryAsync(symbol, assetClass, "1Hour", 120, ct);
        var tf4h = _intraday.GetIntradayHistoryAsync(symbol, assetClass, "4Hour", 120, ct);
        var tf1d = _daily.GetDailyHistoryAsync(symbol, assetClass, 365, ct);

        await Task.WhenAll(tf15m, tf1h, tf4h, tf1d);

        var h15m = await tf15m;
        var h1h = await tf1h;
        var h4h = await tf4h;
        var h1d = await tf1d;

        // Compute indicators for each available timeframe.
        if (h15m?.HasEnough(30) == true)
        {
            analysis.Tf15Min = _indicators.Compute(h15m);
            analysis.Price = analysis.Tf15Min.Price;
        }
        if (h1h?.HasEnough(30) == true)
        {
            analysis.Tf1Hour = _indicators.Compute(h1h);
            analysis.Price ??= analysis.Tf1Hour.Price;
        }
        if (h4h?.HasEnough(30) == true)
        {
            analysis.Tf4Hour = _indicators.Compute(h4h);
            analysis.Price ??= analysis.Tf4Hour.Price;
        }
        if (h1d?.HasEnough(30) == true)
        {
            analysis.Tf1Day = _indicators.Compute(h1d);
            analysis.Price ??= analysis.Tf1Day.Price;
        }

        // Generate trading style recommendations.
        analysis.Scalp = EvaluateScalp(analysis, h15m, assetClass);
        analysis.Swing = EvaluateSwing(analysis, assetClass);
        analysis.LongTerm = EvaluateLongTerm(analysis, assetClass);

        _logger.LogInformation(
            "{Symbol}: Scalp={Scalp}, Swing={Swing}, LongTerm={Long}",
            symbol,
            analysis.Scalp?.Bias ?? "N/A",
            analysis.Swing?.Bias ?? "N/A",
            analysis.LongTerm?.Bias ?? "N/A");

        return analysis;
    }

    private static AssetClass DetectAssetClass(string symbol)
    {
        // Crypto pairs end with -USD (e.g., BTC-USD, ETH-USD).
        if (symbol.EndsWith("-USD", StringComparison.OrdinalIgnoreCase) ||
            symbol.Contains("/USD", StringComparison.OrdinalIgnoreCase))
            return AssetClass.Crypto;
        return AssetClass.Stock;
    }

    /// <summary>
    /// Scalp recommendation: quick 10-20% (crypto) or 2-5% (stocks) within hours/days.
    /// Uses 15m trigger + 4h trend alignment.
    /// </summary>
    private StyleRecommendation? EvaluateScalp(TickerAnalysis a, PriceHistory? entryHistory, AssetClass asset)
    {
        var entry = a.Tf15Min;
        var higher = a.Tf4Hour;
        if (entry is null || higher is null) return null;

        var rec = new StyleRecommendation { Style = "Scalp" };
        var signals = new List<string>();
        int bullScore = 0, bearScore = 0;

        // Higher-timeframe trend filter (4H).
        bool htfAboveVwap = higher.Vwap is { } hv && higher.Price > hv;
        bool htfEmaUp = higher.Ema12 is { } he12 && higher.Ema26 is { } he26 && he12 > he26;
        bool htfMacdUp = higher.Macd is { } hm && higher.MacdSignal is { } hs && hm > hs;
        bool htfAdx = higher.Adx14 is { } adx && adx >= 20;

        if (htfAboveVwap) { bullScore += 2; signals.Add("4H: Price > VWAP"); }
        else if (higher.Vwap is { } hvl && higher.Price < hvl) { bearScore += 2; signals.Add("4H: Price < VWAP"); }

        if (htfEmaUp) { bullScore += 2; signals.Add("4H: EMA12 > EMA26"); }
        else if (higher.Ema12 < higher.Ema26) { bearScore += 2; signals.Add("4H: EMA12 < EMA26"); }

        if (htfMacdUp) { bullScore += 1; signals.Add("4H: MACD bullish"); }
        else if (higher.Macd < higher.MacdSignal) { bearScore += 1; signals.Add("4H: MACD bearish"); }

        if (htfAdx) signals.Add($"4H: ADX {higher.Adx14:F0} (trending)");

        // Entry-timeframe trigger (15m).
        bool entryAboveVwap = entry.Vwap is { } ev && entry.Price > ev;
        bool entryMacdUp = entry.Macd is { } em && entry.MacdSignal is { } es && em > es && (entry.MacdHistogram ?? 0) > 0;
        bool entryRsiMomentum = entry.Rsi14 is { } rsi && rsi >= 50 && rsi <= 72;
        bool entryVolSurge = (entry.VolumeRelativeStrength ?? 0) >= 1.5m;

        if (entryAboveVwap) { bullScore += 1; signals.Add("15m: Price > VWAP"); }
        else if (entry.Vwap is { } evl && entry.Price < evl) { bearScore += 1; signals.Add("15m: Price < VWAP"); }

        if (entryMacdUp) { bullScore += 1; signals.Add("15m: MACD cross up"); }
        else if (entry.Macd < entry.MacdSignal) { bearScore += 1; signals.Add("15m: MACD cross down"); }

        if (entryRsiMomentum) { bullScore += 1; signals.Add($"15m: RSI {entry.Rsi14:F0} (momentum zone)"); }
        else if (entry.Rsi14 > 72) { bearScore += 1; signals.Add($"15m: RSI {entry.Rsi14:F0} (overbought)"); }
        else if (entry.Rsi14 < 30) { bullScore += 1; signals.Add($"15m: RSI {entry.Rsi14:F0} (oversold bounce)"); }

        if (entryVolSurge) signals.Add($"15m: Volume surge {entry.VolumeRelativeStrength:F1}x");

        // Determine bias and strength.
        (rec.Bias, rec.Strength) = ScoreToBias(bullScore, bearScore);
        rec.Signals = signals;

        // Calculate levels.
        var price = entry.Price;
        var atr = entry.Atr14 ?? price * 0.015m;
        var swingLow = entryHistory?.Candles.TakeLast(12).Min(c => c.Low) ?? price * 0.96m;

        if (rec.Bias == "Bullish")
        {
            var stopRaw = Math.Max(swingLow, price - atr);
            var maxRisk = asset == AssetClass.Crypto ? 0.04m : 0.02m;
            rec.StopLoss = Math.Max(stopRaw, price * (1 - maxRisk));
            rec.Entry = price;
            var target1Pct = asset == AssetClass.Crypto ? 0.10m : 0.03m;
            var target2Pct = asset == AssetClass.Crypto ? 0.20m : 0.05m;
            rec.Target1 = Math.Round(price * (1 + target1Pct), 2);
            rec.Target2 = Math.Round(price * (1 + target2Pct), 2);
            rec.RiskPct = Math.Round((price - rec.StopLoss.Value) / price * 100, 2);
            rec.RewardRisk = rec.RiskPct > 0 ? Math.Round(target1Pct * 100 / rec.RiskPct.Value, 1) : 0;
        }
        else if (rec.Bias == "Bearish")
        {
            var swingHigh = entryHistory?.Candles.TakeLast(12).Max(c => c.High) ?? price * 1.04m;
            rec.StopLoss = Math.Min(swingHigh, price + atr);
            rec.Entry = price;
            var target1Pct = asset == AssetClass.Crypto ? 0.10m : 0.03m;
            var target2Pct = asset == AssetClass.Crypto ? 0.20m : 0.05m;
            rec.Target1 = Math.Round(price * (1 - target1Pct), 2);
            rec.Target2 = Math.Round(price * (1 - target2Pct), 2);
            rec.RiskPct = Math.Round((rec.StopLoss.Value - price) / price * 100, 2);
            rec.RewardRisk = rec.RiskPct > 0 ? Math.Round(target1Pct * 100 / rec.RiskPct.Value, 1) : 0;
        }

        return rec;
    }

    /// <summary>
    /// Swing recommendation: 15-30% (crypto) or 8-15% (stocks) over days to weeks.
    /// Uses 4H trigger + Daily trend alignment.
    /// </summary>
    private StyleRecommendation? EvaluateSwing(TickerAnalysis a, AssetClass asset)
    {
        var entry = a.Tf4Hour;
        var higher = a.Tf1Day;
        if (entry is null || higher is null) return null;

        var rec = new StyleRecommendation { Style = "Swing" };
        var signals = new List<string>();
        int bullScore = 0, bearScore = 0;

        // Daily trend filter.
        bool aboveSma200 = higher.Sma200 is { } sma && higher.Price > sma;
        bool aboveSma50 = higher.Sma50 is { } sma50 && higher.Price > sma50;
        bool dailyEmaUp = higher.Ema12 is { } de12 && higher.Ema26 is { } de26 && de12 > de26;
        bool dailyMacdUp = higher.Macd is { } dm && higher.MacdSignal is { } ds && dm > ds;
        bool dailyAdx = higher.Adx14 is { } dadx && dadx >= 20;

        if (aboveSma200) { bullScore += 3; signals.Add("Daily: Above 200 SMA"); }
        else if (higher.Sma200 is { } s200 && higher.Price < s200) { bearScore += 3; signals.Add("Daily: Below 200 SMA"); }

        if (aboveSma50) { bullScore += 2; signals.Add("Daily: Above 50 SMA"); }
        else if (higher.Sma50 is { } s50 && higher.Price < s50) { bearScore += 2; signals.Add("Daily: Below 50 SMA"); }

        if (dailyEmaUp) { bullScore += 1; signals.Add("Daily: EMA12 > EMA26"); }
        else if (higher.Ema12 < higher.Ema26) { bearScore += 1; signals.Add("Daily: EMA12 < EMA26"); }

        if (dailyMacdUp) { bullScore += 1; signals.Add("Daily: MACD bullish"); }
        else if (higher.Macd < higher.MacdSignal) { bearScore += 1; signals.Add("Daily: MACD bearish"); }

        if (dailyAdx) signals.Add($"Daily: ADX {higher.Adx14:F0} (trending)");

        // 4H trigger.
        bool h4AboveVwap = entry.Vwap is { } ev && entry.Price > ev;
        bool h4MacdUp = entry.Macd is { } em && entry.MacdSignal is { } es && em > es;
        bool h4RsiBullish = entry.Rsi14 is { } rsi && rsi >= 45 && rsi <= 70;

        if (h4AboveVwap) { bullScore += 1; signals.Add("4H: Price > VWAP"); }
        else if (entry.Vwap is { } evl && entry.Price < evl) { bearScore += 1; signals.Add("4H: Price < VWAP"); }

        if (h4MacdUp) { bullScore += 1; signals.Add("4H: MACD bullish"); }
        else if (entry.Macd < entry.MacdSignal) { bearScore += 1; signals.Add("4H: MACD bearish"); }

        if (h4RsiBullish) signals.Add($"4H: RSI {entry.Rsi14:F0}");
        else if (entry.Rsi14 > 75) { bearScore += 1; signals.Add($"4H: RSI {entry.Rsi14:F0} (overbought)"); }
        else if (entry.Rsi14 < 25) { bullScore += 1; signals.Add($"4H: RSI {entry.Rsi14:F0} (oversold)"); }

        (rec.Bias, rec.Strength) = ScoreToBias(bullScore, bearScore);
        rec.Signals = signals;

        // Calculate levels.
        var price = entry.Price;
        var atr = higher.Atr14 ?? price * 0.03m;

        if (rec.Bias == "Bullish")
        {
            var maxRisk = asset == AssetClass.Crypto ? 0.08m : 0.05m;
            rec.StopLoss = Math.Max(price - atr * 2, price * (1 - maxRisk));
            // Support as alternative stop.
            if (higher.Support is { } sup && sup < price && sup > rec.StopLoss)
                rec.StopLoss = sup * 0.98m;
            rec.Entry = price;
            var target1Pct = asset == AssetClass.Crypto ? 0.15m : 0.08m;
            var target2Pct = asset == AssetClass.Crypto ? 0.30m : 0.15m;
            rec.Target1 = Math.Round(price * (1 + target1Pct), 2);
            rec.Target2 = Math.Round(price * (1 + target2Pct), 2);
            // Resistance as potential target.
            if (higher.Resistance is { } res && res > price && res < rec.Target1)
                rec.Target1 = res;
            rec.RiskPct = Math.Round((price - rec.StopLoss.Value) / price * 100, 2);
            rec.RewardRisk = rec.RiskPct > 0 ? Math.Round(target1Pct * 100 / rec.RiskPct.Value, 1) : 0;
        }
        else if (rec.Bias == "Bearish")
        {
            var maxRisk = asset == AssetClass.Crypto ? 0.08m : 0.05m;
            rec.StopLoss = Math.Min(price + atr * 2, price * (1 + maxRisk));
            if (higher.Resistance is { } res && res > price && res < rec.StopLoss)
                rec.StopLoss = res * 1.02m;
            rec.Entry = price;
            var target1Pct = asset == AssetClass.Crypto ? 0.15m : 0.08m;
            var target2Pct = asset == AssetClass.Crypto ? 0.30m : 0.15m;
            rec.Target1 = Math.Round(price * (1 - target1Pct), 2);
            rec.Target2 = Math.Round(price * (1 - target2Pct), 2);
            if (higher.Support is { } sup && sup < price && sup > rec.Target1)
                rec.Target1 = sup;
            rec.RiskPct = Math.Round((rec.StopLoss.Value - price) / price * 100, 2);
            rec.RewardRisk = rec.RiskPct > 0 ? Math.Round(target1Pct * 100 / rec.RiskPct.Value, 1) : 0;
        }

        return rec;
    }

    /// <summary>
    /// Long-term recommendation: 30%+ (crypto) or 20%+ (stocks) over weeks to months.
    /// Uses Daily indicators with focus on MA alignment and trend strength.
    /// </summary>
    private StyleRecommendation? EvaluateLongTerm(TickerAnalysis a, AssetClass asset)
    {
        var daily = a.Tf1Day;
        if (daily is null) return null;

        var rec = new StyleRecommendation { Style = "LongTerm" };
        var signals = new List<string>();
        int bullScore = 0, bearScore = 0;

        // Primary trend: 200 SMA and 50 SMA alignment.
        bool aboveSma200 = daily.Sma200 is { } sma200 && daily.Price > sma200;
        bool sma50Above200 = daily.Sma50 is { } sma50 && daily.Sma200 is { } s200 && sma50 > s200;
        bool goldenCross = sma50Above200 && daily.Ema12 > daily.Ema26;
        bool deathCross = daily.Sma50 < daily.Sma200 && daily.Ema12 < daily.Ema26;

        if (aboveSma200) { bullScore += 3; signals.Add($"Price above 200 SMA ({daily.PctFromSma200:+0.0;-0.0}%)"); }
        else if (daily.Sma200 is { } s && daily.Price < s) { bearScore += 3; signals.Add($"Price below 200 SMA ({daily.PctFromSma200:+0.0;-0.0}%)"); }

        if (goldenCross) { bullScore += 3; signals.Add("Golden cross: 50 SMA > 200 SMA"); }
        else if (deathCross) { bearScore += 3; signals.Add("Death cross: 50 SMA < 200 SMA"); }

        // Trend strength.
        if (daily.Adx14 is { } adx)
        {
            if (adx >= 25) { signals.Add($"Strong trend (ADX {adx:F0})"); bullScore++; }
            else if (adx < 20) signals.Add($"Weak/sideways (ADX {adx:F0})");
        }

        // Momentum confirmation.
        if (daily.Rsi14 is { } rsi)
        {
            if (rsi >= 50 && rsi <= 70) { bullScore += 1; signals.Add($"RSI {rsi:F0} (healthy momentum)"); }
            else if (rsi > 75) { bearScore += 1; signals.Add($"RSI {rsi:F0} (overbought - caution)"); }
            else if (rsi < 30) { bullScore += 1; signals.Add($"RSI {rsi:F0} (oversold - potential reversal)"); }
            else if (rsi < 45) { bearScore += 1; signals.Add($"RSI {rsi:F0} (weak momentum)"); }
        }

        // Volume confirmation.
        if (daily.ObvSlope is { } obvSlope)
        {
            if (obvSlope > 0) { bullScore += 1; signals.Add("OBV rising (accumulation)"); }
            else if (obvSlope < 0) { bearScore += 1; signals.Add("OBV falling (distribution)"); }
        }

        // Bollinger band position.
        if (daily.BollingerUpper is { } bbu && daily.BollingerLower is { } bbl)
        {
            var bandwidth = (bbu - bbl) / daily.Price * 100;
            if (daily.Price > bbu) signals.Add("Price above upper BB (extended)");
            else if (daily.Price < bbl) signals.Add("Price below lower BB (oversold)");
            else if (bandwidth < 5) signals.Add($"Tight BBands ({bandwidth:F1}%) - breakout pending");
        }

        (rec.Bias, rec.Strength) = ScoreToBias(bullScore, bearScore);
        rec.Signals = signals;

        // Calculate levels.
        var price = daily.Price;
        var atr = daily.Atr14 ?? price * 0.05m;

        if (rec.Bias == "Bullish")
        {
            var maxRisk = asset == AssetClass.Crypto ? 0.15m : 0.10m;
            // Use 200 SMA as support, or ATR-based stop.
            rec.StopLoss = daily.Sma200 is { } s200Stop && s200Stop < price
                ? s200Stop * 0.97m
                : price * (1 - maxRisk);
            rec.StopLoss = Math.Max(rec.StopLoss.Value, price * (1 - maxRisk));
            rec.Entry = price;
            var target1Pct = asset == AssetClass.Crypto ? 0.30m : 0.20m;
            var target2Pct = asset == AssetClass.Crypto ? 0.50m : 0.35m;
            rec.Target1 = Math.Round(price * (1 + target1Pct), 2);
            rec.Target2 = Math.Round(price * (1 + target2Pct), 2);
            rec.RiskPct = Math.Round((price - rec.StopLoss.Value) / price * 100, 2);
            rec.RewardRisk = rec.RiskPct > 0 ? Math.Round(target1Pct * 100 / rec.RiskPct.Value, 1) : 0;
        }
        else if (rec.Bias == "Bearish")
        {
            var maxRisk = asset == AssetClass.Crypto ? 0.15m : 0.10m;
            rec.StopLoss = daily.Sma200 is { } s200Stop && s200Stop > price
                ? s200Stop * 1.03m
                : price * (1 + maxRisk);
            rec.StopLoss = Math.Min(rec.StopLoss.Value, price * (1 + maxRisk));
            rec.Entry = price;
            var target1Pct = asset == AssetClass.Crypto ? 0.30m : 0.20m;
            var target2Pct = asset == AssetClass.Crypto ? 0.50m : 0.35m;
            rec.Target1 = Math.Round(price * (1 - target1Pct), 2);
            rec.Target2 = Math.Round(price * (1 - target2Pct), 2);
            rec.RiskPct = Math.Round((rec.StopLoss.Value - price) / price * 100, 2);
            rec.RewardRisk = rec.RiskPct > 0 ? Math.Round(target1Pct * 100 / rec.RiskPct.Value, 1) : 0;
        }

        return rec;
    }

    private static (string Bias, string Strength) ScoreToBias(int bull, int bear)
    {
        var net = bull - bear;
        return net switch
        {
            >= 5 => ("Bullish", "Strong"),
            >= 3 => ("Bullish", "Moderate"),
            >= 1 => ("Bullish", "Weak"),
            <= -5 => ("Bearish", "Strong"),
            <= -3 => ("Bearish", "Moderate"),
            <= -1 => ("Bearish", "Weak"),
            _ => ("Neutral", "")
        };
    }
}

/// <summary>
/// Multi-timeframe analysis result for a single ticker.
/// </summary>
public sealed class TickerAnalysis
{
    public required string Symbol { get; init; }
    public AssetClass AssetClass { get; init; }
    public DateTimeOffset AnalyzedAtUtc { get; init; }
    public decimal? Price { get; set; }

    // Multi-timeframe indicator snapshots.
    public IndicatorSet? Tf15Min { get; set; }
    public IndicatorSet? Tf1Hour { get; set; }
    public IndicatorSet? Tf4Hour { get; set; }
    public IndicatorSet? Tf1Day { get; set; }

    // Trading style recommendations.
    public StyleRecommendation? Scalp { get; set; }
    public StyleRecommendation? Swing { get; set; }
    public StyleRecommendation? LongTerm { get; set; }
}

/// <summary>
/// Trading style recommendation with bias, levels, and supporting signals.
/// </summary>
public sealed class StyleRecommendation
{
    public required string Style { get; init; }
    public string Bias { get; set; } = "Neutral";
    public string Strength { get; set; } = "";
    public decimal? Entry { get; set; }
    public decimal? StopLoss { get; set; }
    public decimal? Target1 { get; set; }
    public decimal? Target2 { get; set; }
    public decimal? RiskPct { get; set; }
    public decimal? RewardRisk { get; set; }
    public IReadOnlyList<string> Signals { get; set; } = Array.Empty<string>();
}
