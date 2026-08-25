"""
execution_daemon.py — Lean long-lived Kalshi execution daemon.
"""

import logging
import os
import sys
import threading
import time

from config import (
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

# Guards against stacking refreshes: a full set is five tool-calling round trips
# (~2 minutes), which is longer than nothing but shorter than the 4h TTL, so at
# most one can ever be in flight.
_briefing_refresh_running = threading.Event()


def _refresh_cockpit_briefings() -> None:
    """Regenerate any cockpit briefing that has aged past its TTL.

    Lives here rather than in forecast.runner's scheduler because this daemon is
    what production actually runs; the runner's schedule.* jobs only fire when
    forecast/runner.py is executed directly.

    Runs off-thread so five model round trips never delay a trading cycle, and
    refreshes only what is stale, so the 4h TTL alone sets the cadence.
    """
    try:
        from dashboard.briefing_cache import refresh_stale_briefings

        refreshed = refresh_stale_briefings()
        if refreshed:
            ok = sum(1 for r in refreshed if r.get("ok"))
            logger.info("Cockpit briefings refreshed: %d/%d succeeded.", ok, len(refreshed))
    except Exception:
        logger.exception("Cockpit briefing refresh failed")
    finally:
        _briefing_refresh_running.clear()


def _start_briefing_refresh_if_due() -> None:
    if _briefing_refresh_running.is_set():
        return
    _briefing_refresh_running.set()
    threading.Thread(target=_refresh_cockpit_briefings, daemon=True).start()


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
    # Bankroll comes from Kalshi, not a hand-set constant. ACCOUNT_SIZE stays
    # only as the last-resort floor inside resolve_live_bankroll.
    from runtime.live_account import resolve_live_bankroll

    logger.info(
        "[ExecutionDaemon] Startup bankroll from broker: $%.2f (re-read every cycle)",
        resolve_live_bankroll(),
    )
    # Sweep stray resting orders BEFORE reconciliation. Production never rests
    # an entry; anything found is historical, external, or anomalous and must be
    # cleared before the bot can trust its position snapshot.
    try:
        from execution.kalshi_broker import KalshiBroker

        _sweep_broker = KalshiBroker()
        if not _sweep_broker.connect() or not _sweep_broker.is_connected():
            logger.critical("Startup broker snapshot is not authoritative; refusing to trade.")
            return 2
        _sweep = _sweep_broker.cancel_all_resting_orders(reason="daemon startup")
        if not bool(_sweep.get("ok")):
            logger.critical("Startup orphan sweep is uncertain: %s", _sweep)
            return 3
        if int(_sweep.get("cleared") or 0) > 0:
            logger.warning(
                "[ExecutionDaemon] Cleared %d stray resting order(s) at startup.",
                int(_sweep["cleared"]),
            )
    except Exception:
        logger.exception("Startup orphan sweep failed; refusing to trade")
        return 3

    try:
        run_reconciliation()
    except Exception:
        logger.exception("Position reconciliation failed at startup; refusing to trade")
        return 4
    try:
        sync_incidents_and_notify()
    except Exception:
        logger.exception("Incident sync failed after startup reconciliation")
    logger.info("Execution daemon online (sleep=%ss).", sleep_seconds)
    weather_monitor_started = False

    try:
        from notifications.telegram_bot import start_bot_thread

        start_bot_thread()
        logger.info("Embedded Telegram daemon started inside execution-engine.")
    except Exception:
        logger.exception("Embedded Telegram daemon startup failed")

    from datetime import datetime, timezone
    from forecast.firewall import reset_daily_flag
    last_utc_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        while True:
            cycle_started = time.time()
            current_utc_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if current_utc_day != last_utc_day:
                try:
                    reset_daily_flag()
                    last_utc_day = current_utc_day
                except Exception:
                    logger.exception("Failed to reset daily firewall flag at UTC day boundary")
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
                    # Re-read per cycle: the account moves as positions settle,
                    # so a value captured at startup goes stale within hours.
                    summary = run_execution_cycle(
                        bankroll=resolve_live_bankroll(), run_rbi=True
                    )
                    logger.info("Live Execution cycle complete: %s", summary)

                    try:
                        from runtime.sentinel import check_and_alert

                        check_and_alert()
                    except Exception:
                        logger.exception("Sentinel check failed")

                    if not weather_monitor_started:
                        start_weather_monitor()
                        weather_monitor_started = True
                        logger.info("Weather monitor started after initial on-demand hydration.")

                    _start_briefing_refresh_if_due()
                try:
                    sync_incidents_and_notify()
                except Exception:
                    logger.exception("Incident sync failed after execution cycle")
            except Exception:
                logger.exception("Execution cycle failed")
                try:
                    from notifications.notification_engine import notify_kill_switch

                    notify_kill_switch(
                        "runtime_loop_exception",
                        "Execution cycle crashed before clean completion. Inspect runtime logs immediately.",
                    )
                except Exception:
                    logger.exception("Kill-switch notification dispatch failed")

            elapsed = time.time() - cycle_started
            logger.info("Sleeping %ss before next cycle (elapsed=%.1fs).", sleep_seconds, elapsed)
            time.sleep(sleep_seconds)
    except KeyboardInterrupt:
        logger.info("Execution daemon interrupted. Exiting cleanly.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
