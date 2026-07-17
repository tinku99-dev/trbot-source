using Microsoft.Azure.Functions.Worker;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Indicators;
using TradingResearchBot.Models;
using TradingResearchBot.Notifications;
using TradingResearchBot.Providers;
using TradingResearchBot.Reports;
using TradingResearchBot.Scoring;
using TradingResearchBot.Services;
using TradingResearchBot.Storage;

var host = new HostBuilder()
    .ConfigureFunctionsWorkerDefaults()
    .ConfigureAppConfiguration(cfg =>
    {
        cfg.AddJsonFile("appsettings.sample.json", optional: true, reloadOnChange: false);
        cfg.AddEnvironmentVariables();
    })
    .ConfigureServices((context, services) =>
    {
        var configuration = context.Configuration;

        services.AddApplicationInsightsTelemetryWorkerService();
        services.ConfigureFunctionsApplicationInsights();

        // --- Options ---
        services.Configure<BotOptions>(configuration.GetSection(BotOptions.SectionName));

        // --- Core engines (stateless → singletons) ---
        services.AddSingleton<IIndicatorEngine, IndicatorEngine>();
        services.AddSingleton<IScoringEngine, ScoringEngine>();
        services.AddSingleton<IOptionsStrategist, OptionsStrategist>();
        services.AddSingleton<IReportBuilder, ReportBuilder>();
        services.AddSingleton<IReportStore, FileReportStore>();
        services.AddSingleton<IAlertStateStore, BlobAlertStateStore>();
        services.AddSingleton<MarketHoursGuard>();
        services.AddScoped<InstitutionalOverlayService>();
        services.AddHttpClient<AlpacaPaperTradingService>();
        services.AddScoped<ResearchService>();

        // --- Market data provider (selectable via Bot:MarketProvider) ---
        var providerName = configuration[$"{BotOptions.SectionName}:MarketProvider"] ?? "Mock";
        if (string.Equals(providerName, "Polygon", StringComparison.OrdinalIgnoreCase))
            services.AddHttpClient<IMarketDataProvider, PolygonMarketDataProvider>();
        else if (string.Equals(providerName, "Alpaca", StringComparison.OrdinalIgnoreCase))
            services.AddHttpClient<IMarketDataProvider, AlpacaMarketDataProvider>();
        else if (string.Equals(providerName, "LicensedHttp", StringComparison.OrdinalIgnoreCase))
            services.AddHttpClient<IMarketDataProvider, LicensedHttpMarketDataProvider>();
        else
            services.AddSingleton<IMarketDataProvider, MockMarketDataProvider>();

        // --- Intraday data provider (for the multi-timeframe crypto scalp strategy) ---
        // Only Alpaca offers a free real-time intraday feed; everything else no-ops.
        if (string.Equals(providerName, "Alpaca", StringComparison.OrdinalIgnoreCase))
            services.AddHttpClient<IIntradayMarketDataProvider, AlpacaMarketDataProvider>();
        else
            services.AddSingleton<IIntradayMarketDataProvider, NullIntradayMarketDataProvider>();

        // --- Crypto screener (for dynamic scalp universe selection) ---
        services.AddHttpClient<CryptoScreenerProvider>();

        services.AddSingleton<CryptoScalpEvaluator>();
        services.AddScoped<CryptoScalpService>();

        // --- Asset analyzer (multi-timeframe analysis for any ticker) ---
        services.AddScoped<AssetAnalyzerService>();

        // --- Universe selection (selectable via Bot:Universe:Mode) ---
        var universeMode = configuration[$"{BotOptions.SectionName}:Universe:Mode"] ?? "Static";
        if (string.Equals(universeMode, "Dynamic", StringComparison.OrdinalIgnoreCase))
            services.AddHttpClient<IUniverseProvider, AlpacaScreenerUniverseProvider>();
        else
            services.AddSingleton<IUniverseProvider, StaticUniverseProvider>();

        // --- Options data provider (selectable via Bot:Options:Provider) ---
        var optionsProvider = configuration[$"{BotOptions.SectionName}:Options:Provider"] ?? "Mock";
        if (string.Equals(optionsProvider, "Tradier", StringComparison.OrdinalIgnoreCase))
            services.AddHttpClient<IOptionsDataProvider, TradierOptionsDataProvider>();
        else if (string.Equals(optionsProvider, "LicensedHttp", StringComparison.OrdinalIgnoreCase))
            services.AddHttpClient<IOptionsDataProvider, LicensedHttpOptionsDataProvider>();
        else
            services.AddSingleton<IOptionsDataProvider, MockOptionsDataProvider>();

        // --- Notifications (selectable via Bot:Notifications:Provider) ---
        RegisterNotifications(services, configuration);
    })
    .Build();

host.Run();

static void RegisterNotifications(IServiceCollection services, IConfiguration configuration)
{
    var section = configuration.GetSection($"{BotOptions.SectionName}:Notifications");
    var opts = section.Get<NotificationOptions>() ?? new NotificationOptions();
    // Safe default: when DryRun is missing/unparseable, treat as dry-run (do not send).
    var dryRun = !bool.TryParse(configuration[$"{BotOptions.SectionName}:DryRun"], out var d) || d;

    services.AddHttpClient();

    services.AddSingleton(sp =>
    {
        var reportBuilder = sp.GetRequiredService<IReportBuilder>();
        var loggerFactory = sp.GetRequiredService<ILoggerFactory>();
        var httpFactory = sp.GetRequiredService<IHttpClientFactory>();

        INotificationService Build(string channel) => channel?.Trim().ToLowerInvariant() switch
        {
            "discord" => new DiscordNotificationService(
                httpFactory.CreateClient("discord"),
                loggerFactory.CreateLogger<DiscordNotificationService>(),
                opts.Discord, dryRun, reportBuilder),
            "email" => new EmailNotificationService(
                loggerFactory.CreateLogger<EmailNotificationService>(),
                opts.Email, dryRun, reportBuilder),
            _ => new NullNotificationService()
        };

        return new NotificationRouter(
            intraday: Build(opts.IntradayChannel),
            daily: Build(opts.DailyChannel),
            suppressEmpty: opts.SuppressEmpty);
    });
}
