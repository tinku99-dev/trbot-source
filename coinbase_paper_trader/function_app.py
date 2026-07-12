import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import azure.functions as func
import numpy as np
import pandas as pd
import requests

try:
    from azure.storage.blob import BlobServiceClient
except Exception:
    BlobServiceClient = None

app = func.FunctionApp()

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
TARGET_NETWORK = os.environ.get("TARGET_NETWORK", "base")
DATA_PROVIDER = os.environ.get("DATA_PROVIDER", "gecko").strip().lower()
TIMEFRAMES_TO_SCAN = [
    tf.strip()
    for tf in os.environ.get("TIMEFRAMES_TO_SCAN", "5m,15m,4h").split(",")
    if tf.strip()
]
REQUEST_TIMEOUT_SECONDS = 15
COINBASE_API_BASE = "https://api.exchange.coinbase.com"

CACHE_FILE_PATH = "/tmp/active_trades_cache.json"
ALERT_HISTORY_FILE_PATH = "/tmp/alert_history_cache.json"
STATE_CONTAINER_NAME = os.environ.get("STATE_CONTAINER_NAME", "cointracking-state")
STATE_TRADES_BLOB_NAME = os.environ.get("STATE_TRADES_BLOB_NAME", "active_trades_cache.json")
STATE_ALERTS_BLOB_NAME = os.environ.get("STATE_ALERTS_BLOB_NAME", "alert_history_cache.json")
MAX_ALERT_HISTORY = int(os.environ.get("MAX_ALERT_HISTORY", "500"))

ORB_ENABLED = os.environ.get("ORB_ENABLED", "true").lower() == "true"
ORB_SESSION_START_UTC = os.environ.get("ORB_SESSION_START_UTC", "13:30")
ORB_RANGE_MINUTES = int(os.environ.get("ORB_RANGE_MINUTES", "15"))
ORB_MIN_SCORE_TO_BUY = float(os.environ.get("ORB_MIN_SCORE_TO_BUY", "80"))
ORB_BREAKOUT_BUFFER_PCT = float(os.environ.get("ORB_BREAKOUT_BUFFER_PCT", "0.10"))
ORB_MAX_OVEREXTENSION = float(os.environ.get("ORB_MAX_OVEREXTENSION", "1.50"))
ORB_VOL_RATIO_MIN = float(os.environ.get("ORB_VOL_RATIO_MIN", "1.50"))

BOLLINGER_ENABLED = os.environ.get("BOLLINGER_ENABLED", "true").lower() == "true"
BOLLINGER_PERIOD = int(os.environ.get("BOLLINGER_PERIOD", "20"))
BOLLINGER_STDDEV = float(os.environ.get("BOLLINGER_STDDEV", "2.0"))
BOLLINGER_MIN_SCORE_TO_BUY = float(os.environ.get("BOLLINGER_MIN_SCORE_TO_BUY", "80"))
BOLLINGER_MIN_EXTREME_PCT = float(os.environ.get("BOLLINGER_MIN_EXTREME_PCT", "0.20"))
BOLLINGER_MAX_DISTANCE_FROM_MID_PCT = float(os.environ.get("BOLLINGER_MAX_DISTANCE_FROM_MID_PCT", "4.0"))


def get_storage_connection_string() -> str:
    return os.environ.get("STATE_STORAGE_CONNECTION_STRING") or os.environ.get("AzureWebJobsStorage", "")


def load_json_state(blob_name: str, file_path: str, default_value: Any) -> Any:
    conn_str = get_storage_connection_string()
    if BlobServiceClient and conn_str:
        try:
            service_client = BlobServiceClient.from_connection_string(conn_str)
            container = service_client.get_container_client(STATE_CONTAINER_NAME)
            if not container.exists():
                container.create_container()
            blob = container.get_blob_client(blob_name)
            if blob.exists():
                payload = blob.download_blob().readall().decode("utf-8")
                return json.loads(payload)
        except Exception as exc:
            logging.warning("Blob load failed for %s, using local cache: %s", blob_name, exc)

    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as file_handle:
                return json.load(file_handle)
        except Exception as exc:
            logging.warning("Local state read failed for %s: %s", file_path, exc)

    return default_value


def save_json_state(blob_name: str, file_path: str, payload: Any) -> None:
    try:
        with open(file_path, "w", encoding="utf-8") as file_handle:
            json.dump(payload, file_handle)
    except Exception as exc:
        logging.warning("Local state write failed for %s: %s", file_path, exc)

    conn_str = get_storage_connection_string()
    if BlobServiceClient and conn_str:
        try:
            service_client = BlobServiceClient.from_connection_string(conn_str)
            container = service_client.get_container_client(STATE_CONTAINER_NAME)
            if not container.exists():
                container.create_container()
            blob = container.get_blob_client(blob_name)
            blob.upload_blob(json.dumps(payload), overwrite=True)
        except Exception as exc:
            logging.warning("Blob write failed for %s, local state preserved: %s", blob_name, exc)


def load_tracked_trades() -> Dict[str, Any]:
    state = load_json_state(STATE_TRADES_BLOB_NAME, CACHE_FILE_PATH, {})
    if isinstance(state, dict):
        return state
    return {}


def save_tracked_trades(trades_dict: Dict[str, Any]) -> None:
    save_json_state(STATE_TRADES_BLOB_NAME, CACHE_FILE_PATH, trades_dict)


def load_alert_history() -> List[Dict[str, Any]]:
    state = load_json_state(STATE_ALERTS_BLOB_NAME, ALERT_HISTORY_FILE_PATH, [])
    if isinstance(state, list):
        return state
    return []


def save_alert_history(alerts: List[Dict[str, Any]]) -> None:
    trimmed = alerts[-MAX_ALERT_HISTORY:]
    save_json_state(STATE_ALERTS_BLOB_NAME, ALERT_HISTORY_FILE_PATH, trimmed)


def get_coinbase_mark_price(product_id: str, fallback: float = 0.0) -> float:
    try:
        response = requests.get(
            f"{COINBASE_API_BASE}/products/{product_id}/ticker",
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return float(response.json().get("price") or fallback or 0.0)
    except Exception as exc:
        logging.warning("Coinbase mark price lookup failed for %s: %s", product_id, exc)
        return float(fallback or 0.0)


def build_manual_close_record(
    trader: Any,
    product_id: str,
    position: Dict[str, Any],
    mark_price: float,
    exit_reason: str = "MANUAL_CLOSE_REQUEST",
    source: str = "Manual paper close",
) -> Dict[str, Any]:
    allocated = float(position.get("allocated_usd", trader.CAPITAL_PER_TRADE_USD) or 0)
    quantity = float(position.get("simulated_qty", 0) or 0)
    final_value = quantity * mark_price
    pnl_usd = final_value - allocated
    pnl_pct = (pnl_usd / allocated * 100) if allocated else 0.0
    return {
        "strategy": "Automated Multi-Asset Watchlist Engine",
        "product_id": product_id,
        "mode": position.get("mode", "paper"),
        "live_data_source": source,
        "config": {
            "total_capital_usd": trader.TOTAL_CAPITAL_USD,
            "manual_close": True,
        },
        "entry": {
            "timestamp": position.get("entry_timestamp", ""),
            "price_usd": float(position.get("entry_price", 0) or 0),
            "allocated_capital_usd": allocated,
            "simulated_quantity": quantity,
        },
        "exit": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reason": exit_reason,
            "price_usd": mark_price,
            "highest_tracked_price_usd": float(position.get("highest_tracked_price", mark_price) or mark_price),
        },
        "performance": {
            "pnl_usd": pnl_usd,
            "pnl_percentage": pnl_pct,
            "status": "CLOSED",
        },
    }


