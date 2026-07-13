from __future__ import annotations

import json
import base64
import hashlib
import hmac
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import formatdate
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# CONFIGURATION  — all secrets come from environment variables, never hardcoded
# ---------------------------------------------------------------------------
API_KEY = os.environ.get("CB_API_KEY") or os.environ.get("COINBASE_API_KEY", "")
API_SECRET = os.environ.get("CB_API_SECRET") or os.environ.get("COINBASE_API_SECRET", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# Dynamic watchlist — fetched from Coinbase each cycle, sorted by 24h volume
WATCHLIST_SIZE        = int(os.environ.get("WATCHLIST_SIZE", "50"))  # override via env
QUOTE_CURRENCY        = "USD"   # only trade X-USD pairs
WATCHLIST_STRONG_SETUP_OVERFLOW = os.environ.get("WATCHLIST_STRONG_SETUP_OVERFLOW", "false").lower() == "true"

# ---------------------------------------------------------------------------
# CAPITAL MANAGEMENT
# CAPITAL_PER_TRADE_USD  : fixed dollar size deployed per coin
# MAX_OPEN_POSITIONS     : max simultaneous positions
# TOTAL_CAPITAL_USD      : full budget = per-trade size x max positions
# MAX_DAILY_LOSS_PER_COIN: per-coin daily loss cap (blocks that coin for the day)
# MAX_DAILY_LOSS_PCT     : portfolio-level daily loss cap as % of TOTAL_CAPITAL_USD
#                          If total realized loss for the day exceeds this %,
#                          NO new entries are opened for the rest of the UTC day.
# ---------------------------------------------------------------------------
CAPITAL_PER_TRADE_USD   = float(os.environ.get("CAPITAL_PER_TRADE_USD",   "1000"))  # $1000 per coin
MAX_OPEN_POSITIONS      = int(os.environ.get("MAX_OPEN_POSITIONS",          "3"))    # 3 simultaneous positions
TOTAL_CAPITAL_USD       = float(os.environ.get(
    "TOTAL_CAPITAL_USD", str(CAPITAL_PER_TRADE_USD * MAX_OPEN_POSITIONS)))            # $3,000 budget
MAX_DAILY_LOSS_PER_COIN = float(os.environ.get("MAX_DAILY_LOSS_PER_COIN",  "100"))  # per-coin/day
MAX_DAILY_LOSS_PCT      = float(os.environ.get("MAX_DAILY_LOSS_PCT",        "5.0"))  # 5% portfolio stop

# Dynamic position sizing by signal score
# Size scales linearly from DYNAMIC_SIZE_MIN_PCT% (at MIN_SIGNAL_SCORE)
# to DYNAMIC_SIZE_MAX_PCT% (at score 100) of CAPITAL_PER_TRADE_USD.
# Set DYNAMIC_SIZING_ENABLED=false to revert to a fixed size.
DYNAMIC_SIZING_ENABLED  = os.environ.get("DYNAMIC_SIZING_ENABLED", "true").lower() == "true"
DYNAMIC_SIZE_MIN_PCT    = float(os.environ.get("DYNAMIC_SIZE_MIN_PCT",  "50"))   # % at lowest allowed score
DYNAMIC_SIZE_MAX_PCT    = float(os.environ.get("DYNAMIC_SIZE_MAX_PCT", "150"))   # % at score 100
FRAGILE_SIZE_CAP_ENABLED = os.environ.get("FRAGILE_SIZE_CAP_ENABLED", "true").lower() == "true"
FRAGILE_LOW_PRICE_THRESHOLD_USD = float(os.environ.get("FRAGILE_LOW_PRICE_THRESHOLD_USD", "0.001"))
FRAGILE_LOW_VOLUME_THRESHOLD_USD = float(os.environ.get("FRAGILE_LOW_VOLUME_THRESHOLD_USD", "1000000"))
FRAGILE_MAX_POSITION_PCT = float(os.environ.get("FRAGILE_MAX_POSITION_PCT", "30"))
FRAGILE_FULL_SIZE_MIN_SCORE = float(os.environ.get("FRAGILE_FULL_SIZE_MIN_SCORE", "90"))
FRAGILE_FULL_SIZE_MIN_CONSENSUS = int(os.environ.get("FRAGILE_FULL_SIZE_MIN_CONSENSUS", "3"))

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
RECENT_TRIGGER_CANDLES  = int(os.environ.get("RECENT_TRIGGER_CANDLES", "6"))  # don't miss moves between timer runs (30-min window at 5m candles)
COINBASE_PUBLIC_BASE    = "https://api.exchange.coinbase.com"

# Opening Range Breakout (ORB): for always-open crypto markets this uses a
# configurable UTC session anchor. 13:30 UTC matches the US equity open during
# daylight-saving time; override ORB_SESSION_START_UTC when needed.
ORB_ENABLED             = os.environ.get("ORB_ENABLED", "true").lower() == "true"
ORB_SESSION_START_UTC   = os.environ.get("ORB_SESSION_START_UTC", "13:30")
ORB_RANGE_MINUTES       = int(os.environ.get("ORB_RANGE_MINUTES", "60"))
ORB_SKIP_WEEKENDS       = os.environ.get("ORB_SKIP_WEEKENDS", "true").lower() == "true"
ORB_MIN_SCORE_TO_BUY    = float(os.environ.get("ORB_MIN_SCORE_TO_BUY", "80"))
ORB_BREAKOUT_BUFFER_PCT = float(os.environ.get("ORB_BREAKOUT_BUFFER_PCT", "0.10"))
ORB_MAX_OVEREXTENSION   = float(os.environ.get("ORB_MAX_OVEREXTENSION", "1.50"))
ORB_VOL_RATIO_MIN       = float(os.environ.get("ORB_VOL_RATIO_MIN", "1.50"))

# Breakout execution guard: prefer buying the retest of support rather than the
# first expansion candle. This keeps entries near the range high / new support
# and places the initial stop under structure instead of near the breakout wick.
RETEST_ENTRY_REQUIRED   = os.environ.get("RETEST_ENTRY_REQUIRED", "true").lower() == "true"
RETEST_BUY_ZONE_PCT     = float(os.environ.get("RETEST_BUY_ZONE_PCT", "0.45"))
RETEST_ACCEPT_BUFFER_PCT = float(os.environ.get("RETEST_ACCEPT_BUFFER_PCT", "0.10"))
RETEST_STOP_BUFFER_PCT  = float(os.environ.get("RETEST_STOP_BUFFER_PCT", "0.35"))
RETEST_SCORE_BONUS      = float(os.environ.get("RETEST_SCORE_BONUS", "8.0"))
MAX_STRUCTURAL_STOP_PCT = float(os.environ.get("MAX_STRUCTURAL_STOP_PCT", "3.8"))
LIMIT_ENTRY_ENABLED     = os.environ.get("LIMIT_ENTRY_ENABLED", "true").lower() == "true"
LIMIT_ENTRY_EXPIRY_MINUTES = int(os.environ.get("LIMIT_ENTRY_EXPIRY_MINUTES", "180"))
LIMIT_ENTRY_FILL_BUFFER_PCT = float(os.environ.get("LIMIT_ENTRY_FILL_BUFFER_PCT", "0.12"))
LIMIT_ENTRY_PRICE_MODE  = os.environ.get("LIMIT_ENTRY_PRICE_MODE", "support").strip().lower()
LIMIT_ENTRY_MIN_AGE_MINUTES = int(os.environ.get("LIMIT_ENTRY_MIN_AGE_MINUTES", "20"))
LIMIT_ENTRY_MISS_BREAKOUT_PCT = float(os.environ.get("LIMIT_ENTRY_MISS_BREAKOUT_PCT", "1.2"))
LIMIT_ENTRY_MISS_BREAKOUT_ATR_MULTIPLIER = float(os.environ.get("LIMIT_ENTRY_MISS_BREAKOUT_ATR_MULTIPLIER", "0.75"))
LIMIT_ENTRY_MISS_BREAKOUT_MAX_PCT = float(os.environ.get("LIMIT_ENTRY_MISS_BREAKOUT_MAX_PCT", "4.0"))

# Bollinger mean-reversion entries. The bot only opens long positions, so lower
# band snapback setups are buyable; upper band extensions are recorded as a
# bearish/unsupported reversal signal and are not used for long entries.
BOLLINGER_ENABLED       = os.environ.get("BOLLINGER_ENABLED", "true").lower() == "true"
BOLLINGER_PERIOD        = int(os.environ.get("BOLLINGER_PERIOD", "20"))
BOLLINGER_STDDEV        = float(os.environ.get("BOLLINGER_STDDEV", "2.0"))
BOLLINGER_MIN_SCORE_TO_BUY = float(os.environ.get("BOLLINGER_MIN_SCORE_TO_BUY", "80"))
BOLLINGER_MIN_EXTREME_PCT  = float(os.environ.get("BOLLINGER_MIN_EXTREME_PCT", "0.20"))
BOLLINGER_MAX_DISTANCE_FROM_MID_PCT = float(os.environ.get("BOLLINGER_MAX_DISTANCE_FROM_MID_PCT", "4.0"))
BOLLINGER_VOLUME_CLIMAX_RATIO = float(os.environ.get("BOLLINGER_VOLUME_CLIMAX_RATIO", "2.2"))

# Descending-wedge breakout + RSI bullish divergence (uses 1h candles).
# A descending wedge forms when both highs and lows trend down but highs fall
# faster than lows, narrowing the channel. A volume-confirmed close above the
# upper trendline with RSI divergence is a high-probability reversal entry.
WEDGE_ENABLED           = os.environ.get("WEDGE_ENABLED", "true").lower() == "true"
WEDGE_CANDLE_LOOKBACK   = int(os.environ.get("WEDGE_CANDLE_LOOKBACK",   "40"))   # 1h candles
WEDGE_VOL_RATIO_MIN     = float(os.environ.get("WEDGE_VOL_RATIO_MIN",   "1.4"))  # vol burst
WEDGE_MIN_SCORE_TO_BUY  = float(os.environ.get("WEDGE_MIN_SCORE_TO_BUY", "80"))  # entry bar
WEDGE_MAX_OVEREXTENSION_PCT = float(os.environ.get("WEDGE_MAX_OVEREXTENSION_PCT", "1.5"))  # don't chase
RSI_PERIOD              = int(os.environ.get("RSI_PERIOD", "14"))

# Liquidity / market-value guard. Coinbase public candles do not include market
# cap, so traded USD value is the practical proxy: price * volume. This avoids
# buying thin coins where a breakout candle is easy to spoof or hard to exit.
MIN_24H_DOLLAR_VOLUME   = float(os.environ.get("MIN_24H_DOLLAR_VOLUME", "5000000"))
MIN_BREAKOUT_DOLLAR_VOLUME = float(os.environ.get("MIN_BREAKOUT_DOLLAR_VOLUME", "25000"))
RECENT_LIQUIDITY_WINDOW_CANDLES = int(os.environ.get("RECENT_LIQUIDITY_WINDOW_CANDLES", "6"))
HIGH_CONSENSUS_MIN_COUNT = int(os.environ.get("HIGH_CONSENSUS_MIN_COUNT", "3"))
HIGH_CONSENSUS_BREAKOUT_VOLUME_DISCOUNT = float(os.environ.get("HIGH_CONSENSUS_BREAKOUT_VOLUME_DISCOUNT", "0.80"))

# Early momentum runners catch coins that are already moving hard intraday, but
# have not formed the exact compression/breakout shape yet. These are riskier,
# so they require recent dollar flow and OBV, and can use a lower 24h liquidity
# floor only for this strategy.
MOMENTUM_RUNNER_ENABLED = os.environ.get("MOMENTUM_RUNNER_ENABLED", "true").lower() == "true"
MOMENTUM_RUNNER_MIN_SCORE_TO_BUY = float(os.environ.get("MOMENTUM_RUNNER_MIN_SCORE_TO_BUY", "78"))
MOMENTUM_RUNNER_MIN_24H_CHANGE = float(os.environ.get("MOMENTUM_RUNNER_MIN_24H_CHANGE", "8.0"))
MOMENTUM_RUNNER_MAX_24H_CHANGE = float(os.environ.get("MOMENTUM_RUNNER_MAX_24H_CHANGE", "40.0"))
MOMENTUM_RUNNER_MIN_15M_CHANGE = float(os.environ.get("MOMENTUM_RUNNER_MIN_15M_CHANGE", "0.4"))
MOMENTUM_RUNNER_MIN_1H_CHANGE = float(os.environ.get("MOMENTUM_RUNNER_MIN_1H_CHANGE", "1.2"))
MOMENTUM_RUNNER_MIN_RECENT_DOLLAR_VOLUME = float(os.environ.get("MOMENTUM_RUNNER_MIN_RECENT_DOLLAR_VOLUME", "25000"))
MOMENTUM_RUNNER_MIN_24H_DOLLAR_VOLUME = float(os.environ.get("MOMENTUM_RUNNER_MIN_24H_DOLLAR_VOLUME", "2500000"))
MOMENTUM_RUNNER_MAX_POSITION_PCT = float(os.environ.get("MOMENTUM_RUNNER_MAX_POSITION_PCT", "75"))
WATCHLIST_MIN_DOLLAR_VOLUME = float(os.environ.get(
    "WATCHLIST_MIN_DOLLAR_VOLUME",
    str(MOMENTUM_RUNNER_MIN_24H_DOLLAR_VOLUME),
))

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
HIGH_CONSENSUS_MTF_TOLERANCE = float(os.environ.get("HIGH_CONSENSUS_MTF_TOLERANCE", "5"))
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
LIVE_ALLOWED_PRODUCTS = {
    item.strip().upper()
    for item in os.environ.get("LIVE_ALLOWED_PRODUCTS", "").split(",")
    if item.strip()
}

# Minimum breakout pattern score (0-100) required to BUY. Default 80 so the bot
# only enters strong setups instead of buying everything that ticks up.
MIN_PATTERN_SCORE_TO_BUY = float(os.environ.get("MIN_PATTERN_SCORE_TO_BUY", "80"))

# Consensus scoring: when multiple independent strategies all agree it's a good
# entry, confidence is higher. A strategy is "confirming" when its score is at
# or above CONSENSUS_AGREEING_THRESHOLD even if it hasn't reached the buy bar.
# Lone signals need a higher bar (+5); dual/triple agreement earns a bonus.
CONSENSUS_AGREEING_THRESHOLD    = float(os.environ.get("CONSENSUS_AGREEING_THRESHOLD",    "40"))  # score that counts as "seeing something"
CONSENSUS_SINGLE_THRESHOLD_BUMP = float(os.environ.get("CONSENSUS_SINGLE_THRESHOLD_BUMP", "5"))   # lone signal needs +5 above normal threshold
CONSENSUS_DUAL_BONUS            = float(os.environ.get("CONSENSUS_DUAL_BONUS",            "8"))   # +8 pts when 2 strategies agree
CONSENSUS_TRIPLE_BONUS          = float(os.environ.get("CONSENSUS_TRIPLE_BONUS",          "15"))  # +15 pts when 3+ strategies agree
ENABLED_ENTRY_STRATEGIES = {
    item.strip().upper().replace("-", "_").replace(" ", "_")
    for item in os.environ.get("ENABLED_ENTRY_STRATEGIES", "").split(",")
    if item.strip()
}

TAKE_PROFIT_PERCENT   = float(os.environ.get("TAKE_PROFIT_PERCENT",  "15.0")) # legacy/default first target
NORMAL_TAKE_PROFIT_PERCENT = float(os.environ.get("NORMAL_TAKE_PROFIT_PERCENT", "12.0"))
RUNNER_TAKE_PROFIT_PERCENT = float(os.environ.get("RUNNER_TAKE_PROFIT_PERCENT", "18.0"))
WEDGE_TAKE_PROFIT_PERCENT = float(os.environ.get("WEDGE_TAKE_PROFIT_PERCENT", "20.0"))
HIGH_CONSENSUS_TAKE_PROFIT_PERCENT = float(os.environ.get("HIGH_CONSENSUS_TAKE_PROFIT_PERCENT", "15.0"))
HIGH_CONSENSUS_TP_MIN_COUNT = int(os.environ.get("HIGH_CONSENSUS_TP_MIN_COUNT", "3"))
HIGH_CONSENSUS_TP_MIN_SCORE = float(os.environ.get("HIGH_CONSENSUS_TP_MIN_SCORE", "90.0"))
TRAILING_PERCENT      = float(os.environ.get("TRAILING_PERCENT",     "5.0"))  # 5% trailing floor
MOON_BAG_PERCENT      = max(0.0, min(100.0, float(os.environ.get("MOON_BAG_PERCENT", "30.0"))))
TAKE_PROFIT_SELL_PERCENT = 100.0 - MOON_BAG_PERCENT
QUICK_TAKE_PROFIT_ENABLED = os.environ.get("QUICK_TAKE_PROFIT_ENABLED", "true").lower() == "true"
QUICK_TAKE_PROFIT_PERCENT = float(os.environ.get("QUICK_TAKE_PROFIT_PERCENT", "4.0"))
QUICK_TAKE_PROFIT_SELL_PERCENT = max(0.0, min(100.0, float(os.environ.get("QUICK_TAKE_PROFIT_SELL_PERCENT", "25"))))
LOOP_INTERVAL_SECONDS = 300     # 5-minute loop

# Dynamic trailing stop. A flat 5% trail shakes you out of big runners on the
# first pullback, so the trail WIDENS as unrealized profit grows and can also
# adapt to each coin's volatility via ATR. Goal: protect capital early, then
# give proven winners room to run toward 100%+.
#
# TRAIL_TIERS: "profit_pct:trail_pct" pairs. The trail floor steps up as the
# position's unrealized gain crosses each profit threshold. Below the first
# threshold the base TRAILING_PERCENT is used.
#   0:5   -> up to +15%: 5% trail (tight, protect capital)
#   15:10 -> +15% to +40%: 10% trail
#   40:18 -> +40% to +100%: 18% trail
#   100:25 -> +100%+: 25% trail (let the moonshot breathe)
TIERED_TRAILING_ENABLED = os.environ.get("TIERED_TRAILING_ENABLED", "true").lower() == "true"
TRAIL_TIERS_RAW         = os.environ.get("TRAIL_TIERS", "0:5,15:10,40:18,100:25")
TRAIL_MAX_PCT           = float(os.environ.get("TRAIL_MAX_PCT", "30.0"))  # hard ceiling on any trail

# ATR (Average True Range) volatility-adaptive trailing. When enabled, the trail
# is max(tier floor, ATR_MULT x ATR%), capped at TRAIL_MAX_PCT — so a volatile
# coin automatically gets a wider stop and a calm coin a tighter one.
ATR_TRAILING_ENABLED    = os.environ.get("ATR_TRAILING_ENABLED", "true").lower() == "true"
ATR_PERIOD              = int(os.environ.get("ATR_PERIOD", "14"))
ATR_TRAIL_MULTIPLIER    = float(os.environ.get("ATR_TRAIL_MULTIPLIER", "3.0"))
ATR_CANDLE_GRANULARITY  = int(os.environ.get("ATR_CANDLE_GRANULARITY", "3600"))  # 1h candles


def _parse_trail_tiers(raw: str) -> list[tuple[float, float]]:
    """Parses 'profit_pct:trail_pct' pairs into a sorted [(profit, trail), ...] list."""
    tiers: list[tuple[float, float]] = []
    for pair in raw.split(","):
        if ":" not in pair:
            continue
        profit_s, trail_s = pair.split(":", 1)
        try:
            tiers.append((float(profit_s), float(trail_s)))
        except ValueError:
            continue
    tiers.sort(key=lambda item: item[0])
    return tiers or [(0.0, TRAILING_PERCENT)]


TRAIL_TIERS = _parse_trail_tiers(TRAIL_TIERS_RAW)


# Bearish-reversal exit: sell when the trend flips bearish on candles.
BEARISH_DROP_PCT      = float(os.environ.get("BEARISH_DROP_PCT", "1.5"))   # red candle size
BEARISH_VOL_RATIO     = float(os.environ.get("BEARISH_VOL_RATIO", "1.5"))  # vol confirms selling
BEARISH_REVERSAL_MIN_CONFIRMATIONS = int(os.environ.get("BEARISH_REVERSAL_MIN_CONFIRMATIONS", "2"))

# Exit quality controls. Without these, a noisy 5m bearish pattern can close a
# $1000 position for only a few dollars before the setup has room to work.
BREAKEVEN_STOP_ENABLED = os.environ.get("BREAKEVEN_STOP_ENABLED", "true").lower() == "true"
BREAKEVEN_TRIGGER_PCT  = float(os.environ.get("BREAKEVEN_TRIGGER_PCT", "2.0"))
BREAKEVEN_BUFFER_PCT   = float(os.environ.get("BREAKEVEN_BUFFER_PCT", "0.15"))
BEARISH_EXIT_MIN_PROFIT_PCT = float(os.environ.get("BEARISH_EXIT_MIN_PROFIT_PCT", "3.0"))
BEARISH_EXIT_MAX_LOSS_PCT   = float(os.environ.get("BEARISH_EXIT_MAX_LOSS_PCT", "-0.75"))
BEARISH_EXIT_SEVERE_MIN_PROFIT_PCT = float(os.environ.get("BEARISH_EXIT_SEVERE_MIN_PROFIT_PCT", "8.0"))
BEARISH_EXIT_MIN_HOLD_MINUTES = int(os.environ.get("BEARISH_EXIT_MIN_HOLD_MINUTES", "150"))
BEARISH_EXIT_STALL_PROFIT_PCT = float(os.environ.get("BEARISH_EXIT_STALL_PROFIT_PCT", "0.75"))
POST_TP_BEARISH_EXIT_MIN_GAIN_PCT = float(os.environ.get("POST_TP_BEARISH_EXIT_MIN_GAIN_PCT", "6.0"))

# Periodic Discord performance summary (total P/L, gain/loss, coins bought).
SUMMARY_INTERVAL_HOURS = float(os.environ.get("SUMMARY_INTERVAL_HOURS", "6"))
DAILY_SUMMARY_ENABLED  = os.environ.get("DAILY_SUMMARY_ENABLED", "true").lower() == "true"
DAILY_SUMMARY_UTC_HOUR = int(os.environ.get("DAILY_SUMMARY_UTC_HOUR", "23"))
SHADOW_ALERTS_ENABLED  = os.environ.get("SHADOW_ALERTS_ENABLED", "true").lower() == "true"
SHADOW_ALERT_COOLDOWN_HOURS = float(os.environ.get("SHADOW_ALERT_COOLDOWN_HOURS", "12"))
SHADOW_ALERT_MAX_PER_CYCLE = int(os.environ.get("SHADOW_ALERT_MAX_PER_CYCLE", "5"))

# Where state files are written. On Azure Functions the app folder is read-only,
# so point DATA_DIR at a writable, persisted path (e.g. /home/data) via env var.
DATA_DIR = os.environ.get("DATA_DIR", "").strip()

PORTFOLIO_FILE = "active_paper_positions.json"
PENDING_ENTRY_FILE = "pending_entry_orders.json"
HISTORY_FILE   = "trading_history.json"
MARKET_STATE_FILE = "market_state_cache.json"
DAILY_PNL_FILE = "daily_pnl_ledger.json"
SUMMARY_STATE_FILE = "summary_state.json"
DAILY_SUMMARY_STATE_FILE = "daily_summary_state.json"
SHADOW_ALERTS_FILE = "shadow_signal_alerts.json"
SCAN_SNAPSHOT_FILE = "crypto_scan_snapshot.json"

# ---------------------------------------------------------------------------
# Blob Storage fallback — survives function restarts on Consumption plan
# Uses STATE_STORAGE_CONNECTION_STRING (or AzureWebJobsStorage) +
# STATE_CONTAINER_NAME to persist all state files.
# ---------------------------------------------------------------------------
_BLOB_CONN_STR = (
    os.environ.get("STATE_STORAGE_CONNECTION_STRING")
    or os.environ.get("AzureWebJobsStorage", "")
)
_BLOB_CONTAINER = os.environ.get("STATE_CONTAINER_NAME", "cointracking-state")
_BLOB_PRIMARY = os.environ.get("STATE_BLOB_PRIMARY", "true").lower() != "false"
_STATE_RECONCILE_LOCAL_NEWER = os.environ.get("STATE_RECONCILE_LOCAL_NEWER", "false").lower() == "true"
_STATE_BACKUP_ENABLED = os.environ.get("STATE_BACKUP_ENABLED", "true").lower() != "false"
_STATE_BACKUP_PREFIX = os.environ.get("STATE_BACKUP_PREFIX", "trader-state-backups")
_STATE_BACKUP_MIN_INTERVAL_SECONDS = int(os.environ.get("STATE_BACKUP_MIN_INTERVAL_SECONDS", "900"))
_BLOB_REST_TIMEOUT_SECONDS = int(os.environ.get("BLOB_REST_TIMEOUT_SECONDS", "15"))
_STATE_BACKUP_FILES = {
    PORTFOLIO_FILE,
    PENDING_ENTRY_FILE,
    HISTORY_FILE,
    DAILY_PNL_FILE,
    SUMMARY_STATE_FILE,
    DAILY_SUMMARY_STATE_FILE,
}
_last_state_backup_epoch: dict[str, int] = {}

def _blob_name(filepath: str) -> str:
    return "trader-state/" + os.path.basename(filepath)

def _state_backup_name(filepath: str) -> str:
    now = datetime.now(timezone.utc)
    basename = os.path.basename(filepath)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    return f"{_STATE_BACKUP_PREFIX}/{now:%Y-%m-%d}/{basename}/{stamp}.json"

def _write_local_cache(filepath: str, data) -> None:
    try:
        directory = os.path.dirname(filepath)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=4)
    except OSError:
        pass

