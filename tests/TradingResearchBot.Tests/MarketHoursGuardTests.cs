using Microsoft.Extensions.Options;
using TradingResearchBot.Models;
using TradingResearchBot.Services;
using Xunit;

namespace TradingResearchBot.Tests;

public class MarketHoursGuardTests
{
    private static MarketHoursGuard Build() =>
        new(Options.Create(new BotOptions
        {
            TimeZone = "Eastern Standard Time",
            MarketOpenLocal = "08:30",
            MarketCloseLocal = "15:00"
        }));

    [Fact]
    public void Weekend_IsClosed()
    {
        var guard = Build();
        // 2026-06-13 is a Saturday.
        var sat = new DateTimeOffset(2026, 6, 13, 16, 0, 0, TimeSpan.Zero);
        Assert.False(guard.IsOpen(sat));
    }

    [Fact]
    public void Weekday_MiddayEastern_IsOpen()
    {
        var guard = Build();
        // 2026-06-15 is a Monday. 16:00 UTC ≈ 12:00 ET (EDT) → inside 08:30-15:00.
        var midday = new DateTimeOffset(2026, 6, 15, 16, 0, 0, TimeSpan.Zero);
        Assert.True(guard.IsOpen(midday));
    }

    [Fact]
    public void Weekday_LateEvening_IsClosed()
    {
        var guard = Build();
        // 23:00 UTC ≈ 19:00 ET → after close.
        var evening = new DateTimeOffset(2026, 6, 15, 23, 0, 0, TimeSpan.Zero);
        Assert.False(guard.IsOpen(evening));
    }
}
