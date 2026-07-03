using TradingResearchBot.Indicators;
using TradingResearchBot.Models;
using Xunit;

namespace TradingResearchBot.Tests;

public class IndicatorEngineTests
{
    [Fact]
    public void Sma_ComputesArithmeticMeanOfLastPeriod()
    {
        var values = new decimal[] { 1, 2, 3, 4, 5 };
        var sma = IndicatorEngine.Sma(values, 5);
        Assert.Equal(3m, sma);
    }

    [Fact]
    public void Sma_ReturnsNull_WhenNotEnoughData()
    {
        Assert.Null(IndicatorEngine.Sma(new decimal[] { 1, 2 }, 5));
    }

    [Fact]
    public void Rsi_AllGains_ReturnsHigh()
    {
        var closes = Enumerable.Range(1, 30).Select(i => (decimal)i).ToArray();
        var rsi = IndicatorEngine.Rsi(closes, 14);
        Assert.NotNull(rsi);
        Assert.True(rsi >= 99m, $"Expected ~100 for monotonic uptrend, got {rsi}");
    }

    [Fact]
    public void Rsi_AllLosses_ReturnsLow()
    {
        var closes = Enumerable.Range(1, 30).Select(i => (decimal)(31 - i)).ToArray();
        var rsi = IndicatorEngine.Rsi(closes, 14);
        Assert.NotNull(rsi);
        Assert.True(rsi <= 1m, $"Expected ~0 for monotonic downtrend, got {rsi}");
    }

    [Fact]
    public void Bollinger_MiddleEqualsSma()
    {
        var closes = new decimal[] { 10, 12, 14, 16, 18 };
        var (upper, middle, lower) = IndicatorEngine.Bollinger(closes, 5, 2m);
        Assert.Equal(14m, middle);
        Assert.True(upper > middle && middle > lower);
    }

    [Fact]
    public void Atr_IsPositive_ForVolatileSeries()
    {
        var highs = new decimal[] { 10, 12, 11, 13, 15, 14, 16, 18, 17, 19, 21, 20, 22, 24, 23 };
        var lows = new decimal[] { 9, 10, 9, 11, 12, 12, 14, 15, 15, 16, 18, 18, 20, 21, 21 };
        var closes = new decimal[] { 9.5m, 11, 10, 12, 14, 13, 15, 17, 16, 18, 20, 19, 21, 23, 22 };
        var atr = IndicatorEngine.Atr(highs, lows, closes, 14);
        Assert.NotNull(atr);
        Assert.True(atr > 0);
    }

    [Fact]
    public void Compute_ProducesSma200_WithEnoughData()
    {
        var candles = BuildCandles(260, startPrice: 100m, step: 0.1m);
        var history = new PriceHistory
        {
            Symbol = "TEST",
            AssetClass = AssetClass.Stock,
            Candles = candles
        };

        var ind = new IndicatorEngine().Compute(history);
        Assert.NotNull(ind.Sma200);
        Assert.NotNull(ind.Rsi14);
        Assert.NotNull(ind.Atr14);
        Assert.True(ind.Price > 0);
    }

    private static List<Candle> BuildCandles(int count, decimal startPrice, decimal step)
    {
        var list = new List<Candle>(count);
        var start = DateTimeOffset.UtcNow.AddDays(-count);
        decimal price = startPrice;
        for (int i = 0; i < count; i++)
        {
            decimal open = price;
            decimal close = price + step;
            list.Add(new Candle(start.AddDays(i), open, close + 0.5m, open - 0.5m, close, 1_000_000m));
            price = close;
        }
        return list;
    }
}