def close_paper_positions(
    trader: Any,
    product_filter: str = "",
    archive: bool = True,
    exit_reason: str = "MANUAL_CLOSE_REQUEST",
    source: str = "Manual paper close",
) -> Dict[str, Any]:
    active_positions = trader.load_json_file(trader.PORTFOLIO_FILE)
    history = trader.load_json_file(trader.HISTORY_FILE)
    daily_ledger = trader.load_daily_ledger()
    if not isinstance(active_positions, dict):
        active_positions = {}
    if not isinstance(history, list):
        history = []

    product_filter = (product_filter or "").strip().upper()
    closed_records = []
    remaining_positions = {}
    for product_id, position in active_positions.items():
        if product_filter and product_id.upper() != product_filter:
            remaining_positions[product_id] = position
            continue

        # This endpoint reconciles paper-state positions. It intentionally does
        # not pretend to close live Coinbase holdings; live reconciliation should
        # verify exchange balances/orders before mutating state.
        if position.get("mode") == "live":
            remaining_positions[product_id] = position
            continue

        fallback_price = float(position.get("highest_tracked_price") or position.get("entry_price") or 0.0)
        mark_price = get_coinbase_mark_price(product_id, fallback=fallback_price)
        if archive:
            record = build_manual_close_record(
                trader,
                product_id,
                position,
                mark_price,
                exit_reason=exit_reason,
                source=source,
            )
            history.append(record)
            closed_records.append(record)
            trader.record_daily_pnl(
                daily_ledger,
                product_id,
                float((record.get("performance") or {}).get("pnl_usd", 0) or 0),
            )

    if product_filter and len(closed_records) == 0 and product_filter not in active_positions:
        return {
            "error": "position_not_found",
            "productId": product_filter,
            "statusCode": 404,
        }

    trader.save_json_file(trader.PORTFOLIO_FILE, remaining_positions)
    if archive and closed_records:
        trader.save_json_file(trader.HISTORY_FILE, history)
        trader.save_json_file(trader.DAILY_PNL_FILE, daily_ledger)

    realized = sum(float((r.get("performance") or {}).get("pnl_usd", 0) or 0) for r in closed_records)
    return {
        "status": "ok",
        "archived": archive,
        "closedPositions": len(closed_records),
        "remainingPositions": len(remaining_positions),
        "realizedPnlUsd": round(realized, 2),
        "productId": product_filter or None,
        "closed": [
            {
                "productId": record.get("product_id"),
                "exitPriceUsd": round(float((record.get("exit") or {}).get("price_usd") or 0), 10),
                "pnlUsd": round(float((record.get("performance") or {}).get("pnl_usd") or 0), 2),
                "pnlPercentage": round(float((record.get("performance") or {}).get("pnl_percentage") or 0), 4),
                "exitReason": (record.get("exit") or {}).get("reason"),
            }
            for record in closed_records
        ],
    }


def fresh_start_paper_trading(trader: Any, archive_open_positions: bool = True) -> Dict[str, Any]:
    """
    Resets paper trading to a clean baseline:
    - optionally archives any currently open paper positions at mark price
    - snapshots history/ledger into timestamped backup files
    - clears active positions, realized history, daily ledger, and summary state
    """
    reset_result = {
        "status": "ok",
        "archivedOpenPositions": 0,
        "realizedPnlUsd": 0.0,
    }
    if archive_open_positions:
        reset_result = close_paper_positions(
            trader,
            archive=True,
            exit_reason="MANUAL_FRESH_START_CLOSED_AT_MARK",
            source="Manual fresh start",
        )

    history = trader.load_json_file(trader.HISTORY_FILE)
    daily_ledger = trader.load_json_file(trader.DAILY_PNL_FILE)
    backups_written: List[str] = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if isinstance(history, list) and history:
        backup_name = f"trading_history_fresh_start_backup_{stamp}.json"
        trader.save_json_file(backup_name, history)
        backups_written.append(backup_name)
    if isinstance(daily_ledger, dict) and (
        daily_ledger.get("realized") or daily_ledger.get("blocked") or daily_ledger.get("portfolio_stopped")
    ):
        backup_name = f"daily_pnl_fresh_start_backup_{stamp}.json"
        trader.save_json_file(backup_name, daily_ledger)
        backups_written.append(backup_name)

    trader.save_json_file(trader.PORTFOLIO_FILE, {})
    trader.save_json_file(trader.HISTORY_FILE, [])
    trader.save_json_file(
        trader.DAILY_PNL_FILE,
        {"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "realized": {}, "blocked": []},
    )
    trader.save_json_file(trader.SUMMARY_STATE_FILE, {})
    trader.save_json_file(trader.DAILY_SUMMARY_STATE_FILE, {})
    trader.save_json_file(trader.MARKET_STATE_FILE, {})
    trader.save_json_file(trader.SCAN_SNAPSHOT_FILE, {})

    return {
        "status": "ok",
        "message": "Paper trading reset to a clean baseline.",
        "startingCapitalUsd": float(trader.TOTAL_CAPITAL_USD),
        "archivedOpenPositions": int(reset_result.get("closedPositions", 0) or 0),
        "realizedPnlUsd": round(float(reset_result.get("realizedPnlUsd", 0.0) or 0.0), 2),
        "backupFiles": backups_written,
    }


def record_alert(event_type: str, symbol: str, timeframe: str, details: Dict[str, Any]) -> None:
    alerts = load_alert_history()
    alerts.append(
        {
            "event_type": event_type,
            "symbol": symbol,
            "timeframe": timeframe,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "details": details,
        }
    )
    save_alert_history(alerts)


def calculate_buy_range(current_price: float, atr_val: float, timeframe: str) -> Dict[str, float]:
    range_multipliers = {
        "5m": (0.40, 0.15),
        "15m": (0.70, 0.20),
        "4h": (1.20, 0.35),
    }
    down_mult, up_mult = range_multipliers.get(timeframe, (0.70, 0.20))
    return {
        "buy_low": current_price - (down_mult * atr_val),
        "buy_high": current_price + (up_mult * atr_val),
    }


def calculate_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema"] = df["close"].ewm(span=20, adjust=False).mean()

    df["bollinger_mid"] = df["close"].rolling(BOLLINGER_PERIOD).mean()
    bollinger_std = df["close"].rolling(BOLLINGER_PERIOD).std(ddof=0)
    df["bollinger_upper"] = df["bollinger_mid"] + (BOLLINGER_STDDEV * bollinger_std)
    df["bollinger_lower"] = df["bollinger_mid"] - (BOLLINGER_STDDEV * bollinger_std)

    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / (loss + 1e-10)
    df["rsi"] = 100 - (100 / (1 + rs))

    exp1 = df["close"].ewm(span=12, adjust=False).mean()
    exp2 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd_line"] = exp1 - exp2
    df["signal_line"] = df["macd_line"].ewm(span=9, adjust=False).mean()
    df["histogram"] = df["macd_line"] - df["signal_line"]

    high_low = df["high"] - df["low"]
    high_cp = np.abs(df["high"] - df["close"].shift())
    low_cp = np.abs(df["low"] - df["close"].shift())
    df["atr"] = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1).rolling(10).mean()

    hl2 = (df["high"] + df["low"]) / 2
    df["upper_band"] = hl2 + (3 * df["atr"])
    df["lower_band"] = hl2 - (3 * df["atr"])
    df["supertrend"] = True

    for idx in range(1, len(df)):
        if df.loc[idx, "close"] > df.loc[idx - 1, "upper_band"]:
            df.loc[idx, "supertrend"] = True
        elif df.loc[idx, "close"] < df.loc[idx - 1, "lower_band"]:
            df.loc[idx, "supertrend"] = False
        else:
            df.loc[idx, "supertrend"] = df.loc[idx - 1, "supertrend"]

    return df


