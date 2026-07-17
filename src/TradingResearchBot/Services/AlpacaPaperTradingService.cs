using System.Globalization;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using TradingResearchBot.Models;

namespace TradingResearchBot.Services;

/// <summary>
/// Submits protected whole-share bracket orders to Alpaca's PAPER endpoint only.
/// It also enriches stock candidates with simulated plans when submission is off.
/// </summary>
public sealed class AlpacaPaperTradingService
{
    private const string RequiredPaperHost = "paper-api.alpaca.markets";
    private readonly HttpClient _http;
    private readonly PaperTradingOptions _paper;
    private readonly AlpacaOptions _alpaca;
    private readonly string _marketProvider;
    private readonly ILogger<AlpacaPaperTradingService> _logger;

    public AlpacaPaperTradingService(
        HttpClient http,
        IOptions<BotOptions> options,
        ILogger<AlpacaPaperTradingService> logger)
    {
        _http = http;
        _paper = options.Value.PaperTrading;
        _alpaca = options.Value.Providers.Alpaca;
        _marketProvider = options.Value.MarketProvider;
        _logger = logger;

        if (!string.IsNullOrWhiteSpace(_alpaca.ApiKeyId))
            _http.DefaultRequestHeaders.TryAddWithoutValidation("APCA-API-KEY-ID", _alpaca.ApiKeyId);
        if (!string.IsNullOrWhiteSpace(_alpaca.ApiSecret))
            _http.DefaultRequestHeaders.TryAddWithoutValidation("APCA-API-SECRET-KEY", _alpaca.ApiSecret);
    }

