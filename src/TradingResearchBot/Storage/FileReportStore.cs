using System.Text.Json;
using Microsoft.Extensions.Logging;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Storage;

/// <summary>
/// Simple file-based report store that writes a timestamped JSON file under the
/// system temp directory. Swap for Azure Blob/Table storage in production by
/// providing another <see cref="IReportStore"/> implementation.
/// </summary>
public sealed class FileReportStore : IReportStore
{
    private readonly ILogger<FileReportStore> _logger;
    private readonly string _directory;

    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        WriteIndented = true
    };

    public FileReportStore(ILogger<FileReportStore> logger)
    {
        _logger = logger;
        _directory = Path.Combine(Path.GetTempPath(), "trading-research-bot", "reports");
        Directory.CreateDirectory(_directory);
    }

    public async Task SaveAsync(ResearchReport report, CancellationToken ct = default)
    {
        var fileName = $"report-{report.GeneratedAtUtc:yyyyMMdd-HHmmss}.json";
        var path = Path.Combine(_directory, fileName);
        await using var stream = File.Create(path);
        await JsonSerializer.SerializeAsync(stream, report, JsonOpts, ct);
        _logger.LogInformation("Saved research report to {Path}", path);
    }
}
