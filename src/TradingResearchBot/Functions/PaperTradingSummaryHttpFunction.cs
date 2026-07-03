using System.Globalization;
using System.Net;
using System.Text.Json;
using Microsoft.Azure.Functions.Worker;
using Microsoft.Azure.Functions.Worker.Http;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;

namespace TradingResearchBot.Functions;

/// <summary>
/// Exposes the Python paper-trading ledger as JSON for dashboards/websites.
/// Configure PaperTradingState:DataDir to the folder containing
/// active_paper_positions.json and trading_history.json.
/// </summary>
public sealed class PaperTradingSummaryHttpFunction
{
    private readonly IConfiguration _configuration;
    private readonly ILogger<PaperTradingSummaryHttpFunction> _logger;

    public PaperTradingSummaryHttpFunction(
        IConfiguration configuration,
        ILogger<PaperTradingSummaryHttpFunction> logger)
    {
        _configuration = configuration;
        _logger = logger;
    }

    [Function("PaperTradingSummary")]
    public async Task<HttpResponseData> Run(
        [HttpTrigger(AuthorizationLevel.Function, "get", Route = "paper-trading/summary")] HttpRequestData req,
        CancellationToken ct)
    {
        var dataDir = ResolveDataDir();
        var activePath = Path.Combine(dataDir, "active_paper_positions.json");
        var historyPath = Path.Combine(dataDir, "trading_history.json");

        if (!File.Exists(activePath) && !File.Exists(historyPath))
        {
            var notFound = req.CreateResponse(HttpStatusCode.NotFound);
            await notFound.WriteAsJsonAsync(new
            {
                error = "Paper trading state files were not found.",
                dataDir,
                expectedFiles = new[] { activePath, historyPath }
            }, ct);
            return notFound;
        }

        try
        {
            var active = ReadObject(activePath);
            var history = ReadArray(historyPath);

            var closedTrades = BuildClosedTrades(history);
            var openPositions = BuildOpenPositions(active);

            var realizedPnl = closedTrades.Sum(t => t.PnlUsd);
            var unrealizedPnl = openPositions.Sum(p => p.UnrealizedPnlUsd);
            var wins = closedTrades.Count(t => t.PnlUsd > 0);
            var losses = closedTrades.Count - wins;

            var daily = closedTrades
                .GroupBy(t => t.ExitTimestampUtc.Date)
                .OrderBy(g => g.Key)
                .Select(g => new
                {
                    date = g.Key.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                    closedTrades = g.Count(),
                    realizedPnlUsd = RoundMoney(g.Sum(t => t.PnlUsd))
                });

            var response = req.CreateResponse(HttpStatusCode.OK);
            await response.WriteAsJsonAsync(new
            {
                generatedAtUtc = DateTimeOffset.UtcNow,
                dataDir,
                summary = new
                {
                    closedTrades = closedTrades.Count,
                    wins,
                    losses,
                    winRatePct = closedTrades.Count == 0 ? 0 : Math.Round((decimal)wins / closedTrades.Count * 100, 2),
                    realizedPnlUsd = RoundMoney(realizedPnl),
                    openPositions = openPositions.Count,
                    unrealizedPnlUsd = RoundMoney(unrealizedPnl),
                    totalPnlUsd = RoundMoney(realizedPnl + unrealizedPnl),
                    allocatedUsd = RoundMoney(openPositions.Sum(p => p.AllocatedUsd))
                },
                daily,
                openPositions,
                recentClosedTrades = closedTrades
                    .OrderByDescending(t => t.ExitTimestampUtc)
                    .Take(25),
                notes = new[]
                {
                    "Open P/L uses last known price from the ledger when current live prices are not present.",
                    "Endpoint is protected with Function authorization; pass a function key when deployed."
                }
            }, ct);
            return response;
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _logger.LogError(ex, "Failed to build paper trading summary from {DataDir}.", dataDir);
            var error = req.CreateResponse(HttpStatusCode.InternalServerError);
            await error.WriteAsJsonAsync(new { error = ex.Message, dataDir }, ct);
            return error;
        }
    }

    private string ResolveDataDir()
    {
        var configured = _configuration["PaperTradingState:DataDir"]
            ?? _configuration["PAPER_TRADER_DATA_DIR"]
            ?? _configuration["DATA_DIR"];

        if (!string.IsNullOrWhiteSpace(configured))
        {
            return Environment.ExpandEnvironmentVariables(configured.Trim());
        }

        return Directory.GetCurrentDirectory();
    }

    private static JsonElement ReadObject(string path)
    {
        if (!File.Exists(path))
        {
            return JsonDocument.Parse("{}").RootElement.Clone();
        }

        using var document = JsonDocument.Parse(File.ReadAllText(path));
        return document.RootElement.Clone();
    }

    private static JsonElement ReadArray(string path)
    {
        if (!File.Exists(path))
        {
            return JsonDocument.Parse("[]").RootElement.Clone();
        }

        using var document = JsonDocument.Parse(File.ReadAllText(path));
        return document.RootElement.Clone();
    }

