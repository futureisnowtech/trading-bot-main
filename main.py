"""
main.py — Entry point for the Algo Trading System.
Usage:
  python main.py              # Full system (reads False from .env)
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

VERSION = "v18.19.4"

BANNER = """
╔══════════════════════════════════════════════════════════════════╗
║  ALGO TRADING SYSTEM  v18.19.4                                    ║
║                                                                  ║
║  Lane:       Coinbase spot scalp (tiny live)                    ║
║  Route:      maker_first | CHOP enabled | pullback enabled      ║
║  Truth:      broker-first via runtime/spot_position_truth.py    ║
║  Launch:     python3 scripts/go_live.py (only sanctioned path)  ║
╚══════════════════════════════════════════════════════════════════╝
"""


def parse_args():
    p = argparse.ArgumentParser()
    # v18.17: System is strictly LIVE. --mode removed.
    p.add_argument("--equity-only", action="store_true")
    p.add_argument("--crypto-only", action="store_true")
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
        MAX_RISK_PER_TRADE_PCT,
        MAX_DAILY_LOSS_PCT,
        MAX_DEPLOYED_PCT,
        FORECAST_LANE_ACTIVE,
        STOCKS_LANE_ACTIVE,
        STOCKS_AUTONOMOUS_ENABLED,
        STOCKS_MANUAL_ENABLED,
        FORECAST_DASHBOARD_VISIBLE,
        FORECAST_AUTONOMOUS_ENABLED,
        FORECAST_MANUAL_ENABLED,
        FUTURES_DASHBOARD_VISIBLE,
        STOCKS_DASHBOARD_VISIBLE,
    )

    system_state.state.set_mode("LIVE")

    tz = pytz.timezone(MARKET_TIMEZONE)
    mode = "💰 LIVE"
    try:
        from runtime.live_account import get_live_account_size

        account_display = float(get_live_account_size())
    except Exception:
        account_display = float(ACCOUNT_SIZE)

    # Sanity-check hardcoded risk values — catch accidental misconfiguration
    assert 0 < MAX_RISK_PER_TRADE_PCT <= 0.10, (
        f"MAX_RISK_PER_TRADE_PCT={MAX_RISK_PER_TRADE_PCT} out of safe range (0–10%)"
    )
    _daily_loss_cap = 0.15  # live: 15% safety cap for accidental loss
    assert 0 < MAX_DAILY_LOSS_PCT <= _daily_loss_cap, (
        f"MAX_DAILY_LOSS_PCT={MAX_DAILY_LOSS_PCT} out of safe range"
    )
    assert 0 < MAX_DEPLOYED_PCT <= 1.00, (
        f"MAX_DEPLOYED_PCT={MAX_DEPLOYED_PCT} out of safe range (0–100%)"
    )

    print(f"  Mode:       {mode} TRADING")
    print(f"  Account:    ${account_display}")
    print(
        f"  AI (exits): {'✅ Enabled' if ANTHROPIC_API_KEY else '⚠️ No API key — extended-thinking exits disabled'}"
    )
    print(f"  Time:       {datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S ET')}\n")

    print("=" * 60)
    print("  ⚠️  LIVE TRADING — Real money will be deployed")
    print(f"  Account: ${account_display}")
    print("=" * 60)
    auto_confirm = os.environ.get("ALGO_LIVE_CONFIRM", "").strip()
    if auto_confirm == "I UNDERSTAND":
        print("  Live launch confirmation received from controlled launcher.\n")
    else:
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

    _rt_mode = "live" if not False else "paper"
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
        lane_role="primary",
        enabled=1,
        active=1,
        configured=1,
        dashboard_visible=1,
        autonomous_enabled=1,
        manual_allowed=1,
        mode=_rt_mode,
        health="UNKNOWN",
        connected=0,
        tradable=0,
        capital_deployed_usd=0.0,
        buying_power_usd=0.0,
        readiness_state="STARTING",
        promotion_condition="Primary live lane — keep crypto active and truth-aligned",
    )
    # forecast lane
    upsert_lane_state(
        "forecast",
        db_path=_db_path,
        lane_role="blocked_ready",
        enabled=int(FORECAST_LANE_ACTIVE),
        active=int(FORECAST_LANE_ACTIVE),
        configured=1,
        dashboard_visible=0,
        autonomous_enabled=int(FORECAST_AUTONOMOUS_ENABLED and FORECAST_LANE_ACTIVE),
        manual_allowed=int(FORECAST_MANUAL_ENABLED),
        mode=_rt_mode if FORECAST_LANE_ACTIVE else "disabled",
        health="UNKNOWN",
        readiness_state="LANE_NOT_STARTED"
        if not FORECAST_LANE_ACTIVE
        else "BROKER_DISCONNECTED",
        blocked_reason="" if FORECAST_LANE_ACTIVE else "FORECAST_LANE_ACTIVE=false",
        promotion_condition="Promote only after enrollment, tradable contracts, and stable heartbeat truth",
    )
    # mes archived lane
    upsert_lane_state(
        "mes_archived",
        db_path=_db_path,
        lane_role="archived",
        enabled=int(_FLA),
        active=0,
        configured=int(_FLA),
        dashboard_visible=0,
        autonomous_enabled=0,
        manual_allowed=0,
        mode="archived",
        health="OK",
        readiness_state="DORMANT",
        blocked_reason="" if _FLA else "FUTURES_LANE_ACTIVE=false",
        promotion_condition="Reactivate only after futures approval, lane validation, and FUTURES_LANE_ACTIVE=true",
    )

    # stocks lane
    upsert_lane_state(
        "stocks",
        db_path=_db_path,
        lane_role="dormant_ready",
        enabled=int(STOCKS_LANE_ACTIVE),
        active=int(STOCKS_LANE_ACTIVE and STOCKS_AUTONOMOUS_ENABLED),
        configured=int(STOCKS_LANE_ACTIVE),
        dashboard_visible=0,
        autonomous_enabled=int(STOCKS_AUTONOMOUS_ENABLED and STOCKS_LANE_ACTIVE),
        manual_allowed=int(STOCKS_MANUAL_ENABLED),
        mode=(
            _rt_mode
            if STOCKS_LANE_ACTIVE and STOCKS_AUTONOMOUS_ENABLED
            else "standby"
            if STOCKS_DASHBOARD_VISIBLE
            else "disabled"
        ),
        health="UNKNOWN",
        readiness_state=(
            "STARTING"
            if STOCKS_LANE_ACTIVE and STOCKS_AUTONOMOUS_ENABLED
            else "DORMANT_READY"
            if STOCKS_DASHBOARD_VISIBLE
            else "LANE_NOT_STARTED"
        ),
        blocked_reason=(
            ""
            if STOCKS_LANE_ACTIVE and STOCKS_AUTONOMOUS_ENABLED
            else "STOCKS_AUTONOMOUS_ENABLED=false"
            if STOCKS_DASHBOARD_VISIBLE
            else "STOCKS_LANE_ACTIVE=false"
        ),
        promotion_condition="Promote only after equity edge and PDT-aware operating rules are proven",
    )

    # Run position reconciliation
    run_reconciliation(_db_path)

    # Write startup heartbeat immediately so last_global_heartbeat_at is never blank
    from runtime.runtime_state import write_system_heartbeat

    write_system_heartbeat(_db_path)

    # 📊 Start Prometheus Metrics Server
    from monitoring.metrics import start_metrics_server

    start_metrics_server(port=8000)

    # 🤖 Start Telegram Bot (Command Suite)
    from notifications.telegram_bot import start_bot_thread
    from monitoring.log_alerter import start_log_alerter

    start_bot_thread()

    try:
        start_log_alerter()
    except Exception as _e:
        logging.getLogger(__name__).warning(f"log_alerter start failed: {_e}")

    # 📡 Start Coinbase WebSocket Feed (Asynchronous Ticker Data + Circuit Breaker)
    from config import COINBASE_CDP_KEY_NAME, COINBASE_CDP_PRIVATE_KEY

    if COINBASE_CDP_KEY_NAME and COINBASE_CDP_PRIVATE_KEY:
        from data.coinbase_websocket import start_coinbase_feed
        from config import SPOT_SYMBOLS

        # Coinbase spot products: base-USD (reconciles with execution/coinbase_spot_broker.py)
        products = [f"{s}-USD" for s in SPOT_SYMBOLS]
        start_coinbase_feed(COINBASE_CDP_KEY_NAME, COINBASE_CDP_PRIVATE_KEY, products)
        print(f"   ✅ Coinbase WebSocket feed started for {len(products)} spot pairs\n")

    print("   ✅ Runtime state tables ready\n")

    from memory.trade_memory import get_memory_stats

    mem = get_memory_stats()
    print(
        f"🧠 Trade memory: {mem.get('total', 0)} experiences | Win rate: {mem.get('win_rate', 0):.1%}\n"
    )

    log_event(
        "INFO",
        "main",
        f"Bot started — {'paper' if False else 'live'} mode {VERSION}",
    )

    # ── Forecast lane (optional daemon thread) ────────────────────────────────
    if FORECAST_LANE_ACTIVE:

        def _forecast_daemon():
            """Run forecast lane in its own schedule instance (thread-safe)."""
            import schedule as _sched_lib

            _s = (
                _sched_lib.Scheduler()
            )  # isolated instance — never touches the global default used by v10_runner
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

    # ── Stocks lane (optional daemon thread) ──────────────────────────────────
    if STOCKS_LANE_ACTIVE and STOCKS_AUTONOMOUS_ENABLED:

        def _stocks_daemon():
            """Run stock lane in its own schedule instance (thread-safe)."""
            from scheduler.stock_runner import run_forever as _stocks_run_forever

            try:
                _stocks_run_forever()
            except Exception as _se:
                log_event("ERROR", "StockRunner", f"Stocks lane crashed: {_se}")

        _st = threading.Thread(target=_stocks_daemon, daemon=True, name="StocksLane")
        _st.start()
        upsert_lane_state(
            "stocks", db_path=_db_path, active=1, readiness_state="STARTING"
        )
        log_event(
            "INFO", "StockRunner", "Stocks lane started (STOCKS_LANE_ACTIVE=true)"
        )
        print("   Stocks lane started (STOCKS_LANE_ACTIVE=true)")

    # Populate active_lanes now that all lane startup is done
    upsert_system_state(
        db_path=_db_path,
        active_lanes=json.dumps(["crypto"]),
        launch_readiness_state="NOT_READY",
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
