# Trading Research Bot

A **research / reporting** bot built on **.NET 8 isolated-process Azure Functions (v4)**.
It periodically screens a configurable universe of stocks and crypto, computes
technical indicators, scores and categorizes candidates, and delivers a ranked
research report to Discord and/or email.

> ⚠️ **Important — this is NOT a live-trading system.**
> It produces *educational research output only*. It places **no orders**, connects to
> **no brokerage**, and is **not financial advice**. Suggested buy ranges, stops, and
> targets are informational. Do your own due diligence.
>
> It uses a **compliant market-data provider abstraction**. The included provider is a
> deterministic **mock** for local development. Wire a **licensed** provider
> (Polygon, Tradier, Alpaca, Finnhub, Twelve Data, …) for real data. **Do not** use
> unofficial/scraped or brokerage-internal endpoints (e.g. Robinhood).

---

## What it does

- **Schedule:** Timer trigger runs every 10 minutes on weekdays. The cron covers a
  broad UTC window (`0 */10 12-20 * * 1-5`) and an **in-code Eastern-time guard**
  enforces the precise **08:30–15:00 ET** window (DST-safe).
- **Universe:** Configurable lists of stock and crypto symbols.
- **Indicators:** RSI(14), MACD(12/26/9), Bollinger Bands(20,2), ATR(14),
  SMA(20/50/200), EMA(12/26), support/resistance, volume relative strength,
  plus an advanced battery: **Stochastic(14,3), ADX(14), OBV + slope, rolling VWAP,
  MFI(14), Williams %R(14), CCI(20), historical volatility**, and a 200-day SMA
  crossover/bounce + institutional-volume screener.
- **Conviction score:** A composite **0–100** value blends the whole indicator
  battery — higher means more *independent* signals agree on the buy case, so you
  can prioritize stronger setups.
- **Options research:** For stock OptionsWatch/Scalp/Breakout/Fallout candidates, the
  bot pulls an options chain (via a compliant provider abstraction) and suggests a
  single liquid, directional contract (strike, expiration, ~delta, IV, OI) — **research
  only, no orders**.
- **Scoring & categories:** Ranks candidates and tags each with one or more of:
  `Scalp`, `ShortTerm`, `Swing`, `LongTerm`, `Breakout`, `Fallout`, `OptionsWatch`.
- **Report fields:** buy range, stop loss, target 1 / target 2, conviction, an
  optional options idea, recognized patterns, and the signals that fired.
- **Delivery:** Discord webhook and/or SMTP email. Reports are also persisted to disk.
- **On-demand run:** An HTTP trigger (`POST /api/run`, function-key protected) runs a
  research pass immediately for local testing/manual refresh.

### Categories at a glance

| Category | Rough intent |
|---|---|
| `Scalp` | High volatility + volume surge; quick **+10–20%** target ideas |
| `ShortTerm` | Short EMA uptrend with constructive RSI |
| `Swing` | Multi-day trend with room to resistance |
| `LongTerm` | Above 200-SMA with golden-cross structure |
| `Breakout` | Squeeze / 200-SMA crossover / near resistance |
| `Fallout` | Breakdown / short-bias watch |
| `OptionsWatch` | Elevated volatility → premium-strategy research |

---

## Project layout

```
trading-research-bot/
├─ src/TradingResearchBot/
│  ├─ Abstractions/            # IMarketDataProvider, IIndicatorEngine, IScoringEngine,
│  │                           # IReportBuilder, IReportStore, INotificationService
│  ├─ Functions/               # ResearchTimerFunction (timer trigger)
│  ├─ Indicators/              # IndicatorEngine (pure TA math)
│  ├─ Models/                  # Candle, PriceHistory, IndicatorSet, Candidate, Report, BotOptions
│  ├─ Notifications/           # Discord, Email, Composite, Null
│  ├─ Providers/               # MockMarketDataProvider, LicensedHttpMarketDataProvider (template)
│  ├─ Reports/                 # ReportBuilder
│  ├─ Scoring/                 # ScoringEngine
│  ├─ Services/                # ResearchService, MarketHoursGuard
│  ├─ Program.cs               # DI / host startup
│  ├─ host.json
│  ├─ appsettings.sample.json
│  └─ local.settings.sample.json
└─ tests/TradingResearchBot.Tests/   # xUnit tests
```

