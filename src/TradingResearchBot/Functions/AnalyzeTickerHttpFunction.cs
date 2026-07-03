using System.Net;
using Microsoft.Azure.Functions.Worker;
using Microsoft.Azure.Functions.Worker.Http;
using Microsoft.Extensions.Logging;
using TradingResearchBot.Services;

namespace TradingResearchBot.Functions;

/// <summary>
/// HTTP endpoint to analyze any ticker and get multi-timeframe recommendations
/// for scalp, swing, and long-term trading styles.
/// 
/// Usage:
///   POST /api/analyze/BTC-USD  (crypto)
///   POST /api/analyze/NVDA     (stock)
///   GET  /api/analyze/AAPL
/// 
/// Returns multi-timeframe indicators and trading style recommendations with
/// entry levels, stops, targets, and supporting signals.
/// </summary>
public sealed class AnalyzeTickerHttpFunction
{
    private readonly AssetAnalyzerService _analyzer;
    private readonly ILogger<AnalyzeTickerHttpFunction> _logger;

    public AnalyzeTickerHttpFunction(
        AssetAnalyzerService analyzer,
        ILogger<AnalyzeTickerHttpFunction> logger)
    {
        _analyzer = analyzer;
        _logger = logger;
    }

    [Function("AnalyzeTicker")]
    public async Task<HttpResponseData> Run(
        [HttpTrigger(AuthorizationLevel.Function, "get", "post", Route = "analyze/{symbol}")] HttpRequestData req,
        string symbol,
        CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(symbol))
        {
            var bad = req.CreateResponse(HttpStatusCode.BadRequest);
            await bad.WriteStringAsync("Missing symbol parameter.", ct);
            return bad;
        }

        _logger.LogInformation("Analyzing ticker: {Symbol}", symbol);

        try
        {
            var analysis = await _analyzer.AnalyzeAsync(symbol, ct);

            var response = req.CreateResponse(HttpStatusCode.OK);
            await response.WriteAsJsonAsync(new
            {
                symbol = analysis.Symbol,
                assetClass = analysis.AssetClass.ToString(),
                analyzedAtUtc = analysis.AnalyzedAtUtc,
                price = analysis.Price,

                // Summary recommendation for each style.
                recommendations = new
                {
                    scalp = FormatRecommendation(analysis.Scalp),
                    swing = FormatRecommendation(analysis.Swing),
                    longTerm = FormatRecommendation(analysis.LongTerm)
                },

                // Detailed indicators if someone wants to dig deeper.
                indicators = new
                {
                    tf15Min = FormatIndicators(analysis.Tf15Min, "15m"),
                    tf1Hour = FormatIndicators(analysis.Tf1Hour, "1H"),
                    tf4Hour = FormatIndicators(analysis.Tf4Hour, "4H"),
                    tf1Day = FormatIndicators(analysis.Tf1Day, "1D")
                }
            }, ct);

            return response;
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _logger.LogError(ex, "Failed to analyze {Symbol}.", symbol);
            var err = req.CreateResponse(HttpStatusCode.InternalServerError);
            await err.WriteStringAsync($"Analysis failed: {ex.Message}", ct);
            return err;
        }
    }

    private static object? FormatRecommendation(StyleRecommendation? rec)
    {
        if (rec is null) return new { available = false };

        return new
        {
            available = true,
            style = rec.Style,
            bias = rec.Bias,
            strength = rec.Strength,
            levels = rec.Entry is not null ? new
            {
                entry = rec.Entry,
                stopLoss = rec.StopLoss,
                target1 = rec.Target1,
                target2 = rec.Target2,
                riskPct = rec.RiskPct,
                rewardRisk = rec.RewardRisk
            } : null,
            signals = rec.Signals
        };
    }

    private static object? FormatIndicators(Models.IndicatorSet? ind, string timeframe)
    {
        if (ind is null) return new { available = false, timeframe };

        return new
        {
            available = true,
            timeframe,
            price = ind.Price,
            trend = new
            {
                sma20 = ind.Sma20,
                sma50 = ind.Sma50,
                sma200 = ind.Sma200,
                pctFromSma200 = ind.PctFromSma200,
                ema12 = ind.Ema12,
                ema26 = ind.Ema26,
                vwap = ind.Vwap,
                adx14 = ind.Adx14
            },
            momentum = new
            {
                rsi14 = ind.Rsi14,
                macd = ind.Macd,
                macdSignal = ind.MacdSignal,
                macdHistogram = ind.MacdHistogram,
                stochasticK = ind.StochasticK,
                stochasticD = ind.StochasticD,
                mfi14 = ind.Mfi14,
                williamsR = ind.WilliamsR,
                cci20 = ind.Cci20
            },
            volatility = new
            {
                atr14 = ind.Atr14,
                bollingerUpper = ind.BollingerUpper,
                bollingerMiddle = ind.BollingerMiddle,
                bollingerLower = ind.BollingerLower,
                historicalVolatility = ind.HistoricalVolatility
            },
            volume = new
            {
                volumeRelativeStrength = ind.VolumeRelativeStrength,
                obv = ind.Obv,
                obvSlope = ind.ObvSlope
            },
            levels = new
            {
                support = ind.Support,
                resistance = ind.Resistance
            },
            conviction = ind.ConvictionScore
        };
    }
}
