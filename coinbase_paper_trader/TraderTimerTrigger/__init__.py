"""
Azure Timer Trigger — fires every 5 minutes (cron: 0 */5 * * * *)

Secrets are read from Azure Function App Settings (environment variables),
which are injected securely and never stored in code or source control.

To configure in Azure Portal:
  Function App → Settings → Environment variables → + Add:
    CB_API_KEY        = <your Coinbase API key>
    CB_API_SECRET     = <your Coinbase API secret>
    DISCORD_WEBHOOK_URL = <your Discord webhook URL>  (optional)
"""

import logging
import azure.functions as func

# Import the core trading logic from the sibling module
import sys
import os

# Ensure the parent package is importable when running inside Azure
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trader import (
    _validate_config,
    get_crypto_client,
    get_market_snapshot,
    load_json_file,
    save_json_file,
    load_daily_ledger,
    update_market_state,
    manage_active_positions,
    scan_and_execute_entries,
    maybe_send_summary,
    PORTFOLIO_FILE,
    MARKET_STATE_FILE,
    DAILY_PNL_FILE,
)


def main(timer: func.TimerRequest) -> None:
    """Entry point called by the Azure Functions runtime every 5 minutes."""
    logging.info("Coinbase paper trader cycle started.")

    try:
        _validate_config()
    except EnvironmentError as exc:
        logging.error("Configuration error: %s", exc)
        return

    try:
        client = get_crypto_client()

        products, live_prices = get_market_snapshot(client)

        if not live_prices:
            logging.warning("No prices received — skipping cycle.")
            return

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

        logging.info("Cycle complete. Active positions: %d", len(active_positions))

    except Exception as exc:
        # Log the error; Azure Functions will handle retry policy
        logging.exception("Unhandled exception in trader cycle: %s", exc)
        raise