def post_discord_payload(payload: Dict[str, Any]) -> None:
    if not DISCORD_WEBHOOK_URL:
        logging.info("DISCORD_WEBHOOK_URL is not set. Skipping Discord notification.")
        return

    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
    except Exception as exc:
        logging.warning("Discord webhook post failed: %s", exc)


def dispatch_discord_signal(
    symbol: str,
    pool: str,
    strategy: str,
    buy_price: float,
    buy_low: float,
    buy_high: float,
    sl: float,
    tp1: float,
    tp2: float,
    timeframe: str,
) -> None:
    if DATA_PROVIDER == "coinbase":
        chart_url = f"https://www.coinbase.com/advanced-trade/spot/{pool}"
    else:
        chart_url = f"https://dexscreener.com/{TARGET_NETWORK}/{pool}"

    payload = {
        "embeds": [
            {
                "title": f"{strategy} OPENED ({timeframe})",
                "url": chart_url,
                "color": 65280,
                "fields": [
                    {"name": "Token", "value": f"**{symbol}**", "inline": True},
                    {"name": "Entry Price", "value": f"`${buy_price:.8f}`", "inline": True},
                    {
                        "name": "Suggested Buy Range",
                        "value": f"`${buy_low:.8f}` -> `${buy_high:.8f}`",
                        "inline": False,
                    },
                    {"name": "Initial Stop Loss (SL)", "value": f"`${sl:.8f}`", "inline": False},
                    {"name": "Take Profit 1", "value": f"`${tp1:.8f}`", "inline": True},
                    {"name": "Take Profit 2 (Max)", "value": f"`${tp2:.8f}`", "inline": True},
                ],
                "footer": {"text": "Trailing Engine Engaged"},
            }
        ]
    }
    record_alert(
        "buy_trigger",
        symbol,
        timeframe,
        {
            "pool": pool,
            "entry": buy_price,
            "buy_low": buy_low,
            "buy_high": buy_high,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
        },
    )
    post_discord_payload(payload)


def dispatch_trailing_update(symbol: str, pool: str, price: float, new_sl: float, timeframe: str) -> None:
    payload = {
        "embeds": [
            {
                "title": f"TRAILING STOP UPDATED ({timeframe})",
                "description": f"Price for **{symbol}** pushed higher. Locking in gains.",
                "color": 3447003,
                "fields": [
                    {"name": "Current Price", "value": f"`${price:.8f}`", "inline": True},
                    {"name": "New Trailing SL Floor", "value": f"`${new_sl:.8f}`", "inline": True},
                ],
            }
        ]
    }
    record_alert("trailing_update", symbol, timeframe, {"pool": pool, "price": price, "new_sl": new_sl})
    post_discord_payload(payload)


def dispatch_exit_alert(
    symbol: str,
    pool: str,
    message_type: str,
    exit_price: float,
    entry_price: float,
    timeframe: str,
) -> None:
    pnl_percent = ((exit_price - entry_price) / entry_price) * 100
    color = 15158332 if pnl_percent < 0 else 3066993

    payload = {
        "embeds": [
            {
                "title": f"{message_type} ({timeframe})",
                "color": color,
                "fields": [
                    {"name": "Token", "value": f"**{symbol}**", "inline": True},
                    {"name": "Exit Price", "value": f"`${exit_price:.8f}`", "inline": True},
                    {"name": "Trade PnL %", "value": f"**{pnl_percent:+.2f}%**", "inline": False},
                ],
            }
        ]
    }
    record_alert(
        "trade_exit",
        symbol,
        timeframe,
        {
            "pool": pool,
            "message": message_type,
            "exit_price": exit_price,
            "entry_price": entry_price,
            "pnl_percent": pnl_percent,
        },
    )
    post_discord_payload(payload)


def dispatch_sell_change_alert(
    symbol: str,
    pool: str,
    reason: str,
    sell_price: float,
    entry_price: float,
    timeframe: str,
) -> None:
    pnl_percent = ((sell_price - entry_price) / entry_price) * 100
    payload = {
        "embeds": [
            {
                "title": f"SELL ALERT (TREND CHANGE) ({timeframe})",
                "color": 15105570,
                "fields": [
                    {"name": "Token", "value": f"**{symbol}**", "inline": True},
                    {"name": "Sell Price", "value": f"`${sell_price:.8f}`", "inline": True},
                    {"name": "Reason", "value": reason, "inline": False},
                    {"name": "Trade PnL %", "value": f"**{pnl_percent:+.2f}%**", "inline": False},
                ],
            }
        ]
    }
    record_alert(
        "sell_change_alert",
        symbol,
        timeframe,
        {
            "pool": pool,
            "reason": reason,
            "sell_price": sell_price,
            "entry_price": entry_price,
            "pnl_percent": pnl_percent,
        },
    )
    post_discord_payload(payload)


