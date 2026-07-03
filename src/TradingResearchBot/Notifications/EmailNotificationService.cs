using System.Net;
using System.Net.Mail;
using Microsoft.Extensions.Logging;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Notifications;

/// <summary>
/// Emails the rendered text report via SMTP. All credentials come from
/// configuration/secrets. Supports multiple comma-separated recipients.
/// </summary>
public sealed class EmailNotificationService : INotificationService
{
    private readonly ILogger<EmailNotificationService> _logger;
    private readonly EmailOptions _options;
    private readonly bool _dryRun;
    private readonly IReportBuilder _reportBuilder;

    public EmailNotificationService(
        ILogger<EmailNotificationService> logger,
        EmailOptions options,
        bool dryRun,
        IReportBuilder reportBuilder)
    {
        _logger = logger;
        _options = options;
        _dryRun = dryRun;
        _reportBuilder = reportBuilder;
    }

    public async Task NotifyAsync(ResearchReport report, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(_options.SmtpHost) ||
            string.IsNullOrWhiteSpace(_options.From) ||
            string.IsNullOrWhiteSpace(_options.To))
        {
            _logger.LogWarning("Email settings incomplete; skipping email notification.");
            return;
        }

        var body = _reportBuilder.RenderText(report);
        var subject = $"[Research Bot] {report.Candidates.Count} candidates — {report.GeneratedAtUtc:yyyy-MM-dd HH:mm} UTC";

        if (_dryRun)
        {
            _logger.LogInformation("[DryRun] Would email '{Subject}' to {To} ({Bytes} bytes).",
                subject, _options.To, body.Length);
            return;
        }

        using var message = new MailMessage
        {
            From = new MailAddress(_options.From),
            Subject = subject,
            Body = body,
            IsBodyHtml = false
        };
        foreach (var to in _options.To.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
            message.To.Add(to);

        using var client = new SmtpClient(_options.SmtpHost, _options.SmtpPort)
        {
            EnableSsl = _options.UseSsl,
            DeliveryMethod = SmtpDeliveryMethod.Network
        };
        if (!string.IsNullOrWhiteSpace(_options.Username))
            client.Credentials = new NetworkCredential(_options.Username, _options.Password);

        await client.SendMailAsync(message, ct);
        _logger.LogInformation("Email notification sent to {To}.", _options.To);
    }
}