def _read_local_json(filepath: str):
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None

def _state_record_count(data) -> int:
    if isinstance(data, (dict, list)):
        return len(data)
    return 0

def _local_is_newer_than_blob(filepath: str, blob_last_modified) -> bool:
    if not blob_last_modified:
        return False
    try:
        local_modified = datetime.fromtimestamp(os.path.getmtime(filepath), timezone.utc)
    except OSError:
        return False
    return local_modified > blob_last_modified + timedelta(seconds=30)

def _should_promote_local_state(filepath: str, local_data, blob_data, blob_last_modified) -> bool:
    if not _STATE_RECONCILE_LOCAL_NEWER or local_data is None:
        return False
    local_count = _state_record_count(local_data)
    blob_count = _state_record_count(blob_data)
    if local_count <= 0:
        return False
    if blob_count <= 0:
        return True
    basename = os.path.basename(filepath)
    if basename == HISTORY_FILE and local_count > blob_count:
        return True
    return _local_is_newer_than_blob(filepath, blob_last_modified)

def _load_from_blob(filepath: str, write_cache: bool = True):
    if not _BLOB_CONN_STR:
        return None
    data, _ = _load_from_blob_with_metadata(filepath)
    if data is not None and write_cache:
        _write_local_cache(filepath, data)
    return data

def _load_from_blob_with_metadata(filepath: str):
    if not _BLOB_CONN_STR:
        return None, None
    try:
        from azure.storage.blob import BlobServiceClient
        client = BlobServiceClient.from_connection_string(_BLOB_CONN_STR)
        blob = client.get_blob_client(container=_BLOB_CONTAINER, blob=_blob_name(filepath))
        raw = blob.download_blob().readall().decode("utf-8")
        data = json.loads(raw)
        try:
            properties = blob.get_blob_properties()
            return data, properties.last_modified
        except Exception:
            return data, None
    except Exception as exc:
        print(f"[Blob] load failed for {os.path.basename(filepath)}: {exc}")
        return _load_from_blob_rest(filepath), None

