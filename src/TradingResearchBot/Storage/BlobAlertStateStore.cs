using System.Text.Json;
using Azure.Identity;
using Azure.Storage.Blobs;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Storage;

/// <summary>
/// Stores per-day intraday-alert state as a small JSON blob in the Function App's
/// storage account (container "botstate", blob "alerts/{date}.json").
///
/// Auth mirrors the Functions host:
///   • Azure  — AzureWebJobsStorage__accountName + managed identity (DefaultAzureCredential).
///   • Local  — AzureWebJobsStorage connection string (Azurite "UseDevelopmentStorage=true").
///
/// All operations are best-effort: any storage failure degrades to "no dedup"
/// (returns empty state / skips save) rather than breaking a research run.
/// </summary>
public sealed class BlobAlertStateStore : IAlertStateStore
{
    private const string ContainerName = "botstate";
    private readonly ILogger<BlobAlertStateStore> _logger;
    private readonly BlobContainerClient? _container;

    public BlobAlertStateStore(IConfiguration config, ILogger<BlobAlertStateStore> logger)
    {
        _logger = logger;
        try
        {
            _container = CreateContainerClient(config);
            _container?.CreateIfNotExists();
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Alert-state storage unavailable; intraday dedup disabled.");
            _container = null;
        }
    }

    public async Task<DailyAlertState> GetAsync(string localDate, CancellationToken ct = default)
    {
        var empty = new DailyAlertState { Date = localDate };
        if (_container is null) return empty;

        try
        {
            var blob = _container.GetBlobClient(BlobName(localDate));
            if (!await blob.ExistsAsync(ct)) return empty;

            var resp = await blob.DownloadContentAsync(ct);
            var state = JsonSerializer.Deserialize<DailyAlertState>(resp.Value.Content.ToString());
            if (state is null || !string.Equals(state.Date, localDate, StringComparison.Ordinal))
                return empty; // day rolled over → fresh state
            return state;
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Failed to read alert state for {Date}; treating as empty.", localDate);
            return empty;
        }
    }

    public async Task SaveAsync(DailyAlertState state, CancellationToken ct = default)
    {
        if (_container is null) return;
        try
        {
            var blob = _container.GetBlobClient(BlobName(state.Date));
            var json = JsonSerializer.SerializeToUtf8Bytes(state);
            using var ms = new MemoryStream(json);
            await blob.UploadAsync(ms, overwrite: true, ct);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Failed to save alert state for {Date}.", state.Date);
        }
    }

    private static string BlobName(string localDate) => $"alerts/{localDate}.json";

    private static BlobContainerClient? CreateContainerClient(IConfiguration config)
    {
        var accountName = config["AzureWebJobsStorage__accountName"];
        if (!string.IsNullOrWhiteSpace(accountName))
        {
            var uri = new Uri($"https://{accountName}.blob.core.windows.net");
            var svc = new BlobServiceClient(uri, new DefaultAzureCredential());
            return svc.GetBlobContainerClient(ContainerName);
        }

        var conn = config["AzureWebJobsStorage"];
        if (!string.IsNullOrWhiteSpace(conn))
        {
            var svc = new BlobServiceClient(conn);
            return svc.GetBlobContainerClient(ContainerName);
        }

        return null;
    }
}
