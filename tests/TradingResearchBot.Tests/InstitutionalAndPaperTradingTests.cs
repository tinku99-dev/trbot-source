using System.Net;
using System.Text;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;
using TradingResearchBot.Services;
using Xunit;

namespace TradingResearchBot.Tests;

public sealed class InstitutionalAndPaperTradingTests
{
    [Fact]
    public async Task InstitutionalOverlay_ComputesSectorStrength_AndPairHedge_InShadow()
    {
        var histories = new Dictionary<string, PriceHistory>(StringComparer.OrdinalIgnoreCase)
        {
            ["AAPL"] = History("AAPL", 100m, 1.02m, 90),
            ["XLK"] = History("XLK", 100m, 1.01m, 90),
            ["MSFT"] = History("MSFT", 80m, 1.019m, 90)
        };
        var provider = new FakeMarketProvider(histories);
        var options = new BotOptions();
        var service = new InstitutionalOverlayService(
            provider, Options.Create(options), NullLogger<InstitutionalOverlayService>.Instance);
        var candidate = Candidate("AAPL");
        candidate.Patterns.Add("Bollinger bounce");
        candidate.Categories.Add(ReportCategory.Breakout);

        await service.ApplyAsync(new[] { candidate }, histories);

        Assert.NotNull(candidate.Institutional);
        Assert.True(candidate.Institutional!.ShadowOnly);
        Assert.True(candidate.Institutional.RelativeStrengthQualified);
        Assert.Equal("XLK", candidate.Institutional.SectorBenchmark);
        Assert.Equal("MSFT", candidate.Institutional.HedgePeer);
        Assert.NotNull(candidate.Institutional.ShortNotionalPctOfLong);
        Assert.Equal(80, candidate.Score); // shadow mode cannot change scoring
    }

    [Fact]
    public async Task AlpacaPaperTrading_SubmitsRiskCappedBracket_ToPaperHost()
    {
        string? submitted = null;
        var handler = new StubHandler(async request =>
        {
            string path = request.RequestUri!.AbsolutePath;
            if (path == "/v2/account")
                return Json(HttpStatusCode.OK, "{\"equity\":\"100000\",\"buying_power\":\"50000\",\"trading_blocked\":false}");
            if (request.Method == HttpMethod.Get && (path == "/v2/positions" || path == "/v2/orders"))
                return Json(HttpStatusCode.OK, "[]");
            if (path == "/v2/orders" && request.Method == HttpMethod.Post)
            {
                submitted = await request.Content!.ReadAsStringAsync();
                return Json(HttpStatusCode.OK, "{\"id\":\"paper-order-1\"}");
            }
            return Json(HttpStatusCode.NotFound, "{}");
        });
        var config = new BotOptions
        {
            MarketProvider = "Alpaca",
            PaperTrading = new PaperTradingOptions
            {
                Enabled = true,
                SubmitToAlpaca = true,
                CapitalPerTradeUsd = 1_000m,
                MaxOpenPositions = 10,
                MaxNewPositionsPerDay = 2,
                RiskPerTradePct = 0.5m,
                MinCandidateScore = 70
            },
            Providers = new ProviderOptions
            {
                Alpaca = new AlpacaOptions
                {
                    ApiKeyId = "paper-key",
                    ApiSecret = "paper-secret",
                    PaperTradingBaseUrl = "https://paper-api.alpaca.markets"
                }
            }
        };
        var service = new AlpacaPaperTradingService(
            new HttpClient(handler), Options.Create(config), NullLogger<AlpacaPaperTradingService>.Instance);
        var candidate = Candidate("AAPL");

        await service.PrepareAndSubmitAsync(new[] { candidate });

        Assert.NotNull(submitted);
        Assert.Contains("\"order_class\":\"bracket\"", submitted);
        Assert.Contains("\"qty\":\"10\"", submitted);
        Assert.Equal("submitted", candidate.AlpacaPaperOrder?.Status);
        Assert.Equal("paper-order-1", candidate.AlpacaPaperOrder?.OrderId);
    }

    [Fact]
    public async Task AlpacaPaperTrading_RefusesLiveHost()
    {
        var config = new BotOptions
        {
            MarketProvider = "Alpaca",
            PaperTrading = new PaperTradingOptions { Enabled = true, SubmitToAlpaca = true },
            Providers = new ProviderOptions
            {
                Alpaca = new AlpacaOptions
                {
                    ApiKeyId = "key", ApiSecret = "secret",
                    PaperTradingBaseUrl = "https://api.alpaca.markets"
                }
            }
        };
        var service = new AlpacaPaperTradingService(
            new HttpClient(new StubHandler(_ => throw new InvalidOperationException("network must not be called"))),
            Options.Create(config), NullLogger<AlpacaPaperTradingService>.Instance);
        var candidate = Candidate("AAPL");

        await service.PrepareAndSubmitAsync(new[] { candidate });

        Assert.Equal("error", candidate.AlpacaPaperOrder?.Status);
        Assert.Contains("Refusing brokerage URL", candidate.AlpacaPaperOrder?.Message);
    }

    private static Candidate Candidate(string symbol) => new()
    {
        Symbol = symbol,
        AssetClass = AssetClass.Stock,
        Indicators = new IndicatorSet { Price = 100m },
        Score = 80,
        StopLoss = 95m,
        Target1 = 106m,
        Target2 = 112m
    };

    private static PriceHistory History(string symbol, decimal start, decimal multiplier, int count)
    {
        var candles = new List<Candle>();
        decimal close = start;
        var first = DateTimeOffset.UtcNow.Date.AddDays(-count);
        for (int i = 0; i < count; i++)
        {
            decimal sharedCycle = ((i % 5) - 2) * 0.001m;
            close *= multiplier + sharedCycle;
            candles.Add(new Candle(first.AddDays(i), close, close, close, close, 1_000_000));
        }
        return new PriceHistory { Symbol = symbol, AssetClass = AssetClass.Stock, Candles = candles };
    }

    private static HttpResponseMessage Json(HttpStatusCode status, string body) => new(status)
    {
        Content = new StringContent(body, Encoding.UTF8, "application/json")
    };

    private sealed class FakeMarketProvider(IReadOnlyDictionary<string, PriceHistory> histories) : IMarketDataProvider
    {
        public string Name => "Fake";
        public Task<PriceHistory?> GetDailyHistoryAsync(
            string symbol, AssetClass assetClass, int lookbackDays, CancellationToken ct = default) =>
            Task.FromResult(histories.TryGetValue(symbol, out var history) ? history : null);
    }

    private sealed class StubHandler(Func<HttpRequestMessage, Task<HttpResponseMessage>> response) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct) =>
            response(request);
    }
}