def _parse_blob_connection_string() -> dict[str, str]:
    parts: dict[str, str] = {}
    for item in _BLOB_CONN_STR.split(";"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parts[key] = value
    return parts

def _blob_rest_request(method: str, blob_name: str, body: bytes | None = None) -> bytes:
    parts = _parse_blob_connection_string()
    account = parts.get("AccountName", "")
    account_key = parts.get("AccountKey", "")
    endpoint_suffix = parts.get("EndpointSuffix", "core.windows.net")
    if not account or not account_key:
        raise RuntimeError("Storage connection string is missing AccountName or AccountKey.")

    body = body or b""
    encoded_blob_name = "/".join(urllib.parse.quote(part, safe="") for part in blob_name.split("/"))
    url = f"https://{account}.blob.{endpoint_suffix}/{_BLOB_CONTAINER}/{encoded_blob_name}"
    x_ms_date = formatdate(usegmt=True)
    x_ms_version = "2023-11-03"
    headers = {
        "x-ms-date": x_ms_date,
        "x-ms-version": x_ms_version,
    }
    content_length = ""
    if method in {"PUT", "POST"}:
        content_length = str(len(body))
        headers["Content-Length"] = content_length
    if method == "PUT":
        headers["x-ms-blob-type"] = "BlockBlob"
        headers["Content-Type"] = "application/json"

    canonicalized_headers = "".join(
        f"{key}:{headers[key]}\n"
        for key in sorted(headers)
        if key.lower().startswith("x-ms-")
    )
    canonicalized_resource = f"/{account}/{_BLOB_CONTAINER}/{blob_name}"
    string_to_sign = (
        f"{method}\n\n\n{content_length}\n\n"
        f"{headers.get('Content-Type', '')}\n\n\n\n\n\n\n"
        f"{canonicalized_headers}{canonicalized_resource}"
    )
    signature = base64.b64encode(
        hmac.new(
            base64.b64decode(account_key),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).decode("ascii")
    headers["Authorization"] = f"SharedKey {account}:{signature}"

    request = urllib.request.Request(url, data=body if body else None, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=_BLOB_REST_TIMEOUT_SECONDS) as response:
        return response.read()

def _load_from_blob_rest(filepath: str):
    try:
        raw = _blob_rest_request("GET", _blob_name(filepath))
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        print(f"[Blob REST] load failed for {os.path.basename(filepath)}: {exc}")
        return None

def _backup_existing_blob(container, blob, filepath: str, new_payload: str) -> None:
    if not _STATE_BACKUP_ENABLED or os.path.basename(filepath) not in _STATE_BACKUP_FILES:
        return
    now = _utcnow_epoch()
    blob_key = _blob_name(filepath)
    last_backup = _last_state_backup_epoch.get(blob_key, 0)
    if last_backup and now - last_backup < _STATE_BACKUP_MIN_INTERVAL_SECONDS:
        return
    try:
        if not blob.exists():
            return
        current_payload = blob.download_blob().readall().decode("utf-8")
        if not current_payload or current_payload == new_payload:
            return
        backup = container.get_blob_client(_state_backup_name(filepath))
        backup.upload_blob(current_payload, overwrite=False)
        _last_state_backup_epoch[blob_key] = now
    except Exception as exc:
        print(f"[Blob] backup failed for {os.path.basename(filepath)}: {exc}")

def _save_to_blob(filepath: str, data) -> None:
    if not _BLOB_CONN_STR:
        return
    try:
        from azure.storage.blob import BlobServiceClient, ContainerClient
        client = BlobServiceClient.from_connection_string(_BLOB_CONN_STR)
        container: ContainerClient = client.get_container_client(_BLOB_CONTAINER)
        if not container.exists():
            container.create_container()
        blob = container.get_blob_client(_blob_name(filepath))
        payload = json.dumps(data, indent=4)
        _backup_existing_blob(container, blob, filepath, payload)
        blob.upload_blob(payload, overwrite=True)
    except Exception as exc:
        print(f"[Blob] save failed for {os.path.basename(filepath)}: {exc}")
        _save_to_blob_rest(filepath, data)

def _save_to_blob_rest(filepath: str, data) -> None:
    try:
        payload = json.dumps(data, indent=4).encode("utf-8")
        _blob_rest_request("PUT", _blob_name(filepath), payload)
    except Exception as exc:
        print(f"[Blob REST] save failed for {os.path.basename(filepath)}: {exc}")


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

    LIVE trading strictly requires CB_API_KEY/CB_API_SECRET or the equivalent
    COINBASE_API_KEY/COINBASE_API_SECRET aliases (Trade permission).
    PAPER trading uses authenticated data when keys exist, otherwise falls back
    to Coinbase's public market-data endpoints (no keys needed).
    """
    if LIVE_ORDERS_ACTIVE:
        missing = [name for name, val in [
            ("CB_API_KEY or COINBASE_API_KEY",       API_KEY),
            ("CB_API_SECRET or COINBASE_API_SECRET", API_SECRET),
        ] if not val]
        if missing:
            raise EnvironmentError(
                f"LIVE trading requires: {', '.join(missing)}\n"
                "  export CB_API_KEY='...'       # Coinbase Advanced Trade key, needs Trade permission\n"
                "  export CB_API_SECRET='...'    # matching Coinbase Advanced Trade private key/secret\n"
                "  export TRADING_MODE='live'\n"
                "  export LIVE_TRADING_ENABLED='true'"
            )
        if CAPITAL_PER_TRADE_USD <= 0 or MAX_OPEN_POSITIONS <= 0 or TOTAL_CAPITAL_USD <= 0:
            raise EnvironmentError("LIVE trading requires positive CAPITAL_PER_TRADE_USD, MAX_OPEN_POSITIONS, and TOTAL_CAPITAL_USD.")
        print("[Config] \u26a0\ufe0f  LIVE TRADING ACTIVE \u2014 real Coinbase orders will be placed "
              f"(${CAPITAL_PER_TRADE_USD:,.0f}/coin, max {MAX_OPEN_POSITIONS} coins).")
        if LIVE_ALLOWED_PRODUCTS:
            print(f"[Config] LIVE_ALLOWED_PRODUCTS enabled: {', '.join(sorted(LIVE_ALLOWED_PRODUCTS))}")
        return

    if TRADING_MODE == "live" and not LIVE_TRADING_ENABLED:
        print("[Config] TRADING_MODE=live but LIVE_TRADING_ENABLED is not 'true' "
              "\u2014 staying in SIMULATION. Set LIVE_TRADING_ENABLED=true to place real orders.")

    if API_KEY and API_SECRET:
        print("[Config] Paper mode \u2014 simulated orders, using authenticated Coinbase data.")
    else:
        print("[Config] Paper mode \u2014 simulated orders, using PUBLIC Coinbase market data "
              "(no API keys set).")


_DISCORD_MAX_CHARS = 1900  # Discord limit is 2000; stay under with margin

def send_discord_alert(message: str, retries: int = 3) -> bool:
    """Posts a trade alert to Discord via webhook.
    - Truncates messages over Discord's 2000-char limit.
    - Retries up to 3× on HTTP 429 using the retry_after header.
    - Adds a short sleep after each successful send to avoid rate-limit bursts.
    - Never raises; returns True on success.
    """
    if not DISCORD_WEBHOOK_URL:
        print("[Discord] DISCORD_WEBHOOK_URL is not set. Alert not sent.")
        return False
    if len(message) > _DISCORD_MAX_CHARS:
        message = message[:_DISCORD_MAX_CHARS] + "\n…(truncated)"
    payload = json.dumps({"content": message}).encode("utf-8")
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 — URL from env only
                if resp.status in (200, 204):
                    time.sleep(1.0)  # stay well under Discord's 30 msg/60s webhook limit
                    return True
                print(f"[Discord] Unexpected status {resp.status}")
                return False
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                try:
                    body = json.loads(exc.read().decode("utf-8"))
                    wait = float(body.get("retry_after", 5))
                except Exception:
                    wait = 5.0
                print(f"[Discord] Rate limited; retrying in {wait:.1f}s (attempt {attempt + 1}/{retries})")
                time.sleep(wait)
                continue
            print(f"[Discord] HTTP error {exc.code}: {exc}")
            return False
        except Exception as exc:
            print(f"[Discord] Alert failed: {exc}")
            return False
    print("[Discord] Gave up after retries.")
    return False


def load_json_file(filepath: str):
    """Loads JSON state. In Azure, Blob is the source of truth; disk is cache."""
    filepath = _data_path(filepath)
    local_data = _read_local_json(filepath)
    if _BLOB_PRIMARY:
        blob_data, blob_last_modified = _load_from_blob_with_metadata(filepath)
        if blob_data is not None:
            if _should_promote_local_state(filepath, local_data, blob_data, blob_last_modified):
                print(f"[State] Promoting newer local {os.path.basename(filepath)} to blob storage.")
                _save_to_blob(filepath, local_data)
                return local_data
            _write_local_cache(filepath, blob_data)
            return blob_data
    if local_data is not None:
        return local_data
    if not _BLOB_PRIMARY:
        blob_data = _load_from_blob(filepath)
        if blob_data is not None:
            return blob_data
    return [] if "history" in filepath else {}


def save_json_file(filepath: str, data):
    """Atomically writes JSON to local disk and mirrors to blob storage."""
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
    # Write-through to blob so state survives instance restarts
    _save_to_blob(filepath, data)


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
    """Adds realized PnL for a coin and blocks it for the day if it breaches the per-coin cap."""
    realized = ledger.setdefault("realized", {})
    realized[product_id] = round(realized.get(product_id, 0.0) + pnl_usd, 4)

    # Block the individual coin if it exceeds the per-coin daily loss cap.
    if realized[product_id] <= -abs(MAX_DAILY_LOSS_PER_COIN):
        blocked = ledger.setdefault("blocked", [])
        if product_id not in blocked:
            blocked.append(product_id)
            print(f"  [Coin Stop] {product_id} hit ${realized[product_id]:+.2f} today "
                  f"(cap -${MAX_DAILY_LOSS_PER_COIN:.0f}). Blocked until next UTC day.")

    # Check portfolio-wide daily loss cap.
    total_loss = sum(v for v in realized.values() if v < 0)
    portfolio_cap = -(MAX_DAILY_LOSS_PCT / 100.0) * TOTAL_CAPITAL_USD
    if total_loss <= portfolio_cap and not ledger.get("portfolio_stopped"):
        ledger["portfolio_stopped"] = True
        print(f"  [Portfolio Stop] Daily loss ${total_loss:+.2f} exceeded "
              f"{MAX_DAILY_LOSS_PCT:.0f}% cap (${portfolio_cap:+.2f}). "
              f"No new trades until next UTC day.")
        send_discord_alert(
            f"🛑 **Portfolio Daily Stop Hit**\n"
            f"Total realized loss today: ${total_loss:+.2f}\n"
            f"Limit: {MAX_DAILY_LOSS_PCT:.0f}% of ${TOTAL_CAPITAL_USD:,.0f} = ${portfolio_cap:+.2f}\n"
            f"No new entries until next UTC day ({_today_str()})."
        )


def is_coin_blocked_today(ledger: dict, product_id: str) -> bool:
    """True if the coin has breached its daily loss cap and is paused for the day."""
    return product_id in ledger.get("blocked", [])


def is_portfolio_stopped_today(ledger: dict) -> bool:
    """True if the portfolio-wide daily loss cap has been hit — no new entries."""
    return bool(ledger.get("portfolio_stopped", False))

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


def normalize_entry_strategy_name(value: str) -> str:
    """Returns a stable strategy label for reporting and optional filtering."""
    name = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "BREAKOUT": "CANDLE_BREAKOUT",
        "CANDLE": "CANDLE_BREAKOUT",
        "PRE_BREAKOUT": "CANDLE_BREAKOUT",
        "CANDLE_BREAKOUT": "CANDLE_BREAKOUT",
        "ORB": "OPENING_RANGE_BREAKOUT",
        "ORB_BREAKOUT": "OPENING_RANGE_BREAKOUT",
        "OPENING_RANGE": "OPENING_RANGE_BREAKOUT",
        "OPENING_RANGE_BREAKOUT": "OPENING_RANGE_BREAKOUT",
        "BOLLINGER": "BOLLINGER_LOWER_BAND_REVERSAL",
        "BOLLINGER_REVERSAL": "BOLLINGER_LOWER_BAND_REVERSAL",
        "BOLLINGER_LOWER_BAND_REVERSAL": "BOLLINGER_LOWER_BAND_REVERSAL",
        "WEDGE": "DESCENDING_WEDGE_BREAKOUT",
        "WEDGE_BREAKOUT": "DESCENDING_WEDGE_BREAKOUT",
        "DESCENDING_WEDGE": "DESCENDING_WEDGE_BREAKOUT",
        "DESCENDING_WEDGE_BREAKOUT": "DESCENDING_WEDGE_BREAKOUT",
        "MOMENTUM_RUNNER": "EARLY_MOMENTUM_RUNNER",
        "EARLY_MOMENTUM": "EARLY_MOMENTUM_RUNNER",
        "EARLY_MOMENTUM_RUNNER": "EARLY_MOMENTUM_RUNNER",
        "MOMENTUM_24H": "24H_MOMENTUM_VOLUME",
        "24H_MOMENTUM": "24H_MOMENTUM_VOLUME",
        "24H_MOMENTUM_VOLUME": "24H_MOMENTUM_VOLUME",
        "MOMENTUM_VOLUME": "24H_MOMENTUM_VOLUME",
    }
    return aliases.get(name, name or "UNKNOWN")


def enabled_entry_strategy_names() -> list[str]:
    return sorted(normalize_entry_strategy_name(item) for item in ENABLED_ENTRY_STRATEGIES)


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


def recent_dollar_volume(candles: list[list], window: int = RECENT_LIQUIDITY_WINDOW_CANDLES) -> float:
    """Returns traded USD value across the most recent N candles."""
    if not candles:
        return 0.0
    selected = candles[-max(1, window):]
    return sum(float(c[4] or 0.0) * float(c[5] or 0.0) for c in selected)


def analyze_breakout_retest(
    candles: list[list],
    breakout_idx: int,
    breakout_level: float,
    support_floor: float,
) -> dict:
    """
    Evaluates whether a breakout has retested prior resistance and held.
    Returns structural levels plus an execution-ready buy zone and stop anchor.
    """
    if breakout_level <= 0 or support_floor <= 0 or breakout_idx < 0 or breakout_idx >= len(candles):
        return {}

    latest_close = float(candles[-1][4] or 0.0)
    if latest_close <= 0:
        return {}

    buy_zone_low = breakout_level * (1 - RETEST_BUY_ZONE_PCT / 100.0)
    buy_zone_high = breakout_level * (1 + RETEST_BUY_ZONE_PCT / 100.0)
    acceptance_floor = breakout_level * (1 - RETEST_ACCEPT_BUFFER_PCT / 100.0)
    structural_stop = support_floor * (1 - RETEST_STOP_BUFFER_PCT / 100.0)

    retest_touched = False
    retest_holding = False
    retest_low = 0.0
    retest_close = 0.0
    retest_age = len(candles)
    invalidated = False

    for idx in range(breakout_idx + 1, len(candles)):
        low = float(candles[idx][1] or 0.0)
        high = float(candles[idx][2] or 0.0)
        close = float(candles[idx][4] or 0.0)
        if low <= 0 or high <= 0 or close <= 0:
            continue

        if low < structural_stop:
            invalidated = True

        touched_zone = low <= buy_zone_high and high >= buy_zone_low
        if touched_zone and not retest_touched:
            retest_touched = True
            retest_low = low
            retest_close = close
            retest_age = len(candles) - 1 - idx
            retest_holding = close >= acceptance_floor and low >= structural_stop

    in_buy_zone = buy_zone_low <= latest_close <= buy_zone_high
    accepted_above_level = latest_close >= acceptance_floor
    successful_retest = retest_touched and retest_holding and accepted_above_level and not invalidated
    stop_distance_pct = ((latest_close - structural_stop) / latest_close * 100) if latest_close > structural_stop else 999.0

    return {
        "support_level": breakout_level,
        "support_floor": support_floor,
        "resistance_level": breakout_level,
        "buy_zone_low": round(buy_zone_low, 8),
        "buy_zone_high": round(buy_zone_high, 8),
        "latest_close": round(latest_close, 8),
        "accepted_above_level": accepted_above_level,
        "retest_touched": retest_touched,
        "retest_holding": retest_holding,
        "successful_retest": successful_retest,
        "retest_age_candles": retest_age if retest_touched else None,
        "retest_low": round(retest_low, 8) if retest_low else 0.0,
        "retest_close": round(retest_close, 8) if retest_close else 0.0,
        "invalidated_below_support": invalidated,
        "entry_in_buy_zone": in_buy_zone,
        "structural_stop": round(structural_stop, 8),
        "structural_stop_distance_pct": round(stop_distance_pct, 3),
    }


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
        "base_low": base_low,
        "support_level": base_high,
        "support_floor": base_low,
        "resistance_level": base_high,
        # FVG anchors: the open is the FVG low (stop anchor), close is FVG high
        "trigger_candle_open": l_open,
        "trigger_candle_close": l_close,
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
        features = dict(result.get("features", {}))
        breakout_level = float(features.get("base_high", 0.0) or 0.0)
        support_floor = float(features.get("base_low", 0.0) or 0.0)
        retest = analyze_breakout_retest(candles, end - 1, breakout_level, support_floor)
        if retest:
            features.update(retest)
            if retest.get("successful_retest"):
                score += RETEST_SCORE_BONUS
        if score > best["pattern_score"]:
            features["trigger_age_candles"] = age
            best = {"pattern_score": round(score, 1), "features": features}
    return best


def _parse_session_start_utc(value: str) -> tuple[int, int]:
    """Parses HH:MM into UTC hour/minute, falling back to 13:30."""
    try:
        hour_s, minute_s = value.split(":", 1)
        hour = int(hour_s)
        minute = int(minute_s)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (ValueError, AttributeError):
        pass
    return 13, 30


def _current_or_previous_session_start() -> int:
    """Returns the most recent configured UTC session start as epoch seconds."""
    hour, minute = _parse_session_start_utc(ORB_SESSION_START_UTC)
    now = datetime.now(timezone.utc)
    session = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < session:
        session = session - timedelta(days=1)
    return int(session.timestamp())


def detect_orb_signal(product_id: str, candles: list[list] | None = None) -> dict:
    """
    Scores a long Opening Range Breakout from 0-100.

    The opening range is the high/low from the first ORB_RANGE_MINUTES after the
    configured UTC session start. A buyable signal requires a recent close above
    that range with volume confirmation and limited overextension.
    """
    if not ORB_ENABLED:
        return {"orb_score": 0.0, "features": {}}
    if candles is None:
        candles = fetch_candles(product_id)
    if len(candles) < 3:
        return {"orb_score": 0.0, "features": {}}

    range_seconds = max(CANDLE_GRANULARITY, ORB_RANGE_MINUTES * 60)
    session_start = _current_or_previous_session_start()
    session_dt = datetime.fromtimestamp(session_start, timezone.utc)
    if ORB_SKIP_WEEKENDS and session_dt.weekday() >= 5:
        return {
            "orb_score": 0.0,
            "features": {
                "strategy": "ORB_WEEKEND_SKIPPED",
                "session_start_utc": session_dt.isoformat(),
                "range_minutes": ORB_RANGE_MINUTES,
                "reason": "weekend volume/chop filter",
            },
        }
    range_end = session_start + range_seconds
    range_candles = [c for c in candles if session_start <= int(c[0]) < range_end]
    post_candles = [c for c in candles if int(c[0]) >= range_end]
    if not range_candles or not post_candles:
        return {"orb_score": 0.0, "features": {}}

    range_high = max(float(c[2] or 0.0) for c in range_candles)
    range_low = min(float(c[1] or 0.0) for c in range_candles)
    if range_low <= 0 or range_high <= 0:
        return {"orb_score": 0.0, "features": {}}

    range_vols = sorted(float(c[5] or 0.0) for c in range_candles)
    median_range_vol = range_vols[len(range_vols) // 2] or 1.0
    trigger_window = post_candles[-max(1, RECENT_TRIGGER_CANDLES):]

    best = {"orb_score": 0.0, "features": {}}
    for age, candle in enumerate(reversed(trigger_window)):
        _, low, high, open_price, close, volume = candle
        low = float(low or 0.0)
        high = float(high or 0.0)
        open_price = float(open_price or 0.0)
        close = float(close or 0.0)
        volume = float(volume or 0.0)
        if open_price <= 0 or close <= 0:
            continue

        close_buffer_pct = ((close - range_high) / range_high) * 100
        overextension_pct = close_buffer_pct
        volume_ratio = volume / median_range_vol
        candle_move_pct = ((close - open_price) / open_price) * 100
        range_width_pct = ((range_high - range_low) / range_low) * 100
        bullish_breakout = close_buffer_pct >= ORB_BREAKOUT_BUFFER_PCT
        downside_break = ((range_low - close) / range_low) * 100 if close < range_low else 0.0

        score = 0.0
        if bullish_breakout:
            score += 35
        if volume_ratio >= ORB_VOL_RATIO_MIN * 2:
            score += 25
        elif volume_ratio >= ORB_VOL_RATIO_MIN:
            score += 15
        if high > range_high and low >= range_low:
            score += 15
        if candle_move_pct >= 0.8:
            score += 15
        elif candle_move_pct >= 0.3:
            score += 8
        if range_width_pct <= CANDLE_COMPRESSION_TIGHT * 1.5:
            score += 10

        if overextension_pct > ORB_MAX_OVEREXTENSION:
            score *= 0.5
        score = max(0.0, score - (age * 8.0))

        if score > best["orb_score"]:
            retest = analyze_breakout_retest(candles, len(candles) - 1 - age, range_high, range_low)
            features = {
                "strategy": "ORB_LONG_BREAKOUT" if bullish_breakout else "ORB_NO_LONG_TRIGGER",
                "session_start_utc": datetime.fromtimestamp(session_start, timezone.utc).isoformat(),
                "range_minutes": ORB_RANGE_MINUTES,
                "range_high": range_high,
                "range_low": range_low,
                "range_width_pct": round(range_width_pct, 3),
                "close_above_range_pct": round(close_buffer_pct, 3),
                "downside_break_pct": round(downside_break, 3),
                "volume_ratio": round(volume_ratio, 2),
                "candle_move_pct": round(candle_move_pct, 3),
                "orb_breakout_dollar_volume": round(close * volume, 2),
                "trigger_age_candles": age,
                "support_level": range_high,
                "support_floor": range_low,
                "resistance_level": range_high,
            }
            if retest:
                features.update(retest)
                if retest.get("successful_retest"):
                    score += RETEST_SCORE_BONUS
            best = {
                "orb_score": round(min(score, 100.0), 1),
                "features": features,
            }
    return best


def calculate_rsi(closes: list[float], period: int = 14) -> list[float]:
    """
    Wilder's smoothed RSI. Returns one float per input close.
    Values are 0.0 for the initial period before enough data exists.
    """
    n = len(closes)
    if n < period + 1:
        return [0.0] * n
    rsi: list[float] = [0.0] * period
    deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
    avg_gain = sum(max(0.0, d) for d in deltas[:period]) / period
    avg_loss = sum(max(0.0, -d) for d in deltas[:period]) / period
    rs = avg_gain / (avg_loss + 1e-9)
    rsi.append(round(100.0 - 100.0 / (1.0 + rs), 2))
    for d in deltas[period:]:
        avg_gain = (avg_gain * (period - 1) + max(0.0, d)) / period
        avg_loss = (avg_loss * (period - 1) + max(0.0, -d)) / period
        rs = avg_gain / (avg_loss + 1e-9)
        rsi.append(round(100.0 - 100.0 / (1.0 + rs), 2))
    return rsi


def detect_rsi_divergence(candles: list[list], rsi_period: int = RSI_PERIOD) -> bool:
    """
    Returns True when price makes a lower low in the second half of the candle
    window while RSI makes a higher low — classic bullish hidden divergence.
    """
    if len(candles) < (rsi_period + 1) * 2:
        return False
    closes = [float(c[4] or 0.0) for c in candles]
    lows   = [float(c[1] or 0.0) for c in candles]
    rsi_values = calculate_rsi(closes, rsi_period)
    half = len(candles) // 2
    first_low_idx  = min(range(half), key=lambda i: lows[i])
    second_low_idx = half + min(range(len(candles) - half), key=lambda i: lows[half + i])
    if lows[second_low_idx] >= lows[first_low_idx]:
        return False  # price not making lower lows
    if rsi_values[first_low_idx] == 0.0 or rsi_values[second_low_idx] == 0.0:
        return False  # RSI period not warmed up yet
    return rsi_values[second_low_idx] > rsi_values[first_low_idx]


def detect_wedge_breakout(product_id: str, candles_1h: list[list] | None = None) -> dict:
    """
    Scores a descending-wedge breakout from 0-100 using 1h candles.

    Pattern requirements:
      - Both swing highs and swing lows trend downward (descending structure).
      - Highs fall faster than lows, narrowing the channel (converging wedge).
      - Most recent close breaks above the projected upper trendline.
      - Volume burst: latest volume >= WEDGE_VOL_RATIO_MIN x recent average.
      - RSI bullish divergence adds a large score bonus.

    Scoring:
      35 pts  — descending-wedge structure confirmed + trendline break
      25 pts  — volume burst confirmed
      25 pts  — RSI bullish divergence confirmed
      10 pts  — close well above resistance (>= 0.5%)
       5 pts  — tight wedge width (<= 8%)
    Penalty: if close > WEDGE_MAX_OVEREXTENSION_PCT above resistance, score * 0.5
    """
    if not WEDGE_ENABLED:
        return {"wedge_score": 0.0, "features": {}}
    if candles_1h is None:
        candles_1h = fetch_candles(product_id, granularity=MTF_GRAN_1H)
    need = WEDGE_CANDLE_LOOKBACK + 1
    if len(candles_1h) < need:
        return {"wedge_score": 0.0, "features": {}}

    candles = candles_1h[-need:]
    half = len(candles) // 2

    first_highs  = [float(c[2] or 0.0) for c in candles[:half]]
    first_lows   = [float(c[1] or 0.0) for c in candles[:half]]
    second_highs = [float(c[2] or 0.0) for c in candles[half:]]
    second_lows  = [float(c[1] or 0.0) for c in candles[half:]]

    high1 = max(first_highs)
    low1  = min(first_lows)
    high2 = max(second_highs)
    low2  = min(second_lows)

    if low2 <= 0 or high2 <= 0 or high1 <= 0 or low1 <= 0:
        return {"wedge_score": 0.0, "features": {}}

    # Descending structure: both peaks and troughs falling
    if not (high2 < high1 and low2 < low1):
        return {"wedge_score": 0.0, "features": {}}

    high_slope = (high2 - high1) / half   # negative
    low_slope  = (low2 - low1) / half     # negative, less steep = converging

    # Convergence check: highs must fall faster than lows
    if not (high_slope < low_slope and high_slope < 0):
        return {"wedge_score": 0.0, "features": {}}

    # Project the upper trendline to the final candle
    projected_resistance = high2 + (high_slope * (half - 1))
    latest = candles[-1]
    latest_close  = float(latest[4] or 0.0)
    latest_volume = float(latest[5] or 0.0)
    if latest_close <= 0 or projected_resistance <= 0:
        return {"wedge_score": 0.0, "features": {}}

    close_above_pct = ((latest_close - projected_resistance) / projected_resistance) * 100
    if close_above_pct <= 0:
        return {"wedge_score": 0.0, "features": {}}  # no trendline break yet

    # Volume confirmation
    vol_window = [float(c[5] or 0.0) for c in candles[-25:-1]]
    avg_vol = sum(vol_window) / max(1, len(vol_window))
    if avg_vol < 1.0:
        return {"wedge_score": 0.0, "features": {}}
    volume_ratio   = latest_volume / avg_vol
    vol_confirmed  = volume_ratio >= WEDGE_VOL_RATIO_MIN

    # RSI divergence
    rsi_divergent = detect_rsi_divergence(candles)

    wedge_width_pct    = ((high1 - low1) / low1) * 100
    convergence_ratio  = abs(high_slope) / (abs(low_slope) + 1e-9)

    score = 35.0  # structure + breakout already confirmed above
    if vol_confirmed:
        score += 25
    elif volume_ratio >= 1.1:
        score += 12
    if rsi_divergent:
        score += 25
    if close_above_pct >= 0.5:
        score += 10
    elif close_above_pct >= 0.1:
        score += 5
    if wedge_width_pct <= 8.0:
        score += 5
    if close_above_pct > WEDGE_MAX_OVEREXTENSION_PCT:
        score *= 0.5

    features = {
        "strategy": "DESCENDING_WEDGE_BREAKOUT",
        "high1": round(high1, 8),
        "low1": round(low1, 8),
        "high2": round(high2, 8),
        "low2": round(low2, 8),
        "projected_resistance": round(projected_resistance, 8),
        "close_above_resistance_pct": round(close_above_pct, 3),
        "volume_ratio": round(volume_ratio, 2),
        "volume_confirmed": vol_confirmed,
        "rsi_divergent": rsi_divergent,
        "wedge_width_pct": round(wedge_width_pct, 3),
        "convergence_ratio": round(convergence_ratio, 3),
        "candle_lookback": need,
        "granularity_seconds": MTF_GRAN_1H,
    }
    return {"wedge_score": round(min(score, 100.0), 1), "features": features}


def detect_bollinger_reversal_signal(product_id: str, candles: list[list] | None = None) -> dict:
    """Scores a long Bollinger lower-band snapback from 0-100."""
    if not BOLLINGER_ENABLED:
        return {"bollinger_score": 0.0, "features": {}}
    if candles is None:
        candles = fetch_candles(product_id)
    need = BOLLINGER_PERIOD + 1
    if len(candles) < need:
        return {"bollinger_score": 0.0, "features": {}}

    closes = [float(c[4] or 0.0) for c in candles]
    window = closes[-BOLLINGER_PERIOD:]
    if any(c <= 0 for c in window):
        return {"bollinger_score": 0.0, "features": {}}

    mid = sum(window) / BOLLINGER_PERIOD
    variance = sum((close - mid) ** 2 for close in window) / BOLLINGER_PERIOD
    stddev = variance ** 0.5
    upper = mid + (BOLLINGER_STDDEV * stddev)
    lower = mid - (BOLLINGER_STDDEV * stddev)
    if lower <= 0 or mid <= 0:
        return {"bollinger_score": 0.0, "features": {}}

    latest = candles[-1]
    previous = candles[-2]
    _, low, high, open_price, close, volume = latest
    low = float(low or 0.0)
    high = float(high or 0.0)
    open_price = float(open_price or 0.0)
    close = float(close or 0.0)
    volume = float(volume or 0.0)
    previous_close = float(previous[4] or 0.0)
    if open_price <= 0 or close <= 0 or previous_close <= 0:
        return {"bollinger_score": 0.0, "features": {}}

    lower_extension_pct = ((lower - low) / lower) * 100 if low < lower else 0.0
    upper_extension_pct = ((high - upper) / upper) * 100 if high > upper else 0.0
    reclaimed_lower = low < lower and close > lower
    bullish_reversal = close > open_price and close > previous_close
    distance_to_mid_pct = ((mid - close) / close) * 100
    candle_move_pct = ((close - open_price) / open_price) * 100

    vols = sorted(float(c[5] or 0.0) for c in candles[-BOLLINGER_PERIOD:-1])
    median_vol = vols[len(vols) // 2] or 1.0
    volume_ratio = volume / median_vol

    score = 0.0
    if reclaimed_lower and lower_extension_pct >= BOLLINGER_MIN_EXTREME_PCT:
        score += 35
    elif lower_extension_pct >= BOLLINGER_MIN_EXTREME_PCT:
        score += 20
    if bullish_reversal:
        score += 25
    if 0 <= distance_to_mid_pct <= BOLLINGER_MAX_DISTANCE_FROM_MID_PCT:
        score += 15
    elif distance_to_mid_pct > 0:
        score += 8
    volume_climax = volume_ratio >= BOLLINGER_VOLUME_CLIMAX_RATIO
    if volume_ratio >= BOLLINGER_VOLUME_CLIMAX_RATIO:
        score += 15
    elif volume_ratio >= 1.2:
        score += 8
    if candle_move_pct >= 0.5:
        score += 10

    if upper_extension_pct > 0 and lower_extension_pct <= 0:
        score = 0.0
    if reclaimed_lower and not volume_climax:
        score = min(score, 60.0)

    features = {
        "strategy": "BOLLINGER_LOWER_SNAPBACK" if score > 0 else "BOLLINGER_UPPER_EXTENSION_OR_NO_LONG_TRIGGER",
        "period": BOLLINGER_PERIOD,
        "stddev": BOLLINGER_STDDEV,
        "middle_band": round(mid, 8),
        "upper_band": round(upper, 8),
        "lower_band": round(lower, 8),
        "lower_extension_pct": round(lower_extension_pct, 3),
        "upper_extension_pct": round(upper_extension_pct, 3),
        "reclaimed_lower_band": reclaimed_lower,
        "distance_to_mid_pct": round(distance_to_mid_pct, 3),
        "volume_ratio": round(volume_ratio, 2),
        "volume_climax": volume_climax,
        "volume_climax_required": BOLLINGER_VOLUME_CLIMAX_RATIO,
        "candle_move_pct": round(candle_move_pct, 3),
        "bollinger_signal_dollar_volume": round(close * volume, 2),
    }
    return {"bollinger_score": round(min(score, 100.0), 1), "features": features}


def detect_momentum_runner_signal(product_data: dict) -> dict:
    """
    Scores fast intraday continuation runners. This catches ALLO-style moves that
    are up strongly on the day with fresh 15m/1h follow-through, even before a
    clean compression breakout pattern appears.
    """
    if not MOMENTUM_RUNNER_ENABLED:
        return {"momentum_runner_score": 0.0, "features": {}}

    price_change_24h = float(product_data.get("price_change_24h", 0.0) or 0.0)
    price_change_15m = float(product_data.get("price_change_15m", 0.0) or 0.0)
    price_change_1h = float(product_data.get("price_change_1h", 0.0) or 0.0)
    recent_volume_usd = float(product_data.get("recent_window_dollar_volume", 0.0) or 0.0)
    obv = product_data.get("obv", {}) or {}
    obv_pressure = float(obv.get("obv_pressure_pct", 0.0) or 0.0)
    up_volume_ratio = float(obv.get("up_volume_ratio", 0.0) or 0.0)

    if price_change_24h < MOMENTUM_RUNNER_MIN_24H_CHANGE:
        return {"momentum_runner_score": 0.0, "features": {}}
    if price_change_24h > MOMENTUM_RUNNER_MAX_24H_CHANGE:
        return {"momentum_runner_score": 0.0, "features": {}}

    score = 0.0

    if 8 <= price_change_24h <= 25:
        score += 30
    elif 25 < price_change_24h <= MOMENTUM_RUNNER_MAX_24H_CHANGE:
        score += 18

    if price_change_15m >= 1.0:
        score += 20
    elif price_change_15m >= MOMENTUM_RUNNER_MIN_15M_CHANGE:
        score += 12

    if price_change_1h >= 3.0:
        score += 20
    elif price_change_1h >= MOMENTUM_RUNNER_MIN_1H_CHANGE:
        score += 12

    if recent_volume_usd >= MOMENTUM_RUNNER_MIN_RECENT_DOLLAR_VOLUME * 2:
        score += 15
    elif recent_volume_usd >= MOMENTUM_RUNNER_MIN_RECENT_DOLLAR_VOLUME:
        score += 10

    if obv_pressure >= MIN_OBV_PRESSURE_PCT and up_volume_ratio >= MIN_OBV_UP_VOLUME_RATIO:
        score += 15
    elif obv_pressure > 0 and up_volume_ratio >= 0.50:
        score += 8

    # Do not chase a runner that has already lost current-period follow-through.
    if price_change_15m < 0 or price_change_1h < 0:
        score *= 0.5

    features = {
        "strategy": "EARLY_MOMENTUM_RUNNER",
        "price_change_24h": round(price_change_24h, 3),
        "price_change_15m": round(price_change_15m, 3),
        "price_change_1h": round(price_change_1h, 3),
        "recent_window_dollar_volume": round(recent_volume_usd, 2),
        "obv_pressure_pct": round(obv_pressure, 2),
        "up_volume_ratio": round(up_volume_ratio, 3),
    }
    return {"momentum_runner_score": round(min(score, 100.0), 1), "features": features}


def select_entry_signal(product_data: dict) -> dict:
    """
    Returns the strongest currently buyable signal for a product, with a
    consensus confidence layer applied on top of the individual scores.

    Consensus rules:
      - A strategy is "confirming" when its raw score >= CONSENSUS_AGREEING_THRESHOLD
        (default 40), even if it has not yet crossed its own buy threshold.
      - 1 confirming strategy (lone signal):  no bonus, buy threshold raised by
        CONSENSUS_SINGLE_THRESHOLD_BUMP (default +5) to require more conviction.
      - 2 confirming strategies:  +CONSENSUS_DUAL_BONUS (default +8) to best score,
        normal buy threshold.
      - 3+ confirming strategies: +CONSENSUS_TRIPLE_BONUS (default +15) to best
        score, threshold lowered by 5 (high conviction, easier to trigger).

    Extra return fields vs the old version:
      raw_score           — score before consensus bonus
      consensus_count     — number of strategies that reached the agreeing threshold
      consensus_bonus     — points added to the best raw score
      confidence_level    — "HIGH" | "MEDIUM" | "SINGLE"
      confirming_strategies — list of strategy names that are confirming
    """
    candidates = [
        {
            "type": "candle_breakout",
            "strategy": "CANDLE_BREAKOUT",
            "score": float(product_data.get("pre_breakout_score", 0.0) or 0.0),
            "threshold": MIN_PATTERN_SCORE_TO_BUY,
            "features": product_data.get("pre_breakout_features", {}) or {},
            "requires_mtf": True,
        },
        {
            "type": "orb_breakout",
            "strategy": "OPENING_RANGE_BREAKOUT",
            "score": float(product_data.get("orb_score", 0.0) or 0.0),
            "threshold": ORB_MIN_SCORE_TO_BUY,
            "features": product_data.get("orb_features", {}) or {},
            "requires_mtf": True,
        },
        {
            "type": "bollinger_reversal",
            "strategy": "BOLLINGER_LOWER_BAND_REVERSAL",
            "score": float(product_data.get("bollinger_score", 0.0) or 0.0),
            "threshold": BOLLINGER_MIN_SCORE_TO_BUY,
            "features": product_data.get("bollinger_features", {}) or {},
            "requires_mtf": False,
        },
        {
            "type": "momentum_24h",
            "strategy": "24H_MOMENTUM_VOLUME",
            "score": float(product_data.get("score", 0.0) or 0.0),
            "threshold": MIN_SIGNAL_SCORE,
            "features": {},
            "requires_mtf": True,
        },
        {
            "type": "wedge_breakout",
            "strategy": "DESCENDING_WEDGE_BREAKOUT",
            "score": float(product_data.get("wedge_score", 0.0) or 0.0),
            "threshold": WEDGE_MIN_SCORE_TO_BUY,
            "features": product_data.get("wedge_features", {}) or {},
            "requires_mtf": False,  # already operates on 1h; MTF gate is redundant
        },
        {
            "type": "momentum_runner",
            "strategy": "EARLY_MOMENTUM_RUNNER",
            "score": float(product_data.get("momentum_runner_score", 0.0) or 0.0),
            "threshold": MOMENTUM_RUNNER_MIN_SCORE_TO_BUY,
            "features": product_data.get("momentum_runner_features", {}) or {},
            "requires_mtf": False,
        },
    ]
    enabled = set(enabled_entry_strategy_names())
    if enabled:
        candidates = [
            candidate for candidate in candidates
            if normalize_entry_strategy_name(candidate["strategy"]) in enabled
            or normalize_entry_strategy_name(candidate["type"]) in enabled
        ]
        if not candidates:
            return {
                "type": "none",
                "strategy": "NO_ENABLED_ENTRY_STRATEGY",
                "score": 0.0,
                "raw_score": 0.0,
                "threshold": 100.0,
                "features": {},
                "requires_mtf": False,
                "eligible": False,
                "consensus_count": 0,
                "consensus_bonus": 0.0,
                "confidence_level": "NONE",
                "confirming_strategies": [],
            }
    candidates.sort(key=lambda item: item["score"], reverse=True)

    # --- consensus layer ---
    confirming = [c for c in candidates if c["score"] >= CONSENSUS_AGREEING_THRESHOLD]
    consensus_count = len(confirming)

    if consensus_count >= 3:
        bonus           = CONSENSUS_TRIPLE_BONUS
        confidence_level = "HIGH"
        threshold_bump  = -5.0
    elif consensus_count == 2:
        bonus           = CONSENSUS_DUAL_BONUS
        confidence_level = "MEDIUM"
        threshold_bump  = 0.0
    else:
        bonus           = 0.0
        confidence_level = "SINGLE"
        threshold_bump  = CONSENSUS_SINGLE_THRESHOLD_BUMP

    best             = candidates[0]
    raw_score        = best["score"]
    adjusted_score   = round(min(100.0, raw_score + bonus), 1)
    effective_threshold = best["threshold"] + threshold_bump

    best["raw_score"]            = raw_score
    best["score"]                = adjusted_score
    best["eligible"]             = adjusted_score >= effective_threshold
    best["consensus_count"]      = consensus_count
    best["consensus_bonus"]      = bonus
    best["confidence_level"]     = confidence_level
    best["confirming_strategies"] = [c["strategy"] for c in confirming]
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
    # For low-price tokens a single 5m candle may be tiny — use the 6-candle (30m)
    # rolling window dollar volume stored during enrichment if available, falling back
    # to the single-candle figure. This avoids filtering out valid small-cap breakouts.
    breakout_dollar_volume = max(
        float(features.get("breakout_candle_dollar_volume", 0.0) or 0.0),
        float(features.get("breakout_window_dollar_volume", 0.0) or 0.0),
        float(product_data.get("recent_window_dollar_volume", 0.0) or 0.0),
        float((product_data.get("orb_features", {}) or {}).get("orb_breakout_dollar_volume", 0.0) or 0.0),
        float((product_data.get("bollinger_features", {}) or {}).get("bollinger_signal_dollar_volume", 0.0) or 0.0),
    )

    signal = select_entry_signal(product_data)
    breakout_volume_floor = MIN_BREAKOUT_DOLLAR_VOLUME
    high_consensus = (
        signal.get("eligible")
        and int(signal.get("consensus_count", 0) or 0) >= HIGH_CONSENSUS_MIN_COUNT
    )
    if high_consensus:
        discount = max(0.50, min(1.0, HIGH_CONSENSUS_BREAKOUT_VOLUME_DISCOUNT))
        breakout_volume_floor = MIN_BREAKOUT_DOLLAR_VOLUME * discount
    min_24h_dollar_volume = MIN_24H_DOLLAR_VOLUME
    if signal.get("strategy") == "EARLY_MOMENTUM_RUNNER" and signal.get("eligible"):
        min_24h_dollar_volume = min(MIN_24H_DOLLAR_VOLUME, MOMENTUM_RUNNER_MIN_24H_DOLLAR_VOLUME)

    if dollar_volume_24h < min_24h_dollar_volume:
        return {
            "ok": False,
            "reason": f"24h dollar volume ${dollar_volume_24h:,.0f} < ${min_24h_dollar_volume:,.0f}",
            "dollar_volume_24h": dollar_volume_24h,
            "breakout_dollar_volume": breakout_dollar_volume,
        }
    if breakout_dollar_volume < breakout_volume_floor:
        return {
            "ok": False,
            "reason": f"breakout candle volume ${breakout_dollar_volume:,.0f} < ${breakout_volume_floor:,.0f}",
            "dollar_volume_24h": dollar_volume_24h,
            "breakout_dollar_volume": breakout_dollar_volume,
        }
    return {
        "ok": True,
        "reason": "liquidity OK",
        "dollar_volume_24h": dollar_volume_24h,
        "breakout_dollar_volume": breakout_dollar_volume,
        "breakout_volume_floor": breakout_volume_floor,
        "min_24h_dollar_volume": min_24h_dollar_volume,
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


def structure_filter_result(product_data: dict, signal: dict) -> dict:
    """
    Returns whether the current price location is structurally sound for a long
    breakout entry. This is intentionally strict only for breakout-style trades.
    """
    strategy = signal.get("strategy", "")
    if strategy not in {"CANDLE_BREAKOUT", "OPENING_RANGE_BREAKOUT"}:
        return {"ok": True, "reason": "structure not required", "features": {}}

    features = signal.get("features", {}) or {}
    price = float(product_data.get("price", 0.0) or 0.0)
    support_level = float(features.get("support_level", 0.0) or 0.0)
    support_floor = float(features.get("support_floor", 0.0) or 0.0)
    buy_zone_low = float(features.get("buy_zone_low", 0.0) or 0.0)
    buy_zone_high = float(features.get("buy_zone_high", 0.0) or 0.0)
    structural_stop = float(features.get("structural_stop", 0.0) or 0.0)
    stop_distance_pct = float(features.get("structural_stop_distance_pct", 999.0) or 999.0)
    successful_retest = bool(features.get("successful_retest"))
    accepted_above_level = bool(features.get("accepted_above_level"))
    entry_in_buy_zone = bool(features.get("entry_in_buy_zone")) or (
        buy_zone_low > 0 and buy_zone_high > 0 and buy_zone_low <= price <= buy_zone_high
    )

    if price <= 0 or support_level <= 0 or support_floor <= 0:
        return {"ok": False, "reason": "missing support/resistance structure", "features": features}
    if RETEST_ENTRY_REQUIRED and not successful_retest:
        return {"ok": False, "reason": "breakout has not retested and held support yet", "features": features}
    if not accepted_above_level:
        return {"ok": False, "reason": "price has not reclaimed support after retest", "features": features}
    if not entry_in_buy_zone:
        return {
            "ok": False,
            "reason": f"price ${price:,.6g} is away from buy zone ${buy_zone_low:,.6g}-${buy_zone_high:,.6g}",
            "features": features,
        }
    if structural_stop <= 0 or structural_stop >= price:
        return {"ok": False, "reason": "support-based stop is invalid for this setup", "features": features}
    if stop_distance_pct > MAX_STRUCTURAL_STOP_PCT:
        return {
            "ok": False,
            "reason": f"support stop distance {stop_distance_pct:.2f}% > {MAX_STRUCTURAL_STOP_PCT:.2f}%",
            "features": features,
        }
    return {
        "ok": True,
        "reason": "retest held near support",
        "features": features,
        "buy_zone_low": buy_zone_low,
        "buy_zone_high": buy_zone_high,
        "support_level": support_level,
        "support_floor": support_floor,
        "stop_loss": structural_stop,
        "stop_distance_pct": stop_distance_pct,
    }


def detect_bearish_reversal(product_id: str, candles: list[list] | None = None) -> dict:
    """
    Detects a consolidated bearish trend flip from OHLCV candles.

    A single red candle is often just a shakeout before the next leg. To avoid
    closing runners too early, the exit engine only treats this as bearish when
    multiple independent symptoms agree: support break, distribution volume, and
    lower-high momentum fade.
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
    signals = []
    if l_close < min(base_lows):
        signals.append("BREAKDOWN_BELOW_BASE_LOW")

    # 2) Strong red candle confirmed by volume (distribution)
    if move_pct <= -abs(BEARISH_DROP_PCT) and vol_ratio >= BEARISH_VOL_RATIO:
        signals.append("BEARISH_VOLUME_CANDLE")

    # 3) Lower highs with CONFIRMED structural breakdown (not just a normal pullback).
    # Requires: lower highs in the second half AND a meaningful drop from the recent
    # swing high (>=BEARISH_DROP_PCT%) AND volume confirmation. This prevents firing on
    # routine consolidation candles in an uptrend — true invalidation needs all three.
    half = len(base_highs) // 2
    if half >= 2:
        first_half_high = max(base_highs[:half])
        second_half_high = max(base_highs[half:])
        max_recent_high = max(base_highs)
        drop_from_high_pct = ((max_recent_high - l_close) / max_recent_high * 100) if max_recent_high > 0 else 0.0
        if (second_half_high < first_half_high
                and drop_from_high_pct >= abs(BEARISH_DROP_PCT)
                and l_close < l_open):
            signals.append("LOWER_HIGHS_FADING")

    min_confirmations = max(1, BEARISH_REVERSAL_MIN_CONFIRMATIONS)
    severe = "BREAKDOWN_BELOW_BASE_LOW" in signals and "BEARISH_VOLUME_CANDLE" in signals
    if len(signals) >= min_confirmations:
        reason = "CONFIRMED_" + "_AND_".join(signals)
        return {
            "bearish": True,
            "reason": reason,
            "signals": signals,
            "confirmation_count": len(signals),
            "severe": severe,
            "move_pct": round(move_pct, 3),
            "volume_ratio": round(vol_ratio, 3),
        }

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


def detect_multi_timeframe_signal(product_id: str, min_confirm_score: float = MTF_CONFIRM_MIN_SCORE) -> dict:
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
    if p15["pattern_score"] < min_confirm_score:
        return {"confirmed": False,
                "reason": f"15m weak ({p15['pattern_score']:.0f} < {min_confirm_score:.0f})",
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

      products : list of enriched dicts sorted by liquidity + movement quality
                 (top WATCHLIST_SIZE)
                 each dict: {product_id, price, volume_24h,
                              price_change_24h, volume_change_24h, score,
                              pre_breakout_score, pre_breakout_features,
                              orb_score, orb_features,
                              bollinger_score, bollinger_features}
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
            prices[prod["product_id"]] = price
            dollar_volume_24h = price * volume_24h
            if dollar_volume_24h < WATCHLIST_MIN_DOLLAR_VOLUME:
                continue
            score = compute_signal_score(price_chg, volume_chg)
            products.append({
                "product_id":       prod["product_id"],
                "price":            price,
                "volume_24h":       volume_24h,
                "dollar_volume_24h": round(dollar_volume_24h, 2),
                "price_change_24h": price_chg,
                "volume_change_24h":volume_chg,
                "score":            score,
            })

        # Rank by quality, not raw token units. This keeps the universe focused
        # on liquid coins that are actually moving.
        products.sort(key=_watchlist_quality_score, reverse=True)
        products = products[:WATCHLIST_SIZE]

        # Log top 5 with their scores
        top5 = sorted(products, key=_watchlist_quality_score, reverse=True)[:5]
        print(f"[Snapshot] Top scorers: " +
              ", ".join(f"{p['product_id']} q{_watchlist_quality_score(p):.0f}/{p['score']:.0f}pts" for p in top5))
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
        prices[pid] = price

        day_slice = candles[-per_day:]
        volume_24h = sum(float(c[5] or 0) for c in day_slice)
        first_open = float(day_slice[0][3] or 0)
        price_chg = ((price - first_open) / first_open * 100) if first_open else 0.0

        pat = detect_recent_breakout_pattern(pid, candles)
        orb = detect_orb_signal(pid, candles)
        bollinger = detect_bollinger_reversal_signal(pid, candles)
        obv = calculate_obv_metrics(candles)
        price_chg_15m = candle_change_pct(candles, 3)
        price_chg_1h = candle_change_pct(candles, 12)
        dollar_volume_24h = price * volume_24h
        if dollar_volume_24h < WATCHLIST_MIN_DOLLAR_VOLUME:
            continue
        product = {
            "product_id":            pid,
            "price":                 price,
            "volume_24h":            volume_24h,
            "dollar_volume_24h":     round(dollar_volume_24h, 2),
            "price_change_24h":      round(price_chg, 3),
            "price_change_15m":      round(price_chg_15m, 3),
            "price_change_1h":       round(price_chg_1h, 3),
            "volume_change_24h":     0.0,  # not derivable from candles; pattern path drives entries
            "recent_window_dollar_volume": round(recent_dollar_volume(candles), 2),
            "score":                 compute_signal_score(price_chg, 0.0),
            "pre_breakout_score":    pat["pattern_score"],
            "pre_breakout_features": pat["features"],
            "orb_score":             orb["orb_score"],
            "orb_features":          orb["features"],
            "bollinger_score":       bollinger["bollinger_score"],
            "bollinger_features":    bollinger["features"],
            "obv":                   obv,
        }
        momentum = detect_momentum_runner_signal(product)
        product["momentum_runner_score"] = momentum["momentum_runner_score"]
        product["momentum_runner_features"] = momentum["features"]
        products.append(product)
        time.sleep(0.05)  # stay under the public rate limit

    # Rank the watchlist by quality, not raw token units. Liquidity matters, but
    # the final top 50 should also include names with real recent movement and
    # strategy strength.
    products.sort(key=_watchlist_quality_score, reverse=True)
    watchlist = products[:WATCHLIST_SIZE]

    # Optional overflow for research mode. Production defaults to a hard top-50
    # so we do not keep expanding into weaker names.
    in_list = {p["product_id"] for p in watchlist}
    if WATCHLIST_STRONG_SETUP_OVERFLOW:
        for p in products:
            signal = select_entry_signal(p)
            if p["product_id"] not in in_list and signal["eligible"]:
                watchlist.append(p)
                in_list.add(p["product_id"])
    products = watchlist

    top5 = sorted(products, key=_watchlist_quality_score, reverse=True)[:5]
    print("[Snapshot] Top strategy scorers: " +
          ", ".join(
              f"{p['product_id']} {(s := select_entry_signal(p))['strategy']} "
              f"q{_watchlist_quality_score(p):.0f}/{s['score']:.0f}pts "
              f"(raw {s['raw_score']:.0f} +{s['consensus_bonus']:.0f} {s['confidence_level']} {s['consensus_count']}x)"
              for p in top5
          ))
    return products, prices

# ---------------------------------------------------------------------------
# STRATEGY  — replace the body of this function with real indicator logic
# ---------------------------------------------------------------------------

def evaluate_market_entry_signal(product_data: dict) -> bool:
    """
    Returns True only for strong setups, so the bot does not buy everything.
    Entry requires the strongest candidate strategy to meet its threshold.
    """
    return select_entry_signal(product_data)["eligible"]

# ---------------------------------------------------------------------------
# TRADE EXECUTION
# ---------------------------------------------------------------------------

def _take_profit_percent_for_signal(signal: dict) -> tuple[float, str]:
    """Returns the main TP percent and reason for the selected entry signal."""
    strategy = str(signal.get("strategy", "") or "").upper()
    score = float(signal.get("score", 0.0) or 0.0)
    consensus = int(signal.get("consensus_count", 0) or 0)

    if strategy == "DESCENDING_WEDGE_BREAKOUT":
        return WEDGE_TAKE_PROFIT_PERCENT, "wedge breakout"
    if strategy == "EARLY_MOMENTUM_RUNNER":
        return RUNNER_TAKE_PROFIT_PERCENT, "momentum runner"
    if consensus >= HIGH_CONSENSUS_TP_MIN_COUNT and score >= HIGH_CONSENSUS_TP_MIN_SCORE:
        return HIGH_CONSENSUS_TAKE_PROFIT_PERCENT, f"high consensus {consensus}x score {score:.0f}"
    return NORMAL_TAKE_PROFIT_PERCENT, "normal breakout"


def _take_profit_percent_for_position(pos: dict) -> tuple[float, str]:
    signal = {
        "strategy": pos.get("entry_strategy", ""),
        "score": pos.get("entry_strategy_score", pos.get("signal_score", 0.0)),
        "consensus_count": pos.get("entry_consensus_count", 0),
    }
    return _take_profit_percent_for_signal(signal)


def _position_size_for_score(score: float) -> float:
    """Returns the dollar position size to deploy based on signal score.

    Linearly interpolates between DYNAMIC_SIZE_MIN_PCT% (at MIN_SIGNAL_SCORE)
    and DYNAMIC_SIZE_MAX_PCT% (at score 100) of CAPITAL_PER_TRADE_USD.
    Always clamped to [50%, 200%] of CAPITAL_PER_TRADE_USD.
    Falls back to a fixed CAPITAL_PER_TRADE_USD when dynamic sizing is off.
    """
    if not DYNAMIC_SIZING_ENABLED:
        return CAPITAL_PER_TRADE_USD
    score = max(float(MIN_SIGNAL_SCORE), min(100.0, float(score)))
    score_range = 100.0 - float(MIN_SIGNAL_SCORE)
    pct = DYNAMIC_SIZE_MIN_PCT + (DYNAMIC_SIZE_MAX_PCT - DYNAMIC_SIZE_MIN_PCT) * \
          ((score - float(MIN_SIGNAL_SCORE)) / score_range if score_range > 0 else 1.0)
    pct = max(50.0, min(200.0, pct))  # hard clamp regardless of env vars
    return round(CAPITAL_PER_TRADE_USD * pct / 100.0, 2)


def _risk_adjusted_position_size(product_data: dict, signal: dict, base_size: float) -> tuple[float, list[str]]:
    """Caps fragile low-price or low-liquidity entries unless confirmation is excellent."""
    reasons = []
    if not FRAGILE_SIZE_CAP_ENABLED:
        return base_size, reasons

    price = float(product_data.get("price", 0.0) or 0.0)
    dollar_volume = float(product_data.get("dollar_volume_24h", 0.0) or 0.0)
    consensus = int(signal.get("consensus_count", 0) or 0)
    score = float(signal.get("score", 0.0) or 0.0)
    fragile = False

    if 0 < price <= FRAGILE_LOW_PRICE_THRESHOLD_USD:
        fragile = True
        reasons.append(f"low price ${price:,.6g}")
    if 0 < dollar_volume < FRAGILE_LOW_VOLUME_THRESHOLD_USD:
        fragile = True
        reasons.append(f"24h volume ${dollar_volume:,.0f}")

    if not fragile:
        return base_size, reasons
    if score >= FRAGILE_FULL_SIZE_MIN_SCORE and consensus >= FRAGILE_FULL_SIZE_MIN_CONSENSUS:
        return base_size, []

    cap_pct = max(10.0, min(100.0, FRAGILE_MAX_POSITION_PCT))
    capped_size = round(min(base_size, CAPITAL_PER_TRADE_USD * cap_pct / 100.0), 2)
    reasons.append(f"capped at {cap_pct:.0f}% until score/consensus improves")
    return capped_size, reasons


def _capital_deployed(active_positions: dict) -> float:
    """Returns total USD currently locked in open positions (uses actual allocated_usd)."""
    return sum(float(p.get("allocated_usd") or CAPITAL_PER_TRADE_USD)
               for p in active_positions.values())


def _pending_capital_reserved(pending_entries: dict) -> float:
    """Returns total USD reserved by pending limit-style entries."""
    return sum(float(p.get("allocated_usd") or CAPITAL_PER_TRADE_USD)
               for p in pending_entries.values())


def _budget_is_full(active_positions: dict, pending_entries: dict | None = None,
                    next_size: float | None = None) -> bool:
    """Returns True when opening another position would exceed limits."""
    pending_entries = pending_entries or {}
    if (len(active_positions) + len(pending_entries)) >= MAX_OPEN_POSITIONS:
        return True
    size = next_size if next_size is not None else CAPITAL_PER_TRADE_USD
    reserved = _capital_deployed(active_positions) + _pending_capital_reserved(pending_entries)
    return (reserved + size) > TOTAL_CAPITAL_USD


def _shadow_alert_key(product_id: str, strategy: str) -> str:
    return f"{product_id}|{strategy}"


def should_include_shadow_alert(product_id: str, strategy: str) -> bool:
    """Rate-limits repeated budget-skipped watchlist rows."""
    if not SHADOW_ALERTS_ENABLED:
        return False
    state = load_json_file(SHADOW_ALERTS_FILE)
    if not isinstance(state, dict):
        state = {}
    key = _shadow_alert_key(product_id, strategy)
    now = _utcnow_epoch()
    cooldown = int(SHADOW_ALERT_COOLDOWN_HOURS * 3600)
    last_sent = int(state.get(key, 0) or 0)
    if last_sent and (now - last_sent) < cooldown:
        return False
    state[key] = now
    cutoff = now - max(cooldown * 2, 86400)
    state = {k: v for k, v in state.items() if int(v or 0) >= cutoff}
    save_json_file(SHADOW_ALERTS_FILE, state)
    return True


def _watchlist_quality_score(product_data: dict) -> float:
    """
    Ranks the trade universe by liquidity plus movement quality, not just raw
    volume. This keeps the bot focused on the strongest ~50 names instead of
    spending entries on thin or inactive coins.
    """
    price = float(product_data.get("price", 0.0) or 0.0)
    volume_24h = float(product_data.get("volume_24h", 0.0) or 0.0)
    dollar_volume = float(product_data.get("dollar_volume_24h", 0.0) or 0.0)
    if dollar_volume <= 0 and price > 0 and volume_24h > 0:
        dollar_volume = price * volume_24h

    # Log-like buckets without importing math: each USD threshold adds weight,
    # so BTC/ETH/SOL stay high, but real mid-cap movers can still compete.
    liquidity_score = 0.0
    for threshold, points in (
        (2_500_000, 10),
        (5_000_000, 10),
        (10_000_000, 10),
        (25_000_000, 10),
        (50_000_000, 10),
        (100_000_000, 10),
    ):
        if dollar_volume >= threshold:
            liquidity_score += points

    move_24h = abs(float(product_data.get("price_change_24h", 0.0) or 0.0))
    move_1h = abs(float(product_data.get("price_change_1h", 0.0) or 0.0))
    move_15m = abs(float(product_data.get("price_change_15m", 0.0) or 0.0))
    movement_score = min(25.0, move_24h * 1.5) + min(12.0, move_1h * 4.0) + min(8.0, move_15m * 8.0)

    try:
        signal = select_entry_signal(product_data)
        strategy_score = min(30.0, float(signal.get("score", 0.0) or 0.0) * 0.30)
    except Exception:
        strategy_score = min(15.0, float(product_data.get("score", 0.0) or 0.0) * 0.15)

    return liquidity_score + movement_score + strategy_score


def build_shadow_candidate(product_data: dict, signal: dict, liquidity: dict,
                           obv: dict, mtf_scores: dict) -> dict:
    """Builds one row for the batched 'would buy with more budget' summary."""
    price = float(product_data.get("price", 0.0) or 0.0)
    return {
        "product_id": product_data["product_id"],
        "price": price,
        "strategy": signal["strategy"],
        "strategy_score": signal["score"],
        "confidence_level": signal.get("confidence_level", "SINGLE"),
        "consensus_count": signal.get("consensus_count", 1),
        "consensus_bonus": signal.get("consensus_bonus", 0.0),
        "confirming_strategies": signal.get("confirming_strategies", [signal["strategy"]]),
        "base_score": float(product_data.get("score", 0.0) or 0.0),
        "pattern_score": float(product_data.get("pre_breakout_score", 0.0) or 0.0),
        "orb_score": float(product_data.get("orb_score", 0.0) or 0.0),
        "bollinger_score": float(product_data.get("bollinger_score", 0.0) or 0.0),
        "wedge_score": float(product_data.get("wedge_score", 0.0) or 0.0),
        "dollar_volume_24h": float(liquidity.get("dollar_volume_24h", 0.0) or 0.0),
        "breakout_dollar_volume": float(liquidity.get("breakout_dollar_volume", 0.0) or 0.0),
        "obv_pressure_pct": float(obv.get("metrics", {}).get("obv_pressure_pct", 0.0) or 0.0),
        "mtf_summary": _mtf_summary(mtf_scores),
        "hypothetical_qty": (CAPITAL_PER_TRADE_USD / price) if price else 0.0,
        "take_profit": price * (1 + TAKE_PROFIT_PERCENT / 100) if price else 0.0,
        "stop": price * (1 - TRAILING_PERCENT / 100) if price else 0.0,
    }


def _limit_entry_allowed_for_signal(signal: dict, structure: dict) -> bool:
    strategy = str(signal.get("strategy", "") or "")
    if not LIMIT_ENTRY_ENABLED:
        return False
    if strategy not in {"CANDLE_BREAKOUT", "OPENING_RANGE_BREAKOUT"}:
        return False
    return bool(structure.get("ok"))


def _limit_entry_price(signal: dict, structure: dict, market_price: float) -> float:
    support_level = float(structure.get("support_level", 0.0) or 0.0)
    buy_zone_low = float(structure.get("buy_zone_low", 0.0) or 0.0)
    buy_zone_high = float(structure.get("buy_zone_high", 0.0) or 0.0)
    if LIMIT_ENTRY_PRICE_MODE == "buy_zone_low" and buy_zone_low > 0:
        return buy_zone_low
    if LIMIT_ENTRY_PRICE_MODE == "buy_zone_mid" and buy_zone_low > 0 and buy_zone_high > 0:
        return (buy_zone_low + buy_zone_high) / 2.0
    if support_level > 0:
        return support_level
    if buy_zone_low > 0:
        return buy_zone_low
    return market_price


def _open_position_from_entry(
    active_positions: dict,
    product_data: dict,
    signal: dict,
    structure: dict,
    position_size: float,
    fill_price: float,
    mtf_scores: dict,
    sizing_reasons: list[str],
    entry_mode: str = "market",
    limit_price: float = 0.0,
) -> dict:
    product_id = product_data["product_id"]
    crypto_qty = position_size / fill_price if fill_price > 0 else 0.0
    _sig_feats = signal.get("features", {}) or {}
    _fvg_candle_open = float(_sig_feats.get("trigger_candle_open", 0.0) or 0.0)
    if structure.get("ok") and float(structure.get("stop_loss", 0.0) or 0.0) > 0:
        initial_stop = float(structure["stop_loss"])
        stop_method = (
            f"support retest ${float(structure.get('support_level', 0.0) or 0.0):,.6g} "
            f"/ floor ${float(structure.get('support_floor', 0.0) or 0.0):,.6g}"
        )
    elif _fvg_candle_open > 0 and _fvg_candle_open < fill_price:
        fvg_stop = _fvg_candle_open * (1 - 0.003)
        flat_stop = fill_price * (1 - TRAILING_PERCENT / 100)
        initial_stop = max(fvg_stop, flat_stop)
        stop_method = f"FVG (candle open ${_fvg_candle_open:,.6g})"
    else:
        initial_stop = fill_price * (1 - TRAILING_PERCENT / 100)
        stop_method = f"flat {TRAILING_PERCENT:.1f}%"
    main_tp_pct, main_tp_reason = _take_profit_percent_for_signal(signal)
    take_profit_target = fill_price * (1 + main_tp_pct / 100)
    size_pct = round(position_size / CAPITAL_PER_TRADE_USD * 100) if CAPITAL_PER_TRADE_USD else 0

    active_positions[product_id] = {
        "product_id":             product_id,
        "mode":                   "live" if LIVE_ORDERS_ACTIVE else "paper",
        "entry_mode":             entry_mode,
        "entry_limit_price":      float(limit_price or 0.0),
        "entry_timestamp":        _utcnow_iso(),
        "entry_price":            fill_price,
        "allocated_usd":          position_size,
        "original_allocated_usd": position_size,
        "simulated_qty":          crypto_qty,
        "original_simulated_qty": crypto_qty,
        "position_size_pct":      size_pct,
        "highest_tracked_price":  fill_price,
        "current_trailing_stop":  initial_stop,
        "take_profit_boundary":   take_profit_target,
        "main_take_profit_percent": main_tp_pct,
        "main_take_profit_reason": main_tp_reason,
        "quick_take_profit_boundary": fill_price * (1 + QUICK_TAKE_PROFIT_PERCENT / 100),
        "quick_take_profit_taken": False,
        "partial_take_profit_taken": False,
        "quick_take_profit_sell_percent": QUICK_TAKE_PROFIT_SELL_PERCENT,
        "take_profit_sell_percent": TAKE_PROFIT_SELL_PERCENT,
        "moon_bag_percent":       MOON_BAG_PERCENT,
        "signal_score":           product_data.get("score", 0),
        "pre_breakout_score":     product_data.get("pre_breakout_score", 0),
        "pre_breakout_features":  product_data.get("pre_breakout_features", {}),
        "entry_strategy":         signal["strategy"],
        "entry_strategy_score":   signal["score"],
        "entry_strategy_raw_score": signal.get("raw_score", signal["score"]),
        "entry_strategy_features": signal["features"],
        "entry_confidence_level": signal.get("confidence_level", "SINGLE"),
        "entry_consensus_count":  signal.get("consensus_count", 1),
        "entry_confirming_strategies": signal.get("confirming_strategies", [signal["strategy"]]),
        "orb_score":              product_data.get("orb_score", 0),
        "orb_features":           product_data.get("orb_features", {}),
        "bollinger_score":        product_data.get("bollinger_score", 0),
        "bollinger_features":     product_data.get("bollinger_features", {}),
        "wedge_score":            product_data.get("wedge_score", 0),
        "wedge_features":         product_data.get("wedge_features", {}),
        "momentum_runner_score":  product_data.get("momentum_runner_score", 0),
        "momentum_runner_features": product_data.get("momentum_runner_features", {}),
        "mtf_scores":             mtf_scores,
        "dollar_volume_24h":      float(product_data.get("dollar_volume_24h", 0.0) or 0.0),
        "breakout_dollar_volume": float(product_data.get("recent_window_dollar_volume", 0.0) or 0.0),
        "obv":                    product_data.get("obv", {}),
        "initial_stop_method":    stop_method,
        "fvg_candle_open":        _fvg_candle_open,
        "entry_buy_zone_low":     float(structure.get("buy_zone_low", 0.0) or 0.0),
        "entry_buy_zone_high":    float(structure.get("buy_zone_high", 0.0) or 0.0),
        "entry_support_level":    float(structure.get("support_level", 0.0) or 0.0),
        "entry_support_floor":    float(structure.get("support_floor", 0.0) or 0.0),
        "entry_stop_distance_pct": float(structure.get("stop_distance_pct", 0.0) or 0.0),
        "entry_sizing_notes":     list(sizing_reasons),
    }
    return {
        "crypto_qty": crypto_qty,
        "initial_stop": initial_stop,
        "take_profit_target": take_profit_target,
        "stop_method": stop_method,
        "size_pct": size_pct,
        "main_tp_pct": main_tp_pct,
        "main_tp_reason": main_tp_reason,
    }


def send_shadow_signal_summary(candidates: list[dict], reason: str) -> None:
    """
    Sends one Discord message for coins that SCORED ENOUGH to trade but were
    skipped because the position budget is full. These are NOT pattern alerts —
    they are real trade-quality signals that would have been bought if a slot
    was available.
    """
    if not candidates:
        return
    lines = [
        f"⚠️ MISSED TRADES — {len(candidates)} coin(s) cleared ALL entry gates but were NOT bought",
        f"   Reason: {reason}",
        f"   Limit: {MAX_OPEN_POSITIONS} positions max | ${CAPITAL_PER_TRADE_USD:,.0f}/coin | ${TOTAL_CAPITAL_USD:,.0f} total budget",
        f"   Gates: score ≥ {MIN_SIGNAL_SCORE:.0f}/100 | liquidity ✓ | OBV ✓ | market regime ✓",
        "",
    ]
    for i, item in enumerate(candidates, 1):
        conf_item = item.get("confidence_level", "SINGLE")
        conf_emoji = {"HIGH": "🔥", "MEDIUM": "⚡", "SINGLE": "📍"}.get(conf_item, "📍")
        lines.append(
            f"{i}. {item['product_id']} @ ${item['price']:,.6g}  {conf_emoji} {item['strategy'].replace('_', ' ')} {item['strategy_score']:.0f}/100\n"
            f"   Scores — Pattern {item['pattern_score']:.0f} | ORB {item['orb_score']:.0f} | BB {item['bollinger_score']:.0f} | Wedge {item['wedge_score']:.0f}  (all /100)\n"
            f"   Vol 24h: ${item['dollar_volume_24h']:,.0f} | OBV: {item['obv_pressure_pct']:+.1f}% | MTF: {item['mtf_summary']}\n"
            f"   Would buy: ${CAPITAL_PER_TRADE_USD:,.0f} → TP ${item['take_profit']:,.6g} (+{TAKE_PROFIT_PERCENT:.0f}%) | Stop ${item['stop']:,.6g} (-{TRAILING_PERCENT:.0f}%)"
        )
    message = "\n".join(lines)
    print("  " + message.replace("\n", "\n  "))
    send_discord_alert(message)


def build_scan_snapshot(products: list[dict], active_positions: dict, top_n: int = 25) -> dict:
    """
    Builds a read-only ranked snapshot of the current crypto opportunities using
    the SAME strategy stack the live engine uses (consensus scoring across candle
    breakout, ORB, Bollinger reversal, 24h momentum and descending wedge, plus the
    liquidity/OBV gates). This powers the dashboard's Crypto Scalp tab.

    Held positions are separated into their own 'positions' list with live P&L so
    the dashboard can show a dedicated 'Open Positions' panel instead of cluttering
    the Watchlist with 'already in position' noise. The main 'setups' list only
    contains coins NOT currently held, ranked by consensus score.
    """
    rows: list[dict] = []
    positions: list[dict] = []

    # Build a price lookup from the products list for position P&L
    price_map: dict[str, float] = {}
    for prod in products:
        pid = prod.get("product_id")
        price = float(prod.get("price", 0.0) or 0.0)
        if pid and price > 0:
            price_map[pid] = price

    # --- Enrich active positions with live score + P&L ---
    for product_id, pos in (active_positions.items() if isinstance(active_positions, dict) else []):
        entry_price = float(pos.get("entry_price", 0.0) or 0.0)
        current_price = price_map.get(product_id, 0.0)
        allocated = float(pos.get("allocated_usd", 0.0) or 0.0)
        original_allocated = float(pos.get("original_allocated_usd", allocated) or allocated)
        partial_taken = bool(pos.get("partial_take_profit_taken"))
        current_stop = float(pos.get("current_trailing_stop", 0.0) or 0.0)
        trail_pct = float(pos.get("current_trail_pct", TRAILING_PERCENT) or TRAILING_PERCENT)

        unrealized_pnl_pct = 0.0
        unrealized_pnl_usd = 0.0
        if entry_price > 0 and current_price > 0:
            unrealized_pnl_pct = round((current_price - entry_price) / entry_price * 100, 2)
            qty = float(pos.get("simulated_qty", 0.0) or 0.0)
            unrealized_pnl_usd = round((current_price - entry_price) * qty, 2)

        positions.append({
            "symbol": product_id,
            "entry_price": round(entry_price, 6),
            "current_price": round(current_price, 6),
            "allocated_usd": round(allocated, 2),
            "original_allocated_usd": round(original_allocated, 2),
            "unrealized_pnl_pct": unrealized_pnl_pct,
            "unrealized_pnl_usd": unrealized_pnl_usd,
            "current_stop": round(current_stop, 6),
            "trail_pct": round(trail_pct, 1),
            "partial_taken": partial_taken,
            "entry_score": round(float(pos.get("signal_score", 0.0) or 0.0), 1),
            "entry_timestamp": pos.get("entry_timestamp", ""),
            "mode": pos.get("mode", "paper"),
        })

    # --- Score non-held coins for setups / watchlist ---
    held_set = set(active_positions.keys()) if isinstance(active_positions, dict) else set()
    for prod in products:
        product_id = prod.get("product_id")
        price = float(prod.get("price", 0.0) or 0.0)
        if not product_id or price <= 0:
            continue
        if product_id in held_set:
            continue  # shown in positions panel instead

        signal = select_entry_signal(prod)
        liquidity = liquidity_filter_result(prod)
        obv = obv_filter_result(prod)
        structure = structure_filter_result(prod, signal)

        reasons: list[str] = []
        if not liquidity["ok"]:
            reasons.append(liquidity["reason"])
        if not obv["ok"]:
            reasons.append(obv["reason"])
        if not structure["ok"]:
            reasons.append(structure["reason"])
        if not signal["eligible"]:
            reasons.append(
                f"{signal['strategy']} score {signal['score']:.0f} below buy threshold"
            )

        eligible = signal["eligible"] and liquidity["ok"] and obv["ok"] and structure["ok"]
        liquidity_ok = liquidity["ok"]
        structural_stop = float(structure.get("stop_loss", 0.0) or 0.0)
        stop_loss = structural_stop if 0 < structural_stop < price else price * (1 - TRAILING_PERCENT / 100)
        target_pct, target_reason = _take_profit_percent_for_signal(signal)
        target1 = price * (1 + target_pct / 100)
        target2 = price * (1 + (target_pct * 2) / 100)

        rows.append({
            "product_id": product_id,
            "symbol": product_id,
            "price": round(price, 6),
            "strategy": signal["strategy"],
            "score": round(float(signal["score"]), 1),
            "raw_score": round(float(signal.get("raw_score", signal["score"])), 1),
            "confidence_level": signal.get("confidence_level", "SINGLE"),
            "consensus_count": int(signal.get("consensus_count", 1)),
            "consensus_bonus": round(float(signal.get("consensus_bonus", 0.0)), 1),
            "confirming_strategies": signal.get("confirming_strategies", [signal["strategy"]]),
            "eligible": eligible,
            "status": "READY" if eligible else "WATCH",
            "liquidity_ok": liquidity_ok,
            "reason": "meets all entry gates" if eligible else "; ".join(reasons),
            "base_score": round(float(prod.get("score", 0.0) or 0.0), 1),
            "pattern_score": round(float(prod.get("pre_breakout_score", 0.0) or 0.0), 1),
            "orb_score": round(float(prod.get("orb_score", 0.0) or 0.0), 1),
            "bollinger_score": round(float(prod.get("bollinger_score", 0.0) or 0.0), 1),
            "wedge_score": round(float(prod.get("wedge_score", 0.0) or 0.0), 1),
            "momentum_runner_score": round(float(prod.get("momentum_runner_score", 0.0) or 0.0), 1),
            "price_change_24h": round(float(prod.get("price_change_24h", 0.0) or 0.0), 2),
            "price_change_1h": round(float(prod.get("price_change_1h", 0.0) or 0.0), 2),
            "dollar_volume_24h": round(float(liquidity.get("dollar_volume_24h", 0.0) or 0.0), 2),
            "obv_pressure_pct": round(float(obv.get("metrics", {}).get("obv_pressure_pct", 0.0) or 0.0), 2),
            "buy_range_low": round(float(structure.get("buy_zone_low", price) or price), 6),
            "buy_range_high": round(float(structure.get("buy_zone_high", price * 1.005) or (price * 1.005)), 6),
            "support_level": round(float(structure.get("support_level", 0.0) or 0.0), 6),
            "support_floor": round(float(structure.get("support_floor", 0.0) or 0.0), 6),
            "stop_loss": round(stop_loss, 6),
            "target1": round(target1, 6),
            "target2": round(target2, 6),
            "take_profit_pct": round(target_pct, 2),
            "take_profit_reason": target_reason,
        })

    # Tier the results so the dashboard surfaces actionable coins first:
    #   Tier 1 — READY: pass all gates (eligible=True), sorted by score desc
    #   Tier 2 — LIQUID WATCH: fail only OBV or score gate but pass $5M liquidity
    #   Tier 3 — SMALL-CAP: fail liquidity gate (pattern signal, low volume)
    # Within each tier sort by score descending.
    def _tier(r: dict) -> int:
        if r["eligible"]:
            return 0
        liq_ok = "dollar volume" not in r.get("reason", "")
        return 1 if liq_ok else 2

    rows.sort(key=lambda r: (_tier(r), -r["score"]))
    top = rows[:top_n]
    return {
        "generated_at_utc": _utcnow_iso(),
        "strategy": "CoinbaseConsensus",
        "universe_size": len(products),
        "ready_count": sum(1 for r in top if r["eligible"]),
        "positions_count": len(positions),
        "config": {
            "capital_per_trade_usd": CAPITAL_PER_TRADE_USD,
            "take_profit_pct": TAKE_PROFIT_PERCENT,
            "normal_take_profit_pct": NORMAL_TAKE_PROFIT_PERCENT,
            "runner_take_profit_pct": RUNNER_TAKE_PROFIT_PERCENT,
            "wedge_take_profit_pct": WEDGE_TAKE_PROFIT_PERCENT,
            "high_consensus_take_profit_pct": HIGH_CONSENSUS_TAKE_PROFIT_PERCENT,
            "trailing_pct": TRAILING_PERCENT,
            "min_signal_score": MIN_SIGNAL_SCORE,
        },
        "positions": positions,
        "setups": top,
    }


def manage_pending_entries(client, active_positions: dict, pending_entries: dict,
                           products_by_id: dict[str, dict], live_prices: dict) -> tuple[dict, bool]:
    """Fills or cancels pending limit-style entries using later market runs."""
    if not isinstance(pending_entries, dict):
        pending_entries = {}
    changed = False
    now = datetime.now(timezone.utc)

    for product_id, order in list(pending_entries.items()):
        if product_id in active_positions:
            del pending_entries[product_id]
            changed = True
            continue

        expires_at = str(order.get("expires_at", "") or "")
        try:
            expiry_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00")) if expires_at else None
        except Exception:
            expiry_dt = None
        created_at = str(order.get("created_at", "") or "")
        try:
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00")) if created_at else None
        except Exception:
            created_dt = None
        order_age_minutes = (
            max(0.0, (now - created_dt).total_seconds() / 60.0)
            if created_dt else 0.0
        )
        if expiry_dt and now >= expiry_dt:
            print(f"  [LIMIT CANCEL] {product_id}: order expired before retest fill.")
            del pending_entries[product_id]
            changed = True
            continue

        current_price = float(live_prices.get(product_id, 0.0) or 0.0)
        if current_price <= 0:
            continue

        limit_price = float(order.get("limit_price", 0.0) or 0.0)
        invalidation_floor = float(order.get("invalidation_floor", 0.0) or 0.0)
        signal_price = float(order.get("signal_price", 0.0) or 0.0)
        buy_zone_high = float(order.get("buy_zone_high", 0.0) or 0.0)
        latest_low = current_price
        try:
            candles = fetch_candles(product_id)
            if candles:
                latest_low = float(candles[-1][1] or current_price)
        except Exception:
            candles = []

        if invalidation_floor > 0 and latest_low < invalidation_floor:
            print(
                f"  [LIMIT CANCEL] {product_id}: support failed "
                f"(low ${latest_low:,.6g} < ${invalidation_floor:,.6g})."
            )
            del pending_entries[product_id]
            changed = True
            continue

        reversal = detect_bearish_reversal(product_id, candles)
        if reversal.get("bearish") and (
            bool(reversal.get("severe"))
            or int(reversal.get("confirmation_count", 0) or 0) >= max(2, BEARISH_REVERSAL_MIN_CONFIRMATIONS)
        ):
            print(
                f"  [LIMIT CANCEL] {product_id}: bearish reversion invalidated retest "
                f"({reversal.get('reason', 'confirmed reversal')})."
            )
            del pending_entries[product_id]
            changed = True
            continue

        breakout_reference = max(limit_price, buy_zone_high, signal_price)
        miss_breakout_floor_pct = max(0.1, LIMIT_ENTRY_MISS_BREAKOUT_PCT)
        miss_breakout_cap_pct = max(miss_breakout_floor_pct, LIMIT_ENTRY_MISS_BREAKOUT_MAX_PCT)
        atr_pct = 0.0
        try:
            atr_pct = calculate_atr_pct(product_id)
        except Exception:
            atr_pct = 0.0
        atr_based_escape_pct = atr_pct * max(0.0, LIMIT_ENTRY_MISS_BREAKOUT_ATR_MULTIPLIER)
        miss_breakout_pct = min(
            miss_breakout_cap_pct,
            max(miss_breakout_floor_pct, atr_based_escape_pct),
        )
        breakout_escape_price = breakout_reference * (1 + miss_breakout_pct / 100.0) if breakout_reference > 0 else 0.0
        min_age_minutes = max(0, LIMIT_ENTRY_MIN_AGE_MINUTES)
        if (
            breakout_escape_price > 0
            and order_age_minutes >= min_age_minutes
            and current_price >= breakout_escape_price
            and latest_low > max(limit_price, buy_zone_high, 0.0)
        ):
            print(
                f"  [LIMIT CANCEL] {product_id}: breakout ran without retest "
                f"(spot ${current_price:,.6g} >= ${breakout_escape_price:,.6g}, "
                f"escape {miss_breakout_pct:.2f}%, ATR {atr_pct:.2f}%, age {order_age_minutes:.0f}m)."
            )
            del pending_entries[product_id]
            changed = True
            continue

        fill_buffer = max(0.0, LIMIT_ENTRY_FILL_BUFFER_PCT)
        touched_limit = latest_low <= limit_price if limit_price > 0 else False
        current_in_fill_zone = current_price <= (limit_price * (1 + fill_buffer / 100.0)) if limit_price > 0 else False
        if not touched_limit and not current_in_fill_zone:
            continue

        product_data = products_by_id.get(product_id)
        if not isinstance(product_data, dict):
            print(f"  [LIMIT HOLD] {product_id}: product snapshot missing, waiting for next run.")
            continue

        fill_price = current_price if current_price <= limit_price else limit_price
        signal = order.get("signal", {}) or {}
        structure = order.get("structure", {}) or {}
        mtf_scores = order.get("mtf_scores", {}) or {}
        sizing_reasons = list(order.get("sizing_reasons", []) or [])
        position_size = float(order.get("allocated_usd", CAPITAL_PER_TRADE_USD) or CAPITAL_PER_TRADE_USD)

        if LIVE_ORDERS_ACTIVE:
            if LIVE_ALLOWED_PRODUCTS and product_id.upper() not in LIVE_ALLOWED_PRODUCTS:
                print(f"  [LIVE LIMIT SKIP] {product_id}: not in LIVE_ALLOWED_PRODUCTS")
                del pending_entries[product_id]
                changed = True
                continue
            try:
                order_resp = client.market_order_buy(
                    client_order_id=f"trader-limit-fill-{product_id}-{int(now.timestamp())}",
                    product_id=product_id,
                    quote_size=str(round(position_size, 2)),
                )
                print(f"  [LIVE ORDER] {product_id} pending fill order_id={order_resp.get('order_id', 'unknown')}")
            except Exception as exc:
                print(f"  [LIVE ORDER ERROR] {product_id}: {exc}")
                continue

        entry_meta = _open_position_from_entry(
            active_positions,
            product_data,
            signal,
            structure,
            position_size,
            fill_price,
            mtf_scores,
            sizing_reasons,
            entry_mode="limit_fill",
            limit_price=limit_price,
        )
        del pending_entries[product_id]
        changed = True

        confidence_label = signal.get("confidence_level", "SINGLE")
        msg = (
            f"🟢 [{'LIVE BUY' if LIVE_ORDERS_ACTIVE else 'PAPER BUY'}] {product_id} — Pending Limit Filled\n"
            f"💵 Fill: ${fill_price:,.6g}  |  Limit: ${limit_price:,.6g}\n"
            f"📍 Support: ${float(structure.get('support_level', 0.0) or 0.0):,.6g}  |  "
            f"Zone: ${float(structure.get('buy_zone_low', 0.0) or 0.0):,.6g}-${float(structure.get('buy_zone_high', 0.0) or 0.0):,.6g}\n"
            f"🛡️ Stop: ${entry_meta['initial_stop']:,.6g} [{entry_meta['stop_method']}]\n"
            f"📈 Quick TP: +{QUICK_TAKE_PROFIT_PERCENT:.1f}%  |  Main TP: ${entry_meta['take_profit_target']:,.6g} (+{entry_meta['main_tp_pct']:.0f}% {entry_meta['main_tp_reason']})\n"
            f"📊 Entry strategy: {signal.get('strategy', 'unknown')} (score {float(signal.get('score', 0.0) or 0.0):.0f}, {confidence_label})"
        )
        print(f"  {msg}")
        send_discord_alert(msg)

    return pending_entries, changed


def scan_and_execute_entries(client, active_positions: dict, pending_entries: dict,
                             products: list[dict], market_state: dict, daily_ledger: dict):
    """
    Scans enriched product list, scores each coin, and enters positions
    only when score >= MIN_SIGNAL_SCORE and budget allows.
    Products are already sorted by 24h volume; we re-sort by score for entry priority.
    Coins that breached their daily loss cap are skipped until the next UTC day.
    """
    # Enrich each product with breakout pattern features and score.
    # Public snapshot already computed these; only fill in when missing.
    # Primary: candle-based detector. Fallback: rolling-snapshot detector.
    candle_candidates = sorted(
        products,
        key=_watchlist_quality_score,
        reverse=True,
    )[:CANDLE_SCAN_LIMIT]
    candle_ids = {p["product_id"] for p in candle_candidates}

    for prod in products:
        if "pre_breakout_score" in prod:
            continue  # already scored during the public market snapshot
        if prod["product_id"] in candle_ids:
            candles = fetch_candles(prod["product_id"])
            result = detect_recent_breakout_pattern(prod["product_id"], candles)
            orb = detect_orb_signal(prod["product_id"], candles)
            bollinger = detect_bollinger_reversal_signal(prod["product_id"], candles)
            prod["pre_breakout_features"] = result["features"]
            prod["pre_breakout_score"] = result["pattern_score"]
            prod["orb_features"] = orb["features"]
            prod["orb_score"] = orb["orb_score"]
            prod["bollinger_features"] = bollinger["features"]
            prod["bollinger_score"] = bollinger["bollinger_score"]
            prod["obv"] = calculate_obv_metrics(candles)
            prod["recent_window_dollar_volume"] = round(recent_dollar_volume(candles), 2)
            prod["price_change_15m"] = round(candle_change_pct(candles, 3), 3)
            prod["price_change_1h"] = round(candle_change_pct(candles, 12), 3)
            momentum = detect_momentum_runner_signal(prod)
            prod["momentum_runner_score"] = momentum["momentum_runner_score"]
            prod["momentum_runner_features"] = momentum["features"]
            wedge = detect_wedge_breakout(prod["product_id"])
            prod["wedge_features"] = wedge["features"]
            prod["wedge_score"] = wedge["wedge_score"]
            # Compute 30-min rolling window dollar volume (best 6-candle sum).
            # Low-price tokens have tiny single-candle $ values but healthy 30m flow.
            if len(candles) >= 6:
                last_price = float(candles[-1][4] or 0.0)
                if last_price > 0:
                    best_window_usd = 0.0
                    for _wi in range(len(candles) - 6, len(candles)):
                        window_usd = sum(float(candles[_j][5] or 0.0) * float(candles[_j][4] or 0.0)
                                        for _j in range(max(0, _wi - 5), _wi + 1))
                        best_window_usd = max(best_window_usd, window_usd)
                    if result["features"]:
                        result["features"]["breakout_window_dollar_volume"] = round(best_window_usd, 2)
                        prod["pre_breakout_features"] = result["features"]
        else:
            features = _compute_pre_breakout_features(prod, market_state)
            prod["pre_breakout_features"] = features
            prod["pre_breakout_score"] = compute_pre_breakout_score(features)
            prod.setdefault("orb_features", {})
            prod.setdefault("orb_score", 0.0)
            prod.setdefault("bollinger_features", {})
            prod.setdefault("bollinger_score", 0.0)
            prod.setdefault("wedge_features", {})
            prod.setdefault("wedge_score", 0.0)

    # Prioritise either strong breakout setup or strong base score.
    by_score = sorted(
        products,
        key=lambda x: select_entry_signal(x)["score"],
        reverse=True,
    )

    market_context = get_btc_market_context() if MARKET_REGIME_FILTER else {"allow_buys": True, "reason": "disabled", "btc_1h_change": 0.0}
    if MARKET_REGIME_FILTER:
        print(f"  [Market Regime] {market_context['reason']} | BTC 15m {market_context.get('btc_15m_change', 0.0):+.2f}% | BTC 1h {market_context.get('btc_1h_change', 0.0):+.2f}%")
    if MARKET_REGIME_FILTER and not market_context["allow_buys"]:
        print("  [Market Regime] New entries paused; managing existing positions only.")
        save_json_file(PORTFOLIO_FILE, active_positions)
        return

    shadow_candidates = []
    budget_skip_reason = ""

    # Portfolio daily stop: if total loss today >= MAX_DAILY_LOSS_PCT, skip all entries.
    if is_portfolio_stopped_today(daily_ledger):
        total_loss = sum(v for v in daily_ledger.get("realized", {}).values() if v < 0)
        print(f"  [Portfolio Stop] Skipping all entries — daily loss ${total_loss:+.2f} "
              f"hit {MAX_DAILY_LOSS_PCT:.0f}% cap. Trading resumes tomorrow.")
        return

    for prod in by_score:
        product_id = prod["product_id"]
        price      = prod["price"]

        if product_id in active_positions or product_id in pending_entries:
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

        signal = select_entry_signal(prod)
        structure = structure_filter_result(prod, signal)
        if not structure["ok"]:
            print(f"  [STRUCTURE SKIP] {product_id}: {structure['reason']}")
            continue

        if MARKET_REGIME_FILTER and product_id != "BTC-USD":
            coin_1h = float(prod.get("price_change_1h", 0.0) or 0.0)
            btc_1h = float(market_context.get("btc_1h_change", 0.0) or 0.0)
            rel_strength = coin_1h - btc_1h
            if rel_strength < MIN_REL_STRENGTH_VS_BTC:
                print(f"  [RS SKIP] {product_id}: 1h {coin_1h:+.2f}% vs BTC {btc_1h:+.2f}% (RS {rel_strength:+.2f}% < {MIN_REL_STRENGTH_VS_BTC:.2f}%)")
                continue
        if signal["eligible"]:
            # Multi-timeframe confirmation: the 5m trigger must hold up on 15m/1h
            # and not contradict the 4h trend. Only runs for triggered coins, so
            # the extra candle calls are limited to a handful per cycle.
            mtf_scores = {}
            if MULTI_TIMEFRAME_CONFIRM and signal["requires_mtf"]:
                mtf_min_score = MTF_CONFIRM_MIN_SCORE
                if int(signal.get("consensus_count", 0) or 0) >= HIGH_CONSENSUS_MIN_COUNT:
                    mtf_min_score = max(0.0, MTF_CONFIRM_MIN_SCORE - HIGH_CONSENSUS_MTF_TOLERANCE)
                mtf = detect_multi_timeframe_signal(product_id, mtf_min_score)
                mtf_scores = mtf["scores"]
                if not mtf["confirmed"]:
                    print(f"  [MTF SKIP] {product_id}: {mtf['reason']} | {mtf['summary']}")
                    continue
                print(f"  [MTF OK]   {product_id}: {mtf['summary']}")

            position_size      = _position_size_for_score(signal["score"])
            sizing_reasons     = []
            if signal.get("strategy") == "EARLY_MOMENTUM_RUNNER":
                momentum_cap = CAPITAL_PER_TRADE_USD * max(10.0, min(100.0, MOMENTUM_RUNNER_MAX_POSITION_PCT)) / 100.0
                position_size = min(position_size, round(momentum_cap, 2))
                sizing_reasons.append(f"runner cap {MOMENTUM_RUNNER_MAX_POSITION_PCT:.0f}%")
            position_size, fragile_reasons = _risk_adjusted_position_size(prod, signal, position_size)
            sizing_reasons.extend(fragile_reasons)

            if _budget_is_full(active_positions, pending_entries, next_size=position_size):
                budget_skip_reason = (
                    f"budget is full ({len(active_positions) + len(pending_entries)}/{MAX_OPEN_POSITIONS} slots, "
                    f"${_capital_deployed(active_positions) + _pending_capital_reserved(pending_entries):,.0f}/${TOTAL_CAPITAL_USD:,.0f} reserved)"
                )
                if len(shadow_candidates) < SHADOW_ALERT_MAX_PER_CYCLE and should_include_shadow_alert(product_id, signal["strategy"]):
                    shadow_candidates.append(build_shadow_candidate(prod, signal, liquidity, obv, mtf_scores))
                if len(shadow_candidates) >= SHADOW_ALERT_MAX_PER_CYCLE:
                    break
                continue

            if (not LIVE_ORDERS_ACTIVE) and _limit_entry_allowed_for_signal(signal, structure):
                limit_price = _limit_entry_price(signal, structure, price)
                if limit_price <= 0:
                    limit_price = price
                expiry_dt = datetime.now(timezone.utc) + timedelta(minutes=max(5, LIMIT_ENTRY_EXPIRY_MINUTES))
                pending_entries[product_id] = {
                    "product_id": product_id,
                    "created_at": _utcnow_iso(),
                    "expires_at": expiry_dt.isoformat(),
                    "mode": "paper",
                    "entry_style": "limit_retest",
                    "limit_price": float(limit_price),
                    "signal_price": float(price),
                    "allocated_usd": float(position_size),
                    "signal": signal,
                    "structure": structure,
                    "mtf_scores": mtf_scores,
                    "sizing_reasons": list(sizing_reasons),
                    "support_level": float(structure.get("support_level", 0.0) or 0.0),
                    "support_floor": float(structure.get("support_floor", 0.0) or 0.0),
                    "invalidation_floor": float(structure.get("support_floor", 0.0) or 0.0),
                    "buy_zone_low": float(structure.get("buy_zone_low", 0.0) or 0.0),
                    "buy_zone_high": float(structure.get("buy_zone_high", 0.0) or 0.0),
                    "product_snapshot": {
                        "price": float(price),
                        "score": float(prod.get("score", 0.0) or 0.0),
                        "dollar_volume_24h": float(prod.get("dollar_volume_24h", 0.0) or 0.0),
                    },
                }

                confirming_strategies = signal.get("confirming_strategies", [signal["strategy"]])
                confidence_label = signal.get("confidence_level", "SINGLE")
                raw_score = signal.get("raw_score", signal["score"])
                bonus = signal.get("consensus_bonus", 0.0)
                bonus_str = f" +{bonus:.0f} bonus" if bonus > 0 else ""
                sizing_note = f"\n   Sizing: {'; '.join(sizing_reasons)}" if sizing_reasons else ""
                confirming_str = " + ".join(s.replace("_", " ") for s in confirming_strategies)
                msg = (
                    f"🟦 [PAPER LIMIT] {product_id}\n"
                    f"💵 Signal: ${price:,.6g}  |  Limit: ${limit_price:,.6g}  |  Size: ${position_size:,.0f}\n"
                    f"📍 Support: ${float(structure.get('support_level', 0.0) or 0.0):,.6g}  |  "
                    f"Zone: ${float(structure.get('buy_zone_low', 0.0) or 0.0):,.6g}-${float(structure.get('buy_zone_high', 0.0) or 0.0):,.6g}\n"
                    f"⏳ Expires: {expiry_dt.strftime('%Y-%m-%d %H:%M UTC')}\n"
                    f"📊 Score: {signal['score']:.0f}/100 [{confidence_label}]  (raw {raw_score:.0f}{bonus_str})\n"
                    f"   Strategy: {signal['strategy'].replace('_', ' ')}\n"
                    f"   Confirmed by: {confirming_str}"
                    f"{sizing_note}"
                )
                print(f"  {msg}")
                send_discord_alert(msg)
                continue

            mode_label = "LIVE BUY" if LIVE_ORDERS_ACTIVE else "PAPER BUY"

            if LIVE_ORDERS_ACTIVE:
                if LIVE_ALLOWED_PRODUCTS and product_id.upper() not in LIVE_ALLOWED_PRODUCTS:
                    print(f"  [LIVE SKIP] {product_id}: not in LIVE_ALLOWED_PRODUCTS")
                    continue
                # --- LIVE ORDER EXECUTION ---
                # Requires API key with 'trade' permission.
                # Coinbase minimum order is $1 USD.
                try:
                    order = client.market_order_buy(
                        client_order_id=f"trader-{product_id}-{int(datetime.now(timezone.utc).timestamp())}",
                        product_id=product_id,
                        quote_size=str(round(position_size, 2)),
                    )
                    order_id = order.get("order_id", "unknown")
                    print(f"  [LIVE ORDER] {product_id} order_id={order_id}")
                except Exception as exc:
                    print(f"  [LIVE ORDER ERROR] {product_id}: {exc}")
                    continue  # skip position tracking if order failed

            entry_meta = _open_position_from_entry(
                active_positions,
                prod,
                signal,
                structure,
                position_size,
                price,
                mtf_scores,
                sizing_reasons,
                entry_mode="market",
            )

            confirming_strategies = signal.get("confirming_strategies", [signal["strategy"]])
            confidence_label = signal.get("confidence_level", "SINGLE")
            raw_score = signal.get("raw_score", signal["score"])
            bonus = signal.get("consensus_bonus", 0.0)
            bonus_str = f" +{bonus:.0f} bonus" if bonus > 0 else ""
            sizing_note = f"\n   Sizing: {'; '.join(sizing_reasons)}" if sizing_reasons else ""
            conf_emoji = {"HIGH": "🔥", "MEDIUM": "⚡", "SINGLE": "📍"}.get(confidence_label, "📍")
            mode_emoji = "🟢"
            confirming_str = " + ".join(s.replace("_", " ") for s in confirming_strategies)
            msg = (
                f"{mode_emoji} [{mode_label}] {product_id}\n"
                f"💵 Entry: ${price:,.6g}  |  Size: ${position_size:,.0f} ({entry_meta['size_pct']}% · score {signal['score']:.0f})  |  Qty: {entry_meta['crypto_qty']:.6g}\n"
                f"{conf_emoji} Score: {signal['score']:.0f}/100 [{confidence_label}]  (raw {raw_score:.0f}{bonus_str})\n"
                f"   Strategy: {signal['strategy'].replace('_', ' ')}\n"
                f"   Confirmed by: {confirming_str}\n"
                f"   Pattern {prod.get('pre_breakout_score', 0):.0f}  |  ORB {prod.get('orb_score', 0):.0f}  |  BB {prod.get('bollinger_score', 0):.0f}  |  Wedge {prod.get('wedge_score', 0):.0f}  |  Runner {prod.get('momentum_runner_score', 0):.0f}  (all /100)\n"
                f"📈 Quick TP: +{QUICK_TAKE_PROFIT_PERCENT:.1f}%/{QUICK_TAKE_PROFIT_SELL_PERCENT:.0f}%  |  Main TP: ${entry_meta['take_profit_target']:,.6g} (+{entry_meta['main_tp_pct']:.0f}% {entry_meta['main_tp_reason']})  |  Stop: ${entry_meta['initial_stop']:,.6g} [{entry_meta['stop_method']}]\n"
                f"💧 24h Vol: ${liquidity.get('dollar_volume_24h', 0.0):,.0f}  |  OBV: {obv.get('metrics', {}).get('obv_pressure_pct', 0.0):+.1f}%\n"
                f"🏦 Budget used: ${_capital_deployed(active_positions) + _pending_capital_reserved(pending_entries):,.0f} / ${TOTAL_CAPITAL_USD:,.0f}  ({len(active_positions) + len(pending_entries)}/{MAX_OPEN_POSITIONS} slots)"
                f"{sizing_note}"
            )
            print(f"  {msg}")
            send_discord_alert(msg)

    if shadow_candidates:
        send_shadow_signal_summary(shadow_candidates, budget_skip_reason)

    save_json_file(PORTFOLIO_FILE, active_positions)
    save_json_file(PENDING_ENTRY_FILE, pending_entries)


def calculate_atr_pct(product_id: str, candles: list[list] | None = None, period: int = ATR_PERIOD) -> float:
    """
    Returns the Average True Range as a percentage of the latest close.
    Candle shape: [time, low, high, open, close, volume]. 0.0 on insufficient data.
    """
    if candles is None:
        candles = fetch_candles(product_id, granularity=ATR_CANDLE_GRANULARITY)
    if len(candles) < period + 1:
        return 0.0
    true_ranges = []
    for idx in range(1, len(candles)):
        high = float(candles[idx][2] or 0.0)
        low = float(candles[idx][1] or 0.0)
        prev_close = float(candles[idx - 1][4] or 0.0)
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    atr = sum(true_ranges[-period:]) / period
    last_close = float(candles[-1][4] or 0.0)
    return (atr / last_close * 100) if last_close else 0.0


def _trail_pct_for_profit(profit_pct: float) -> float:
    """Returns the tier trail-floor % for the current unrealized profit level."""
    trail = TRAILING_PERCENT
    for threshold, tier_trail in TRAIL_TIERS:
        if profit_pct >= threshold:
            trail = tier_trail
        else:
            break
    return trail


def compute_trailing_stop_pct(product_id: str, entry_price: float, current_price: float,
                              atr_cache: dict) -> tuple[float, float]:
    """
    Returns (trail_pct, profit_pct) for a position. The trail widens as profit
    grows (tiers) and adapts to volatility (ATR), capped at TRAIL_MAX_PCT.
    """
    profit_pct = ((current_price - entry_price) / entry_price * 100) if entry_price else 0.0
    tier_floor = _trail_pct_for_profit(profit_pct) if TIERED_TRAILING_ENABLED else TRAILING_PERCENT
    trail_pct = tier_floor
    if ATR_TRAILING_ENABLED:
        if product_id not in atr_cache:
            atr_cache[product_id] = calculate_atr_pct(product_id)
        atr_pct = atr_cache[product_id]
        if atr_pct and atr_pct > 0:
            trail_pct = max(tier_floor, ATR_TRAIL_MULTIPLIER * atr_pct)
    trail_pct = min(TRAIL_MAX_PCT, max(TRAILING_PERCENT, trail_pct))
    return round(trail_pct, 3), round(profit_pct, 3)


def _position_profit_pct(pos: dict, current_price: float) -> float:
    entry_price = float(pos.get("entry_price", 0.0) or 0.0)
    return ((current_price - entry_price) / entry_price * 100) if entry_price else 0.0


def _position_held_minutes(pos: dict) -> int:
    entry_ts = pos.get("entry_timestamp", "")
    if not entry_ts:
        return 0
    try:
        entry_dt = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))
        return max(0, int((datetime.now(timezone.utc) - entry_dt).total_seconds() / 60))
    except Exception:
        return 0


def _entry_review_snapshot(pos: dict) -> dict:
    """Captures entry context so closed-trade review can explain why a trade failed."""
    features = pos.get("entry_strategy_features", {}) or {}
    return {
        "entry_strategy": pos.get("entry_strategy", "unknown"),
        "entry_strategy_score": float(pos.get("entry_strategy_score", pos.get("signal_score", 0.0)) or 0.0),
        "entry_confidence_level": pos.get("entry_confidence_level", "SINGLE"),
        "entry_consensus_count": int(pos.get("entry_consensus_count", 0) or 0),
        "entry_confirming_strategies": pos.get("entry_confirming_strategies", []),
        "initial_stop_method": pos.get("initial_stop_method", ""),
        "entry_buy_zone_low": float(pos.get("entry_buy_zone_low", 0.0) or 0.0),
        "entry_buy_zone_high": float(pos.get("entry_buy_zone_high", 0.0) or 0.0),
        "entry_support_level": float(pos.get("entry_support_level", 0.0) or 0.0),
        "entry_support_floor": float(pos.get("entry_support_floor", 0.0) or 0.0),
        "entry_stop_distance_pct": float(pos.get("entry_stop_distance_pct", 0.0) or 0.0),
        "entry_successful_retest": bool(features.get("successful_retest", False)),
        "entry_accepted_above_level": bool(features.get("accepted_above_level", False)),
        "entry_in_buy_zone": bool(features.get("entry_in_buy_zone", False)),
        "entry_retest_age_candles": features.get("retest_age_candles"),
        "entry_pre_breakout_score": float(pos.get("pre_breakout_score", 0.0) or 0.0),
        "entry_orb_score": float(pos.get("orb_score", 0.0) or 0.0),
        "entry_bollinger_score": float(pos.get("bollinger_score", 0.0) or 0.0),
        "entry_wedge_score": float(pos.get("wedge_score", 0.0) or 0.0),
        "entry_momentum_runner_score": float(pos.get("momentum_runner_score", 0.0) or 0.0),
    }


def _should_exit_on_bearish_reversal(pos: dict, current_price: float, reversal: dict) -> tuple[bool, str]:
    """
    Filters noisy bearish-reversal exits. The bot should not close promising
    runners just because one pullback candle appears; quick TP/breakeven handles
    early protection, while the main target remains 15%+.
    """
    profit_pct = _position_profit_pct(pos, current_price)
    held_minutes = _position_held_minutes(pos)
    severe = bool(reversal.get("severe"))
    confirmations = int(reversal.get("confirmation_count", 0) or 0)

    if pos.get("partial_take_profit_taken"):
        if severe:
            return True, "severe reversal after partial take-profit"
        if profit_pct <= POST_TP_BEARISH_EXIT_MIN_GAIN_PCT:
            return True, (
                f"post-TP runner faded to {profit_pct:+.2f}% <= "
                f"{POST_TP_BEARISH_EXIT_MIN_GAIN_PCT:+.2f}%"
            )
        return False, (
            f"moon bag still healthy at {profit_pct:+.2f}% after partial take-profit"
        )
    if profit_pct <= BEARISH_EXIT_MAX_LOSS_PCT:
        return True, f"loss {profit_pct:+.2f}% <= {BEARISH_EXIT_MAX_LOSS_PCT:+.2f}%"
    if severe and profit_pct >= BEARISH_EXIT_SEVERE_MIN_PROFIT_PCT:
        return True, f"severe reversal; lock larger gain {profit_pct:+.2f}%"
    if held_minutes >= BEARISH_EXIT_MIN_HOLD_MINUTES and profit_pct <= BEARISH_EXIT_STALL_PROFIT_PCT:
        return True, f"stalled {held_minutes}m with {profit_pct:+.2f}%"

    return False, (
        f"hold despite {reversal.get('reason', 'bearish signal')}: "
        f"{profit_pct:+.2f}% after {held_minutes}m "
        f"({confirmations} confirmations, severe={severe})"
    )


def _migrate_legacy_position(pos: dict) -> bool:
    """Back-fills fields that were added after a position was first persisted.
    Returns True if any field was updated (signals the caller to save the file)."""
    changed = False

    # original_allocated_usd tracks full size before moon-bag partial sells.
    if "original_allocated_usd" not in pos:
        pos["original_allocated_usd"] = float(pos.get("allocated_usd", CAPITAL_PER_TRADE_USD) or CAPITAL_PER_TRADE_USD)
        changed = True

    # Moon-bag / take-profit percentages: back-fill from current env so the
    # manage loop doesn't fall back to hard-coded 100% sell (full exit).
    if "take_profit_sell_percent" not in pos:
        pos["take_profit_sell_percent"] = TAKE_PROFIT_SELL_PERCENT
        changed = True
    if "moon_bag_percent" not in pos:
        pos["moon_bag_percent"] = MOON_BAG_PERCENT
        changed = True
    if "quick_take_profit_taken" not in pos:
        pos["quick_take_profit_taken"] = False
        changed = True
    if "quick_take_profit_sell_percent" not in pos:
        pos["quick_take_profit_sell_percent"] = QUICK_TAKE_PROFIT_SELL_PERCENT
        changed = True

    # Keep open positions aligned with strategy-specific main targets. Normal
    # breakouts should not wait for runner-sized moves, while runners/wedges can.
    entry_price = float(pos.get("entry_price", 0.0) or 0.0)
    current_tp = float(pos.get("take_profit_boundary", 0.0) or 0.0)
    if entry_price > 0 and "quick_take_profit_boundary" not in pos:
        pos["quick_take_profit_boundary"] = round(entry_price * (1 + QUICK_TAKE_PROFIT_PERCENT / 100), 8)
        changed = True
    if entry_price > 0 and current_tp > 0 and not pos.get("partial_take_profit_taken"):
        stored_tp_pct = (current_tp - entry_price) / entry_price * 100
        desired_tp_pct, desired_tp_reason = _take_profit_percent_for_position(pos)
        if abs(stored_tp_pct - desired_tp_pct) > 0.25:
            pos["take_profit_boundary"] = round(entry_price * (1 + desired_tp_pct / 100), 8)
            pos["main_take_profit_percent"] = desired_tp_pct
            pos["main_take_profit_reason"] = desired_tp_reason
            print(
                f"  [MIGRATE] {pos.get('product_id','?')} TP adjusted from "
                f"+{stored_tp_pct:.1f}% → +{desired_tp_pct:.1f}% ({desired_tp_reason}) "
                f"(${current_tp:,.6g} → ${pos['take_profit_boundary']:,.6g})"
            )
            changed = True
    if "main_take_profit_percent" not in pos and entry_price > 0:
        desired_tp_pct, desired_tp_reason = _take_profit_percent_for_position(pos)
        pos["main_take_profit_percent"] = desired_tp_pct
        pos["main_take_profit_reason"] = desired_tp_reason
        changed = True

    # entry_strategy: infer from available features when missing.
    if not pos.get("entry_strategy"):
        if pos.get("pre_breakout_features"):
            pos["entry_strategy"] = "candle_breakout"
        elif pos.get("orb_features"):
            pos["entry_strategy"] = "orb"
        elif pos.get("bollinger_features"):
            pos["entry_strategy"] = "bollinger_reversal"
        else:
            pos["entry_strategy"] = "breakout"
        changed = True

    return changed


def manage_active_positions(client, active_positions: dict, live_prices: dict, daily_ledger: dict):
    """Updates trailing stops and closes positions on take-profit, trailing stop,
    or a confirmed bearish reversal. Records realized PnL into the daily ledger."""
    closed = []
    changed = False
    history = load_json_file(HISTORY_FILE)
    atr_cache: dict = {}

    for product_id, pos in list(active_positions.items()):
        current_price = live_prices.get(product_id, 0.0)
        if current_price <= 0:
            continue

        # Back-fill any fields that were added after this position was persisted.
        if _migrate_legacy_position(pos):
            changed = True

        entry_price = float(pos.get("entry_price", 0.0) or 0.0)
        profit_pct_now = _position_profit_pct(pos, current_price)

        # Once the trade has moved enough in our favor, pull the stop above
        # breakeven. This protects capital while still letting the setup attempt
        # the real take-profit target instead of closing on tiny noisy reversals.
        if BREAKEVEN_STOP_ENABLED and entry_price > 0 and profit_pct_now >= BREAKEVEN_TRIGGER_PCT:
            breakeven_stop = entry_price * (1 + BREAKEVEN_BUFFER_PCT / 100)
            current_stop = float(pos.get("current_trailing_stop", 0.0) or 0.0)
            if breakeven_stop > current_stop:
                pos["current_trailing_stop"] = breakeven_stop
                pos["breakeven_stop_active"] = True
                changed = True
                print(
                    f"  [STOP BE] {product_id} +{profit_pct_now:.2f}% → "
                    f"stop raised to ${breakeven_stop:,.6g} "
                    f"(entry +{BREAKEVEN_BUFFER_PCT:.2f}%)"
                )

        # Ratchet trailing stop upward. The trail width widens as unrealized
        # profit grows (tiers) and adapts to volatility (ATR), so proven runners
        # get room to breathe instead of being shaken out on the first pullback.
        if current_price > pos["highest_tracked_price"]:
            pos["highest_tracked_price"] = current_price
            trail_pct, profit_pct = compute_trailing_stop_pct(
                product_id, float(pos.get("entry_price", 0.0) or 0.0), current_price, atr_cache
            )
            new_stop = current_price * (1 - trail_pct / 100)
            pos["current_trailing_stop"] = max(float(pos.get("current_trailing_stop", 0.0) or 0.0), new_stop)
            pos["current_trail_pct"] = trail_pct
            print(
                f"  [STOP UP] {product_id} new high ${current_price:,.2f} "
                f"(+{profit_pct:.1f}%) → trail {trail_pct:.1f}% → "
                f"stop ${pos['current_trailing_stop']:,.2f}"
            )

        exit_triggered = False
        exit_reason    = ""

        quick_tp = float(pos.get("quick_take_profit_boundary", 0.0) or 0.0)
        if (
            QUICK_TAKE_PROFIT_ENABLED
            and quick_tp > 0
            and current_price >= quick_tp
            and not pos.get("quick_take_profit_taken", False)
            and not pos.get("partial_take_profit_taken", False)
        ):
            sell_fraction = max(0.0, min(1.0, float(pos.get("quick_take_profit_sell_percent", QUICK_TAKE_PROFIT_SELL_PERCENT) or 0.0) / 100.0))
            if sell_fraction > 0.0:
                partial_qty = float(pos["simulated_qty"]) * sell_fraction
                partial_allocated = float(pos.get("allocated_usd", CAPITAL_PER_TRADE_USD) or 0.0) * sell_fraction
                partial_value = partial_qty * current_price
                pnl_usd = partial_value - partial_allocated
                pnl_pct = (pnl_usd / partial_allocated) * 100 if partial_allocated else 0.0
                mode_label = "LIVE SELL" if pos.get("mode") == "live" else "PAPER SELL"

                if pos.get("mode") == "live":
                    try:
                        order = client.market_order_sell(
                            client_order_id=f"trader-quick-tp-{product_id}-{int(datetime.now(timezone.utc).timestamp())}",
                            product_id=product_id,
                            base_size=str(round(partial_qty, 8)),
                        )
                        print(f"  [LIVE QUICK TP SELL] {product_id} order_id={order.get('order_id', 'unknown')}")
                    except Exception as exc:
                        print(f"  [LIVE QUICK TP SELL ERROR] {product_id}: {exc}")
                        continue

                pos["simulated_qty"] = float(pos["simulated_qty"]) - partial_qty
                pos["allocated_usd"] = float(pos.get("allocated_usd", CAPITAL_PER_TRADE_USD) or 0.0) - partial_allocated
                pos["quick_take_profit_taken"] = True
                pos["quick_take_profit_at"] = _utcnow_iso()
                breakeven_stop = float(pos.get("entry_price", 0.0) or 0.0) * (1 + BREAKEVEN_BUFFER_PCT / 100)
                if breakeven_stop > float(pos.get("current_trailing_stop", 0.0) or 0.0):
                    pos["current_trailing_stop"] = breakeven_stop
                    pos["breakeven_stop_active"] = True

                main_tp_pct = float(pos.get("main_take_profit_percent", TAKE_PROFIT_PERCENT) or TAKE_PROFIT_PERCENT)
                trade_record = {
                    "strategy":         "Automated Multi-Asset Watchlist Engine",
                    "product_id":       product_id,
                    "mode":             pos.get("mode", "paper"),
                    "live_data_source": "Coinbase Advanced API",
                    "config": {
                        "trailing_percent":                 TRAILING_PERCENT,
                        "quick_take_profit_percent":        QUICK_TAKE_PROFIT_PERCENT,
                        "quick_take_profit_sell_percent":   sell_fraction * 100,
                        "take_profit_percent":              main_tp_pct,
                        "total_capital_usd":                TOTAL_CAPITAL_USD,
                    },
                    "entry": {
                        "timestamp":             pos["entry_timestamp"],
                        "price_usd":             pos["entry_price"],
                        "allocated_capital_usd": partial_allocated,
                        "simulated_quantity":    partial_qty,
                    },
                    "exit": {
                        "timestamp":                 _utcnow_iso(),
                        "reason":                    "QUICK_TAKE_PROFIT_PARTIAL",
                        "price_usd":                 current_price,
                        "highest_tracked_price_usd": pos["highest_tracked_price"],
                    },
                    "performance": {
                        "pnl_usd":        pnl_usd,
                        "pnl_percentage": pnl_pct,
                        "status":         "PARTIAL",
                    },
                    "entry_review": _entry_review_snapshot(pos),
                }
                history.append(trade_record)
                record_daily_pnl(daily_ledger, product_id, pnl_usd)
                changed = True

                entry_price    = float(pos.get("entry_price", 0.0) or 0.0)
                entry_strategy = pos.get("entry_strategy", "unknown").replace("_", " ")
                entry_score    = pos.get("entry_strategy_score", pos.get("signal_score", 0))
                move_from_entry = ((current_price - entry_price) / entry_price * 100) if entry_price else 0.0
                msg = (
                    f"🟡 [{mode_label}] {product_id} — Quick Take-Profit\n"
                    f"💵 Exit: ${current_price:,.6g}  |  Entry: ${entry_price:,.6g} ({move_from_entry:+.2f}%)\n"
                    f"💰 Realized: ${pnl_usd:+.2f} ({pnl_pct:+.2f}%) on {sell_fraction * 100:.0f}% sold\n"
                    f"🛡️ Stop: ${pos['current_trailing_stop']:,.6g}  |  Remaining size: ${float(pos.get('allocated_usd', 0.0) or 0.0):,.2f}\n"
                    f"📊 Entry strategy: {entry_strategy} (score {entry_score:.0f})"
                )
                print(f"  {msg}")
                send_discord_alert(msg)
                continue

        if current_price >= pos["take_profit_boundary"] and not pos.get("partial_take_profit_taken", False):
            sell_fraction = max(0.0, min(1.0, float(pos.get("take_profit_sell_percent", TAKE_PROFIT_SELL_PERCENT) or 0.0) / 100.0))
            if sell_fraction >= 1.0:
                exit_triggered = True
                exit_reason    = "TAKE_PROFIT_LIMIT_HIT"
            elif sell_fraction > 0.0:
                partial_qty = float(pos["simulated_qty"]) * sell_fraction
                partial_allocated = float(pos.get("allocated_usd", CAPITAL_PER_TRADE_USD) or 0.0) * sell_fraction
                partial_value = partial_qty * current_price
                pnl_usd = partial_value - partial_allocated
                pnl_pct = (pnl_usd / partial_allocated) * 100 if partial_allocated else 0.0
                mode_label = "LIVE SELL" if pos.get("mode") == "live" else "PAPER SELL"

                if pos.get("mode") == "live":
                    try:
                        order = client.market_order_sell(
                            client_order_id=f"trader-tp-{product_id}-{int(datetime.now(timezone.utc).timestamp())}",
                            product_id=product_id,
                            base_size=str(round(partial_qty, 8)),
                        )
                        print(f"  [LIVE PARTIAL SELL] {product_id} order_id={order.get('order_id', 'unknown')}")
                    except Exception as exc:
                        print(f"  [LIVE PARTIAL SELL ERROR] {product_id}: {exc}")
                        continue

                pos["simulated_qty"] = float(pos["simulated_qty"]) - partial_qty
                pos["allocated_usd"] = float(pos.get("allocated_usd", CAPITAL_PER_TRADE_USD) or 0.0) - partial_allocated
                pos["partial_take_profit_taken"] = True
                pos["moon_bag_started_at"] = _utcnow_iso()
                pos["moon_bag_entry_price"] = current_price
                # Moon bag rides a wider, profit-/volatility-aware trail so a big
                # winner is not stopped out on a normal pullback after take-profit.
                moon_trail_pct, _ = compute_trailing_stop_pct(
                    product_id, float(pos.get("entry_price", 0.0) or 0.0), current_price, atr_cache
                )
                pos["current_trailing_stop"] = max(
                    float(pos.get("current_trailing_stop", 0.0) or 0.0),
                    current_price * (1 - moon_trail_pct / 100),
                )
                pos["current_trail_pct"] = moon_trail_pct

                main_tp_pct = float(pos.get("main_take_profit_percent", TAKE_PROFIT_PERCENT) or TAKE_PROFIT_PERCENT)
                trade_record = {
                    "strategy":         "Automated Multi-Asset Watchlist Engine",
                    "product_id":       product_id,
                    "mode":             pos.get("mode", "paper"),
                    "live_data_source": "Coinbase Advanced API",
                    "config": {
                        "trailing_percent":           TRAILING_PERCENT,
                        "take_profit_percent":        main_tp_pct,
                        "take_profit_sell_percent":   sell_fraction * 100,
                        "moon_bag_percent":           100 - (sell_fraction * 100),
                        "total_capital_usd":          TOTAL_CAPITAL_USD,
                    },
                    "entry": {
                        "timestamp":             pos["entry_timestamp"],
                        "price_usd":             pos["entry_price"],
                        "allocated_capital_usd": partial_allocated,
                        "simulated_quantity":    partial_qty,
                    },
                    "exit": {
                        "timestamp":                 _utcnow_iso(),
                        "reason":                    "TAKE_PROFIT_PARTIAL_MOON_BAG",
                        "price_usd":                 current_price,
                        "highest_tracked_price_usd": pos["highest_tracked_price"],
                    },
                    "performance": {
                        "pnl_usd":        pnl_usd,
                        "pnl_percentage": pnl_pct,
                        "status":         "PARTIAL",
                    },
                    "entry_review": _entry_review_snapshot(pos),
                }
                history.append(trade_record)
                record_daily_pnl(daily_ledger, product_id, pnl_usd)
                changed = True

                entry_price    = float(pos.get("entry_price", 0.0) or 0.0)
                entry_ts       = pos.get("entry_timestamp", "")
                entry_strategy = pos.get("entry_strategy", "unknown").replace("_", " ")
                entry_score    = pos.get("entry_strategy_score", pos.get("signal_score", 0))
                entry_conf     = pos.get("entry_confidence_level", "SINGLE")
                held_mins      = 0
                if entry_ts:
                    try:
                        held_mins = int((datetime.now(timezone.utc) - datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))).total_seconds() / 60)
                    except Exception:
                        pass
                held_str       = f"{held_mins // 60}h {held_mins % 60}m" if held_mins >= 60 else f"{held_mins}m"
                move_from_entry = ((current_price - entry_price) / entry_price * 100) if entry_price else 0.0
                msg = (
                    f"🟡 [{mode_label}] {product_id} — Partial Take-Profit (Moon Bag)\n"
                    f"💵 Exit: ${current_price:,.6g}  |  Entry was: ${entry_price:,.6g} ({move_from_entry:+.2f}%)\n"
                    f"⏱️  Held: {held_str}  |  Peak so far: ${pos['highest_tracked_price']:,.6g}\n"
                    f"💰 Realized: ${pnl_usd:+.2f} ({pnl_pct:+.2f}%) on {sell_fraction * 100:.0f}% sold\n"
                    f"🌙 Moon bag: {100 - (sell_fraction * 100):.0f}% still running\n"
                    f"   Runner trail: {pos.get('current_trail_pct', TRAILING_PERCENT):.1f}%  |  Stop: ${pos['current_trailing_stop']:,.6g}\n"
                    f"📊 Entry strategy: {entry_strategy} (score {entry_score:.0f}, {entry_conf})"
                )
                print(f"  {msg}")
                send_discord_alert(msg)
                continue
        elif current_price <= pos["current_trailing_stop"]:
            exit_triggered = True
            exit_reason    = "TRAILING_STOP_LOSS_TRIGGERED"
        else:
            # Bearish-reversal exit: leave before the trailing stop if the trend flips.
            reversal = detect_bearish_reversal(product_id)
            if reversal["bearish"]:
                should_exit, decision_reason = _should_exit_on_bearish_reversal(pos, current_price, reversal)
                if should_exit:
                    exit_triggered = True
                    exit_reason    = f"BEARISH_REVERSAL_{reversal['reason']}"
                else:
                    print(f"  [BEARISH HOLD] {product_id}: {decision_reason}")

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
                    continue

            main_tp_pct = float(pos.get("main_take_profit_percent", TAKE_PROFIT_PERCENT) or TAKE_PROFIT_PERCENT)
            trade_record = {
                "strategy":         "Automated Multi-Asset Watchlist Engine",
                "product_id":       product_id,
                "mode":             pos.get("mode", "paper"),
                "live_data_source": "Coinbase Advanced API",
                "config": {
                    "trailing_percent":    TRAILING_PERCENT,
                    "take_profit_percent": main_tp_pct,
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
                "entry_review": _entry_review_snapshot(pos),
            }

            history.append(trade_record)
            closed.append(product_id)

            # Record realized PnL; blocks the coin for the day if it breaches the cap.
            record_daily_pnl(daily_ledger, product_id, pnl_usd)

            entry_price     = float(pos.get("entry_price", 0.0) or 0.0)
            entry_ts        = pos.get("entry_timestamp", "")
            entry_strategy  = pos.get("entry_strategy", "unknown").replace("_", " ")
            entry_score     = pos.get("entry_strategy_score", pos.get("signal_score", 0))
            entry_conf      = pos.get("entry_confidence_level", "SINGLE")
            entry_confirming = pos.get("entry_confirming_strategies", [])
            confirming_str  = " + ".join(s.replace("_", " ") for s in entry_confirming) if entry_confirming else entry_strategy
            peak_price      = float(pos.get("highest_tracked_price", current_price) or current_price)
            peak_gain_pct   = ((peak_price - entry_price) / entry_price * 100) if entry_price else 0.0
            move_from_entry = ((current_price - entry_price) / entry_price * 100) if entry_price else 0.0
            had_partial     = bool(pos.get("partial_take_profit_taken"))
            had_quick       = bool(pos.get("quick_take_profit_taken"))
            held_mins       = 0
            if entry_ts:
                try:
                    held_mins = int((datetime.now(timezone.utc) - datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))).total_seconds() / 60)
                except Exception:
                    pass
            held_str        = f"{held_mins // 60}h {held_mins % 60}m" if held_mins >= 60 else f"{held_mins}m"
            result_emoji    = "🟢" if pnl_usd >= 0 else "🔴"
            reason_clean    = exit_reason.replace("_", " ").title()
            msg = (
                f"{result_emoji} [{mode_label}] {product_id} — {reason_clean}\n"
                f"💵 Exit: ${current_price:,.6g}  |  Entry was: ${entry_price:,.6g} ({move_from_entry:+.2f}%)\n"
                f"⏱️  Held: {held_str}  |  Peak: ${peak_price:,.6g} (+{peak_gain_pct:.2f}% from entry)\n"
                f"💰 PnL: ${pnl_usd:+.2f} ({pnl_pct:+.2f}%)"
                + ("  [had quick TP]" if had_quick else "")
                + ("  [had partial TP]" if had_partial else "") + "\n"
                f"📊 Entry: {entry_strategy} — score {entry_score:.0f}/100 [{entry_conf}]\n"
                f"   Confirmed by: {confirming_str}"
            )
            print(f"  {msg}")
            send_discord_alert(msg)

    if closed or changed:
        for pid in closed:
            del active_positions[pid]
        save_json_file(PORTFOLIO_FILE, active_positions)
        save_json_file(HISTORY_FILE, history)
        save_json_file(DAILY_PNL_FILE, daily_ledger)
        if closed:
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


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _trade_event_key(trade: dict) -> str:
    entry = trade.get("entry") or {}
    exit_ = trade.get("exit") or {}
    perf = trade.get("performance") or {}
    return "|".join([
        str(trade.get("product_id") or ""),
        str(entry.get("timestamp") or ""),
        str(exit_.get("timestamp") or ""),
        str(exit_.get("reason") or ""),
        str(perf.get("status") or ""),
        f"{_safe_float(perf.get('pnl_usd')):.8f}",
    ])


