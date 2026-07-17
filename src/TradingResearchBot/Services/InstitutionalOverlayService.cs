using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Services;

/// <summary>
/// Adds sector-relative strength, beta-neutral pair, and PEAD measurements to
/// stock candidates. Defaults to shadow-only and never submits an order.
/// </summary>
public sealed class InstitutionalOverlayService
{
    private const int MarketLookbackDays = 260;
    private readonly IMarketDataProvider _market;
    private readonly InstitutionalOverlayOptions _options;
    private readonly ILogger<InstitutionalOverlayService> _logger;

    public InstitutionalOverlayService(
        IMarketDataProvider market,
        IOptions<BotOptions> options,
        ILogger<InstitutionalOverlayService> logger)
    {
        _market = market;
        _options = options.Value.Institutional;
        _logger = logger;
    }

    public async Task ApplyAsync(
        IReadOnlyList<Candidate> candidates,
        IReadOnlyDictionary<string, PriceHistory> evaluatedHistories,
        CancellationToken ct = default)
    {
        if (!_options.Enabled) return;

        var cache = new Dictionary<string, PriceHistory>(evaluatedHistories, StringComparer.OrdinalIgnoreCase);
        foreach (var candidate in candidates.Where(c => c.AssetClass == AssetClass.Stock))
        {
            ct.ThrowIfCancellationRequested();
            if (!cache.TryGetValue(candidate.Symbol, out var asset)) continue;

            var result = new InstitutionalOverlayResult { ShadowOnly = _options.ShadowOnly };
            candidate.Institutional = result;

            await ApplyRelativeStrengthAsync(candidate, asset, result, cache, ct);
            await ApplyPairShadowAsync(candidate, asset, result, cache, ct);
            ApplyPead(candidate, asset, result);
        }
    }

    private async Task ApplyRelativeStrengthAsync(
        Candidate candidate,
        PriceHistory asset,
        InstitutionalOverlayResult result,
        Dictionary<string, PriceHistory> cache,
        CancellationToken ct)
    {
        if (!_options.SectorBenchmarks.TryGetValue(candidate.Symbol, out var benchmarkSymbol)) return;
        var benchmark = await GetHistoryAsync(benchmarkSymbol, cache, ct);
        if (benchmark is null) return;

        var aligned = AlignReturns(asset, benchmark, _options.RelativeStrengthLookbackDays);
        if (aligned.Asset.Count == 0) return;

        decimal assetReturn = CompoundReturnPct(aligned.Asset);
        decimal benchmarkReturn = CompoundReturnPct(aligned.Other);
        decimal excess = assetReturn - benchmarkReturn;
        bool qualified = excess >= _options.MinSectorExcessReturnPct;

        result.SectorBenchmark = benchmarkSymbol;
        result.AssetReturnPct = Round(assetReturn);
        result.BenchmarkReturnPct = Round(benchmarkReturn);
        result.SectorExcessReturnPct = Round(excess);
        result.RelativeStrengthQualified = qualified;
        candidate.Signals.Add(
            $"[shadow] Sector RS vs {benchmarkSymbol}: {excess:+0.00;-0.00;0.00}% excess " +
            $"({(qualified ? "pass" : "fail")} ≥ {_options.MinSectorExcessReturnPct:F2}%)");

        if (_options.ShadowOnly) return;

        candidate.Score = Math.Round(candidate.Score + (qualified ? 6 : -8), 2);
        bool breakout = candidate.Categories.Contains(ReportCategory.Breakout) ||
                        candidate.Patterns.Any(p => p.Contains("breakout", StringComparison.OrdinalIgnoreCase));
        if (_options.RequireSectorStrengthForBreakouts && breakout && !qualified)
            candidate.StrategyQualified = false;
    }

    private async Task ApplyPairShadowAsync(
        Candidate candidate,
        PriceHistory asset,
        InstitutionalOverlayResult result,
        Dictionary<string, PriceHistory> cache,
        CancellationToken ct)
    {
        if (!candidate.Patterns.Contains("Bollinger bounce")) return;
        if (!_options.PairPeers.TryGetValue(candidate.Symbol, out var peerSymbol)) return;
        var peer = await GetHistoryAsync(peerSymbol, cache, ct);
        if (peer is null) return;

        var aligned = AlignReturns(asset, peer, _options.PairLookbackDays);
        if (aligned.Asset.Count < 20) return;

        var stats = PairStatistics(aligned.Asset, aligned.Other);
        result.HedgePeer = peerSymbol;
        result.PairCorrelation = Round(stats.Correlation);
        result.HedgeBeta = Round(stats.Beta);

        if (stats.Correlation < _options.MinPairCorrelation || stats.Beta <= 0)
        {
            candidate.Signals.Add(
                $"[shadow] Pair hedge rejected: {peerSymbol} correlation {stats.Correlation:F2} " +
                $"< {_options.MinPairCorrelation:F2}");
            return;
        }

        decimal shortPct = Math.Clamp(stats.Beta * 100m, 25m, 150m);
        result.ShortNotionalPctOfLong = Round(shortPct);
        candidate.Patterns.Add("Beta-neutral Bollinger pair (shadow)");
        candidate.Signals.Add(
            $"[shadow] Long {candidate.Symbol} / short {peerSymbol} at {shortPct:F0}% of long " +
            $"(corr {stats.Correlation:F2}, beta {stats.Beta:F2})");
    }