def process_trailing_stops(
    symbol: str,
    current_price: float,
    pool_address: str,
    timeframe: str,
    active_trades: Dict[str, Any],
    current_row: pd.Series,
    previous_row: pd.Series,
) -> None:
    trade_id = f"{pool_address}_{timeframe}"
    if trade_id not in active_trades:
        return

    trade = active_trades[trade_id]
    entry_price = trade["entry_price"]
    highest_price = max(trade["highest_recorded_price"], current_price)
    current_sl = trade["current_stop_loss"]

    active_trades[trade_id]["highest_recorded_price"] = highest_price

    if current_price <= current_sl:
        dispatch_exit_alert(
            symbol,
            pool_address,
            "TRAILING STOP TRIGGERED (EXIT POSITION)",
            current_price,
            entry_price,
            timeframe,
        )
        del active_trades[trade_id]
        return

    if current_price >= trade["tp2"]:
        dispatch_exit_alert(
            symbol,
            pool_address,
            "TAKE PROFIT 2 HIT (MAX RETURN EXIT)",
            current_price,
            entry_price,
            timeframe,
        )
        del active_trades[trade_id]
        return

    bearish_macd_cross = (
        previous_row["macd_line"] >= previous_row["signal_line"]
        and current_row["macd_line"] < current_row["signal_line"]
    )
    ema_break = current_row["close"] < current_row["ema"]
    supertrend_break = bool(current_row["supertrend"] is False)
    rsi_weakening = current_row["rsi"] < 45

    sell_reason = None
    if bearish_macd_cross and ema_break:
        sell_reason = "MACD turned bearish and price fell below EMA"
    elif supertrend_break and rsi_weakening:
        sell_reason = "Supertrend flipped bearish with weak RSI"

    if sell_reason:
        dispatch_sell_change_alert(symbol, pool_address, sell_reason, current_price, entry_price, timeframe)
        del active_trades[trade_id]
        return

    atr_buffer = trade["atr_value"] * (1.5 if timeframe == "5m" else 2.5)
    new_calculated_sl = highest_price - atr_buffer

    if new_calculated_sl > current_sl:
        active_trades[trade_id]["current_stop_loss"] = new_calculated_sl
        dispatch_trailing_update(symbol, pool_address, current_price, new_calculated_sl, timeframe)


def _parse_session_start_utc(value: str) -> tuple[int, int]:
    try:
        hour_s, minute_s = value.split(":", 1)
        hour = int(hour_s)
        minute = int(minute_s)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (AttributeError, ValueError):
        pass
    return 13, 30


def _current_or_previous_session_start() -> int:
    hour, minute = _parse_session_start_utc(ORB_SESSION_START_UTC)
    now = datetime.now(timezone.utc)
    session = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < session:
        session = session - timedelta(days=1)
    return int(session.timestamp())


def score_opening_range_breakout(df: pd.DataFrame) -> Dict[str, Any]:
    if not ORB_ENABLED or "timestamp" not in df.columns or len(df) < 3:
        return {"score": 0.0, "features": {}}

    session_start = _current_or_previous_session_start()
    range_end = session_start + max(300, ORB_RANGE_MINUTES * 60)
    range_df = df[(df["timestamp"] >= session_start) & (df["timestamp"] < range_end)]
    post_df = df[df["timestamp"] >= range_end]
    if range_df.empty or post_df.empty:
        return {"score": 0.0, "features": {}}

    range_high = float(range_df["high"].max())
    range_low = float(range_df["low"].min())
    if range_high <= 0 or range_low <= 0:
        return {"score": 0.0, "features": {}}

    median_volume = float(range_df["volume"].median()) or 1.0
    current = post_df.iloc[-1]
    current_price = float(current["close"])
    open_price = float(current["open"])
    if current_price <= 0 or open_price <= 0:
        return {"score": 0.0, "features": {}}

    close_above_range_pct = ((current_price - range_high) / range_high) * 100
    volume_ratio = float(current["volume"]) / median_volume
    candle_move_pct = ((current_price - open_price) / open_price) * 100
    range_width_pct = ((range_high - range_low) / range_low) * 100
    bullish_breakout = close_above_range_pct >= ORB_BREAKOUT_BUFFER_PCT

    score = 0.0
    if bullish_breakout:
        score += 35
    if volume_ratio >= ORB_VOL_RATIO_MIN * 2:
        score += 25
    elif volume_ratio >= ORB_VOL_RATIO_MIN:
        score += 15
    if float(current["high"]) > range_high and float(current["low"]) >= range_low:
        score += 15
    if candle_move_pct >= 0.8:
        score += 15
    elif candle_move_pct >= 0.3:
        score += 8
    if range_width_pct <= 3.0:
        score += 10
    if close_above_range_pct > ORB_MAX_OVEREXTENSION:
        score *= 0.5

    return {
        "score": round(min(score, 100.0), 1),
        "features": {
            "range_high": range_high,
            "range_low": range_low,
            "close_above_range_pct": round(close_above_range_pct, 3),
            "volume_ratio": round(volume_ratio, 2),
            "range_width_pct": round(range_width_pct, 3),
            "candle_move_pct": round(candle_move_pct, 3),
        },
    }


def score_bollinger_reversal(df: pd.DataFrame) -> Dict[str, Any]:
    if not BOLLINGER_ENABLED or len(df) < BOLLINGER_PERIOD + 1:
        return {"score": 0.0, "features": {}}

    current = df.iloc[-1]
    previous = df.iloc[-2]
    lower_band = float(current.get("bollinger_lower", np.nan))
    upper_band = float(current.get("bollinger_upper", np.nan))
    middle_band = float(current.get("bollinger_mid", np.nan))
    if any(np.isnan(value) for value in [lower_band, upper_band, middle_band]) or lower_band <= 0:
        return {"score": 0.0, "features": {}}

    low = float(current["low"])
    high = float(current["high"])
    close = float(current["close"])
    open_price = float(current["open"])
    previous_close = float(previous["close"])
    if close <= 0 or open_price <= 0 or previous_close <= 0:
        return {"score": 0.0, "features": {}}

    lower_extension_pct = ((lower_band - low) / lower_band) * 100 if low < lower_band else 0.0
    upper_extension_pct = ((high - upper_band) / upper_band) * 100 if high > upper_band else 0.0
    reclaimed_lower = low < lower_band and close > lower_band
    bullish_reversal = close > open_price and close > previous_close
    distance_to_mid_pct = ((middle_band - close) / close) * 100
    volume_ratio = float(current["volume"]) / (float(df["volume"].tail(BOLLINGER_PERIOD + 1).head(BOLLINGER_PERIOD).median()) or 1.0)
    candle_move_pct = ((close - open_price) / open_price) * 100

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
    if volume_ratio >= 1.8:
        score += 15
    elif volume_ratio >= 1.2:
        score += 8
    if candle_move_pct >= 0.5:
        score += 10
    if upper_extension_pct > 0 and lower_extension_pct <= 0:
        score = 0.0

    return {
        "score": round(min(score, 100.0), 1),
        "features": {
            "lower_extension_pct": round(lower_extension_pct, 3),
            "upper_extension_pct": round(upper_extension_pct, 3),
            "reclaimed_lower_band": reclaimed_lower,
            "distance_to_mid_pct": round(distance_to_mid_pct, 3),
            "volume_ratio": round(volume_ratio, 2),
            "candle_move_pct": round(candle_move_pct, 3),
        },
    }