def _strategy_from_trade(trade: dict) -> str:
    review = trade.get("entry_review") or {}
    strategy = normalize_entry_strategy_name(
        review.get("entry_strategy")
        or trade.get("entry_strategy")
        or ""
    )
    if strategy not in {"", "UNKNOWN", "LEGACY/UNKNOWN"}:
        return strategy

    score_fields = [
        ("CANDLE_BREAKOUT", "entry_pre_breakout_score"),
        ("OPENING_RANGE_BREAKOUT", "entry_orb_score"),
        ("BOLLINGER_LOWER_BAND_REVERSAL", "entry_bollinger_score"),
        ("DESCENDING_WEDGE_BREAKOUT", "entry_wedge_score"),
        ("EARLY_MOMENTUM_RUNNER", "entry_momentum_runner_score"),
    ]
    best_strategy = "UNKNOWN"
    best_score = 0.0
    for name, field in score_fields:
        score = _safe_float(review.get(field))
        if score > best_score:
            best_strategy = name
            best_score = score
    return best_strategy if best_score > 0 else "UNKNOWN"


def _trade_hold_minutes(trade: dict) -> int:
    entry_ts = (trade.get("entry") or {}).get("timestamp") or ""
    exit_ts = (trade.get("exit") or {}).get("timestamp") or ""
    if not entry_ts or not exit_ts:
        return 0
    try:
        entry_dt = datetime.fromisoformat(str(entry_ts).replace("Z", "+00:00"))
        exit_dt = datetime.fromisoformat(str(exit_ts).replace("Z", "+00:00"))
        return max(0, int((exit_dt - entry_dt).total_seconds() / 60))
    except Exception:
        return 0


