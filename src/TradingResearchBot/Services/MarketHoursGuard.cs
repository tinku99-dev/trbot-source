using Microsoft.Extensions.Options;
using TradingResearchBot.Models;

namespace TradingResearchBot.Services;

/// <summary>
/// Determines whether the current instant falls inside the configured local
/// market window on a weekday. Used as an in-code guard in addition to the cron
/// schedule (cron runs in UTC; this enforces Eastern wall-clock + DST safely).
/// </summary>
public sealed class MarketHoursGuard
{
    private readonly TimeZoneInfo _tz;
    private readonly TimeOnly _open;
    private readonly TimeOnly _close;
    private readonly TimeOnly _cryptoClose;

    public MarketHoursGuard(IOptions<BotOptions> options)
    {
        var o = options.Value;
        _tz = ResolveTimeZone(o.TimeZone);
        _open = ParseTime(o.MarketOpenLocal, new TimeOnly(8, 30));
        _close = ParseTime(o.MarketCloseLocal, new TimeOnly(15, 0));
        _cryptoClose = ParseTime(o.CryptoCloseLocal, new TimeOnly(22, 0));
    }

    /// <summary>True during the regular weekday stock session (open..close, weekdays).</summary>
    public bool AreStocksOpen(DateTimeOffset utcNow)
    {
        var local = TimeZoneInfo.ConvertTime(utcNow, _tz);
        if (local.DayOfWeek is DayOfWeek.Saturday or DayOfWeek.Sunday)
            return false;

        var t = TimeOnly.FromTimeSpan(local.TimeOfDay);
        return t >= _open && t <= _close;
    }

    /// <summary>
    /// True during the crypto window — every day (crypto is 24/7) from the morning
    /// open through the configured evening crypto cutoff.
    /// </summary>
    public bool IsCryptoOpen(DateTimeOffset utcNow)
    {
        var local = TimeZoneInfo.ConvertTime(utcNow, _tz);
        var t = TimeOnly.FromTimeSpan(local.TimeOfDay);
        return t >= _open && t <= _cryptoClose;
    }

    /// <summary>Back-compat alias: the stock session.</summary>
    public bool IsOpen(DateTimeOffset utcNow) => AreStocksOpen(utcNow);

    public string TimeZoneId => _tz.Id;

    /// <summary>The market-local calendar date for a given UTC instant (yyyy-MM-dd).</summary>
    public string LocalDate(DateTimeOffset utcNow) =>
        TimeZoneInfo.ConvertTime(utcNow, _tz).ToString("yyyy-MM-dd");

    private static TimeZoneInfo ResolveTimeZone(string id)
    {
        // Try the configured id first; fall back across Windows/IANA naming.
        foreach (var candidate in new[] { id, "Eastern Standard Time", "America/New_York" })
        {
            try { return TimeZoneInfo.FindSystemTimeZoneById(candidate); }
            catch (TimeZoneNotFoundException) { }
            catch (InvalidTimeZoneException) { }
        }
        return TimeZoneInfo.Utc;
    }

    private static TimeOnly ParseTime(string value, TimeOnly fallback) =>
        TimeOnly.TryParse(value, out var t) ? t : fallback;
}
