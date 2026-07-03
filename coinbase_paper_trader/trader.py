from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# CONFIGURATION  — all secrets come from environment variables, never hardcoded
# ---------------------------------------------------------------------------
API_KEY = os.environ.get("CB_API_KEY", "")
API_SECRET = os.environ.get("CB_API_SECRET", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# Dynamic watchlist — fetched from Coinbase each cycle, sorted by 24h volume
WATCHLIST_SIZE        = int(os.environ.get("WATCHLIST_SIZE", "50"))  # override via env
QUOTE_CURRENCY        = "USD"   # only trade X-USD pairs

# ---------------------------------------------------------------------------
# CAPITAL MANAGEMENT
# CAPITAL_PER_TRADE_USD : fixed dollar size deployed per coin ($1000 default)
# MAX_OPEN_POSITIONS    : max simultaneous positions (up to 10 coins)
# TOTAL_CAPITAL_USD     : full budget = per-trade size x max positions
# MAX_DAILY_LOSS_PER_COIN: if a coin's realized loss in a UTC day exceeds this,
#                          it is blocked from new trades until the next UTC day.
# ---------------------------------------------------------------------------
CAPITAL_PER_TRADE_USD = float(os.environ.get("CAPITAL_PER_TRADE_USD", "1000"))  # $1000 per coin
MAX_OPEN_POSITIONS    = int(os.environ.get("MAX_OPEN_POSITIONS",      "10"))    # up to 10 coins
TOTAL_CAPITAL_USD     = float(os.environ.get(
    "TOTAL_CAPITAL_USD", str(CAPITAL_PER_TRADE_USD * MAX_OPEN_POSITIONS)))      # $10,000 budget
MAX_DAILY_LOSS_PER_COIN = float(os.environ.get("MAX_DAILY_LOSS_PER_COIN", "100"))  # per-coin/day

# Minimum probability score (0-100) required before entering a position
MIN_SIGNAL_SCORE    = float(os.environ.get("MIN_SIGNAL_SCORE",    "80"))

# Generic pattern detector (all USD pairs): tries to catch pre-breakout setups
MIN_BASE_SCORE_FOR_PATTERN = float(os.environ.get("MIN_BASE_SCORE_FOR_PATTERN", "45"))
PRE_BREAKOUT_MIN_SCORE     = float(os.environ.get("PRE_BREAKOUT_MIN_SCORE", "70"))
PATTERN_LOOKBACK_MINUTES   = int(os.environ.get("PATTERN_LOOKBACK_MINUTES", "60"))
SHORT_WINDOW_MINUTES       = int(os.environ.get("SHORT_WINDOW_MINUTES", "5"))
MEDIUM_WINDOW_MINUTES      = int(os.environ.get("MEDIUM_WINDOW_MINUTES", "15"))
MIN_SHORT_MOVE_PCT         = float(os.environ.get("MIN_SHORT_MOVE_PCT", "1.0"))
MIN_MEDIUM_MOVE_PCT        = float(os.environ.get("MIN_MEDIUM_MOVE_PCT", "1.8"))
MIN_VOLUME_ACCEL_RATIO     = float(os.environ.get("MIN_VOLUME_ACCEL_RATIO", "1.8"))
MAX_RETRACE_FROM_HIGH_PCT  = float(os.environ.get("MAX_RETRACE_FROM_HIGH_PCT", "1.2"))

# Candle-based breakout detector (works immediately from public OHLCV history).
# Thresholds derived from real XLM-USD breakout analysis across timeframes.
CANDLE_GRANULARITY      = int(os.environ.get("CANDLE_GRANULARITY", "300"))   # 5m candles
CANDLE_BASE_LOOKBACK    = int(os.environ.get("CANDLE_BASE_LOOKBACK", "12"))   # base candles
CANDLE_VOL_RATIO_STRONG = float(os.environ.get("CANDLE_VOL_RATIO_STRONG", "3.0"))
CANDLE_VOL_RATIO_MIN    = float(os.environ.get("CANDLE_VOL_RATIO_MIN", "1.5"))
CANDLE_COMPRESSION_TIGHT= float(os.environ.get("CANDLE_COMPRESSION_TIGHT", "2.0"))
CANDLE_MAX_OVEREXTENSION= float(os.environ.get("CANDLE_MAX_OVEREXTENSION", "2.0"))
CANDLE_SCAN_LIMIT       = int(os.environ.get("CANDLE_SCAN_LIMIT", "30"))      # top-N by volume
RECENT_TRIGGER_CANDLES  = int(os.environ.get("RECENT_TRIGGER_CANDLES", "3"))  # don't miss moves between timer runs
COINBASE_PUBLIC_BASE    = "https://api.exchange.coinbase.com"

# Liquidity / market-value guard. Coinbase public candles do not include market
# cap, so traded USD value is the practical proxy: price * volume. This avoids
# buying thin coins where a breakout candle is easy to spoof or hard to exit.
MIN_24H_DOLLAR_VOLUME   = float(os.environ.get("MIN_24H_DOLLAR_VOLUME", "5000000"))
MIN_BREAKOUT_DOLLAR_VOLUME = float(os.environ.get("MIN_BREAKOUT_DOLLAR_VOLUME", "25000"))

# OBV (On-Balance Volume) confirmation: price should be breaking out with net
# accumulation, not just a thin candle. `obv_pressure_pct` normalizes OBV change
# by total volume in the lookback window so it works across cheap and expensive coins.
OBV_CONFIRMATION        = os.environ.get("OBV_CONFIRMATION", "true").lower() == "true"
OBV_LOOKBACK_CANDLES    = int(os.environ.get("OBV_LOOKBACK_CANDLES", "12"))
MIN_OBV_PRESSURE_PCT    = float(os.environ.get("MIN_OBV_PRESSURE_PCT", "8.0"))
MIN_OBV_UP_VOLUME_RATIO = float(os.environ.get("MIN_OBV_UP_VOLUME_RATIO", "0.52"))

# Market-regime guard: most alt breakouts fail when BTC is rolling over. New
# entries are allowed only when BTC is not in a short-term pullback, and each
# alt must show relative strength vs BTC on the 1h window.
MARKET_REGIME_FILTER    = os.environ.get("MARKET_REGIME_FILTER", "true").lower() == "true"
BTC_15M_MAX_DROP_PCT    = float(os.environ.get("BTC_15M_MAX_DROP_PCT", "-0.35"))
BTC_1H_MAX_DROP_PCT     = float(os.environ.get("BTC_1H_MAX_DROP_PCT", "-0.80"))
MIN_REL_STRENGTH_VS_BTC = float(os.environ.get("MIN_REL_STRENGTH_VS_BTC", "0.50"))

# Multi-timeframe confirmation: the 5m breakout is the TRIGGER, but a real move
# should also hold up on higher timeframes. We confirm on 15m + 1h and use 4h
# (aggregated from 1h) as a veto so we don't buy into a higher-timeframe downtrend.
# Coinbase has no native 4h candle, so 4h is built from six 1h candles.
MULTI_TIMEFRAME_CONFIRM = os.environ.get("MULTI_TIMEFRAME_CONFIRM", "true").lower() == "true"
MTF_CONFIRM_MIN_SCORE   = float(os.environ.get("MTF_CONFIRM_MIN_SCORE", "35"))   # min pattern score on a confirm timeframe
MTF_GRAN_15M            = 900
MTF_GRAN_1H             = 3600
MTF_4H_AGG_FACTOR       = 4      # build 4h candles from four 1h candles

# ---------------------------------------------------------------------------
# TRADING MODE  — the flag that switches between paper (simulated) and live (real)
#
# TRADING_MODE:
#   "paper" (default) — simulated only, zero real money, safe to run anytime.
#   "live"            — executes REAL Coinbase market orders.
#
# LIVE_TRADING_ENABLED: a second safety confirmation. Real orders fire ONLY when
#   TRADING_MODE="live" AND LIVE_TRADING_ENABLED="true". This prevents an
#   accidental "live" setting from spending real money on its own.
#
# Required keys for LIVE trading (set as env vars / Function App settings):
#   CB_API_KEY    — Coinbase Advanced Trade API key  (needs 'Trade' permission)
#   CB_API_SECRET — matching API secret
# Paper mode still reads market data and works best with the same keys, but will
# never place an order regardless of key permissions.
# ---------------------------------------------------------------------------
TRADING_MODE = os.environ.get("TRADING_MODE", "paper").lower()
LIVE_TRADING_ENABLED = os.environ.get("LIVE_TRADING_ENABLED", "false").lower() == "true"
# Real orders are placed only when BOTH the mode and the confirmation flag agree.
LIVE_ORDERS_ACTIVE = (TRADING_MODE == "live") and LIVE_TRADING_ENABLED

# Minimum breakout pattern score (0-100) required to BUY. Default 80 so the bot
# only enters strong setups instead of buying everything that ticks up.
MIN_PATTERN_SCORE_TO_BUY = float(os.environ.get("MIN_PATTERN_SCORE_TO_BUY", "80"))

TAKE_PROFIT_PERCENT   = float(os.environ.get("TAKE_PROFIT_PERCENT",  "5.0"))  # 5% ceiling
TRAILING_PERCENT      = float(os.environ.get("TRAILING_PERCENT",     "2.0"))  # 2% floor
LOOP_INTERVAL_SECONDS = 300     # 5-minute loop

# Bearish-reversal exit: sell when the trend flips bearish on candles.
BEARISH_DROP_PCT      = float(os.environ.get("BEARISH_DROP_PCT", "1.5"))   # red candle size
BEARISH_VOL_RATIO     = float(os.environ.get("BEARISH_VOL_RATIO", "1.5"))  # vol confirms selling

# Periodic Discord performance summary (total P/L, gain/loss, coins bought).
SUMMARY_INTERVAL_HOURS = float(os.environ.get("SUMMARY_INTERVAL_HOURS", "6"))

# Where state files are written. On Azure Functions the app folder is read-only,
# so point DATA_DIR at a writable, persisted path (e.g. /home/data) via env var.
DATA_DIR = os.environ.get("DATA_DIR", "").strip()

PORTFOLIO_FILE = "active_paper_positions.json"
HISTORY_FILE   = "trading_history.json"
MARKET_STATE_FILE = "market_state_cache.json"
DAILY_PNL_FILE = "daily_pnl_ledger.json"
SUMMARY_STATE_FILE = "summary_state.json"


def _data_path(filename: str) -> str:
    """Resolves a state filename against DATA_DIR (created if needed)."""
    if not DATA_DIR:
        return filename
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except OSError:
        return filename
    return os.path.join(DATA_DIR, filename)

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _validate_config():
    """Guards live trading; paper mode can run with or without API keys.

    LIVE trading strictly requires CB_API_KEY/CB_API_SECRET (Trade permission).
    PAPER trading uses authenticated data when keys exist, otherwise falls back
    to Coinbase's public market-data endpoints (no keys needed).
    """
    if LIVE_ORDERS_ACTIVE:
        missing = [name for name, val in [
            ("CB_API_KEY",    API_KEY),
            ("CB_API_SECRET", API_SECRET),
        ] if not val]
        if missing:
            raise EnvironmentError(
                f"LIVE trading requires: {', '.join(missing)}\n"
                "  export CB_API_KEY='...'      # needs 'Trade' permission\n"
                "  export CB_API_SECRET='...'\n"
                "  export TRADING_MODE='live'\n"
                "  export LIVE_TRADING_ENABLED='true'"
            )
        print("[Config] \u26a0\ufe0f  LIVE TRADING ACTIVE \u2014 real Coinbase orders will be placed "
              f"(${CAPITAL_PER_TRADE_USD:,.0f}/coin, max {MAX_OPEN_POSITIONS} coins).")
        return

    if TRADING_MODE == "live" and not LIVE_TRADING_ENABLED:
        print("[Config] TRADING_MODE=live but LIVE_TRADING_ENABLED is not 'true' "
              "\u2014 staying in SIMULATION. Set LIVE_TRADING_ENABLED=true to place real orders.")

    if API_KEY and API_SECRET:
        print("[Config] Paper mode \u2014 simulated orders, using authenticated Coinbase data.")
    else:
        print("[Config] Paper mode \u2014 simulated orders, using PUBLIC Coinbase market data "
              "(no API keys set).")


def send_discord_alert(message: str):
    """Posts a trade alert to Discord via webhook (best-effort, never raises)."""
    if not DISCORD_WEBHOOK_URL:
        return
    payload = json.dumps({"content": message}).encode("utf-8")
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 — URL from env only
            if resp.status not in (200, 204):
                print(f"[Discord] Unexpected status {resp.status}")
    except Exception as exc:
        print(f"[Discord] Alert failed: {exc}")


def load_json_file(filepath: str):
    """Safely loads a JSON file; returns [] for history files, {} for others."""
    filepath = _data_path(filepath)
    if not os.path.exists(filepath):
        return [] if "history" in filepath else {}
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return [] if "history" in filepath else {}


def save_json_file(filepath: str, data):
    """Atomically writes JSON to disk to prevent corruption on crash."""
    filepath = _data_path(filepath)
    tmp = filepath + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=4)
        os.replace(tmp, filepath)   # atomic on POSIX and Windows
    except OSError as exc:
        print(f"[Ledger] Failed to save {filepath}: {exc}")
        if os.path.exists(tmp):
            os.remove(tmp)