def _new_pattern_bucket(strategy: str) -> dict:
    return {
        "strategy": strategy,
        "events": 0,
        "closedTrades": 0,
        "partialTakes": 0,
        "wins": 0,
        "losses": 0,
        "realizedPnlUsd": 0.0,
        "grossProfitUsd": 0.0,
        "grossLossUsd": 0.0,
        "pnlPctSum": 0.0,
        "pnlPctCount": 0,
        "scoreSum": 0.0,
        "scoreCount": 0,
        "holdMinutesSum": 0,
        "holdMinutesCount": 0,
        "supportRecorded": 0,
        "retestRecorded": 0,
        "buyZoneRecorded": 0,
        "exitReasons": {},
    }


def _finalize_pattern_bucket(bucket: dict) -> dict:
    closed = bucket["closedTrades"]
    events = bucket["events"]
    gross_loss_abs = abs(bucket["grossLossUsd"])
    profit_factor = None if gross_loss_abs == 0 else bucket["grossProfitUsd"] / gross_loss_abs
    return {
        "strategy": bucket["strategy"],
        "events": events,
        "closedTrades": closed,
        "partialTakes": bucket["partialTakes"],
        "wins": bucket["wins"],
        "losses": bucket["losses"],
        "winRatePct": round((bucket["wins"] / closed * 100), 2) if closed else 0.0,
        "realizedPnlUsd": round(bucket["realizedPnlUsd"], 2),
        "grossProfitUsd": round(bucket["grossProfitUsd"], 2),
        "grossLossUsd": round(bucket["grossLossUsd"], 2),
        "profitFactor": round(profit_factor, 2) if profit_factor is not None else None,
        "avgPnlPct": round(bucket["pnlPctSum"] / bucket["pnlPctCount"], 2) if bucket["pnlPctCount"] else 0.0,
        "avgScore": round(bucket["scoreSum"] / bucket["scoreCount"], 1) if bucket["scoreCount"] else 0.0,
        "avgHoldMinutes": round(bucket["holdMinutesSum"] / bucket["holdMinutesCount"], 1) if bucket["holdMinutesCount"] else 0.0,
        "supportRecorded": bucket["supportRecorded"],
        "supportCoveragePct": round((bucket["supportRecorded"] / events * 100), 2) if events else 0.0,
        "retestRecorded": bucket["retestRecorded"],
        "retestCoveragePct": round((bucket["retestRecorded"] / events * 100), 2) if events else 0.0,
        "buyZoneRecorded": bucket["buyZoneRecorded"],
        "buyZoneCoveragePct": round((bucket["buyZoneRecorded"] / events * 100), 2) if events else 0.0,
        "exitReasons": dict(sorted(
            bucket["exitReasons"].items(),
            key=lambda item: (-item[1], item[0]),
        )),
    }