    private static List<ClosedTradeDto> BuildClosedTrades(JsonElement history)
    {
        var trades = new List<ClosedTradeDto>();
        if (history.ValueKind != JsonValueKind.Array)
        {
            return trades;
        }

        foreach (var item in history.EnumerateArray())
        {
            var performance = GetObject(item, "performance");
            if (!string.Equals(GetString(performance, "status"), "CLOSED", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            var entry = GetObject(item, "entry");
            var exit = GetObject(item, "exit");
            trades.Add(new ClosedTradeDto(
                ProductId: GetString(item, "product_id"),
                Mode: GetString(item, "mode"),
                EntryTimestampUtc: GetDateTimeOffset(entry, "timestamp"),
                ExitTimestampUtc: GetDateTimeOffset(exit, "timestamp"),
                EntryPriceUsd: GetDecimal(entry, "price_usd"),
                ExitPriceUsd: GetDecimal(exit, "price_usd"),
                ExitReason: GetString(exit, "reason"),
                PnlUsd: GetDecimal(performance, "pnl_usd"),
                PnlPercentage: GetDecimal(performance, "pnl_percentage")));
        }

        return trades;
    }

    private static List<OpenPositionDto> BuildOpenPositions(JsonElement active)
    {
        var positions = new List<OpenPositionDto>();
        if (active.ValueKind != JsonValueKind.Object)
        {
            return positions;
        }

        foreach (var property in active.EnumerateObject())
        {
            var item = property.Value;
            var productId = GetString(item, "product_id");
            if (string.IsNullOrWhiteSpace(productId))
            {
                productId = property.Name;
            }

            var entryPrice = GetDecimal(item, "entry_price");
            var highestPrice = GetDecimal(item, "highest_tracked_price");
            var allocated = GetDecimal(item, "allocated_usd");
            var quantity = GetDecimal(item, "simulated_qty");
            var markPrice = highestPrice > 0 ? highestPrice : entryPrice;
            var value = quantity * markPrice;
            var pnl = value - allocated;

            positions.Add(new OpenPositionDto(
                ProductId: productId,
                Mode: GetString(item, "mode"),
                EntryTimestampUtc: GetDateTimeOffset(item, "entry_timestamp"),
                EntryPriceUsd: entryPrice,
                MarkPriceUsd: markPrice,
                AllocatedUsd: allocated,
                Quantity: quantity,
                CurrentTrailingStop: GetDecimal(item, "current_trailing_stop"),
                TakeProfitBoundary: GetDecimal(item, "take_profit_boundary"),
                Strategy: GetString(item, "entry_strategy"),
                StrategyScore: GetDecimal(item, "entry_strategy_score"),
                UnrealizedPnlUsd: pnl,
                UnrealizedPnlPct: allocated == 0 ? 0 : pnl / allocated * 100));
        }

        return positions;
    }

    private static JsonElement GetObject(JsonElement element, string name)
    {
        return element.ValueKind == JsonValueKind.Object && element.TryGetProperty(name, out var value)
            ? value
            : default;
    }

    private static string GetString(JsonElement element, string name)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(name, out var value))
        {
            return string.Empty;
        }

        return value.ValueKind == JsonValueKind.String ? value.GetString() ?? string.Empty : value.ToString();
    }

    private static decimal GetDecimal(JsonElement element, string name)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(name, out var value))
        {
            return 0m;
        }

        return value.ValueKind switch
        {
            JsonValueKind.Number when value.TryGetDecimal(out var number) => number,
            JsonValueKind.String when decimal.TryParse(value.GetString(), NumberStyles.Any, CultureInfo.InvariantCulture, out var number) => number,
            _ => 0m
        };
    }

    private static DateTimeOffset GetDateTimeOffset(JsonElement element, string name)
    {
        var value = GetString(element, name);
        return DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var parsed)
            ? parsed.ToUniversalTime()
            : DateTimeOffset.MinValue;
    }

    private static decimal RoundMoney(decimal value) => Math.Round(value, 2, MidpointRounding.AwayFromZero);

    private sealed record ClosedTradeDto(
        string ProductId,
        string Mode,
        DateTimeOffset EntryTimestampUtc,
        DateTimeOffset ExitTimestampUtc,
        decimal EntryPriceUsd,
        decimal ExitPriceUsd,
        string ExitReason,
        decimal PnlUsd,
        decimal PnlPercentage);

    private sealed record OpenPositionDto(
        string ProductId,
        string Mode,
        DateTimeOffset EntryTimestampUtc,
        decimal EntryPriceUsd,
        decimal MarkPriceUsd,
        decimal AllocatedUsd,
        decimal Quantity,
        decimal CurrentTrailingStop,
        decimal TakeProfitBoundary,
        string Strategy,
        decimal StrategyScore,
        decimal UnrealizedPnlUsd,
        decimal UnrealizedPnlPct);
}
