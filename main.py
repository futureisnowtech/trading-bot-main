"""
main.py — Entry point for the Kalshi Weather Prediction Engine.
Usage:
  python main.py
"""

import sys, os, argparse, time, traceback, logging, threading, json
from datetime import datetime
import pytz

# Ensure project root is on sys.path so runtime/ resolves cleanly
_MAIN_ROOT = os.path.dirname(os.path.abspath(__file__))
if _MAIN_ROOT not in sys.path:
    sys.path.insert(0, _MAIN_ROOT)


# Configure root logger to bot.log + console before anything imports
def _setup_logging():
    _log_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "logs", "bot.log"
    )
    fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.FileHandler(_log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    # Silence noisy third-party loggers
    for noisy in ("urllib3", "requests", "peewee", "schedule"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_setup_logging()

import csv, uuid, sqlite3
from typing import Optional
import logging_db.trade_logger  # noqa: F401

VERSION = "v19.1.KALSHI"

BANNER = """
╔══════════════════════════════════════════════════════════════════╗
║  KALSHI WEATHER ENGINE  v19.1.KALSHI (Sovereign)                ║
║  Hard Architectural Isolation: Pure Prediction Markets           ║
║                                                                  ║
║  Lane:       Kalshi Weather (31-member GFS Ensemble)            ║
║  Route:      maker_only | EV-Aware Expectancy Gates             ║
║  Truth:      broker-direct (Ledgerless v19.1)                    ║
║  Launch:     python3 main.py (Unified Entry Point)              ║
╚══════════════════════════════════════════════════════════════════╝
"""


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--no-alerts", action="store_true")
    return p.parse_args()


def main():
    print(BANNER)
    args = parse_args()

    import system_state

    from config import (
        ACCOUNT_SIZE,
        MARKET_TIMEZONE,
        ANTHROPIC_API_KEY,
        FORECAST_LANE_ACTIVE,
        FORECAST_AUTONOMOUS_ENABLED,
        FORECAST_MANUAL_ENABLED,
    )

    system_state.state.set_mode("LIVE")

    tz = pytz.timezone(MARKET_TIMEZONE)
    mode = "💰 LIVE"
    account_display = float(ACCOUNT_SIZE)

    print(f"  Mode:       {mode} TRADING")
    print(f"  Account:    ${account_display}")
    print(
        f"  AI (exits): {'✅ Enabled' if ANTHROPIC_API_KEY else '⚠️ No API key — extended-thinking exits disabled'}"
    )
    print(f"  Time:       {datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S ET')}\n")

    print("=" * 60)
    print("  💰 KALSHI WEATHER ARCHITECTURE ENFORCED")
    print(f"  Account: ${account_display}")
    print("=" * 60)

    print("📦 Initializing database...")
    from logging_db.trade_logger import init_db, log_event

    init_db()
    print("   ✅ logs/trades.db ready\n")

    # ── Runtime truth tables ──────────────────────────────────────────────────
    from runtime.runtime_state import (
        init_runtime_tables,
        upsert_system_state,
        upsert_lane_state,
    )
    from runtime.incident_tracker import init_incident_table

    _db_path = os.path.join(_MAIN_ROOT, "logs", "trades.db")
    init_runtime_tables(_db_path)
    init_incident_table(_db_path)

    _rt_mode = "live"
    upsert_system_state(
        db_path=_db_path,
        process_mode=_rt_mode,
        startup_ts=datetime.now(pytz.utc).isoformat(),
        process_alive=1,
        global_status="OK",
        launch_readiness_state="NOT_READY",
        active_lanes="['forecast']",
    )

    # forecast lane
    upsert_lane_state(
        "forecast",
        db_path=_db_path,
        lane_role="sovereign",
        enabled=int(FORECAST_LANE_ACTIVE),
        active=int(FORECAST_LANE_ACTIVE),
        configured=1,
        dashboard_visible=1,
        autonomous_enabled=int(FORECAST_AUTONOMOUS_ENABLED and FORECAST_LANE_ACTIVE),
        manual_allowed=int(FORECAST_MANUAL_ENABLED),
        mode=_rt_mode if FORECAST_LANE_ACTIVE else "disabled",
        health="UNKNOWN",
        readiness_state="LANE_NOT_STARTED"
        if not FORECAST_LANE_ACTIVE
        else "BROKER_DISCONNECTED",
        blocked_reason="" if FORECAST_LANE_ACTIVE else "FORECAST_LANE_ACTIVE=false",
        promotion_condition="Sovereign live lane — Kalshi Weather Engine",
    )

    # Write startup heartbeat
    from runtime.runtime_state import write_system_heartbeat
    write_system_heartbeat(_db_path)

    # 📊 Start Prometheus Metrics Server
    from monitoring.metrics import start_metrics_server
    start_metrics_server(port=8000)

    # 🤖 Start Telegram Bot (Command Suite)
    from notifications.telegram_bot import start_bot_thread
    start_bot_thread()

    print("   ✅ Runtime state tables ready\n")

    log_event(
        "INFO",
        "main",
        f"Kalshi Weather Engine started — {VERSION}",
    )

    # ── Forecast lane (Main Execution Loop) ──────────────────────────────────
    if FORECAST_LANE_ACTIVE:
        import schedule as _sched_lib
        from forecast.db import init_forecast_db
        from forecast.runner import (
            run_discovery_cycle,
            run_strategy_cycle,
            run_position_monitor,
            _get_broker,
            _get_harvester,
        )

        init_forecast_db()
        broker = _get_broker()
        connected = broker.connect()
        
        if connected:
            upsert_lane_state(
                "forecast",
                db_path=_db_path,
                connected=1,
                readiness_state="READY",
            )
        
        harvester = _get_harvester()
        harvester.start()
        
        # Initial cycles
        run_discovery_cycle()
        run_strategy_cycle(100.0)
        run_position_monitor()

        # Schedule
        schedule = _sched_lib.Scheduler()
        schedule.every(30).minutes.do(run_discovery_cycle)
        schedule.every(5).minutes.do(lambda: run_strategy_cycle(100.0))
        schedule.every(30).seconds.do(run_position_monitor)
        
        # v19.1.KALSHI: Weather RBI (Learning Loop)
        from learning.weather_rbi import run_weather_rbi
        schedule.every(12).hours.do(run_weather_rbi)
        run_weather_rbi() # Initial run on boot

        print("   ✅ Forecast lane cycles scheduled")
        print("=" * 60)
        print("  Kalshi Weather Engine is live.")
        print("  Stop:      Ctrl+C")
        print("=" * 60 + "\n")

        while True:
            schedule.run_pending()
            time.sleep(1)
    else:
        print("⚠️ FORECAST_LANE_ACTIVE is false. System idling.")
        while True:
            time.sleep(60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nShutdown complete.")
        sys.exit(0)
    except Exception as e:
        print(f"\n💥 Fatal error: {e}")
        traceback.print_exc()
        sys.exit(1)