def _pattern_review_recommendation(rows: list[dict]) -> str:
    closed_rows = [row for row in rows if row["closedTrades"] > 0]
    if not closed_rows:
        return "No closed trades with strategy metadata yet. Keep the fresh paper run collecting data before disabling patterns."

    enough_sample = [row for row in closed_rows if row["closedTrades"] >= 5]
    if not enough_sample:
        best = max(closed_rows, key=lambda row: (row["realizedPnlUsd"], row["winRatePct"]))
        return (
            f"Sample is still thin. Early leader is {best['strategy']} "
            f"({best['closedTrades']} closed, {best['winRatePct']:.0f}% win rate, "
            f"${best['realizedPnlUsd']:+.2f}), but wait for at least 5-10 closed trades "
            "per active strategy before making it the only pattern."
        )

    profitable = [
        row for row in enough_sample
        if row["realizedPnlUsd"] > 0 and row["winRatePct"] >= 50.0
    ]
    if profitable:
        best = max(profitable, key=lambda row: (row["realizedPnlUsd"], row["profitFactor"] or 0.0))
        return (
            f"Best supported candidate is {best['strategy']} "
            f"({best['closedTrades']} closed, {best['winRatePct']:.0f}% win rate, "
            f"${best['realizedPnlUsd']:+.2f}). Consider a focused paper run with "
            f"ENABLED_ENTRY_STRATEGIES={best['strategy']}."
        )

    return (
        "No strategy has enough profitable evidence yet. Keep the retest/support gate active, "
        "avoid widening the strategy set, and collect a larger clean sample before increasing risk."
    )


