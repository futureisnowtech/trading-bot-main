"""
execution_daemon.py — Lean long-lived Kalshi execution daemon.
"""

import logging
import os
import sys
import time

from config import (
    ACCOUNT_SIZE,
    FORECAST_AUTONOMOUS_ENABLED,
    FORECAST_LANE_ACTIVE,
    KALSHI_ENABLED,
)
from data.kalshi_weather_monitor import start_weather_monitor
from forecast.runner import run_execution_cycle
from runtime.incident_tracker import sync_incidents_and_notify
from runtime.logging_setup import configure_runtime_logging
from runtime.position_reconciler import run_reconciliation
from runtime.storage_maintenance import maintain_runtime_storage
from runtime.storage_guard import runtime_storage_status

configure_runtime_logging()

logger = logging.getLogger("execution_daemon")


def main() -> int:
    if not KALSHI_ENABLED:
        logger.warning("Kalshi trading disabled. Exiting cleanly.")
        return 0

    if not FORECAST_LANE_ACTIVE:
        logger.warning("Forecast lane inactive. Exiting cleanly.")
        return 0

    if not FORECAST_AUTONOMOUS_ENABLED:
        logger.warning("Autonomous forecast trading disabled. Exiting cleanly.")
        return 0

    sleep_seconds = max(1, int(float(os.getenv("SNIPER_SLEEP_SECONDS", "300"))))
    bankroll = float(ACCOUNT_SIZE)
    telegram_thread_started = False

    try:
        run_reconciliation()
    except Exception:
        logger.exception("Position reconciliation failed at startup")
    try:
        sync_incidents_and_notify()
    except Exception:
        logger.exception("Incident sync failed after startup reconciliation")
    logger.info("Execution daemon online (sleep=%ss).", sleep_seconds)
    weather_monitor_started = False

    try:
        from notifications.telegram_bot import start_bot_thread

        start_bot_thread()
        telegram_thread_started = True
        logger.info("Embedded Telegram daemon started inside execution-engine.")
    except Exception:
        logger.exception("Embedded Telegram daemon startup failed")

    try:
        while True:
            cycle_started = time.time()
            try:
                try:
                    maintain_runtime_storage()
                except Exception:
                    logger.exception("Runtime storage maintenance failed")

                storage = runtime_storage_status()
                if not storage["ok"]:
                    logger.error(
                        "Low disk headroom: %.0fMB free at %s (threshold=%.0fMB). "
                        "Skipping execution cycle.",
                        storage["free_mb"],
                        storage["path"],
                        storage["threshold_mb"],
                    )
                else:
                    summary = run_execution_cycle(bankroll=bankroll, run_rbi=True)
                    logger.info("Execution cycle complete: %s", summary)
                    if not weather_monitor_started:
                        start_weather_monitor()
                        weather_monitor_started = True
                        logger.info("Weather monitor started after initial on-demand hydration.")
                try:
                    sync_incidents_and_notify()
                except Exception:
                    logger.exception("Incident sync failed after execution cycle")
            except Exception:
                logger.exception("Execution cycle failed")

            elapsed = time.time() - cycle_started
            logger.info("Sleeping %ss before next cycle (elapsed=%.1fs).", sleep_seconds, elapsed)
            time.sleep(sleep_seconds)
    except KeyboardInterrupt:
        logger.info("Execution daemon interrupted. Exiting cleanly.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
