using TradingResearchBot.Abstractions;

namespace TradingResearchBot.Notifications;

/// <summary>
/// Routes a research report to the correct channel based on cadence:
///   • Intraday alerts  → typically Discord (frequent, low-noise).
///   • Daily digest     → typically email (one summary per day).
/// Each channel is an <see cref="INotificationService"/> (Discord, Email, or Null).
/// </summary>
public sealed class NotificationRouter
{
    public INotificationService Intraday { get; }
    public INotificationService Daily { get; }

    /// <summary>When true, intraday runs with no NEW qualified names notify nothing.</summary>
    public bool SuppressEmpty { get; }

    public NotificationRouter(INotificationService intraday, INotificationService daily, bool suppressEmpty)
    {
        Intraday = intraday;
        Daily = daily;
        SuppressEmpty = suppressEmpty;
    }
}
