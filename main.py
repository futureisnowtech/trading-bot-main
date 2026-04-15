"""
main.py — Entry point for the Algo Trading System.
Usage:
  python main.py              # Full system (reads PAPER_TRADING from .env)
  python main.py --mode paper # Force paper trading
  python main.py --mode live  # Force live (requires typed confirmation)
  python main.py --crypto-only
  python main.py --equity-only
"""

import sys, os, argparse, time, traceback, logging, threading
from datetime import datetime
import pytz


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

    from memory.trade_memory import get_memory_stats

    mem = get_memory_stats()
    print(
        f"🧠 Trade memory: {mem.get('total', 0)} experiences | Win rate: {mem.get('win_rate', 0):.1%}\n"
    )

    log_event(
        "INFO", "main",
        f"Bot started — {'paper' if PAPER_TRADING else 'live'} mode | v15.1"
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
                broker.connect()
                harvester = _get_harvester()
                harvester.start()
                run_discovery_cycle()
                _s.every(30).minutes.do(run_discovery_cycle)
                _s.every(5).minutes.do(lambda: run_strategy_cycle(100.0))
                _s.every(30).seconds.do(run_position_monitor)
                log_event("INFO", "ForecastRunner", "Forecast lane started — FORECAST_LANE_ACTIVE=true")
                while True:
                    _s.run_pending()
                    time.sleep(1)
            except Exception as _fe:
                log_event("ERROR", "ForecastRunner", f"Forecast lane crashed: {_fe}")

        _ft = threading.Thread(target=_forecast_daemon, daemon=True, name="ForecastLane")
        _ft.start()
        print("   ForecastEx lane started (FORECAST_LANE_ACTIVE=true)")

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