def verify_and_trigger_signals(
    df: pd.DataFrame,
    symbol: str,
    pool_address: str,
    timeframe: str,
    active_trades: Dict[str, Any],
) -> Dict[str, Any]:
    if len(df) < 30:
        return {"triggered": False, "score": 0}

    current = df.iloc[-1]
    previous = df.iloc[-2]
    current_price = float(current["close"])
    atr_val = float(current["atr"]) if pd.notna(current["atr"]) else 0.0
    if atr_val <= 0:
        return {"triggered": False, "score": 0}

    range_info = calculate_buy_range(current_price, atr_val, timeframe)

    trade_id = f"{pool_address}_{timeframe}"
    if trade_id in active_trades:
        process_trailing_stops(
            symbol,
            current_price,
            pool_address,
            timeframe,
            active_trades,
            current,
            previous,
        )
        return {
            "triggered": False,
            "score": 0,
            "in_trade": True,
            "spot": current_price,
            "buy_low": range_info["buy_low"],
            "buy_high": range_info["buy_high"],
        }

    orb_signal = score_opening_range_breakout(df) if timeframe == "5m" else {"score": 0.0, "features": {}}
    bollinger_signal = score_bollinger_reversal(df)
    selected_signal = None
    if orb_signal["score"] >= ORB_MIN_SCORE_TO_BUY:
        selected_signal = {"strategy": "OPENING RANGE BREAKOUT BUY", "score": orb_signal["score"], "features": orb_signal["features"]}
    if bollinger_signal["score"] >= BOLLINGER_MIN_SCORE_TO_BUY:
        bollinger_entry = {"strategy": "BOLLINGER LOWER BAND REVERSAL BUY", "score": bollinger_signal["score"], "features": bollinger_signal["features"]}
        if selected_signal is None or bollinger_entry["score"] > selected_signal["score"]:
            selected_signal = bollinger_entry

    if selected_signal:
        sl = current_price - (1.8 * atr_val)
        tp1 = current_price + (1.8 * atr_val)
        tp2 = current_price + (3.6 * atr_val)
        active_trades[trade_id] = {
            "entry_price": current_price,
            "highest_recorded_price": current_price,
            "current_stop_loss": sl,
            "tp1": tp1,
            "tp2": tp2,
            "atr_value": atr_val,
            "strategy": selected_signal["strategy"],
            "strategy_score": selected_signal["score"],
            "strategy_features": selected_signal["features"],
        }
        dispatch_discord_signal(
            symbol,
            pool_address,
            selected_signal["strategy"],
            current_price,
            range_info["buy_low"],
            range_info["buy_high"],
            sl,
            tp1,
            tp2,
            timeframe,
        )
        return {
            "triggered": True,
            "score": selected_signal["score"],
            "strategy": selected_signal["strategy"],
            "spot": current_price,
            "buy_low": range_info["buy_low"],
            "buy_high": range_info["buy_high"],
        }

    if timeframe == "5m":
        rsi_signal = previous["rsi"] <= 30 and current["rsi"] > 30
        macd_signal = current["histogram"] > previous["histogram"]
        trend_bullish = bool(current["supertrend"] is True and current["close"] > current["ema"])
        score = int(rsi_signal) + int(macd_signal) + int(trend_bullish)

        if rsi_signal and macd_signal and trend_bullish:
            sl = current_price - (1.5 * atr_val)
            tp1 = current_price + (1.5 * atr_val)
            tp2 = current_price + (3.0 * atr_val)
            active_trades[trade_id] = {
                "entry_price": current_price,
                "highest_recorded_price": current_price,
                "current_stop_loss": sl,
                "tp1": tp1,
                "tp2": tp2,
                "atr_value": atr_val,
            }
            dispatch_discord_signal(
                symbol,
                pool_address,
                "TRAILING FAST SCALP BUY",
                current_price,
                range_info["buy_low"],
                range_info["buy_high"],
                sl,
                tp1,
                tp2,
                timeframe,
            )
            return {
                "triggered": True,
                "score": score,
                "spot": current_price,
                "buy_low": range_info["buy_low"],
                "buy_high": range_info["buy_high"],
            }

        return {
            "triggered": False,
            "score": score,
            "spot": current_price,
            "buy_low": range_info["buy_low"],
            "buy_high": range_info["buy_high"],
        }

    if timeframe == "15m":
        rsi_signal = previous["rsi"] <= 40 and current["rsi"] > 40
        macd_signal = current["macd_line"] > current["signal_line"] and current["histogram"] >= previous["histogram"]
        trend_bullish = bool(current["supertrend"] is True and current["close"] > current["ema"])
        score = int(rsi_signal) + int(macd_signal) + int(trend_bullish)

        if rsi_signal and macd_signal and trend_bullish:
            sl = current_price - (2.0 * atr_val)
            tp1 = current_price + (2.0 * atr_val)
            tp2 = current_price + (4.0 * atr_val)
            active_trades[trade_id] = {
                "entry_price": current_price,
                "highest_recorded_price": current_price,
                "current_stop_loss": sl,
                "tp1": tp1,
                "tp2": tp2,
                "atr_value": atr_val,
            }
            dispatch_discord_signal(
                symbol,
                pool_address,
                "TRAILING MID SWING BUY",
                current_price,
                range_info["buy_low"],
                range_info["buy_high"],
                sl,
                tp1,
                tp2,
                timeframe,
            )
            return {
                "triggered": True,
                "score": score,
                "spot": current_price,
                "buy_low": range_info["buy_low"],
                "buy_high": range_info["buy_high"],
            }

        return {
            "triggered": False,
            "score": score,
            "spot": current_price,
            "buy_low": range_info["buy_low"],
            "buy_high": range_info["buy_high"],
        }

    if timeframe == "4h":
        macd_crossover = previous["macd_line"] <= previous["signal_line"] and current["macd_line"] > current["signal_line"]
        deep_oversold = current["rsi"] < 35 or previous["rsi"] < 30
        trend_flip = bool(previous["supertrend"] is False and current["supertrend"] is True)
        score = int(macd_crossover) + int(deep_oversold) + int(trend_flip)

        if (macd_crossover and deep_oversold) or trend_flip:
            sl = current_price - (2.5 * atr_val)
            tp1 = current_price + (2.5 * atr_val)
            tp2 = current_price + (5.0 * atr_val)
            active_trades[trade_id] = {
                "entry_price": current_price,
                "highest_recorded_price": current_price,
                "current_stop_loss": sl,
                "tp1": tp1,
                "tp2": tp2,
                "atr_value": atr_val,
            }
            dispatch_discord_signal(
                symbol,
                pool_address,
                "TRAILING LONG SWING BUY",
                current_price,
                range_info["buy_low"],
                range_info["buy_high"],
                sl,
                tp1,
                tp2,
                timeframe,
            )
            return {
                "triggered": True,
                "score": score,
                "spot": current_price,
                "buy_low": range_info["buy_low"],
                "buy_high": range_info["buy_high"],
            }

        return {
            "triggered": False,
            "score": score,
            "spot": current_price,
            "buy_low": range_info["buy_low"],
            "buy_high": range_info["buy_high"],
        }

    return {
        "triggered": False,
        "score": 0,
        "spot": current_price,
        "buy_low": range_info["buy_low"],
        "buy_high": range_info["buy_high"],
    }