def summarize_trade_patterns(history: list) -> dict:
    """Groups realized trade performance by entry strategy and structure quality."""
    unique_events: list[dict] = []
    seen: set[str] = set()
    for trade in history:
        if not isinstance(trade, dict):
            continue
        status = (trade.get("performance") or {}).get("status")
        if status not in {"PARTIAL", "CLOSED"}:
            continue
        key = _trade_event_key(trade)
        if key in seen:
            continue
        seen.add(key)
        unique_events.append(trade)

    buckets: dict[str, dict] = {}
    total_pnl = 0.0
    wins = 0
    losses = 0
    closed_count = 0
    partial_count = 0
    exit_reasons: dict[str, int] = {}

    for trade in unique_events:
        strategy = _strategy_from_trade(trade)
        bucket = buckets.setdefault(strategy, _new_pattern_bucket(strategy))
        review = trade.get("entry_review") or {}
        perf = trade.get("performance") or {}
        exit_ = trade.get("exit") or {}
        status = perf.get("status")
        pnl = _safe_float(perf.get("pnl_usd"))
        pnl_pct = _safe_float(perf.get("pnl_percentage"))
        score = _safe_float(review.get("entry_strategy_score"))
        hold_minutes = _trade_hold_minutes(trade)
        exit_reason = str(exit_.get("reason") or "UNKNOWN")

        bucket["events"] += 1
        bucket["realizedPnlUsd"] += pnl
        bucket["pnlPctSum"] += pnl_pct
        bucket["pnlPctCount"] += 1
        if score > 0:
            bucket["scoreSum"] += score
            bucket["scoreCount"] += 1
        if hold_minutes > 0:
            bucket["holdMinutesSum"] += hold_minutes
            bucket["holdMinutesCount"] += 1
        if _safe_float(review.get("entry_support_level")) > 0:
            bucket["supportRecorded"] += 1
        if bool(review.get("entry_successful_retest")):
            bucket["retestRecorded"] += 1
        if bool(review.get("entry_in_buy_zone")) or (
            _safe_float(review.get("entry_buy_zone_low")) > 0
            and _safe_float(review.get("entry_buy_zone_high")) > 0
        ):
            bucket["buyZoneRecorded"] += 1
        bucket["exitReasons"][exit_reason] = bucket["exitReasons"].get(exit_reason, 0) + 1
        exit_reasons[exit_reason] = exit_reasons.get(exit_reason, 0) + 1

        total_pnl += pnl
        if status == "PARTIAL":
            bucket["partialTakes"] += 1
            partial_count += 1
        elif status == "CLOSED":
            bucket["closedTrades"] += 1
            closed_count += 1
            if pnl > 0:
                bucket["wins"] += 1
                bucket["grossProfitUsd"] += pnl
                wins += 1
            else:
                bucket["losses"] += 1
                bucket["grossLossUsd"] += pnl
                losses += 1

    rows = [_finalize_pattern_bucket(bucket) for bucket in buckets.values()]
    rows.sort(key=lambda row: (row["realizedPnlUsd"], row["closedTrades"]), reverse=True)

    return {
        "enabledEntryStrategies": enabled_entry_strategy_names() or ["ALL"],
        "realizedEvents": len(unique_events),
        "closedTrades": closed_count,
        "partialTakes": partial_count,
        "wins": wins,
        "losses": losses,
        "winRatePct": round((wins / closed_count * 100), 2) if closed_count else 0.0,
        "realizedPnlUsd": round(total_pnl, 2),
        "strategyCount": len(rows),
        "strategyStats": rows,
        "exitReasons": dict(sorted(exit_reasons.items(), key=lambda item: (-item[1], item[0]))),
        "recommendation": _pattern_review_recommendation(rows),
    }