    public async Task PrepareAndSubmitAsync(IReadOnlyList<Candidate> candidates, CancellationToken ct = default)
    {
        if (!_paper.Enabled) return;

        foreach (var candidate in candidates.Where(IsEligibleStock))
            candidate.PaperTrade = BuildPlan(candidate, _paper.CapitalPerTradeUsd, _paper.MaxOpenPositions);

        if (!_paper.SubmitToAlpaca) return;
        AccountSnapshot account;
        try
        {
            EnsurePaperEndpoint();
            EnsureCredentials();
            account = await GetAccountAsync(ct);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _logger.LogError(ex, "Alpaca paper trading initialization failed; no orders submitted.");
            foreach (var candidate in candidates.Where(IsEligibleStock))
                candidate.AlpacaPaperOrder = new AlpacaPaperOrderResult(
                    "error", null, null, 0, 0, 0, 0, ex.Message);
            return;
        }
        if (account.TradingBlocked)
        {
            foreach (var candidate in candidates.Where(IsEligibleStock))
                candidate.AlpacaPaperOrder = Skipped("Alpaca paper account is trading-blocked");
            return;
        }

        List<JsonElement> positions;
        List<JsonElement> todaysOrders;
        try
        {
            positions = await GetCollectionAsync("/v2/positions", ct);
            var midnightUtc = new DateTimeOffset(DateTime.UtcNow.Date, TimeSpan.Zero);
            string today = Uri.EscapeDataString(midnightUtc.ToString("O", CultureInfo.InvariantCulture));
            todaysOrders = await GetCollectionAsync($"/v2/orders?status=all&after={today}&limit=500", ct);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _logger.LogError(ex, "Could not read Alpaca paper positions/orders; no orders submitted.");
            foreach (var candidate in candidates.Where(IsEligibleStock))
                candidate.AlpacaPaperOrder = new AlpacaPaperOrderResult(
                    "error", null, null, 0, 0, 0, 0, ex.Message);
            return;
        }
        var occupied = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        decimal currentExposure = 0;
        foreach (var position in positions)
        {
            var symbol = GetString(position, "symbol");
            if (symbol.Length > 0) occupied.Add(symbol);
            currentExposure += Math.Abs(GetDecimal(position, "market_value"));
        }
        int submittedToday = 0;
        foreach (var order in todaysOrders)
        {
            var symbol = GetString(order, "symbol");
            if (symbol.Length > 0) occupied.Add(symbol);
            string clientId = GetString(order, "client_order_id");
            if (clientId.StartsWith("trbot-", StringComparison.OrdinalIgnoreCase) &&
                string.Equals(GetString(order, "side"), "buy", StringComparison.OrdinalIgnoreCase))
                submittedToday++;
        }

        int slots = Math.Max(0, _paper.MaxOpenPositions - positions.Count);
        int dailyRoom = Math.Max(0, _paper.MaxNewPositionsPerDay - submittedToday);
        int remaining = Math.Min(dailyRoom, slots);
        decimal exposureCap = account.Equity * Math.Clamp(_paper.MaxAccountExposurePct, 0m, 100m) / 100m;

        foreach (var candidate in candidates.Where(IsEligibleStock))
        {
            if (remaining <= 0) break;
            if (occupied.Contains(candidate.Symbol))
            {
                candidate.AlpacaPaperOrder = Skipped("existing position or open order");
                continue;
            }

            decimal entry = candidate.Indicators.Price;
            decimal stop = candidate.StopLoss ?? 0;
            decimal target = candidate.Target1 ?? 0;
            if (entry <= 0 || stop <= 0 || stop >= entry || target <= entry)
            {
                candidate.AlpacaPaperOrder = Skipped("invalid entry/stop/target structure");
                continue;
            }

            decimal riskPerShare = entry - stop;
            decimal riskBudget = account.Equity * Math.Clamp(_paper.RiskPerTradePct, 0m, 5m) / 100m;
            decimal room = Math.Max(0, exposureCap - currentExposure);
            decimal notionalCap = Math.Min(_paper.CapitalPerTradeUsd, Math.Min(account.BuyingPower, room));
            int quantityByCash = (int)Math.Floor(notionalCap / entry);
            int quantityByRisk = riskPerShare > 0 ? (int)Math.Floor(riskBudget / riskPerShare) : 0;
            int quantity = Math.Min(quantityByCash, quantityByRisk);
            if (quantity < 1)
            {
                candidate.AlpacaPaperOrder = Skipped("risk, buying-power, or exposure cap allows no whole share");
                continue;
            }

            decimal notional = quantity * entry;
            string clientOrderId = BuildClientOrderId(candidate.Symbol);
            var payload = new
            {
                symbol = candidate.Symbol.ToUpperInvariant(),
                qty = quantity.ToString(CultureInfo.InvariantCulture),
                side = "buy",
                type = "market",
                time_in_force = "day",
                order_class = "bracket",
                take_profit = new { limit_price = target.ToString("0.00########", CultureInfo.InvariantCulture) },
                stop_loss = new { stop_price = stop.ToString("0.00########", CultureInfo.InvariantCulture) },
                client_order_id = clientOrderId
            };

            try
            {
                using var response = await _http.PostAsJsonAsync("/v2/orders", payload, ct);
                string responseJson = await response.Content.ReadAsStringAsync(ct);
                if (!response.IsSuccessStatusCode)
                {
                    candidate.AlpacaPaperOrder = new AlpacaPaperOrderResult(
                        "rejected", null, clientOrderId, quantity, notional, stop, target,
                        $"Alpaca HTTP {(int)response.StatusCode}: {SafeMessage(responseJson)}");
                    _logger.LogWarning("Alpaca paper order rejected for {Symbol}: HTTP {Status}.",
                        candidate.Symbol, (int)response.StatusCode);
                    continue;
                }

                using var doc = JsonDocument.Parse(responseJson);
                string orderId = GetString(doc.RootElement, "id");
                candidate.AlpacaPaperOrder = new AlpacaPaperOrderResult(
                    "submitted", orderId, clientOrderId, quantity, notional, stop, target, null);
                occupied.Add(candidate.Symbol);
                account = account with { BuyingPower = Math.Max(0, account.BuyingPower - notional) };
                currentExposure += notional;
                remaining--;
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                candidate.AlpacaPaperOrder = new AlpacaPaperOrderResult(
                    "error", null, clientOrderId, quantity, notional, stop, target, ex.Message);
                _logger.LogError(ex, "Alpaca paper order submission failed for {Symbol}.", candidate.Symbol);
            }
        }
    }

    private bool IsEligibleStock(Candidate candidate) =>
        candidate.AssetClass == AssetClass.Stock && candidate.StrategyQualified &&
        candidate.Score >= _paper.MinCandidateScore;

