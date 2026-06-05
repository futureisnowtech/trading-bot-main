"""
sniper_cron.py — Lean single-pass execution entrypoint.
"""

import logging
import sys

from config import (
    ACCOUNT_SIZE,
    FORECAST_AUTONOMOUS_ENABLED,
    FORECAST_LANE_ACTIVE,
    KALSHI_ENABLED,
)
from forecast.runner import run_execution_cycle
from runtime.incident_tracker import sync_incidents_and_notify
from runtime.position_reconciler import run_reconciliation
from runtime.storage_maintenance import maintain_runtime_storage
from runtime.storage_guard import runtime_storage_status


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


def main() -> int:
    if not KALSHI_ENABLED:
        logging.warning("Kalshi trading disabled. Exiting cleanly.")
        return 0

    if not FORECAST_LANE_ACTIVE:
        logging.warning("Forecast lane inactive. Exiting cleanly.")
        return 0

    if not FORECAST_AUTONOMOUS_ENABLED:
        logging.warning("Autonomous forecast trading disabled. Exiting cleanly.")
        return 0

    try:
        run_reconciliation()
    except Exception:
        logging.exception("Position reconciliation failed")
    try:
        sync_incidents_and_notify()
    except Exception:
        logging.exception("Incident sync failed after reconciliation")

    try:
        maintain_runtime_storage()
    except Exception:
        logging.exception("Runtime storage maintenance failed")

    storage = runtime_storage_status()
    if not storage["ok"]:
        logging.error(
            "Low disk headroom: %.0fMB free at %s (threshold=%.0fMB). "
            "Skipping sniper cycle.",
            storage["free_mb"],
            storage["path"],
            storage["threshold_mb"],
        )
        return 0

    summary = run_execution_cycle(bankroll=float(ACCOUNT_SIZE), run_rbi=True)
    try:
        sync_incidents_and_notify()
    except Exception:
        logging.exception("Incident sync failed after sniper cycle")
    logging.info("Sniper cycle complete: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