def build_trade_review_report(active_positions: dict, history: list) -> str:
    """Builds a compact diagnostic report focused on entry quality and stop behavior."""
    lines = [
        "Trade Review",
        f"Generated: {_utcnow_iso()}",
    ]

    closed = [
        t for t in history
        if isinstance(t, dict) and t.get("performance", {}).get("status") == "CLOSED"
    ]
    if closed:
        total = len(closed)
        wins = sum(1 for t in closed if float(t.get("performance", {}).get("pnl_usd", 0.0) or 0.0) > 0)
        stopouts = [t for t in closed if "TRAILING_STOP" in str(t.get("exit", {}).get("reason", ""))]
        bearish = [t for t in closed if "BEARISH_REVERSAL" in str(t.get("exit", {}).get("reason", ""))]
        avg_pnl = sum(float(t.get("performance", {}).get("pnl_percentage", 0.0) or 0.0) for t in closed) / max(1, total)
        avg_stop_distance = sum(
            float((t.get("entry_review", {}) or {}).get("entry_stop_distance_pct", 0.0) or 0.0)
            for t in closed
        ) / max(1, len([t for t in closed if (t.get("entry_review", {}) or {}).get("entry_stop_distance_pct")]))
        lines.extend([
            f"Closed trades: {total} | wins {wins} | losses {total - wins} | avg pnl {avg_pnl:+.2f}%",
            f"Exit mix: trailing stops {len(stopouts)} | bearish reversals {len(bearish)}",
            f"Average recorded stop distance: {avg_stop_distance:.2f}%",
        ])
    else:
        lines.append("Closed trades: none recorded yet in trading_history.json")

    pattern_stats = summarize_trade_patterns(history)
    if pattern_stats.get("strategyStats"):
        lines.append("By strategy:")
        for row in pattern_stats["strategyStats"][:8]:
            lines.append(
                f"- {row['strategy']}: {row['closedTrades']} closed, "
                f"{row['winRatePct']:.0f}% wins, ${row['realizedPnlUsd']:+.2f}, "
                f"avg {row['avgPnlPct']:+.2f}%, support {row['supportCoveragePct']:.0f}%"
            )
        lines.append("Pattern recommendation: " + str(pattern_stats.get("recommendation", "")))

    if active_positions:
        lines.append(f"Open positions: {len(active_positions)}")
        for pid, pos in active_positions.items():
            entry = float(pos.get("entry_price", 0.0) or 0.0)
            stop = float(pos.get("current_trailing_stop", 0.0) or 0.0)
            stop_pct = ((entry - stop) / entry * 100) if entry and stop else 0.0
            strategy = pos.get("entry_strategy", "legacy/unknown")
            score = float(pos.get("entry_strategy_score", pos.get("signal_score", 0.0)) or 0.0)
            support = float(pos.get("entry_support_level", 0.0) or 0.0)
            lines.append(
                f"- {pid}: strategy {strategy}, score {score:.0f}, stop {stop_pct:.2f}% below entry"
                + (f", support {support:.6g}" if support > 0 else ", legacy entry without recorded structure")
            )
    else:
        lines.append("Open positions: none")

    legacy_tight = [
        pid for pid, pos in active_positions.items()
        if float(pos.get("entry_support_level", 0.0) or 0.0) <= 0
        and float(pos.get("entry_price", 0.0) or 0.0) > 0
        and float(pos.get("current_trailing_stop", 0.0) or 0.0) > 0
        and ((float(pos.get("entry_price", 0.0) or 0.0) - float(pos.get("current_trailing_stop", 0.0) or 0.0))
             / float(pos.get("entry_price", 1.0) or 1.0) * 100) <= 2.25
    ]
    if legacy_tight:
        lines.append("Likely issue: legacy breakout entries used very tight stops without saved support structure.")
        lines.append("Affected open positions: " + ", ".join(legacy_tight))

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
    if not send_discord_alert(report):
        return

    state["last_sent_epoch"] = now
    save_json_file(SUMMARY_STATE_FILE, state)


def maybe_send_daily_summary(active_positions: dict, live_prices: dict, force: bool = False):
    """Sends one end-of-day summary after DAILY_SUMMARY_UTC_HOUR."""
    if not DAILY_SUMMARY_ENABLED and not force:
        return

    now_dt = datetime.now(timezone.utc)
    if not force and now_dt.hour < DAILY_SUMMARY_UTC_HOUR:
        return

    today = now_dt.strftime("%Y-%m-%d")
    state = load_json_file(DAILY_SUMMARY_STATE_FILE)
    if not isinstance(state, dict):
        state = {}
    if not force and state.get("last_sent_date") == today:
        return

    day_start = int(now_dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    history = load_json_file(HISTORY_FILE)
    summary_lines = build_portfolio_summary(active_positions, history, live_prices, day_start).splitlines()
    if summary_lines:
        summary_lines[0] = f"Paper Trading Summary (today UTC) — {_utcnow_iso()[:19]}Z"
    report = "End-of-Day Paper Trading Summary\n" + "\n".join(summary_lines)

    print("\n" + report + "\n")
    if not send_discord_alert(report):
        return

    state["last_sent_date"] = today
    state["last_sent_epoch"] = _utcnow_epoch()
    save_json_file(DAILY_SUMMARY_STATE_FILE, state)


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
    pending_entries = load_json_file(PENDING_ENTRY_FILE)
    market_state = load_json_file(MARKET_STATE_FILE)
    daily_ledger = load_daily_ledger()

    if not isinstance(active_positions, dict):
        active_positions = {}
    if not isinstance(pending_entries, dict):
        pending_entries = {}
    if not isinstance(market_state, dict):
        market_state = {}

    update_market_state(market_state, products)
    save_json_file(MARKET_STATE_FILE, market_state)

    if active_positions:
        manage_active_positions(client, active_positions, live_prices, daily_ledger)

    products_by_id = {
        str(prod.get("product_id") or ""): prod
        for prod in products
        if isinstance(prod, dict) and prod.get("product_id")
    }
    pending_entries, pending_changed = manage_pending_entries(
        client,
        active_positions,
        pending_entries,
        products_by_id,
        live_prices,
    )
    if pending_changed:
        save_json_file(PENDING_ENTRY_FILE, pending_entries)

    scan_and_execute_entries(client, active_positions, pending_entries, products, market_state, daily_ledger)
    save_json_file(DAILY_PNL_FILE, daily_ledger)
    save_json_file(PENDING_ENTRY_FILE, pending_entries)

    # Persist a read-only ranked snapshot of current opportunities (same strategy
    # stack the engine trades on) so the dashboard's Crypto Scalp tab can render
    # real Coinbase-derived setups without a second data provider.
    try:
        snapshot = build_scan_snapshot(products, active_positions)
        save_json_file(SCAN_SNAPSHOT_FILE, snapshot)
    except Exception as exc:
        print(f"  [Scan] snapshot build failed: {exc}")

    # Periodic Discord performance summary (every SUMMARY_INTERVAL_HOURS).
    maybe_send_summary(active_positions, live_prices)
    maybe_send_daily_summary(active_positions, live_prices)
    return active_positions, live_prices


def run_scan_snapshot() -> dict:
    """
    On-demand, read-only crypto scan for the dashboard. Builds the watchlist from
    Coinbase public data, scores every coin with the full strategy stack, and
    returns/persists a ranked snapshot. Used as a bootstrap fallback when the
    5-minute paper-trader cycle has not yet written a snapshot (e.g. fresh deploy).
    """
    products, _ = get_market_snapshot_public()
    active_positions = load_json_file(PORTFOLIO_FILE)
    if not isinstance(active_positions, dict):
        active_positions = {}

    # Best-effort wedge enrichment for the strongest volume names (the public
    # snapshot already carries candle/ORB/Bollinger/OBV scores).
    for prod in sorted(products, key=lambda x: x.get("volume_24h", 0), reverse=True)[:CANDLE_SCAN_LIMIT]:
        if "wedge_score" in prod:
            continue
        try:
            wedge = detect_wedge_breakout(prod["product_id"])
            prod["wedge_features"] = wedge["features"]
            prod["wedge_score"] = wedge["wedge_score"]
        except Exception:
            prod.setdefault("wedge_score", 0.0)

    snapshot = build_scan_snapshot(products, active_positions)
    save_json_file(SCAN_SNAPSHOT_FILE, snapshot)
    return snapshot


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


def run_review():
    """Prints a local diagnostic review from archived history + open positions."""
    active_positions = load_json_file(PORTFOLIO_FILE)
    history = load_json_file(HISTORY_FILE)
    if not isinstance(active_positions, dict):
        active_positions = {}
    if not isinstance(history, list):
        history = []
    print("=" * 52)
    print("  COINBASE PAPER-TRADING — TRADE REVIEW")
    print("=" * 52)
    print(build_trade_review_report(active_positions, history))


if __name__ == "__main__":
    # `python trader.py scan` — on-demand breakout scan (public data, no keys).
    # `python trader.py review` — inspect archived trades and open-position stop quality.
    # `python trader.py once` — run exactly one trading cycle, then exit.
    # `python trader.py`      — run the continuous trading loop.
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "scan":
        scan_breakouts_now()
    elif mode == "review":
        run_review()
    elif mode == "once":
        run_once()
    else:
        main_orchestrator()