    private PaperTradePlan? BuildPlan(Candidate candidate, decimal allocation, int maxPositions)
    {
        decimal entry = candidate.Indicators.Price;
        decimal stop = candidate.StopLoss ?? 0;
        decimal target1 = candidate.Target1 ?? 0;
        decimal target2 = candidate.Target2 ?? 0;
        if (allocation <= 0 || entry <= 0 || stop <= 0 || target1 <= 0 || target2 <= 0) return null;
        decimal quantity = Math.Floor(allocation / entry);
        if (quantity < 1) return null;
        return new PaperTradePlan(
            quantity * entry, quantity, entry, stop, target1, target2,
            quantity * Math.Max(0, entry - stop), quantity * Math.Max(0, target1 - entry),
            quantity * Math.Max(0, target2 - entry), _paper.TotalCapitalUsd, maxPositions);
    }

    private void EnsurePaperEndpoint()
    {
        if (!Uri.TryCreate(_alpaca.PaperTradingBaseUrl, UriKind.Absolute, out var uri) ||
            uri.Scheme != Uri.UriSchemeHttps ||
            !string.Equals(uri.Host, RequiredPaperHost, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException(
                $"Refusing brokerage URL '{_alpaca.PaperTradingBaseUrl}'. Only https://{RequiredPaperHost} is allowed.");
        _http.BaseAddress = new Uri($"https://{RequiredPaperHost}");
    }

    private void EnsureCredentials()
    {
        if (!string.Equals(_marketProvider, "Alpaca", StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException(
                "Alpaca paper submission requires Bot:MarketProvider=Alpaca so orders use the evaluated market data.");
        if (string.IsNullOrWhiteSpace(_alpaca.ApiKeyId) || string.IsNullOrWhiteSpace(_alpaca.ApiSecret))
            throw new InvalidOperationException("Alpaca paper credentials are not configured.");
    }

    private async Task<AccountSnapshot> GetAccountAsync(CancellationToken ct)
    {
        using var response = await _http.GetAsync("/v2/account", ct);
        string json = await response.Content.ReadAsStringAsync(ct);
        response.EnsureSuccessStatusCode();
        using var doc = JsonDocument.Parse(json);
        return new AccountSnapshot(
            GetDecimal(doc.RootElement, "equity"),
            GetDecimal(doc.RootElement, "buying_power"),
            GetBool(doc.RootElement, "trading_blocked"));
    }

    private async Task<List<JsonElement>> GetCollectionAsync(string path, CancellationToken ct)
    {
        using var response = await _http.GetAsync(path, ct);
        string json = await response.Content.ReadAsStringAsync(ct);
        response.EnsureSuccessStatusCode();
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement.ValueKind == JsonValueKind.Array
            ? doc.RootElement.EnumerateArray().Select(x => x.Clone()).ToList()
            : new List<JsonElement>();
    }

    private static AlpacaPaperOrderResult Skipped(string message) =>
        new("skipped", null, null, 0, 0, 0, 0, message);

    private static string BuildClientOrderId(string symbol) =>
        $"trbot-{symbol.ToLowerInvariant()}-{DateTimeOffset.UtcNow:yyyyMMdd}";

    private static string SafeMessage(string json)
    {
        if (json.Length <= 300) return json;
        return json[..300];
    }

    private static string GetString(JsonElement element, string property) =>
        element.TryGetProperty(property, out var value) ? value.ToString() : "";

    private static decimal GetDecimal(JsonElement element, string property)
    {
        if (!element.TryGetProperty(property, out var value)) return 0;
        return value.ValueKind switch
        {
            JsonValueKind.Number when value.TryGetDecimal(out var number) => number,
            JsonValueKind.String when decimal.TryParse(value.GetString(), NumberStyles.Any,
                CultureInfo.InvariantCulture, out var number) => number,
            _ => 0
        };
    }

    private static bool GetBool(JsonElement element, string property) =>
        element.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.True;

    private sealed record AccountSnapshot(decimal Equity, decimal BuyingPower, bool TradingBlocked);
}
