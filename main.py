"""
main.py — Entry point for the Algo Trading System.
Usage:
  python main.py              # Full system (reads PAPER_TRADING from .env)
  python main.py --mode paper # Force paper trading
  python main.py --mode live  # Force live (requires typed confirmation)
  python main.py --crypto-only
  python main.py --equity-only
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
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)


_setup_logging()

# Pre-import stdlib modules and the core DB module at module level.
# Python 3.14 on macOS deadlocks (EDEADLK) when importing these lazily
# inside a function in a launchd daemon context. Pre-loading them here
# (in the "safe" startup context) puts them in sys.modules so the lazy
# imports inside main() become zero-cost cache hits.
import csv, uuid, sqlite3
from typing import Optional
import logging_db.trade_logger  # noqa: F401 — pre-warm, prevents EDEADLK

BANNER = """
╔══════════════════════════════════════════════════════════════════╗
║  ALGO TRADING SYSTEM  v14.0                                     ║
║                                                                  ║
║  Scanner:    Kraken + Binance + Hyperliquid | 7-filter | top 50 ║
║  Signals:    Two-tower (technical 0-100 + ML 0-100)             ║
║  Exits:      7-priority stack (trailing / scale / thesis)       ║
║  Learning:   57-feature snapshots | integrity tiers | retrain   ║
╚══════════════════════════════════════════════════════════════════╝
"""


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["paper", "live"], default=None)
    p.add_argument("--equity-only", action="store_true")
    p.add_argument("--crypto-only", action="store_true")
    p.add_argument("--no-alerts", action="store_true")
    return p.parse_args()


def main():
    print(BANNER)
    args = parse_args()
    if args.mode:
        os.environ["PAPER_TRADING"] = "true" if args.mode == "paper" else "false"

    from config import (
        PAPER_TRADING,
        ACCOUNT_SIZE,
        MARKET_TIMEZONE,
        ANTHROPIC_API_KEY,
        MAX_RISK_PER_TRADE_PCT,
        MAX_DAILY_LOSS_PCT,
        MAX_DEPLOYED_PCT,
        FORECAST_LANE_ACTIVE,
    )

    tz = pytz.timezone(MARKET_TIMEZONE)
    mode = "📄 PAPER" if PAPER_TRADING else "💰 LIVE"

    # Sanity-check hardcoded risk values — catch accidental misconfiguration
    assert 0 < MAX_RISK_PER_TRADE_PCT <= 0.10, (
        f"MAX_RISK_PER_TRADE_PCT={MAX_RISK_PER_TRADE_PCT} out of safe range (0–10%)"
    )
    _daily_loss_cap = 1.00 if PAPER_TRADING else 0.15  # paper: no learning-halt cap
    assert 0 < MAX_DAILY_LOSS_PCT <= _daily_loss_cap, (
        f"MAX_DAILY_LOSS_PCT={MAX_DAILY_LOSS_PCT} out of safe range"
    )
    assert 0 < MAX_DEPLOYED_PCT <= 1.00, (
        f"MAX_DEPLOYED_PCT={MAX_DEPLOYED_PCT} out of safe range (0–100%)"
    )

    print(f"  Mode:       {mode} TRADING")
    print(f"  Account:    ${ACCOUNT_SIZE}")
    print(
        f"  AI (exits): {'✅ Enabled' if ANTHROPIC_API_KEY else '⚠️ No API key — extended-thinking exits disabled'}"
    )
    print(f"  Time:       {datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S ET')}\n")

    if not PAPER_TRADING:
        print("=" * 60)
        print("  ⚠️  LIVE TRADING — Real money will be deployed")
        print(f"  Account: ${ACCOUNT_SIZE}")
        print("=" * 60)
        resp = input("\n  Type 'I UNDERSTAND' to confirm: ").strip()
        if resp != "I UNDERSTAND":
            print("Cancelled.")
            sys.exit(0)

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
    from runtime.position_reconciler import run_reconciliation
    from config import FUTURES_LANE_ACTIVE as _FLA

    _db_path = os.path.join(_MAIN_ROOT, "logs", "trades.db")
    init_runtime_tables(_db_path)
    init_incident_table(_db_path)

    _rt_mode = "live" if not PAPER_TRADING else "paper"
    upsert_system_state(
        db_path=_db_path,
        process_mode=_rt_mode,
        startup_ts=datetime.now(pytz.utc).isoformat(),
        process_alive=1,
        global_status="OK",
        launch_readiness_state="NOT_READY",
        active_lanes="[]",
    )
    # crypto lane — always active
    upsert_lane_state(
        "crypto",
        db_path=_db_path,
        enabled=1,
        active=1,
        configured=1,
        mode=_rt_mode,
        health="OK",
        readiness_state="OPERATIONAL",
    )
    # forecast lane
    upsert_lane_state(
        "forecast",
        db_path=_db_path,
        enabled=int(FORECAST_LANE_ACTIVE),
        active=int(FORECAST_LANE_ACTIVE),
        configured=1,
        mode=_rt_mode if FORECAST_LANE_ACTIVE else "disabled",
        health="UNKNOWN",
        readiness_state="LANE_NOT_STARTED"
        if not FORECAST_LANE_ACTIVE
        else "BROKER_DISCONNECTED",
    )
    # mes archived lane
    upsert_lane_state(
        "mes_archived",
        db_path=_db_path,
        enabled=int(_FLA),
        active=0,
        configured=int(_FLA),
        mode="archived",
        health="OK",
        readiness_state="DORMANT",
        blocked_reason="" if _FLA else "FUTURES_LANE_ACTIVE=false",
    )

    # Run position reconciliation
    run_reconciliation(_db_path)

    # Write startup heartbeat immediately so last_global_heartbeat_at is never blank
    from runtime.runtime_state import write_system_heartbeat

    write_system_heartbeat(_db_path)

    print("   ✅ Runtime state tables ready\n")

    from memory.trade_memory import get_memory_stats

    mem = get_memory_stats()
    print(
        f"🧠 Trade memory: {mem.get('total', 0)} experiences | Win rate: {mem.get('win_rate', 0):.1%}\n"
    )

    log_event(
        "INFO",
        "main",
        f"Bot started — {'paper' if PAPER_TRADING else 'live'} mode v15.2",
    )

    # ── Forecast lane (optional daemon thread) ────────────────────────────────
    if FORECAST_LANE_ACTIVE:

        def _forecast_daemon():
            """Run forecast lane in its own schedule instance (thread-safe)."""
            import schedule as _s
            from forecast.db import init_forecast_db
            from forecast.runner import (
                run_discovery_cycle,
                run_strategy_cycle,
                run_position_monitor,
                _get_broker,
                _get_harvester,
            )

            try:
                init_forecast_db()
                broker = _get_broker()
                _connected = broker.connect()
                if not _connected:
                    time.sleep(4)
                    _connected = broker.is_connected()
                try:
                    from runtime.runtime_state import upsert_lane_state as _uls

                    _uls(
                        "forecast",
                        db_path=_db_path,
                        connected=int(_connected),
                        readiness_state="BROKER_DISCONNECTED"
                        if not _connected
                        else "NO_UNDERLIERS",
                    )
                except Exception:
                    pass
                harvester = _get_harvester()
                harvester.start()
                run_discovery_cycle()
                _s.every(30).minutes.do(run_discovery_cycle)
                _s.every(5).minutes.do(lambda: run_strategy_cycle(100.0))
                _s.every(30).seconds.do(run_position_monitor)
                log_event(
                    "INFO",
                    "ForecastRunner",
                    "Forecast lane started — FORECAST_LANE_ACTIVE=true",
                )
                while True:
                    _s.run_pending()
                    time.sleep(1)
            except Exception as _fe:
                log_event("ERROR", "ForecastRunner", f"Forecast lane crashed: {_fe}")

        _ft = threading.Thread(
            target=_forecast_daemon, daemon=True, name="ForecastLane"
        )
        _ft.start()
        upsert_lane_state(
            "forecast",
            db_path=_db_path,
            active=1,
            readiness_state="BROKER_DISCONNECTED",
        )
        log_event(
            "INFO",
            "ForecastRunner",
            "Forecast lane started (FORECAST_LANE_ACTIVE=true)",
        )
        print("   ForecastEx lane started (FORECAST_LANE_ACTIVE=true)")

    # Populate active_lanes now that all lane startup is done
    _active = ["crypto"]
    if FORECAST_LANE_ACTIVE:
        _active.append("forecast")
    upsert_system_state(
        db_path=_db_path,
        active_lanes=json.dumps(_active),
        launch_readiness_state="READY",
    )

    print("=" * 60)
    print("  Scheduler starting. System is live.")
    print("  Dashboard: streamlit run dashboard/app.py --server.runOnSave true")
    print("  Database:  logs/trades.db")
    print("  Stop:      Ctrl+C")
    print("=" * 60 + "\n")

    from scheduler.v10_runner import run_forever

    run_forever()


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