def fetch_coinbase_products(max_products: int = 150) -> List[Dict[str, str]]:
    response = requests.get(f"{COINBASE_API_BASE}/products", timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    products = response.json()

    ranked: List[Dict[str, Any]] = []
    for item in products:
        product_id = item.get("id")
        if not product_id:
            continue

        if item.get("status") not in (None, "online"):
            continue

        if item.get("trading_disabled") is True:
            continue

        quote_currency = item.get("quote_currency", "")
        if quote_currency not in ("USD", "USDC"):
            continue

        try:
            volume_24h = float(item.get("volume_24h") or 0.0)
        except (TypeError, ValueError):
            volume_24h = 0.0

        ranked.append(
            {
                "symbol": item.get("display_name") or product_id,
                "address": product_id,
                "volume_24h": volume_24h,
            }
        )

    ranked.sort(key=lambda x: x["volume_24h"], reverse=True)
    return [{"symbol": row["symbol"], "address": row["address"]} for row in ranked[:max_products]]


def fetch_top_pools(max_pages: int = 7, max_pools: int = 150) -> List[Dict[str, str]]:
    if DATA_PROVIDER == "coinbase":
        return fetch_coinbase_products(max_products=max_pools)

    discovered_pools: List[Dict[str, str]] = []
    for page in range(1, max_pages + 1):
        try:
            top_pools_url = f"https://api.geckoterminal.com/api/v2/networks/{TARGET_NETWORK}/pools?page={page}"
            response = requests.get(top_pools_url, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            data = response.json()
            for item in data.get("data", []):
                attrs = item.get("attributes", {})
                address = attrs.get("address")
                name = attrs.get("name", "UNK")
                if address:
                    discovered_pools.append({"symbol": name, "address": address})
        except Exception as exc:
            logging.warning("Pool discovery failed on page %s: %s", page, exc)
            break

    return discovered_pools[:max_pools]


def fetch_coinbase_ohlcv_5m(product_id: str) -> pd.DataFrame:
    candles_url = f"{COINBASE_API_BASE}/products/{product_id}/candles?granularity=300"
    response = requests.get(candles_url, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    candles = response.json()

    df = pd.DataFrame(candles, columns=["timestamp", "low", "high", "open", "close", "volume"])
    if df.empty:
        raise ValueError(f"No candles returned for product {product_id}")

    df = df.sort_values("timestamp").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    return df


def fetch_ohlcv_5m(pool_address: str) -> pd.DataFrame:
    if DATA_PROVIDER == "coinbase":
        return fetch_coinbase_ohlcv_5m(pool_address)

    ohlcv_url = (
        f"https://api.geckoterminal.com/api/v2/networks/{TARGET_NETWORK}/pools/{pool_address}/ohlcv/minute"
        "?aggregate=5&limit=100"
    )
    response = requests.get(ohlcv_url, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    ohlcv_list = payload["data"]["attributes"]["ohlcv_list"]

    df = pd.DataFrame(ohlcv_list, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    return df


def run_scanner_cycle() -> None:
    logging.info("Starting trailing scanner cycle")
    active_trades = load_tracked_trades()
    discovered_pools = fetch_top_pools()

    for pool in discovered_pools:
        try:
            df = fetch_ohlcv_5m(pool["address"])
            df_5m = calculate_technical_indicators(df.copy())

            df["dt"] = pd.to_datetime(df["timestamp"], unit="s")
            df_15m = (
                df.resample("15min", on="dt")
                .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
                .dropna()
                .reset_index(drop=True)
            )
            if len(df_15m) > 0:
                df_15m = calculate_technical_indicators(df_15m)

            df_4h = (
                df.resample("4h", on="dt")
                .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
                .dropna()
                .reset_index(drop=True)
            )
            if len(df_4h) > 0:
                df_4h = calculate_technical_indicators(df_4h)

            timeframe_map = {
                "5m": df_5m,
                "15m": df_15m,
                "4h": df_4h,
            }

            for timeframe in TIMEFRAMES_TO_SCAN:
                tf_df = timeframe_map.get(timeframe)
                if tf_df is None or len(tf_df) == 0:
                    continue
                verify_and_trigger_signals(tf_df, pool["symbol"], pool["address"], timeframe, active_trades)
        except Exception as exc:
            logging.debug("Skipping pool %s due to error: %s", pool.get("address", "unknown"), exc)
            continue

    save_tracked_trades(active_trades)
    logging.info("Trailing scanner cycle completed. Active trades: %s", len(active_trades))


@app.timer_trigger(
    schedule="0 */10 * * * *",
    arg_name="myTimer",
    run_on_startup=False,
    use_monitor=True,
)
def trailing_macro_dynamic_scanner(myTimer: func.TimerRequest) -> None:
    if DATA_PROVIDER == "coinbase":
        logging.info("Skipping trailing_macro_dynamic_scanner because DATA_PROVIDER=coinbase.")
        return
    if myTimer.past_due:
        logging.info("The timer is past due.")
    run_scanner_cycle()


@app.timer_trigger(
    schedule="0 */10 * * * *",
    arg_name="cointrackingTimer",
    run_on_startup=False,
    use_monitor=True,
)
def cointracking(cointrackingTimer: func.TimerRequest) -> None:
    if DATA_PROVIDER != "coinbase":
        logging.info("Skipping cointracking because DATA_PROVIDER is not coinbase.")
        return
    if cointrackingTimer.past_due:
        logging.info("cointracking timer is past due.")
    run_scanner_cycle()


@app.timer_trigger(
    schedule="0 */5 * * * *",
    arg_name="paperTimer",
    run_on_startup=False,
    use_monitor=True,
)
def paper_trader(paperTimer: func.TimerRequest) -> None:
    """
    Paper/live trading engine (multi-timeframe breakout entries, trailing-stop and
    bearish-reversal exits, per-coin daily loss caps, 6-hourly Discord summaries).

    Controlled by app settings:
      PAPER_TRADER_ENABLED = "true"/"false"  (master switch, default true)
      TRADING_MODE         = "paper"/"live"
      LIVE_TRADING_ENABLED = "true"/"false"  (extra live safety)
      DATA_DIR             = writable state path (e.g. /home/data)
    Runs entirely on public market data when no CB_API_KEY/CB_API_SECRET are set.
    """
    if os.environ.get("PAPER_TRADER_ENABLED", "true").strip().lower() != "true":
        logging.info("Skipping paper_trader because PAPER_TRADER_ENABLED is not true.")
        return
    if paperTimer.past_due:
        logging.info("paper_trader timer is past due.")
    try:
        import trader  # sibling module deployed alongside function_app.py
        client = trader.get_crypto_client()  # None -> public-data paper mode
        active_positions, _ = trader.run_trading_cycle(client)
        logging.info("paper_trader cycle complete: %d open position(s).", len(active_positions))
    except Exception as exc:
        logging.exception("paper_trader cycle failed: %s", exc)


@app.route(route="paper-trading/summary", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def paper_trading_summary(req: func.HttpRequest) -> func.HttpResponse:
    try:
        import trader

        # Use the same blob-primary state loader as the trading cycle. Keeping a
        # separate summary-only loader can silently fall back to an empty local
        # file after clean deploys, while the real portfolio blob still has data.
        active_positions = trader.load_json_file(trader.PORTFOLIO_FILE)
        history = trader.load_json_file(trader.HISTORY_FILE)
        if not isinstance(active_positions, dict):
            active_positions = {}
        if not isinstance(history, list):
            history = []

        live_prices = {}
        for product_id, position in active_positions.items():
            fallback_price = float(position.get("highest_tracked_price") or position.get("entry_price") or 0.0)
            live_prices[product_id] = get_coinbase_mark_price(product_id, fallback=fallback_price)

        # A position can produce TWO realized records: a PARTIAL (moon-bag
        # take-profit) and a CLOSED (final exit). Both carry realized pnl_usd, so
        # realized totals and the daily breakdown must include BOTH — otherwise
        # moon-bag profits silently vanish from the dashboard.
        realized_events = [
            trade for trade in history
            if isinstance(trade, dict)
            and trade.get("performance", {}).get("status") in ("PARTIAL", "CLOSED")
        ]
        closed = [
            trade for trade in realized_events
            if trade.get("performance", {}).get("status") == "CLOSED"
        ]
        realized_total = sum(float(trade["performance"].get("pnl_usd", 0) or 0) for trade in realized_events)
        wins = sum(1 for trade in closed if float(trade["performance"].get("pnl_usd", 0) or 0) > 0)
        losses = len(closed) - wins

        open_positions = []
        unrealized_total = 0.0
        total_invested_usd = 0.0
        open_market_value_usd = 0.0
        for product_id, position in active_positions.items():
            entry_price = float(position.get("entry_price", 0) or 0)
            mark_price = float(live_prices.get(product_id, entry_price) or entry_price)
            allocated = float(position.get("allocated_usd", trader.CAPITAL_PER_TRADE_USD) or 0)
            # original_allocated_usd tracks the full position size before any
            # partial take-profit reduced allocated_usd (moon bag).
            original_allocated = float(
                position.get("original_allocated_usd", allocated) or allocated
            )
            total_invested_usd += original_allocated
            quantity = float(position.get("simulated_qty", 0) or 0)
            market_value = quantity * mark_price
            open_market_value_usd += market_value
            pnl = market_value - allocated
            pnl_pct = (pnl / allocated * 100) if allocated else 0.0
            unrealized_total += pnl
            open_positions.append({
                # camelCase keys so the React dashboard can consume this endpoint
                # directly without a field-name translation layer.
                "productId": product_id,
                "mode": position.get("mode", "paper"),
                "entryTimestampUtc": position.get("entry_timestamp"),
                "entryPriceUsd": entry_price,
                "markPriceUsd": mark_price,
                "allocatedUsd": allocated,
                "originalAllocatedUsd": original_allocated,
                "marketValueUsd": round(market_value, 2),
                "quantity": quantity,
                "currentTrailingStop": float(position.get("current_trailing_stop", 0) or 0),
                "currentTrailPct": float(position.get("current_trail_pct", 0) or 0),
                "takeProfitBoundary": float(position.get("take_profit_boundary", 0) or 0),
                "strategy": position.get("entry_strategy", ""),
                "strategyScore": float(position.get("entry_strategy_score", 0) or 0),
                "partialTakeProfitTaken": bool(position.get("partial_take_profit_taken", False)),
                "unrealizedPnlUsd": round(pnl, 2),
                "unrealizedPnlPct": round(pnl_pct, 2),
            })

        # Also add total invested from closed history trades that are fully exited
        # (PARTIAL + CLOSED together reconstruct the original position size).
        history_allocated = sum(
            float((t.get("entry") or {}).get("allocated_capital_usd") or 0)
            for t in realized_events
        )
        total_invested_usd += history_allocated

        # Daily breakdown across ALL realized events (partial + full), so every
        # trading day with any realized profit/loss shows up.
        daily = {}
        for trade in realized_events:
            exit_timestamp = (trade.get("exit") or {}).get("timestamp", "")
            day = exit_timestamp[:10] if exit_timestamp else "unknown"
            status = (trade.get("performance") or {}).get("status")
            bucket = daily.setdefault(day, {"closed_trades": 0, "partial_takes": 0, "realized_pnl_usd": 0.0})
            if status == "CLOSED":
                bucket["closed_trades"] += 1
            else:
                bucket["partial_takes"] += 1
            bucket["realized_pnl_usd"] += float((trade.get("performance") or {}).get("pnl_usd", 0) or 0)

        daily_rows = [
            {
                "date": day,
                "closedTrades": values["closed_trades"],
                "partialTakes": values["partial_takes"],
                "realizedPnlUsd": round(values["realized_pnl_usd"], 2),
            }
            for day, values in sorted(daily.items(), reverse=True)
        ]

        def _parse_exit_timestamp(trade: dict):
            value = (trade.get("exit") or {}).get("timestamp") or ""
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except Exception:
                return None

        rolling_windows = []
        rolling_days = [
            int(value.strip())
            for value in os.environ.get("SUMMARY_ROLLING_WINDOWS_DAYS", "1,7,30").split(",")
            if value.strip().isdigit()
        ]
        now_utc = datetime.now(timezone.utc)
        for days in rolling_days:
            cutoff = now_utc - timedelta(days=days)
            window_events = []
            for trade in realized_events:
                exit_dt = _parse_exit_timestamp(trade)
                if exit_dt and exit_dt >= cutoff:
                    window_events.append(trade)
            window_closed = [
                trade for trade in window_events
                if (trade.get("performance") or {}).get("status") == "CLOSED"
            ]
            window_pnl = sum(float((trade.get("performance") or {}).get("pnl_usd", 0) or 0) for trade in window_events)
            rolling_windows.append({
                "days": days,
                "closedTrades": len(window_closed),
                "partialTakes": len(window_events) - len(window_closed),
                "realizedPnlUsd": round(window_pnl, 2),
            })

        # Flatten each realized-event record into a clean camelCase dict that
        # matches what the React dashboard expects (productId, entryPriceUsd, …).
        def _flatten_trade(t: dict) -> dict:
            entry = t.get("entry") or {}
            exit_ = t.get("exit") or {}
            perf = t.get("performance") or {}
            return {
                "productId": t.get("product_id") or "",
                "mode": t.get("mode") or "paper",
                "entryTimestampUtc": entry.get("timestamp") or "",
                "exitTimestampUtc": exit_.get("timestamp") or "",
                "entryPriceUsd": float(entry.get("price_usd") or 0),
                "exitPriceUsd": float(exit_.get("price_usd") or 0),
                "exitReason": exit_.get("reason") or "",
                "pnlUsd": float(perf.get("pnl_usd") or 0),
                "pnlPercentage": float(perf.get("pnl_percentage") or 0),
                "allocatedCapitalUsd": float(entry.get("allocated_capital_usd") or 0),
                "status": perf.get("status") or "",
            }

        # Full realized history (partial + closed), newest first.
        max_trades = int(os.environ.get("SUMMARY_MAX_TRADES", "500"))
        recent_closed = sorted(
            realized_events,
            key=lambda t: (t.get("exit") or {}).get("timestamp", ""),
            reverse=True,
        )[:max_trades]
        allocated_total = sum(p["allocatedUsd"] for p in open_positions)
        starting_capital_usd = float(trader.TOTAL_CAPITAL_USD)
        available_cash_usd = starting_capital_usd + realized_total - allocated_total
        total_equity_usd = available_cash_usd + open_market_value_usd

        payload = {
            # All keys are camelCase so the React dashboard can use this endpoint
            # directly without a translation layer (no C# proxy needed).
            "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "closedTrades": len(closed),
                "partialTakes": len(realized_events) - len(closed),
                "wins": wins,
                "losses": losses,
                "winRatePct": round((wins / len(closed) * 100), 2) if closed else 0.0,
                "realizedPnlUsd": round(realized_total, 2),
                "openPositions": len(open_positions),
                "unrealizedPnlUsd": round(unrealized_total, 2),
                "totalPnlUsd": round(realized_total + unrealized_total, 2),
                "startingCapitalUsd": round(starting_capital_usd, 2),
                "allocatedUsd": round(allocated_total, 2),
                "availableCashUsd": round(available_cash_usd, 2),
                "openMarketValueUsd": round(open_market_value_usd, 2),
                "totalEquityUsd": round(total_equity_usd, 2),
                "liquidationCashUsd": round(total_equity_usd, 2),
                # Total capital ever committed to trade entries (open + closed/partial).
                "totalInvestedUsd": round(total_invested_usd, 2),
            },
            "daily": daily_rows,
            "rollingWindows": rolling_windows,
            "openPositions": open_positions,
            "recentClosedTrades": [_flatten_trade(t) for t in recent_closed],
        }
        return func.HttpResponse(json.dumps(payload), status_code=200, mimetype="application/json")
    except Exception as exc:
        logging.exception("paper_trading_summary failed: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": str(exc)}),
            status_code=500,
            mimetype="application/json",
        )


@app.route(route="paper-trading/close", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def paper_trading_close(req: func.HttpRequest) -> func.HttpResponse:
    try:
        import trader

        try:
            body = req.get_json()
            if not isinstance(body, dict):
                body = {}
        except Exception:
            body = {}

        product_filter = (
            req.params.get("productId")
            or req.params.get("product_id")
            or body.get("productId")
            or body.get("product_id")
            or ""
        ).strip().upper()
        close_all = str(req.params.get("all") or body.get("all") or "").strip().lower() == "true"
        if not product_filter and not close_all:
            return func.HttpResponse(
                json.dumps({
                    "error": "product_required",
                    "message": "Pass productId=COIN-USD to close one paper position, or all=true to close every open paper position.",
                }),
                status_code=400,
                mimetype="application/json",
            )

        result = close_paper_positions(
            trader,
            product_filter=product_filter,
            archive=True,
            exit_reason="MANUAL_CLOSE_REQUEST",
            source="Authenticated manual paper close",
        )
        status_code = int(result.pop("statusCode", 200))
        return func.HttpResponse(json.dumps(result), status_code=status_code, mimetype="application/json")
    except Exception as exc:
        logging.exception("paper_trading_close failed: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": "close_failed", "message": str(exc)}),
            status_code=500,
            mimetype="application/json",
        )


@app.route(route="paper-trading/reset", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def paper_trading_reset(req: func.HttpRequest) -> func.HttpResponse:
    try:
        import trader

        confirm = (req.params.get("confirm") or "").strip()
        if confirm != "RESET":
            return func.HttpResponse(
                json.dumps({
                    "error": "confirmation_required",
                    "message": "POST with ?confirm=RESET to archive open paper positions and clear the active-position blob.",
                }),
                status_code=400,
                mimetype="application/json",
            )

        archive = (req.params.get("archive") or "true").strip().lower() != "false"
        product_filter = (req.params.get("productId") or req.params.get("product_id") or "").strip().upper()

        result = close_paper_positions(
            trader,
            product_filter=product_filter,
            archive=archive,
            exit_reason="MANUAL_RESET_CLOSED_AT_MARK",
            source="Manual paper reset",
        )
        status_code = int(result.pop("statusCode", 200))
        return func.HttpResponse(json.dumps(result), status_code=status_code, mimetype="application/json")
    except Exception as exc:
        logging.exception("paper_trading_reset failed: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": "reset_failed", "message": str(exc)}),
            status_code=500,
            mimetype="application/json",
        )


@app.route(route="paper-trading/fresh-start", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def paper_trading_fresh_start(req: func.HttpRequest) -> func.HttpResponse:
    try:
        import trader

        confirm = (req.params.get("confirm") or "").strip()
        if confirm != "RESET":
            return func.HttpResponse(
                json.dumps({
                    "error": "confirmation_required",
                    "message": "POST with ?confirm=RESET to archive any open paper positions, backup history, and reset the paper account to a fresh baseline.",
                }),
                status_code=400,
                mimetype="application/json",
            )

        archive_open_positions = (req.params.get("archiveOpenPositions") or "true").strip().lower() != "false"
        result = fresh_start_paper_trading(trader, archive_open_positions=archive_open_positions)
        return func.HttpResponse(json.dumps(result), status_code=200, mimetype="application/json")
    except Exception as exc:
        logging.exception("paper_trading_fresh_start failed: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": "fresh_start_failed", "message": str(exc)}),
            status_code=500,
            mimetype="application/json",
        )


