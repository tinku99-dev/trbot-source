using Microsoft.Extensions.Logging;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Notifications;

/// <summary>Fans a report out to multiple notification channels.</summary>
public sealed class CompositeNotificationService : INotificationService
{
    private readonly IReadOnlyList<INotificationService> _services;
    private readonly ILogger<CompositeNotificationService> _logger;

    public CompositeNotificationService(
        IEnumerable<INotificationService> services,
        ILogger<CompositeNotificationService> logger)
    {
        _services = services.ToList();
        _logger = logger;
    }

    public async Task NotifyAsync(ResearchReport report, CancellationToken ct = default)
    {
        if (_services.Count == 0)
        {
            _logger.LogInformation("No notification channels configured.");
            return;
        }

        foreach (var svc in _services)
        {
            try
            {
                await svc.NotifyAsync(report, ct);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Notification channel {Channel} failed.", svc.GetType().Name);
            }
        }
    }
}

/// <summary>No-op channel used when notifications are disabled.</summary>
public sealed class NullNotificationService : INotificationService
{
    public Task NotifyAsync(ResearchReport report, CancellationToken ct = default) => Task.CompletedTask;
}
