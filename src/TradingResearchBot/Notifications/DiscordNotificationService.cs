using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Notifications;

/// <summary>
/// Posts a compact summary of the report to a Discord channel via webhook.
/// Webhook URL comes from configuration/secrets — never hard-coded.
/// </summary>
public sealed class DiscordNotificationService : INotificationService
{
    private readonly HttpClient _http;
    private readonly ILogger<DiscordNotificationService> _logger;
    private readonly DiscordOptions _options;
    private readonly bool _dryRun;
    private readonly IReportBuilder _reportBuilder;

    public DiscordNotificationService(
        HttpClient http,
        ILogger<DiscordNotificationService> logger,
        DiscordOptions options,
        bool dryRun,
        IReportBuilder reportBuilder)
    {
        _http = http;
        _logger = logger;
        _options = options;
        _dryRun = dryRun;
        _reportBuilder = reportBuilder;
    }

    public async Task NotifyAsync(ResearchReport report, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(_options.WebhookUrl))
        {
            _logger.LogWarning("Discord webhook URL not configured; skipping Discord notification.");
            return;
        }

        var fields = report.Candidates.Take(10).Select(c => new
        {
            name = $"{Emoji(c)} {c.TierLabel} {c.Symbol} ({c.StrategyMode ?? string.Join("/", c.Categories.Take(2))})",
            value = BuildCandidateValue(c),
            inline = false
        }).ToArray();

        var payload = new
        {
            username = "Trading Research Bot",
            embeds = new[]
            {
                new
                {
                    title = $"{report.StrategyMode} research candidates",
                    description = $"Top {fields.Length} of {report.Candidates.Count} ranked candidates. Educational research only; no live orders.",
                    color = 3066993,
                    fields
                }
            }
        };

        var json = JsonSerializer.Serialize(payload);

        if (_dryRun)
        {
            _logger.LogInformation("[DryRun] Would POST to Discord webhook. Payload bytes: {Bytes}", json.Length);
            return;
        }

        using var content = new StringContent(json, Encoding.UTF8, "application/json");
        using var resp = await _http.PostAsync(_options.WebhookUrl, content, ct);
        if (resp.IsSuccessStatusCode)
            _logger.LogInformation("Discord notification sent ({Status}).", (int)resp.StatusCode);
        else
            _logger.LogError("Discord notification failed: {Status}", (int)resp.StatusCode);
    }

    private static string Emoji(Candidate c) =>
        c.Categories.Contains(ReportCategory.Fallout) ? "🔻" : "📈";

    private static string BuildCandidateValue(Candidate c)
    {
        var ind = c.Indicators;
        var sb = new StringBuilder();

        sb.Append("**Setup:** ")
            .Append(c.Patterns.FirstOrDefault() ?? c.StrategyMode ?? "Research")
            .Append(" | **Tier:** ").Append(c.TierLabel)
            .Append(" | **Score:** ").Append(c.Score.ToString("F0"))
            .Append("\n");

        sb.Append("**Market:** $").Append(ind.Price.ToString("F4"))
            .Append(" | VolRS ").Append(Fmt(ind.VolumeRelativeStrength, "F2", "x"))
            .Append(" | RSI ").Append(Fmt(ind.Rsi14, "F0"))
            .Append(" | OBV ").Append(FmtSigned(ind.ObvPressurePct, "F1", "%"))
            .Append(" / up-vol ").Append(FmtPercent(ind.ObvUpVolumeRatio))
            .Append("\n");

        sb.Append("**Levels:** Buy ").Append(FmtRange(c.BuyRangeLow, c.BuyRangeHigh))
            .Append(" | Stop ").Append(FmtMoney(c.StopLoss))
            .Append(" | T1 ").Append(FmtMoney(c.Target1))
            .Append(" | T2 ").Append(FmtMoney(c.Target2))
            .Append("\n");

        if (c.PaperTrade is { } paper)
        {
            sb.Append("**Paper ").Append(c.TierLabel).Append(":** $").Append(paper.AllocationUsd.ToString("F0"))
                .Append(" size (~").Append(paper.EstimatedQuantity.ToString("0.########"))
                .Append(") | Risk $").Append(paper.RiskUsd.ToString("F2"))
                .Append(" | T1 +$").Append(paper.Target1ProfitUsd.ToString("F2"))
                .Append(" | Budget $").Append(paper.TotalBudgetUsd.ToString("F0"))
                .Append("/").Append(paper.MaxOpenPositions).Append(" slots")
                .Append("\n");
        }

        if (c.Signals.Count > 0)
            sb.Append("**Why:** ").Append(string.Join("; ", c.Signals.Take(3)));

        return sb.ToString();
    }

    private static string Fmt(decimal? value, string format, string suffix = "") =>
        value is { } v ? v.ToString(format) + suffix : "n/a";

    private static string FmtSigned(decimal? value, string format, string suffix = "") =>
        value is { } v ? (v >= 0 ? "+" : "") + v.ToString(format) + suffix : "n/a";

    private static string FmtPercent(decimal? value) =>
        value is { } v ? v.ToString("P0") : "n/a";

    private static string FmtMoney(decimal? value) =>
        value is { } v ? "$" + v.ToString("F4") : "n/a";

    private static string FmtRange(decimal? low, decimal? high) =>
        low is { } l && high is { } h ? $"${l:F4}-${h:F4}" : "n/a";
}
