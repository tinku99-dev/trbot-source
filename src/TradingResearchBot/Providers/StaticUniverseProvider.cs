using Microsoft.Extensions.Options;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Providers;

/// <summary>
/// Returns the fixed universe configured via Bot:StockUniverse and Bot:CryptoUniverse.
/// Deterministic and free (no extra API calls) — the default.
/// </summary>
public sealed class StaticUniverseProvider : IUniverseProvider
{
    private readonly BotOptions _options;

    public StaticUniverseProvider(IOptions<BotOptions> options) => _options = options.Value;

    public Task<IReadOnlyList<UniverseEntry>> GetUniverseAsync(CancellationToken ct = default)
    {
        var list = _options.StockSymbols().Select(s => new UniverseEntry(s, AssetClass.Stock))
            .Concat(_options.CryptoSymbols().Select(s => new UniverseEntry(s, AssetClass.Crypto)))
            .ToList();
        return Task.FromResult<IReadOnlyList<UniverseEntry>>(list);
    }
}