@app.route(route="crypto-scan", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def crypto_scan(req: func.HttpRequest) -> func.HttpResponse:
    """
    Read-only crypto opportunity scan for the dashboard's Crypto Scalp tab.
    Serves the ranked snapshot produced by the paper-trader cycle (same Coinbase
    data + strategy stack the engine trades on). Pass ?refresh=1 to force a fresh
    on-demand scan when the cached snapshot is missing or stale.
    """
    try:
        import trader

        refresh = (req.params.get("refresh", "") or "").strip().lower() in ("1", "true", "yes")
        snapshot = trader.load_json_file(trader.SCAN_SNAPSHOT_FILE)

        stale = True
        if isinstance(snapshot, dict) and snapshot.get("generated_at_utc"):
            try:
                generated = datetime.fromisoformat(
                    str(snapshot["generated_at_utc"]).replace("Z", "+00:00")
                )
                age_seconds = (datetime.now(timezone.utc) - generated).total_seconds()
                stale = age_seconds > int(os.environ.get("SCAN_SNAPSHOT_MAX_AGE_SECONDS", "1200"))
            except Exception:
                stale = True

        if refresh or not isinstance(snapshot, dict) or not snapshot.get("setups") or stale:
            snapshot = trader.run_scan_snapshot()

        return func.HttpResponse(json.dumps(snapshot), status_code=200, mimetype="application/json")
    except Exception as exc:
        logging.exception("crypto_scan failed: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": str(exc), "setups": []}),
            status_code=500,
            mimetype="application/json",
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_scanner_cycle()
