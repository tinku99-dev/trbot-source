using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using TradingResearchBot.Abstractions;
using TradingResearchBot.Models;

namespace TradingResearchBot.Services;

public sealed class GeminiOptionsAdvisor : IAiOptionsAdvisor
{
    private static readonly JsonSerializerOptions JsonOpts = new(JsonSerializerDefaults.Web);

    private readonly HttpClient _http;
    private readonly AiOptionsResearchOptions _options;
    private readonly ILogger<GeminiOptionsAdvisor> _logger;

    public GeminiOptionsAdvisor(
        HttpClient http,
        IOptions<BotOptions> botOptions,
        ILogger<GeminiOptionsAdvisor> logger)
    {
        _http = http;
        _options = botOptions.Value.AiOptions;
        _logger = logger;

        if (_http.BaseAddress is null && !string.IsNullOrWhiteSpace(_options.BaseUrl))
            _http.BaseAddress = new Uri(_options.BaseUrl);
    }

    public async Task EnrichAsync(Candidate candidate, CancellationToken ct = default)
    {
        if (candidate.OptionIdea is not { } option) return;
        if (candidate.Score < _options.MinScoreToAskAi) return;
        if (string.IsNullOrWhiteSpace(_options.ApiKey))
        {
            _logger.LogWarning("Gemini API key not configured (Bot:AiOptions:ApiKey).");
            return;
        }

        try
        {
            var path = $"/v1beta/models/{Uri.EscapeDataString(_options.Model)}:generateContent";
            using var req = new HttpRequestMessage(HttpMethod.Post, path);
            req.Headers.TryAddWithoutValidation("x-goog-api-key", _options.ApiKey);
            req.Content = JsonContent.Create(BuildRequest(candidate, option), options: JsonOpts);

            using var resp = await _http.SendAsync(req, ct);
            if (!resp.IsSuccessStatusCode)
            {
                _logger.LogWarning("Gemini options advisor returned {Status} for {Symbol}.", (int)resp.StatusCode, candidate.Symbol);
                return;
            }

            var json = await resp.Content.ReadAsStringAsync(ct);
            var text = ExtractText(json);
            if (string.IsNullOrWhiteSpace(text)) return;

            option.AiGrade = JsonSerializer.Deserialize<AiOptionGrade>(text, JsonOpts);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _logger.LogWarning(ex, "Gemini options advisor failed for {Symbol}.", candidate.Symbol);
        }
    }

    private static object BuildRequest(Candidate c, OptionSuggestion option) => new
    {
        contents = new[]
        {
            new
            {
                role = "user",
                parts = new[]
                {
                    new { text = BuildPrompt(c, option) }
                }
            }
        },
        generationConfig = new
        {
            temperature = 0.2,
            responseMimeType = "application/json"
        }
    };

    private static string BuildPrompt(Candidate c, OptionSuggestion option)
    {
        var ind = c.Indicators;
        return $$"""
        You are an options research assistant. Grade this already-selected real options contract.

        Rules:
        - Do not invent option prices, quotes, news, earnings dates, analyst ratings, or live data.
        - Use only the numeric data provided below.
        - This is educational research, not financial advice.
        - "Lotto option" means high-risk small-premium directional speculation that can expire worthless.
        - Favor liquidity, tight spread, trend confirmation, volume participation, and clear invalidation.
        - Penalize weak trend, low volume, overextended RSI, missing IV/delta, and poor reward/risk.
        - Return valid JSON only.

        Candidate:
        symbol={{c.Symbol}}
        assetClass={{c.AssetClass}}
        score={{c.Score:F1}}
        conviction={{c.Conviction:F0}}
        categories={{string.Join(", ", c.Categories)}}
        patterns={{string.Join(", ", c.Patterns.Distinct())}}
        signals={{string.Join("; ", c.Signals.Take(8))}}

        Market:
        price={{ind.Price:F4}}
        rsi14={{Fmt(ind.Rsi14)}}
        adx14={{Fmt(ind.Adx14)}}
        volumeRelativeStrength={{Fmt(ind.VolumeRelativeStrength)}}
        sma50={{Fmt(ind.Sma50)}}
        sma200={{Fmt(ind.Sma200)}}
        vwap={{Fmt(ind.Vwap)}}
        obvPressurePct={{Fmt(ind.ObvPressurePct)}}

        Option:
        type={{option.Type}}
        strike={{option.Strike:F2}}
        expiration={{option.Expiration:yyyy-MM-dd}}
        dte={{option.DaysToExpiration}}
        entryMid={{Fmt(option.EntryMid)}}
        delta={{Fmt(option.Delta)}}
        impliedVolatility={{Fmt(option.ImpliedVolatility)}}
        openInterest={{option.OpenInterest}}
        rationale={{option.Rationale}}

        JSON fields:
        {
          "lottoScore": integer 0-100,
          "strategy": "call breakout" | "put breakdown" | "watch only" | "avoid",
          "thesis": short reason this contract is or is not a good lotto candidate,
          "entryPlan": concise entry condition using only provided levels/signals,
          "exitPlan": concise profit/stop/time-decay plan,
          "riskWarning": concise risk note,
          "action": "consider" | "watch" | "avoid"
        }
        """;
    }

    private static string? ExtractText(string json)
    {
        using var doc = JsonDocument.Parse(json);
        if (!doc.RootElement.TryGetProperty("candidates", out var candidates) ||
            candidates.ValueKind != JsonValueKind.Array ||
            candidates.GetArrayLength() == 0)
            return null;

        var first = candidates[0];
        if (!first.TryGetProperty("content", out var content) ||
            !content.TryGetProperty("parts", out var parts) ||
            parts.ValueKind != JsonValueKind.Array ||
            parts.GetArrayLength() == 0)
            return null;

        return parts[0].TryGetProperty("text", out var text) ? text.GetString() : null;
    }

    private static string Fmt(decimal? value) => value is { } v ? v.ToString("F4") : "n/a";
}

public sealed class NullAiOptionsAdvisor : IAiOptionsAdvisor
{
    public Task EnrichAsync(Candidate candidate, CancellationToken ct = default) => Task.CompletedTask;
}
