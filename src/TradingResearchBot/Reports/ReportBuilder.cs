using System.Text;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Reports;

/// <summary>Builds and renders the ranked research report.</summary>
public sealed class ReportBuilder : IReportBuilder
{
    public ResearchReport Build(IEnumerable<Candidate> candidates, int maxCandidates)
    {
        var all = candidates.ToList();

        var ranked = all
            .Where(c => c.StrategyQualified)
            .OrderByDescending(c => c.Score)
            .Take(Math.Max(1, maxCandidates))
            .ToList();

        var rejected = all
            .Where(c => !c.StrategyQualified)
            .OrderByDescending(c => c.Score)
            .ToList();

        var mode = all.FirstOrDefault()?.StrategyMode ?? "Blended";

        return new ResearchReport
        {
            Candidates = ranked,
            Rejected = rejected,
            StrategyMode = mode
        };
    }

    public string RenderText(ResearchReport report)
    {
        var sb = new StringBuilder();
        sb.AppendLine("==============================================");
        sb.AppendLine(" TRADING RESEARCH BOT — CANDIDATE REPORT");
        sb.AppendLine($" Generated (UTC): {report.GeneratedAtUtc:yyyy-MM-dd HH:mm}");
        sb.AppendLine($" Strategy: {report.StrategyMode}");
        sb.AppendLine($" Candidates: {report.Candidates.Count}");
        sb.AppendLine("==============================================");
        sb.AppendLine();

        int rank = 1;
        foreach (var c in report.Candidates)
        {
            var ind = c.Indicators;
            string cats = string.Join(", ", c.Categories);
            string patterns = c.Patterns.Count > 0 ? string.Join(", ", c.Patterns.Distinct()) : "—";

            sb.AppendLine($"#{rank,-2} {c.Symbol,-10} [{c.AssetClass}]  Tier: {c.TierLabel}   Score: {c.Score:F1}   Conviction: {c.Conviction:F0}/100");
            sb.AppendLine($"     Categories : {cats}");
            sb.AppendLine($"     Patterns   : {patterns}");
            sb.AppendLine($"     Price      : {ind.Price:F2}   RSI: {Fmt(ind.Rsi14)}   ATR: {Fmt(ind.Atr14)}   ADX: {Fmt(ind.Adx14)}");
            sb.AppendLine($"     SMA50/200  : {Fmt(ind.Sma50)} / {Fmt(ind.Sma200)}   VWAP: {Fmt(ind.Vwap)}   VolRS: {Fmt(ind.VolumeRelativeStrength)}x");
            sb.AppendLine($"     Stoch/MFI  : {Fmt(ind.StochasticK)}/{Fmt(ind.StochasticD)}  MFI: {Fmt(ind.Mfi14)}  OBVslope: {(ind.ObvSlope is { } o ? (o > 0 ? "+" : "-") : "n/a")}");
            sb.AppendLine($"     Buy range  : {Fmt(c.BuyRangeLow)} - {Fmt(c.BuyRangeHigh)}");
            sb.AppendLine($"     Stop loss  : {Fmt(c.StopLoss)}");
            sb.AppendLine($"     Targets    : T1 {Fmt(c.Target1)}  |  T2 {Fmt(c.Target2)}");
            if (c.OptionIdea is { } opt)
            {
                sb.AppendLine($"     Option idea: {opt.Describe()}");
                sb.AppendLine($"                  {opt.Rationale}");
            }
            if (c.Signals.Count > 0)
                sb.AppendLine($"     Signals    : {string.Join("; ", c.Signals)}");
            sb.AppendLine();
            rank++;
        }

        sb.AppendLine("----------------------------------------------");
        sb.AppendLine(report.Disclaimer);
        return sb.ToString();
    }

    private static string Fmt(decimal? v) => v.HasValue ? v.Value.ToString("F2") : "n/a";
}
