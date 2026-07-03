using TradingResearchBot.Models;
using TradingResearchBot.Providers;
using Xunit;

namespace TradingResearchBot.Tests;

public class ProviderParsingTests
{
    [Fact]
    public void Polygon_ToTicker_FormatsCrypto()
    {
        Assert.Equal("AAPL", PolygonMarketDataProvider.ToPolygonTicker("aapl", AssetClass.Stock));
        Assert.Equal("X:BTCUSD", PolygonMarketDataProvider.ToPolygonTicker("BTC-USD", AssetClass.Crypto));
        Assert.Equal("X:ETHUSD", PolygonMarketDataProvider.ToPolygonTicker("eth/usd", AssetClass.Crypto));
    }

    [Fact]
    public void Polygon_ParseAggregates_MapsBars()
    {
        const string json = """
        {
          "ticker": "AAPL",
          "status": "OK",
          "resultsCount": 2,
          "results": [
            { "t": 1718000000000, "o": 100.0, "h": 105.5, "l": 99.0, "c": 104.0, "v": 1234567 },
            { "t": 1718086400000, "o": 104.0, "h": 106.0, "l": 103.0, "c": 105.0, "v": 2345678 }
          ]
        }
        """;

        var candles = PolygonMarketDataProvider.ParseAggregates(json);

        Assert.Equal(2, candles.Count);
        Assert.Equal(100.0m, candles[0].Open);
        Assert.Equal(104.0m, candles[0].Close);
        Assert.Equal(1234567m, candles[0].Volume);
        Assert.Equal(105.0m, candles[1].Close);
        Assert.True(candles[1].Timestamp > candles[0].Timestamp);
    }

    [Fact]
    public void Polygon_ParseAggregates_EmptyWhenNoResults()
    {
        Assert.Empty(PolygonMarketDataProvider.ParseAggregates("""{ "status": "OK" }"""));
    }

    [Fact]
    public void Tradier_ParseExpirations_HandlesArray()
    {
        const string json = """
        { "expirations": { "date": [ "2026-07-17", "2026-08-21", "2026-09-18" ] } }
        """;
        var dates = TradierOptionsDataProvider.ParseExpirations(json);
        Assert.Equal(3, dates.Count);
        Assert.Equal(new DateOnly(2026, 7, 17), dates[0]);
    }

    [Fact]
    public void Tradier_PickExpiration_ChoosesInsideWindow()
    {
        var today = DateOnly.FromDateTime(DateTime.UtcNow);
        var expirations = new[]
        {
            today.AddDays(2),   // too soon
            today.AddDays(14),  // in window
            today.AddDays(30),  // in window but further
            today.AddDays(120)  // too far
        };
        var pick = TradierOptionsDataProvider.PickExpiration(expirations, 7, 45);
        Assert.Equal(today.AddDays(14), pick);
    }

    [Fact]
    public void Tradier_ParseChain_MapsContractsWithGreeks()
    {
        const string json = """
        {
          "options": {
            "option": [
              {
                "symbol": "AAPL260717C00150000",
                "option_type": "call",
                "strike": 150.0,
                "expiration_date": "2026-07-17",
                "bid": 5.10, "ask": 5.30, "last": 5.20,
                "volume": 1200, "open_interest": 8000,
                "greeks": { "mid_iv": 0.32, "delta": 0.55 }
              },
              {
                "symbol": "AAPL260717P00150000",
                "option_type": "put",
                "strike": 150.0,
                "expiration_date": "2026-07-17",
                "bid": 4.80, "ask": 5.00, "last": 4.90,
                "volume": 900, "open_interest": 6000,
                "greeks": { "mid_iv": 0.30, "delta": -0.45 }
              }
            ]
          }
        }
        """;

        var contracts = TradierOptionsDataProvider.ParseChain(json, "AAPL");
        Assert.Equal(2, contracts.Count);

        var call = contracts.First(c => c.Type == OptionType.Call);
        Assert.Equal(150.0m, call.Strike);
        Assert.Equal(8000, call.OpenInterest);
        Assert.Equal(0.55m, call.Delta);
        Assert.Equal(0.32m, call.ImpliedVolatility);
        Assert.Equal(5.20m, call.Mid);

        var put = contracts.First(c => c.Type == OptionType.Put);
        Assert.Equal(-0.45m, put.Delta);
    }

    [Fact]
    public void Tradier_ParseChain_HandlesSingleObject()
    {
        const string json = """
        {
          "options": {
            "option": {
              "option_type": "call",
              "strike": 100.0,
              "expiration_date": "2026-07-17",
              "bid": 1.0, "ask": 1.2,
              "volume": 10, "open_interest": 50
            }
          }
        }
        """;
        var contracts = TradierOptionsDataProvider.ParseChain(json, "TEST");
        Assert.Single(contracts);
        Assert.Equal(100.0m, contracts[0].Strike);
    }
}
