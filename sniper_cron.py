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

    summary = run_execution_cycle(bankroll=float(ACCOUNT_SIZE))
    logging.info("Sniper cycle complete: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