---

## Getting started (local)

### Prerequisites
- [.NET 8 SDK](https://dotnet.microsoft.com/download) (or newer)
- [Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local)
- An Azure Storage emulator ([Azurite](https://learn.microsoft.com/azure/storage/common/storage-use-azurite)) for the timer trigger

### Build & test
```powershell
dotnet build
dotnet test
```

### Configure
Copy the sample local settings and edit (the real file is git-ignored):
```powershell
Copy-Item src/TradingResearchBot/local.settings.sample.json src/TradingResearchBot/local.settings.json
```

Key settings (under the `Bot` section / `Bot__*` env vars):

| Setting | Default | Notes |
|---|---|---|
| `MarketProvider` | `Mock` | `Mock`, `Polygon`, or `LicensedHttp` |
| `MaxCandidates` | `20` | Top N in the report |
| `DryRun` | `true` | When true, notifications are **logged, not sent** |
| `StockUniverse` | sample list | Comma-separated symbols |
| `CryptoUniverse` | sample list | Comma-separated `XXX-USD` pairs |
| `Sma200__VolumeRatioMin` | `1.20` | Volume confirmation (≥120% of 30-day avg) |
| `Sma200__BounceZonePct` | `2.0` | % above 200-SMA counted as a "bounce" |
| `Sma200__TargetPct` | `10.0` | Screener target % |
| `Sma200__StopPct` | `3.0` | Screener stop % |
| `Options__Enabled` | `true` | Toggle options-chain enrichment |
| `Options__Provider` | `Mock` | `Mock`, `Tradier`, or `LicensedHttp` |
| `Providers__Polygon__ApiKey` | _(empty)_ | Polygon.io key (stocks + crypto) |
| `Providers__Tradier__ApiKey` | _(empty)_ | Tradier key (options + equities) |
| `Notifications__Provider` | `None` | `None`, `Discord`, `Email`, or `Both` |

### Run
```powershell
cd src/TradingResearchBot
func start
```
With `DryRun=true` (default) you can run safely without real webhooks/SMTP — the bot
logs what it *would* send.

### Run a pass on demand (HTTP)
With the host running, trigger an immediate research pass (bypasses the market-hours
guard). The route is protected by a function key:
```powershell
# Local dev (no key needed locally)
curl -X POST http://localhost:7071/api/run
```
The response is a JSON summary of ranked candidates (symbol, score, conviction,
categories, levels, and any options idea).

---

## Notifications

### Discord
1. Server Settings → Integrations → **Webhooks** → New Webhook → copy URL.
2. Set `Bot__Notifications__Provider = Discord` (or `Both`) and
   `Bot__Notifications__Discord__WebhookUrl = <url>`.
3. Set `Bot__DryRun = false` to actually send.

### Email (SMTP)
Set `Bot__Notifications__Provider = Email` (or `Both`) and fill the
`Bot__Notifications__Email__*` values. Use an app password / API credential, **never**
your primary password. For Gmail use an [App Password](https://support.google.com/accounts/answer/185833).
Recipients may be comma-separated.

> **Secrets:** Never commit real webhook URLs, SMTP passwords, or API keys. Use
> `local.settings.json` (git-ignored) locally and **App Settings / Azure Key Vault**
> in the cloud.

---

## Plugging in a licensed market-data provider

`MockMarketDataProvider` produces deterministic synthetic data for development.
For real data, implement `IMarketDataProvider` against a licensed API and select it:

| Provider | Stocks | Crypto | Notes |
|---|---|---|---|
| [Polygon.io](https://polygon.io) | ✅ | ✅ | **Implemented** — one key covers both (Aggregates API) |
| [Tradier](https://tradier.com) | ✅ | — | Brokerage + market data API |
| [Alpaca](https://alpaca.markets) | ✅ | ✅ | Market data v2 |
| [Finnhub](https://finnhub.io) | ✅ | ✅ | Candles endpoint |
| [Twelve Data](https://twelvedata.com) | ✅ | ✅ | Time series endpoint |

### Stocks & coins data (built-in: Polygon.io)

`PolygonMarketDataProvider` is fully implemented and pulls **both** asset classes from a
single licensed key via Polygon's daily Aggregates (bars) API:

- **Stocks** use the plain ticker, e.g. `AAPL` → `/v2/aggs/ticker/AAPL/range/1/day/...`
- **Crypto** config symbols like `BTC-USD` are auto-converted to Polygon's
  `X:BTCUSD` form → `/v2/aggs/ticker/X:BTCUSD/range/1/day/...`

Enable it:
```
Bot__MarketProvider = Polygon
Bot__Providers__Polygon__ApiKey = <your-polygon-key>
```
The `CryptoUniverse` already uses `XXX-USD` pairs, so the same list works unchanged.
`LicensedHttpMarketDataProvider` remains as a generic template if you prefer another vendor.

## Options data & the Robinhood question

**Can it connect to Robinhood?** No — and you should not rely on it. Robinhood has **no
official public market-data or options API**. The only way to "connect" is through
unofficial/reverse-engineered endpoints, which **violate Robinhood's Terms of Service**
(risking account suspension), can break without notice, and are explicitly out of scope
for this project. This bot deliberately uses a **compliant options-data abstraction**
(`IOptionsDataProvider`) instead.

Use a **licensed options-data API** — you get the same strike/expiry suggestions safely:

| Provider | Options data | Notes |
|---|---|---|
| [Tradier](https://documentation.tradier.com) | ✅ (excellent) | `/v1/markets/options/chains` + greeks/IV |
| [Polygon.io](https://polygon.io) | ✅ | Options snapshots & aggregates |
| [Alpaca](https://alpaca.markets) | ✅ | Options market data (v1beta1) |
| [CBOE / OPRA vendors](https://www.cboe.com) | ✅ | Institutional-grade feeds |

`LicensedHttpOptionsDataProvider` is a ready-to-fill template. Map the chain response
into `OptionsChain` and set `Bot__Options__Provider = LicensedHttp`. The included
`MockOptionsDataProvider` generates a deterministic synthetic chain for local dev.

**Built-in Tradier options:** `TradierOptionsDataProvider` is fully implemented — it
lists expirations, picks one inside the DTE window, and pulls that chain with greeks/IV.
Enable it:
```
Bot__Options__Provider = Tradier
Bot__Providers__Tradier__ApiKey = <your-tradier-token>
Bot__Providers__Tradier__BaseUrl = https://sandbox.tradier.com   # or https://api.tradier.com
```

> The `OptionsStrategist` picks **one** liquid directional contract per relevant
> candidate (bullish → call, fallout → put) near a target delta with sufficient open
> interest. It is **research output only** and places no orders.

---

## Deploy to Azure (outline)

```bash
az login
az group create --name TradingResearchRG --location eastus
az storage account create --name tradingresearchsa --resource-group TradingResearchRG --location eastus --sku Standard_LRS
az functionapp create --resource-group TradingResearchRG --consumption-plan-location eastus \
  --runtime dotnet-isolated --runtime-version 8 --functions-version 4 \
  --name MyTradingResearchApp --os-type Windows --storage-account tradingresearchsa

cd src/TradingResearchBot
func azure functionapp publish MyTradingResearchApp
```

Then set App Settings (`Bot__*`, `Notifications__*`) in the Function App configuration,
optionally referencing Key Vault. To shift cron interpretation to a specific zone, set
`WEBSITE_TIME_ZONE` (the in-code ET guard still applies regardless).

---

## Disclaimer

This software is provided for educational and research purposes only. It is **not**
financial, investment, or trading advice and makes **no** trade executions. Markets are
risky; you are solely responsible for any decisions you make. The authors accept no
liability for losses arising from use of this software.