    private void ApplyPead(Candidate candidate, PriceHistory asset, InstitutionalOverlayResult result)
    {
        var events = _options.EarningsEvents
            .Where(e => string.Equals(e.Symbol, candidate.Symbol, StringComparison.OrdinalIgnoreCase))
            .Where(e => e.ReportedAtUtc <= asset.Latest.Timestamp)
            .OrderBy(e => e.ReportedAtUtc)
            .ToList();
        if (events.Count < 5) return;

        var latest = events[^1];
        double ageDays = (asset.Latest.Timestamp - latest.ReportedAtUtc).TotalDays;
        if (ageDays < 0 || ageDays > _options.PeadMaxAgeDays) return;

        var prior = events.Take(events.Count - 1).Select(SurprisePct).ToList();
        decimal mean = prior.Average();
        decimal variance = prior.Sum(x => (x - mean) * (x - mean)) / (prior.Count - 1);
        decimal stddev = (decimal)Math.Sqrt((double)variance);
        if (stddev <= 0) return;

        decimal surprise = SurprisePct(latest);
        decimal z = (surprise - mean) / stddev;
        bool qualified = z >= _options.MinPeadSurpriseZ;

        result.EarningsReportedAtUtc = latest.ReportedAtUtc;
        result.EarningsSurprisePct = Round(surprise);
        result.EarningsSurpriseZ = Round(z);
        result.PeadQualified = qualified;
        candidate.Signals.Add(
            $"[shadow] PEAD: EPS surprise {surprise:+0.00;-0.00;0.00}%, z={z:F2}, " +
            $"{ageDays:F0}d old ({(qualified ? "pass" : "fail")})");
        if (qualified)
        {
            candidate.Patterns.Add("Post-earnings announcement drift (shadow)");
            if (!_options.ShadowOnly) candidate.Score = Math.Round(candidate.Score + 8, 2);
        }
    }

    private async Task<PriceHistory?> GetHistoryAsync(
        string symbol,
        Dictionary<string, PriceHistory> cache,
        CancellationToken ct)
    {
        if (cache.TryGetValue(symbol, out var existing)) return existing;
        try
        {
            var fetched = await _market.GetDailyHistoryAsync(symbol, AssetClass.Stock, MarketLookbackDays, ct);
            if (fetched is not null && fetched.Candles.Count > 1) cache[symbol] = fetched;
            return fetched;
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Institutional overlay data fetch failed for {Symbol}.", symbol);
            return null;
        }
    }

    internal static (IReadOnlyList<decimal> Asset, IReadOnlyList<decimal> Other) AlignReturns(
        PriceHistory asset, PriceHistory other, int lookbackDays)
    {
        var left = DailyReturns(asset).ToDictionary(x => x.Date, x => x.Return);
        var right = DailyReturns(other).ToDictionary(x => x.Date, x => x.Return);
        var dates = left.Keys.Intersect(right.Keys).OrderBy(x => x).TakeLast(Math.Max(1, lookbackDays)).ToList();
        return (dates.Select(d => left[d]).ToList(), dates.Select(d => right[d]).ToList());
    }

    internal static (decimal Correlation, decimal Beta) PairStatistics(
        IReadOnlyList<decimal> asset, IReadOnlyList<decimal> peer)
    {
        if (asset.Count != peer.Count || asset.Count < 2) return (0, 0);
        decimal meanAsset = asset.Average();
        decimal meanPeer = peer.Average();
        decimal covariance = 0, assetVariance = 0, peerVariance = 0;
        for (int i = 0; i < asset.Count; i++)
        {
            decimal a = asset[i] - meanAsset;
            decimal p = peer[i] - meanPeer;
            covariance += a * p;
            assetVariance += a * a;
            peerVariance += p * p;
        }
        if (assetVariance <= 0 || peerVariance <= 0) return (0, 0);
        decimal correlation = covariance / (decimal)Math.Sqrt((double)(assetVariance * peerVariance));
        decimal beta = covariance / peerVariance;
        return (correlation, beta);
    }

    private static IEnumerable<(DateOnly Date, decimal Return)> DailyReturns(PriceHistory history)
    {
        for (int i = 1; i < history.Candles.Count; i++)
        {
            decimal previous = history.Candles[i - 1].Close;
            if (previous <= 0) continue;
            yield return (DateOnly.FromDateTime(history.Candles[i].Timestamp.UtcDateTime),
                history.Candles[i].Close / previous - 1m);
        }
    }

    private static decimal CompoundReturnPct(IReadOnlyList<decimal> returns) =>
        (returns.Aggregate(1m, (value, next) => value * (1m + next)) - 1m) * 100m;

    private static decimal SurprisePct(EarningsEventOptions e)
    {
        decimal denominator = Math.Max(Math.Abs(e.EstimateEps), 0.01m);
        return (e.ActualEps - e.EstimateEps) / denominator * 100m;
    }

    private static decimal Round(decimal value) => Math.Round(value, 4);
}