def _utcnow_iso() -> str:
    """Returns current UTC time as ISO-8601 string (timezone-aware)."""
    return datetime.now(timezone.utc).isoformat()


def _utcnow_epoch() -> int:
    """Returns current UTC timestamp in epoch seconds."""
    return int(datetime.now(timezone.utc).timestamp())


def _today_str() -> str:
    """Returns the current UTC date as YYYY-MM-DD (used for daily PnL buckets)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _iso_to_epoch(iso_str: str) -> int:
    """Parses an ISO-8601 timestamp to epoch seconds; returns 0 on failure."""
    if not iso_str:
        return 0
    try:
        return int(datetime.fromisoformat(iso_str).timestamp())
    except (ValueError, TypeError):
        return 0


def load_daily_ledger() -> dict:
    """
    Loads the per-coin daily realized-PnL ledger, resetting it on UTC day rollover.
    Shape: {"date": "YYYY-MM-DD", "realized": {product_id: pnl}, "blocked": [ids]}
    """
    ledger = load_json_file(DAILY_PNL_FILE)
    today = _today_str()
    if not isinstance(ledger, dict) or ledger.get("date") != today:
        ledger = {"date": today, "realized": {}, "blocked": []}
    ledger.setdefault("realized", {})
    ledger.setdefault("blocked", [])
    return ledger


def record_daily_pnl(ledger: dict, product_id: str, pnl_usd: float):
    """Adds realized PnL for a coin and blocks it for the day if it breaches the loss cap."""
    realized = ledger.setdefault("realized", {})
    realized[product_id] = round(realized.get(product_id, 0.0) + pnl_usd, 4)

    # Block the coin for the rest of the UTC day once daily loss exceeds the cap.
    if realized[product_id] <= -abs(MAX_DAILY_LOSS_PER_COIN):
        blocked = ledger.setdefault("blocked", [])
        if product_id not in blocked:
            blocked.append(product_id)
            print(f"  [Daily Stop] {product_id} hit ${realized[product_id]:+.2f} today "
                  f"(cap -${MAX_DAILY_LOSS_PER_COIN:.0f}). Blocked until next UTC day.")


def is_coin_blocked_today(ledger: dict, product_id: str) -> bool:
    """True if the coin has breached its daily loss cap and is paused for the day."""
    return product_id in ledger.get("blocked", [])

# ---------------------------------------------------------------------------
# COINBASE CLIENT
# ---------------------------------------------------------------------------

def get_crypto_client():
    """
    Initialises the Coinbase Advanced REST client when API keys are present.
    Returns None when no keys are configured (paper mode then uses public data).
    """
    if not (API_KEY and API_SECRET):
        return None
    from coinbase.rest import RESTClient  # deferred so missing package gives clear error
    return RESTClient(api_key=API_KEY, api_secret=API_SECRET)


# ---------------------------------------------------------------------------
# PROBABILITY SCORER
# ---------------------------------------------------------------------------

def compute_signal_score(price_change_24h: float, volume_change_24h: float) -> float:
    """
    Returns a 0-100 probability score for a trade entry.
    Only coins scoring >= MIN_SIGNAL_SCORE (default 80) are traded.

    Three factors:
      Factor 1 — Price Momentum   (max 35 pts)
        Ideal: steady uptrend 3-15%.  Parabolic (>25%) or falling = fewer/no pts.
      Factor 2 — Volume Surge     (max 35 pts)
        Rising volume confirms conviction.  2x+ volume surge = full 35 pts.
      Factor 3 — Risk / Volatility filter (max 30 pts)
        Penalises extreme moves (crash or parabola) — high volatility = risky entry.
    """
    score = 0.0

    # Factor 1: Price Momentum
    if   3   <= price_change_24h <= 15:  score += 35
    elif 1   <= price_change_24h <   3:  score += 20
    elif 0   <= price_change_24h <   1:  score += 10
    elif 15  <  price_change_24h <= 25:  score += 15   # strong but extended
    # negative or >25% = 0 pts

    # Factor 2: Volume Surge
    if   volume_change_24h >= 100: score += 35
    elif volume_change_24h >=  50: score += 28
    elif volume_change_24h >=  25: score += 20
    elif volume_change_24h >=  10: score += 12
    elif volume_change_24h >=   0: score +=  5
    # declining volume = 0 pts

    # Factor 3: Volatility Risk
    abs_chg = abs(price_change_24h)
    if   abs_chg <=  5: score += 30
    elif abs_chg <= 10: score += 20
    elif abs_chg <= 15: score += 10
    elif abs_chg <= 20: score +=  5
    # >20% swing = 0 pts

    return round(score, 1)


def _compute_pre_breakout_features(product_data: dict, market_state: dict) -> dict:
    """
    Builds short-horizon features from rolling snapshots to detect early breakout patterns.
    Works for any product in the watchlist (not symbol-specific).
    """
    product_id = product_data["product_id"]
    now_ts = _utcnow_epoch()
    history = market_state.get(product_id, [])
    if not history:
        return {}

    short_cutoff = now_ts - (SHORT_WINDOW_MINUTES * 60)
    med_cutoff = now_ts - (MEDIUM_WINDOW_MINUTES * 60)

    short_slice = [h for h in history if h.get("ts", 0) >= short_cutoff]
    med_slice = [h for h in history if h.get("ts", 0) >= med_cutoff]

    if len(short_slice) < 2 or len(med_slice) < 2:
        return {}

    short_first = short_slice[0]
    short_last = short_slice[-1]
    med_first = med_slice[0]

    short_price_first = float(short_first.get("price", 0) or 0)
    short_price_last = float(short_last.get("price", 0) or 0)
    med_price_first = float(med_first.get("price", 0) or 0)
    if short_price_first <= 0 or short_price_last <= 0 or med_price_first <= 0:
        return {}

    short_move_pct = ((short_price_last - short_price_first) / short_price_first) * 100
    med_move_pct = ((short_price_last - med_price_first) / med_price_first) * 100

    # Approximate interval volume by 24h volume delta between snapshots.
    short_vol_start = float(short_first.get("volume_24h", 0) or 0)
    short_vol_end = float(short_last.get("volume_24h", 0) or 0)
    short_vol_delta = max(0.0, short_vol_end - short_vol_start)

    deltas = []
    for i in range(1, len(med_slice)):
        prev_v = float(med_slice[i - 1].get("volume_24h", 0) or 0)
        cur_v = float(med_slice[i].get("volume_24h", 0) or 0)
        deltas.append(max(0.0, cur_v - prev_v))

    baseline_vol_delta = 0.0
    if deltas:
        sorted_deltas = sorted(deltas)
        baseline_vol_delta = sorted_deltas[len(sorted_deltas) // 2]  # median

    if baseline_vol_delta > 0:
        volume_accel_ratio = short_vol_delta / baseline_vol_delta
    else:
        volume_accel_ratio = 0.0

    high_in_medium = max(float(h.get("price", 0) or 0) for h in med_slice)
    retrace_from_high_pct = 0.0
    if high_in_medium > 0:
        retrace_from_high_pct = ((high_in_medium - short_price_last) / high_in_medium) * 100

    return {
        "short_move_pct": round(short_move_pct, 3),
        "medium_move_pct": round(med_move_pct, 3),
        "short_vol_delta": round(short_vol_delta, 3),
        "baseline_vol_delta": round(baseline_vol_delta, 3),
        "volume_accel_ratio": round(volume_accel_ratio, 3),
        "retrace_from_high_pct": round(retrace_from_high_pct, 3),
    }


def compute_pre_breakout_score(features: dict) -> float:
    """Scores early breakout characteristics from 0-100."""
    if not features:
        return 0.0

    score = 0.0
    short_move = features.get("short_move_pct", 0.0)
    med_move = features.get("medium_move_pct", 0.0)
    vol_ratio = features.get("volume_accel_ratio", 0.0)
    retrace = features.get("retrace_from_high_pct", 100.0)

    # Momentum quality: fast + sustained push
    if short_move >= MIN_SHORT_MOVE_PCT:
        score += 30
    elif short_move >= (MIN_SHORT_MOVE_PCT * 0.6):
        score += 15

    if med_move >= MIN_MEDIUM_MOVE_PCT:
        score += 30
    elif med_move >= (MIN_MEDIUM_MOVE_PCT * 0.6):
        score += 15

    # Relative volume burst confirms participation
    if vol_ratio >= MIN_VOLUME_ACCEL_RATIO:
        score += 30
    elif vol_ratio >= (MIN_VOLUME_ACCEL_RATIO * 0.7):
        score += 15

    # Avoid extended reversals; prefer price near local highs
    if retrace <= MAX_RETRACE_FROM_HIGH_PCT:
        score += 10
    elif retrace <= (MAX_RETRACE_FROM_HIGH_PCT * 1.5):
        score += 5

    return round(min(score, 100.0), 1)


def fetch_candles(product_id: str, granularity: int = CANDLE_GRANULARITY) -> list[list]:
    """
    Fetches recent OHLCV candles from Coinbase's public market-data endpoint.
    No authentication required. Returns oldest-first list of
    [time, low, high, open, close, volume]. Empty list on any failure.
    """
    url = f"{COINBASE_PUBLIC_BASE}/products/{product_id}/candles?granularity={granularity}"
    req = urllib.request.Request(url, headers={"User-Agent": "coinbase-paper-trader/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310 - fixed Coinbase host
            data = json.load(resp)
    except Exception as exc:
        print(f"  [Candles] {product_id} fetch failed: {exc}")
        return []
    if not isinstance(data, list):
        return []
    data.sort(key=lambda c: c[0])  # API returns newest-first; make oldest-first
    return data


def detect_breakout_pattern(product_id: str, candles: list[list] | None = None) -> dict:
    """
    Detects a pre/early breakout setup from OHLCV candles and scores it 0-100.

    Pattern (validated on real XLM-USD data):
      - tight compression base, then
      - a volume burst vs the base median, with
      - price closing above the base range high (breakout), while
      - not already overextended above the breakout level.
    Returns {pattern_score, features}. Score 0 when data is insufficient.
    """
    if candles is None:
        candles = fetch_candles(product_id)

    need = CANDLE_BASE_LOOKBACK + 1
    if len(candles) < need:
        return {"pattern_score": 0.0, "features": {}}

    base = candles[-need:-1]          # the N candles forming the base
    latest = candles[-1]              # the potential breakout candle
    _, _, _, l_open, l_close, l_vol = latest

    base_highs = [c[2] for c in base]
    base_lows = [c[3] for c in base]
    base_vols = [c[5] for c in base]

    base_high = max(base_highs)
    base_low = min(base_lows)
    if base_low <= 0 or l_open <= 0:
        return {"pattern_score": 0.0, "features": {}}

    sorted_vols = sorted(base_vols)
    median_vol = sorted_vols[len(sorted_vols) // 2] or 1.0

    compression_pct = (base_high - base_low) / base_low * 100
    vol_ratio = l_vol / median_vol
    move_pct = (l_close - l_open) / l_open * 100
    breakout = l_close > base_high
    overextension_pct = (l_close - base_high) / base_high * 100 if base_high else 0.0

    half = len(base_lows) // 2
    first_half = sum(base_lows[:half]) / max(1, half)
    second_half = sum(base_lows[half:]) / max(1, len(base_lows) - half)
    higher_lows = second_half > first_half

    # ---- Scoring (volume burst is the strongest real-world predictor) ----
    score = 0.0
    if vol_ratio >= CANDLE_VOL_RATIO_STRONG:
        score += 35
    elif vol_ratio >= 2.0:
        score += 20
    elif vol_ratio >= CANDLE_VOL_RATIO_MIN:
        score += 10

    if breakout:
        score += 25

    if compression_pct <= CANDLE_COMPRESSION_TIGHT:
        score += 20
    elif compression_pct <= CANDLE_COMPRESSION_TIGHT * 1.75:
        score += 12
    elif compression_pct <= CANDLE_COMPRESSION_TIGHT * 2.5:
        score += 6

    if higher_lows:
        score += 10

    if move_pct >= 1.0:
        score += 10
    elif move_pct >= 0.5:
        score += 5

    # Overextension guard: don't chase a move that already ran away.
    if overextension_pct > CANDLE_MAX_OVEREXTENSION:
        score *= 0.4

    features = {
        "compression_pct": round(compression_pct, 3),
        "volume_ratio": round(vol_ratio, 2),
        "breakout_candle_dollar_volume": round(l_close * l_vol, 2),
        "breakout_close": breakout,
        "higher_lows": higher_lows,
        "candle_move_pct": round(move_pct, 3),
        "overextension_pct": round(overextension_pct, 3),
        "base_high": base_high,
    }
    return {"pattern_score": round(min(score, 100.0), 1), "features": features}


def detect_recent_breakout_pattern(product_id: str, candles: list[list] | None = None,
                                   trigger_candles: int = RECENT_TRIGGER_CANDLES) -> dict:
    """
    Scores the best breakout candle within the most recent trigger window.
    A timer can easily run one candle after the actual breakout; this keeps that
    setup eligible while applying a small freshness penalty per candle of delay.
    """
    if candles is None:
        candles = fetch_candles(product_id)
    need = CANDLE_BASE_LOOKBACK + 1
    if len(candles) < need:
        return {"pattern_score": 0.0, "features": {}}

    best = {"pattern_score": 0.0, "features": {}}
    max_age = max(1, trigger_candles)
    for age in range(max_age):
        end = len(candles) - age
        if end < need:
            break
        result = detect_breakout_pattern(product_id, candles[:end])
        score = max(0.0, result["pattern_score"] - (age * 8.0))
        if score > best["pattern_score"]:
            features = dict(result.get("features", {}))
            features["trigger_age_candles"] = age
            best = {"pattern_score": round(score, 1), "features": features}
    return best


def candle_change_pct(candles: list[list], periods: int) -> float:
    """Returns close-vs-open percent change over the last N candles."""
    if len(candles) < periods:
        return 0.0
    window = candles[-periods:]
    first_open = float(window[0][3] or 0)
    last_close = float(window[-1][4] or 0)
    return ((last_close - first_open) / first_open * 100) if first_open else 0.0


def calculate_obv_metrics(candles: list[list], lookback: int = OBV_LOOKBACK_CANDLES) -> dict:
    """
    Calculates OBV confirmation metrics from OHLCV candles.
    Candle shape: [time, low, high, open, close, volume].
    """
    if len(candles) < lookback + 1:
        return {"obv_pressure_pct": 0.0, "up_volume_ratio": 0.0, "obv_change": 0.0}

    obv = [0.0]
    for idx in range(1, len(candles)):
        prev_close = float(candles[idx - 1][4] or 0.0)
        close = float(candles[idx][4] or 0.0)
        volume = float(candles[idx][5] or 0.0)
        if close > prev_close:
            obv.append(obv[-1] + volume)
        elif close < prev_close:
            obv.append(obv[-1] - volume)
        else:
            obv.append(obv[-1])

    window = candles[-lookback:]
    up_volume = 0.0
    total_volume = 0.0
    for idx in range(len(candles) - lookback, len(candles)):
        prev_close = float(candles[idx - 1][4] or 0.0)
        close = float(candles[idx][4] or 0.0)
        volume = float(candles[idx][5] or 0.0)
        total_volume += volume
        if close > prev_close:
            up_volume += volume

    obv_change = obv[-1] - obv[-lookback - 1]
    obv_pressure_pct = (obv_change / total_volume * 100) if total_volume else 0.0
    up_volume_ratio = (up_volume / total_volume) if total_volume else 0.0
    return {
        "obv_pressure_pct": round(obv_pressure_pct, 2),
        "up_volume_ratio": round(up_volume_ratio, 3),
        "obv_change": round(obv_change, 4),
        "lookback_candles": lookback,
        "window_volume": round(total_volume, 4),
    }


def get_btc_market_context() -> dict:
    """Returns whether new alt entries are allowed under the BTC regime guard."""
    candles = fetch_candles("BTC-USD")
    if len(candles) < 12:
        return {"allow_buys": True, "reason": "BTC data unavailable", "btc_15m_change": 0.0, "btc_1h_change": 0.0}
    btc_15m = candle_change_pct(candles, 3)
    btc_1h = candle_change_pct(candles, 12)
    if btc_15m <= BTC_15M_MAX_DROP_PCT:
        return {"allow_buys": False, "reason": f"BTC 15m weak ({btc_15m:.2f}%)", "btc_15m_change": btc_15m, "btc_1h_change": btc_1h}
    if btc_1h <= BTC_1H_MAX_DROP_PCT:
        return {"allow_buys": False, "reason": f"BTC 1h weak ({btc_1h:.2f}%)", "btc_15m_change": btc_15m, "btc_1h_change": btc_1h}
    return {"allow_buys": True, "reason": "BTC regime OK", "btc_15m_change": btc_15m, "btc_1h_change": btc_1h}


def liquidity_filter_result(product_data: dict) -> dict:
    """Returns whether a candidate has enough traded USD liquidity to enter."""
    price = float(product_data.get("price", 0.0) or 0.0)
    volume_24h = float(product_data.get("volume_24h", 0.0) or 0.0)
    dollar_volume_24h = float(product_data.get("dollar_volume_24h", 0.0) or 0.0)
    if dollar_volume_24h <= 0 and price > 0 and volume_24h > 0:
        dollar_volume_24h = price * volume_24h

    features = product_data.get("pre_breakout_features", {}) or {}
    breakout_dollar_volume = float(features.get("breakout_candle_dollar_volume", 0.0) or 0.0)

    if dollar_volume_24h < MIN_24H_DOLLAR_VOLUME:
        return {
            "ok": False,
            "reason": f"24h dollar volume ${dollar_volume_24h:,.0f} < ${MIN_24H_DOLLAR_VOLUME:,.0f}",
            "dollar_volume_24h": dollar_volume_24h,
            "breakout_dollar_volume": breakout_dollar_volume,
        }
    if breakout_dollar_volume < MIN_BREAKOUT_DOLLAR_VOLUME:
        return {
            "ok": False,
            "reason": f"breakout candle volume ${breakout_dollar_volume:,.0f} < ${MIN_BREAKOUT_DOLLAR_VOLUME:,.0f}",
            "dollar_volume_24h": dollar_volume_24h,
            "breakout_dollar_volume": breakout_dollar_volume,
        }
    return {
        "ok": True,
        "reason": "liquidity OK",
        "dollar_volume_24h": dollar_volume_24h,
        "breakout_dollar_volume": breakout_dollar_volume,
    }


def obv_filter_result(product_data: dict) -> dict:
    """Returns whether OBV confirms accumulation for an entry candidate."""
    if not OBV_CONFIRMATION:
        return {"ok": True, "reason": "OBV disabled", "metrics": {}}
    metrics = product_data.get("obv", {}) or {}
    pressure = float(metrics.get("obv_pressure_pct", 0.0) or 0.0)
    up_ratio = float(metrics.get("up_volume_ratio", 0.0) or 0.0)
    if pressure < MIN_OBV_PRESSURE_PCT:
        return {"ok": False, "reason": f"OBV pressure {pressure:+.1f}% < {MIN_OBV_PRESSURE_PCT:.1f}%", "metrics": metrics}
    if up_ratio < MIN_OBV_UP_VOLUME_RATIO:
        return {"ok": False, "reason": f"OBV up-volume ratio {up_ratio:.2f} < {MIN_OBV_UP_VOLUME_RATIO:.2f}", "metrics": metrics}
    return {"ok": True, "reason": "OBV accumulation OK", "metrics": metrics}


def detect_bearish_reversal(product_id: str, candles: list[list] | None = None) -> dict:
    """
    Detects a bearish trend flip from OHLCV candles so an open position can be exited
    before the trailing stop is hit. Returns {bearish: bool, reason: str}.

    Bearish if any of:
      - Breakdown: latest close drops below the recent base low.
      - Distribution candle: a strong red candle on rising volume.
      - Lower highs: the most recent highs are stepping down (momentum fading).
    """
    if candles is None:
        candles = fetch_candles(product_id)

    need = CANDLE_BASE_LOOKBACK + 1
    if len(candles) < need:
        return {"bearish": False, "reason": ""}

    base = candles[-need:-1]
    latest = candles[-1]
    _, _, _, l_open, l_close, l_vol = latest

    base_lows = [c[3] for c in base]
    base_highs = [c[2] for c in base]
    base_vols = [c[5] for c in base]
    if l_open <= 0:
        return {"bearish": False, "reason": ""}

    sorted_vols = sorted(base_vols)
    median_vol = sorted_vols[len(sorted_vols) // 2] or 1.0
    vol_ratio = l_vol / median_vol
    move_pct = (l_close - l_open) / l_open * 100

    # 1) Breakdown below the base support
    if l_close < min(base_lows):
        return {"bearish": True, "reason": "BREAKDOWN_BELOW_BASE_LOW"}

    # 2) Strong red candle confirmed by volume (distribution)
    if move_pct <= -abs(BEARISH_DROP_PCT) and vol_ratio >= BEARISH_VOL_RATIO:
        return {"bearish": True, "reason": "BEARISH_VOLUME_CANDLE"}

    # 3) Lower highs across the recent window (fading momentum)
    half = len(base_highs) // 2
    if half >= 2:
        first_half_high = max(base_highs[:half])
        second_half_high = max(base_highs[half:])
        if second_half_high < first_half_high and l_close < l_open:
            return {"bearish": True, "reason": "LOWER_HIGHS_FADING"}

    return {"bearish": False, "reason": ""}


def aggregate_candles(candles: list[list], factor: int) -> list[list]:
    """
    Aggregates fine-grained candles into coarser ones (e.g. 1h -> 4h with factor=4).
    Input/output candle shape: [time, low, high, open, close, volume], oldest-first.
    Groups are taken from the most recent candle backwards so the latest bucket is
    aligned to "now". Partial leading groups are dropped.
    """
    if factor <= 1 or len(candles) < factor:
        return candles
    usable = len(candles) - (len(candles) % factor)
    trimmed = candles[len(candles) - usable:]  # keep newest, drop oldest remainder
    out = []
    for i in range(0, len(trimmed), factor):
        group = trimmed[i:i + factor]
        out.append([
            group[0][0],                       # time of first candle in the group
            min(c[1] for c in group),          # low
            max(c[2] for c in group),          # high
            group[0][3],                       # open of first
            group[-1][4],                      # close of last
            sum(c[5] for c in group),          # summed volume
        ])
    return out


def detect_multi_timeframe_signal(product_id: str) -> dict:
    """
    Multi-timeframe confirmation filter (Option A).

    The 5m breakout is the entry TRIGGER; this function confirms the move holds on
    higher timeframes before we commit capital:
      - 15m : must score >= MTF_CONFIRM_MIN_SCORE and not be bearish (early confirm)
      - 1h  : must not be bearish (trend confirm)
      - 4h  : veto only — skip if bearish or wildly overextended (context guard)

    Returns:
      {confirmed: bool, reason: str, summary: str, scores: {tf: score}}
    """
    scores = {}

    # --- 15m confirmation ---
    c15 = fetch_candles(product_id, MTF_GRAN_15M)
    p15 = detect_recent_breakout_pattern(product_id, c15)
    b15 = detect_bearish_reversal(product_id, c15)
    scores["15m"] = p15["pattern_score"]
    if b15["bearish"]:
        return {"confirmed": False, "reason": f"15m bearish ({b15['reason']})",
                "summary": _mtf_summary(scores), "scores": scores}
    if p15["pattern_score"] < MTF_CONFIRM_MIN_SCORE:
        return {"confirmed": False,
                "reason": f"15m weak ({p15['pattern_score']:.0f} < {MTF_CONFIRM_MIN_SCORE:.0f})",
                "summary": _mtf_summary(scores), "scores": scores}

    # --- 1h trend confirmation ---
    c1h = fetch_candles(product_id, MTF_GRAN_1H)
    p1h = detect_recent_breakout_pattern(product_id, c1h)
    b1h = detect_bearish_reversal(product_id, c1h)
    scores["1h"] = p1h["pattern_score"]
    if b1h["bearish"]:
        return {"confirmed": False, "reason": f"1h bearish ({b1h['reason']})",
                "summary": _mtf_summary(scores), "scores": scores}

    # --- 4h veto (aggregated from 1h candles) ---
    c4h = aggregate_candles(c1h, MTF_4H_AGG_FACTOR)
    if len(c4h) >= CANDLE_BASE_LOOKBACK + 1:
        p4h = detect_breakout_pattern(product_id, c4h)
        b4h = detect_bearish_reversal(product_id, c4h)
        scores["4h"] = p4h["pattern_score"]
        if b4h["bearish"]:
            return {"confirmed": False, "reason": f"4h bearish veto ({b4h['reason']})",
                    "summary": _mtf_summary(scores), "scores": scores}
        overext = p4h["features"].get("overextension_pct", 0.0)
        if overext > CANDLE_MAX_OVEREXTENSION:
            return {"confirmed": False,
                    "reason": f"4h overextended ({overext:.1f}%)",
                    "summary": _mtf_summary(scores), "scores": scores}

    return {"confirmed": True, "reason": "aligned across timeframes",
            "summary": _mtf_summary(scores), "scores": scores}


def _mtf_summary(scores: dict) -> str:
    """Compact one-line summary of per-timeframe pattern scores."""
    return " ".join(f"{tf}:{s:.0f}" for tf, s in scores.items()) or "n/a"


def update_market_state(market_state: dict, products: list[dict]):
    """Updates rolling per-product snapshots and prunes old records."""
    now_ts = _utcnow_epoch()
    cutoff = now_ts - (PATTERN_LOOKBACK_MINUTES * 60)

    seen = set()
    for prod in products:
        pid = prod["product_id"]
        seen.add(pid)
        hist = market_state.get(pid, [])
        hist.append({
            "ts": now_ts,
            "price": prod.get("price", 0.0),
            "volume_24h": prod.get("volume_24h", 0.0),
        })
        hist = [h for h in hist if h.get("ts", 0) >= cutoff]
        market_state[pid] = hist

    # Prune stale products that are no longer in the recent watchlist
    for pid in list(market_state.keys()):
        hist = [h for h in market_state.get(pid, []) if h.get("ts", 0) >= cutoff]
        if not hist and pid not in seen:
            del market_state[pid]
        else:
            market_state[pid] = hist


# ---------------------------------------------------------------------------
# MARKET DATA  (single API call returns watchlist + prices + scores)
# ---------------------------------------------------------------------------

def get_market_snapshot(client) -> tuple[list[dict], dict]:
    """
    Returns (products, prices). When `client` is None (no API keys), uses
    Coinbase's public market-data endpoints so paper trading still works.

      products : list of enriched dicts sorted by 24h volume (top WATCHLIST_SIZE)
                 each dict: {product_id, price, volume_24h,
                              price_change_24h, volume_change_24h, score,
                              pre_breakout_score, pre_breakout_features}
      prices   : {product_id: price}  convenience lookup for position management
    """
    if client is None:
        return get_market_snapshot_public()

    STABLECOINS = {"USDT", "USDC", "DAI", "BUSD", "TUSD", "USDP", "GUSD", "FRAX", "LUSD"}
    products, prices = [], {}
    try:
        raw = client.get_products()
        for prod in raw.get("products", []):
            if prod.get("quote_currency_id") != QUOTE_CURRENCY: continue
            if prod.get("status")            != "online":        continue
            if prod.get("product_type")      != "SPOT":          continue
            if prod.get("base_currency_id", "") in STABLECOINS:  continue
            try:
                price          = float(prod.get("price")                      or 0)
                volume_24h     = float(prod.get("volume_24h")                 or 0)
                price_chg      = float(prod.get("price_percentage_change_24h") or 0)
                volume_chg     = float(prod.get("volume_percentage_change_24h") or 0)
            except (TypeError, ValueError):
                continue
            if price <= 0 or volume_24h <= 0:
                continue
            score = compute_signal_score(price_chg, volume_chg)
            products.append({
                "product_id":       prod["product_id"],
                "price":            price,
                "volume_24h":       volume_24h,
                "price_change_24h": price_chg,
                "volume_change_24h":volume_chg,
                "score":            score,
            })
            prices[prod["product_id"]] = price

        products.sort(key=lambda x: x["volume_24h"], reverse=True)
        products = products[:WATCHLIST_SIZE]

        # Log top 5 with their scores
        top5 = sorted(products, key=lambda x: x["score"], reverse=True)[:5]
        print(f"[Snapshot] Top scorers: " +
              ", ".join(f"{p['product_id']} {p['score']:.0f}pts" for p in top5))
    except Exception as exc:
        print(f"[Snapshot] Error: {exc}")

    return products, prices


def get_market_snapshot_public() -> tuple[list[dict], dict]:
    """
    Builds the watchlist + prices from Coinbase's PUBLIC candle endpoint (no keys).
    Price, 24h volume, and 24h change are derived from candles, and the breakout
    pattern score is computed in the same pass (so entries need no extra calls).
    """
    ids = list_public_usd_products()
    per_day = max(1, int(86400 / CANDLE_GRANULARITY))  # candles spanning ~24h
    products, prices = [], {}
    print(f"[Snapshot] Public data: scanning {len(ids)} USD pairs...")

    for pid in ids:
        candles = fetch_candles(pid)
        if len(candles) < CANDLE_BASE_LOOKBACK + 1:
            continue
        last = candles[-1]
        price = float(last[4] or 0)  # close of most recent candle
        if price <= 0:
            continue

        day_slice = candles[-per_day:]
        volume_24h = sum(float(c[5] or 0) for c in day_slice)
        first_open = float(day_slice[0][3] or 0)
        price_chg = ((price - first_open) / first_open * 100) if first_open else 0.0

        pat = detect_recent_breakout_pattern(pid, candles)
        obv = calculate_obv_metrics(candles)
        price_chg_15m = candle_change_pct(candles, 3)
        price_chg_1h = candle_change_pct(candles, 12)
        products.append({
            "product_id":            pid,
            "price":                 price,
            "volume_24h":            volume_24h,
            "dollar_volume_24h":     round(price * volume_24h, 2),
            "price_change_24h":      round(price_chg, 3),
            "price_change_15m":      round(price_chg_15m, 3),
            "price_change_1h":       round(price_chg_1h, 3),
            "volume_change_24h":     0.0,  # not derivable from candles; pattern path drives entries
            "score":                 compute_signal_score(price_chg, 0.0),
            "pre_breakout_score":    pat["pattern_score"],
            "pre_breakout_features": pat["features"],
            "obv":                   obv,
        })
        prices[pid] = price
        time.sleep(0.05)  # stay under the public rate limit

    # Rank the watchlist by USD volume (price x token volume), not raw token
    # units — otherwise cheap, high-supply coins crowd out real movers.
    products.sort(key=lambda x: x["price"] * x["volume_24h"], reverse=True)
    watchlist = products[:WATCHLIST_SIZE]

    # Never drop a strong breakout setup just because it ranks low on volume:
    # always include any coin already at/above the buy threshold.
    in_list = {p["product_id"] for p in watchlist}
    for p in products:
        if p["product_id"] not in in_list and p["pre_breakout_score"] >= MIN_PATTERN_SCORE_TO_BUY:
            watchlist.append(p)
            in_list.add(p["product_id"])
    products = watchlist

    top5 = sorted(products, key=lambda x: x["pre_breakout_score"], reverse=True)[:5]
    print("[Snapshot] Top pattern scorers: " +
          ", ".join(f"{p['product_id']} {p['pre_breakout_score']:.0f}pts" for p in top5))
    return products, prices

# ---------------------------------------------------------------------------
# STRATEGY  — replace the body of this function with real indicator logic
# ---------------------------------------------------------------------------

def evaluate_market_entry_signal(product_data: dict) -> bool:
    """
    Returns True only for strong setups, so the bot does not buy everything.
    Entry requires EITHER:
      - breakout pattern score >= MIN_PATTERN_SCORE_TO_BUY (default 80), or
      - the high-confidence 24h score >= MIN_SIGNAL_SCORE (default 80).
    """
    score = product_data.get("score", 0.0)
    pre_breakout_score = product_data.get("pre_breakout_score", 0.0)

    # Path 1: strong breakout pattern (candle-based) — primary entry trigger.
    if pre_breakout_score >= MIN_PATTERN_SCORE_TO_BUY:
        return True

    # Path 2: strong 24h momentum/volume score.
    if score >= MIN_SIGNAL_SCORE:
        return True
    return False

# ---------------------------------------------------------------------------
# TRADE EXECUTION
# ---------------------------------------------------------------------------

def _capital_deployed(active_positions: dict) -> float:
    """Returns total USD currently locked in open positions."""
    return len(active_positions) * CAPITAL_PER_TRADE_USD


def scan_and_execute_entries(client, active_positions: dict, products: list[dict], market_state: dict, daily_ledger: dict):
    """
    Scans enriched product list, scores each coin, and enters positions
    only when score >= MIN_SIGNAL_SCORE and budget allows.
    Products are already sorted by 24h volume; we re-sort by score for entry priority.
    Coins that breached their daily loss cap are skipped until the next UTC day.
    """
    # Enrich each product with breakout pattern features and score.
    # Public snapshot already computed these; only fill in when missing.
    # Primary: candle-based detector. Fallback: rolling-snapshot detector.
    candle_candidates = sorted(products, key=lambda x: x.get("volume_24h", 0), reverse=True)[:CANDLE_SCAN_LIMIT]
    candle_ids = {p["product_id"] for p in candle_candidates}

    for prod in products:
        if "pre_breakout_score" in prod:
            continue  # already scored during the public market snapshot
        if prod["product_id"] in candle_ids:
            candles = fetch_candles(prod["product_id"])
            result = detect_recent_breakout_pattern(prod["product_id"], candles)
            prod["pre_breakout_features"] = result["features"]
            prod["pre_breakout_score"] = result["pattern_score"]
            prod["obv"] = calculate_obv_metrics(candles)
        else:
            features = _compute_pre_breakout_features(prod, market_state)
            prod["pre_breakout_features"] = features
            prod["pre_breakout_score"] = compute_pre_breakout_score(features)

    # Prioritise either strong breakout setup or strong base score.
    by_score = sorted(
        products,
        key=lambda x: max(
            x.get("pre_breakout_score", 0.0),
            x.get("score", 0.0),
        ),
        reverse=True,
    )

    market_context = get_btc_market_context() if MARKET_REGIME_FILTER else {"allow_buys": True, "reason": "disabled", "btc_1h_change": 0.0}
    if MARKET_REGIME_FILTER:
        print(f"  [Market Regime] {market_context['reason']} | BTC 15m {market_context.get('btc_15m_change', 0.0):+.2f}% | BTC 1h {market_context.get('btc_1h_change', 0.0):+.2f}%")
    if MARKET_REGIME_FILTER and not market_context["allow_buys"]:
        print("  [Market Regime] New entries paused; managing existing positions only.")
        save_json_file(PORTFOLIO_FILE, active_positions)
        return

    for prod in by_score:
        product_id = prod["product_id"]
        price      = prod["price"]

        # Hard cap: never exceed max open positions or total capital
        if len(active_positions) >= MAX_OPEN_POSITIONS:
            print(f"  [Budget] Max {MAX_OPEN_POSITIONS} positions reached (${_capital_deployed(active_positions):,.0f} deployed). No new entries.")
            break

        if product_id in active_positions:
            continue

        if price <= 0:
            continue

        # Per-coin daily loss cap: skip coins paused for the rest of the day.
        if is_coin_blocked_today(daily_ledger, product_id):
            continue

        liquidity = liquidity_filter_result(prod)
        if not liquidity["ok"]:
            print(f"  [LIQ SKIP] {product_id}: {liquidity['reason']}")
            continue

        obv = obv_filter_result(prod)
        if not obv["ok"]:
            print(f"  [OBV SKIP] {product_id}: {obv['reason']}")
            continue

        if MARKET_REGIME_FILTER and product_id != "BTC-USD":
            coin_1h = float(prod.get("price_change_1h", 0.0) or 0.0)
            btc_1h = float(market_context.get("btc_1h_change", 0.0) or 0.0)
            rel_strength = coin_1h - btc_1h
            if rel_strength < MIN_REL_STRENGTH_VS_BTC:
                print(f"  [RS SKIP] {product_id}: 1h {coin_1h:+.2f}% vs BTC {btc_1h:+.2f}% (RS {rel_strength:+.2f}% < {MIN_REL_STRENGTH_VS_BTC:.2f}%)")
                continue

        if evaluate_market_entry_signal(prod):
            # Multi-timeframe confirmation: the 5m trigger must hold up on 15m/1h
            # and not contradict the 4h trend. Only runs for triggered coins, so
            # the extra candle calls are limited to a handful per cycle.
            mtf_scores = {}
            if MULTI_TIMEFRAME_CONFIRM:
                mtf = detect_multi_timeframe_signal(product_id)
                mtf_scores = mtf["scores"]
                if not mtf["confirmed"]:
                    print(f"  [MTF SKIP] {product_id}: {mtf['reason']} | {mtf['summary']}")
                    continue
                print(f"  [MTF OK]   {product_id}: {mtf['summary']}")

            crypto_qty         = CAPITAL_PER_TRADE_USD / price
            initial_stop       = price * (1 - TRAILING_PERCENT / 100)
            take_profit_target = price * (1 + TAKE_PROFIT_PERCENT / 100)
            mode_label         = "LIVE BUY" if LIVE_ORDERS_ACTIVE else "PAPER BUY"

            if LIVE_ORDERS_ACTIVE:
                # --- LIVE ORDER EXECUTION ---
                # Requires API key with 'trade' permission.
                # Coinbase minimum order is $1 USD.
                try:
                    order = client.market_order_buy(
                        client_order_id=f"trader-{product_id}-{int(datetime.now(timezone.utc).timestamp())}",
                        product_id=product_id,
                        quote_size=str(round(CAPITAL_PER_TRADE_USD, 2)),
                    )
                    order_id = order.get("order_id", "unknown")
                    print(f"  [LIVE ORDER] {product_id} order_id={order_id}")
                except Exception as exc:
                    print(f"  [LIVE ORDER ERROR] {product_id}: {exc}")
                    continue  # skip position tracking if order failed

            active_positions[product_id] = {
                "product_id":             product_id,
                "mode":                   "live" if LIVE_ORDERS_ACTIVE else "paper",
                "entry_timestamp":        _utcnow_iso(),
                "entry_price":            price,
                "allocated_usd":          CAPITAL_PER_TRADE_USD,
                "simulated_qty":          crypto_qty,
                "highest_tracked_price":  price,
                "current_trailing_stop":  initial_stop,
                "take_profit_boundary":   take_profit_target,
            }

            active_positions[product_id]["signal_score"] = prod.get("score", 0)
            active_positions[product_id]["pre_breakout_score"] = prod.get("pre_breakout_score", 0)
            active_positions[product_id]["pre_breakout_features"] = prod.get("pre_breakout_features", {})
            active_positions[product_id]["mtf_scores"] = mtf_scores
            active_positions[product_id]["dollar_volume_24h"] = liquidity.get("dollar_volume_24h", 0.0)
            active_positions[product_id]["breakout_dollar_volume"] = liquidity.get("breakout_dollar_volume", 0.0)
            active_positions[product_id]["obv"] = obv.get("metrics", {})

            msg = (
                f"[{mode_label}] {product_id} @ ${price:,.4f} | "
                f"Score: {prod.get('score', 0):.0f}/100 | "
                f"Pattern: {prod.get('pre_breakout_score', 0):.0f}/100 | "
                f"24h$: ${liquidity.get('dollar_volume_24h', 0.0):,.0f} | "
                f"OBV: {obv.get('metrics', {}).get('obv_pressure_pct', 0.0):+.1f}% | "
                f"Size: ${CAPITAL_PER_TRADE_USD:,.0f} | "
                f"Stop: ${initial_stop:,.4f} | TP: ${take_profit_target:,.4f} | "
                f"Budget: ${_capital_deployed(active_positions):,.0f}/${TOTAL_CAPITAL_USD:,.0f}"
            )
            print(f"  {msg}")
            send_discord_alert(msg)

    save_json_file(PORTFOLIO_FILE, active_positions)


def manage_active_positions(client, active_positions: dict, live_prices: dict, daily_ledger: dict):
    """Updates trailing stops and closes positions on take-profit, trailing stop,
    or a confirmed bearish reversal. Records realized PnL into the daily ledger."""
    closed = []
    history = load_json_file(HISTORY_FILE)

    for product_id, pos in list(active_positions.items()):
        current_price = live_prices.get(product_id, 0.0)
        if current_price <= 0:
            continue

        # Ratchet trailing stop upward
        if current_price > pos["highest_tracked_price"]:
            pos["highest_tracked_price"]   = current_price
            pos["current_trailing_stop"]   = current_price * (1 - TRAILING_PERCENT / 100)
            print(
                f"  [STOP UP] {product_id} new high ${current_price:,.2f} → "
                f"stop ${pos['current_trailing_stop']:,.2f}"
            )

        exit_triggered = False
        exit_reason    = ""

        if current_price >= pos["take_profit_boundary"]:
            exit_triggered = True
            exit_reason    = "TAKE_PROFIT_LIMIT_HIT"
        elif current_price <= pos["current_trailing_stop"]:
            exit_triggered = True
            exit_reason    = "TRAILING_STOP_LOSS_TRIGGERED"
        else:
            # Bearish-reversal exit: leave before the trailing stop if the trend flips.
            reversal = detect_bearish_reversal(product_id)
            if reversal["bearish"]:
                exit_triggered = True
                exit_reason    = f"BEARISH_REVERSAL_{reversal['reason']}"

        if exit_triggered:
            initial_value = pos.get("allocated_usd", CAPITAL_PER_TRADE_USD)
            final_value   = pos["simulated_qty"] * current_price
            pnl_usd       = final_value - initial_value
            pnl_pct       = (pnl_usd / initial_value) * 100
            mode_label    = "LIVE SELL" if pos.get("mode") == "live" else "PAPER SELL"

            if pos.get("mode") == "live":
                # --- LIVE SELL ORDER ---
                try:
                    order = client.market_order_sell(
                        client_order_id=f"trader-sell-{product_id}-{int(datetime.now(timezone.utc).timestamp())}",
                        product_id=product_id,
                        base_size=str(round(pos["simulated_qty"], 8)),
                    )
                    print(f"  [LIVE SELL] {product_id} order_id={order.get('order_id', 'unknown')}")
                except Exception as exc:
                    print(f"  [LIVE SELL ERROR] {product_id}: {exc}")
                    # Still record the exit so we don't hold the position forever

            trade_record = {
                "strategy":         "Automated Multi-Asset Watchlist Engine",
                "product_id":       product_id,
                "mode":             pos.get("mode", "paper"),
                "live_data_source": "Coinbase Advanced API",
                "config": {
                    "trailing_percent":    TRAILING_PERCENT,
                    "take_profit_percent": TAKE_PROFIT_PERCENT,
                    "total_capital_usd":   TOTAL_CAPITAL_USD,
                },
                "entry": {
                    "timestamp":             pos["entry_timestamp"],
                    "price_usd":             pos["entry_price"],
                    "allocated_capital_usd": initial_value,
                    "simulated_quantity":    pos["simulated_qty"],
                },
                "exit": {
                    "timestamp":                 _utcnow_iso(),
                    "reason":                    exit_reason,
                    "price_usd":                 current_price,
                    "highest_tracked_price_usd": pos["highest_tracked_price"],
                },
                "performance": {
                    "pnl_usd":        pnl_usd,
                    "pnl_percentage": pnl_pct,
                    "status":         "CLOSED",
                },
            }

            history.append(trade_record)
            closed.append(product_id)

            # Record realized PnL; blocks the coin for the day if it breaches the cap.
            record_daily_pnl(daily_ledger, product_id, pnl_usd)

            msg = (
                f"[{mode_label}] {product_id} closed @ ${current_price:,.4f} | "
                f"PnL: ${pnl_usd:+.2f} ({pnl_pct:+.2f}%) | Reason: {exit_reason}"
            )
            print(f"  {msg}")
            send_discord_alert(msg)

    if closed:
        for pid in closed:
            del active_positions[pid]
        save_json_file(PORTFOLIO_FILE, active_positions)
        save_json_file(HISTORY_FILE, history)
        save_json_file(DAILY_PNL_FILE, daily_ledger)
        print(f"  [Ledger] {len(closed)} position(s) archived to {HISTORY_FILE}")

# ---------------------------------------------------------------------------
# STANDALONE BREAKOUT SCANNER (on-demand, public data only)
# ---------------------------------------------------------------------------

def list_public_usd_products() -> list[str]:
    """Lists tradable X-USD product ids using Coinbase's public endpoint (no auth)."""
    stable = {"USDT", "USDC", "DAI", "BUSD", "TUSD", "USDP", "GUSD", "FRAX",
              "LUSD", "PYUSD", "USD"}
    url = f"{COINBASE_PUBLIC_BASE}/products"
    req = urllib.request.Request(url, headers={"User-Agent": "coinbase-paper-trader/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # nosec B310 - fixed Coinbase host
            data = json.load(resp)
    except Exception as exc:
        print(f"[Scan] product list failed: {exc}")
        return []
    out = []
    for p in data:
        if p.get("quote_currency") != "USD":
            continue
        if p.get("status") != "online":
            continue
        if p.get("trading_disabled") or p.get("limit_only") or p.get("cancel_only"):
            continue
        if p.get("base_currency") in stable:
            continue
        out.append(p["id"])
    return out


def scan_breakouts_now(min_score: float = 55.0, top_n: int = 20) -> list[dict]:
    """
    Scans every tradable USD pair for the breakout pattern using public candle data
    and prints a ranked table. Returns the list of matches. No API key required.
    """
    ids = list_public_usd_products()
    print(f"[Scan] Scanning {len(ids)} USD pairs for breakout pattern...")
    hits = []
    for pid in ids:
        candles = fetch_candles(pid)
        if not candles:
            continue
        res = detect_breakout_pattern(pid, candles)
        if res["pattern_score"] >= min_score:
            hits.append({"product_id": pid, "score": res["pattern_score"], **res["features"]})
        time.sleep(0.08)  # stay under the public rate limit

    hits.sort(key=lambda x: x["score"], reverse=True)

    print(f"\n=== Pattern matches (score >= {min_score:.0f}): {len(hits)} ===")
    header = (f"{'Coin':<14}{'Score':>6}  {'Vol':>6}  {'Comp%':>6}  "
              f"{'Break':>5}  {'HiLo':>5}  {'Move%':>6}  {'Ext%':>6}")
    print(header)
    print("-" * len(header))
    for h in hits[:top_n]:
        print(
            f"{h['product_id']:<14}{h['score']:>6.1f}  "
            f"{h.get('volume_ratio', 0):>5.1f}x  "
            f"{h.get('compression_pct', 0):>6.2f}  "
            f"{str(h.get('breakout_close')):>5}  "
            f"{str(h.get('higher_lows')):>5}  "
            f"{h.get('candle_move_pct', 0):>6.2f}  "
            f"{h.get('overextension_pct', 0):>6.2f}"
        )
    if not hits:
        print("No coins currently match the pattern threshold.")
    return hits


# ---------------------------------------------------------------------------
# PERIODIC PERFORMANCE SUMMARY (Discord every SUMMARY_INTERVAL_HOURS)
# ---------------------------------------------------------------------------

def build_portfolio_summary(active_positions: dict, history: list, live_prices: dict,
                            since_epoch: int) -> str:
    """
    Builds a human-readable performance summary:
      - total P/L (realized all-time + open unrealized)
      - realized win/loss counts
      - open positions with per-coin gain/loss
      - coins bought and coins sold during the reporting window
    """
    closed = [t for t in history if isinstance(t, dict)
              and t.get("performance", {}).get("status") == "CLOSED"]
    realized_total = sum(float(t["performance"].get("pnl_usd", 0) or 0) for t in closed)
    wins = sum(1 for t in closed if float(t["performance"].get("pnl_usd", 0) or 0) > 0)
    losses = len(closed) - wins

    unrealized_total = 0.0
    open_lines = []
    for pid, pos in active_positions.items():
        cur = live_prices.get(pid, pos.get("entry_price", 0.0)) or 0.0
        alloc = float(pos.get("allocated_usd", CAPITAL_PER_TRADE_USD) or 0.0)
        qty = float(pos.get("simulated_qty", 0.0) or 0.0)
        pnl = (qty * cur) - alloc
        pnl_pct = (pnl / alloc * 100) if alloc else 0.0
        unrealized_total += pnl
        open_lines.append(f"  {pid}: ${pnl:+.2f} ({pnl_pct:+.2f}%) @ ${cur:,.4f}")

    total_pnl = realized_total + unrealized_total

    bought = [pid for pid, pos in active_positions.items()
              if _iso_to_epoch(pos.get("entry_timestamp", "")) >= since_epoch]
    sold = [(t["product_id"], float(t["performance"].get("pnl_usd", 0) or 0))
            for t in closed
            if _iso_to_epoch(t.get("exit", {}).get("timestamp", "")) >= since_epoch]

    hours = SUMMARY_INTERVAL_HOURS
    lines = [
        f"📊 **Paper Trading Summary** (last {hours:.0f}h) — {_utcnow_iso()[:19]}Z",
        f"**Total P/L:** ${total_pnl:+,.2f}  (realized ${realized_total:+,.2f} + open ${unrealized_total:+,.2f})",
        f"**Closed trades:** {len(closed)}  ({wins} wins / {losses} losses)",
        f"**Open positions:** {len(active_positions)}/{MAX_OPEN_POSITIONS}  "
        f"(${_capital_deployed(active_positions):,.0f} deployed)",
    ]
    if open_lines:
        lines.append("Open:\n" + "\n".join(open_lines))
    lines.append("**Bought this period:** " + (", ".join(bought) if bought else "none"))
    if sold:
        lines.append("**Sold this period:** " +
                     ", ".join(f"{pid} ${pnl:+.2f}" for pid, pnl in sold))
    return "\n".join(lines)


def maybe_send_summary(active_positions: dict, live_prices: dict, force: bool = False):
    """Sends the performance summary to Discord every SUMMARY_INTERVAL_HOURS."""
    state = load_json_file(SUMMARY_STATE_FILE)
    if not isinstance(state, dict):
        state = {}

    last_sent = int(state.get("last_sent_epoch", 0) or 0)
    now = _utcnow_epoch()
    interval = int(SUMMARY_INTERVAL_HOURS * 3600)

    if not force and last_sent and (now - last_sent) < interval:
        return  # not time yet

    since = last_sent if last_sent else (now - interval)
    history = load_json_file(HISTORY_FILE)
    report = build_portfolio_summary(active_positions, history, live_prices, since)

    print("\n" + report + "\n")
    send_discord_alert(report)

    state["last_sent_epoch"] = now
    save_json_file(SUMMARY_STATE_FILE, state)


# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------

def run_trading_cycle(client) -> tuple[dict, dict]:
    """Runs one full trade cycle: snapshot, manage exits, scan entries, summary.
    Returns (active_positions, live_prices) after the cycle."""
    print(f"\n[Cycle] {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

    products, live_prices = get_market_snapshot(client)
    if not live_prices:
        print("  [Market] No prices received — skipping cycle.")
        return (load_json_file(PORTFOLIO_FILE) or {}, {})

    active_positions = load_json_file(PORTFOLIO_FILE)
    market_state = load_json_file(MARKET_STATE_FILE)
    daily_ledger = load_daily_ledger()

    if not isinstance(market_state, dict):
        market_state = {}

    update_market_state(market_state, products)
    save_json_file(MARKET_STATE_FILE, market_state)

    if active_positions:
        manage_active_positions(client, active_positions, live_prices, daily_ledger)

    scan_and_execute_entries(client, active_positions, products, market_state, daily_ledger)
    save_json_file(DAILY_PNL_FILE, daily_ledger)

    # Periodic Discord performance summary (every SUMMARY_INTERVAL_HOURS).
    maybe_send_summary(active_positions, live_prices)
    return active_positions, live_prices


def main_orchestrator():
    print("=" * 52)
    print("  COINBASE ADVANCED PAPER-TRADING SYSTEM")
    print("=" * 52)

    _validate_config()

    client = get_crypto_client()

    while True:
        try:
            run_trading_cycle(client)
            print(f"  [Sleep] Next cycle in {LOOP_INTERVAL_SECONDS // 60} min.")
            time.sleep(LOOP_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            print("\n[Stop] Engine shut down by operator.")
            break
        except Exception as exc:
            print(f"[Error] Unhandled exception: {exc} — restarting in 30 s.")
            time.sleep(30)


def run_once():
    """Runs a single trading cycle and prints the current portfolio summary."""
    print("=" * 52)
    print("  COINBASE PAPER-TRADING — SINGLE CYCLE")
    print("=" * 52)
    _validate_config()
    client = get_crypto_client()
    active_positions, live_prices = run_trading_cycle(client)
    # Always print where we're at after a one-shot run (no extra Discord send).
    history = load_json_file(HISTORY_FILE)
    since = _utcnow_epoch() - int(SUMMARY_INTERVAL_HOURS * 3600)
    print("\n" + build_portfolio_summary(active_positions, history, live_prices, since))


if __name__ == "__main__":
    # `python trader.py scan` — on-demand breakout scan (public data, no keys).
    # `python trader.py once` — run exactly one trading cycle, then exit.
    # `python trader.py`      — run the continuous trading loop.
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "scan":
        scan_breakouts_now()
    elif mode == "once":
        run_once()
    else:
        main_orchestrator()
