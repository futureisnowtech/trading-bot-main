"""
forecast_main.py — Isolated orchestrator for the Kalshi Forecast lane.
v18.34.FORENSIC: Sovereign Separation Phase
"""

import sys, os, time, traceback, logging, threading, json
from datetime import datetime
import pytz

# Ensure project root is on sys.path
_MAIN_ROOT = os.path.dirname(os.path.abspath(__file__))
if _MAIN_ROOT not in sys.path:
    sys.path.insert(0, _MAIN_ROOT)

def _setup_logging():
    _log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "forecast.log")
    fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.FileHandler(_log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    for noisy in ("urllib3", "requests", "schedule"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

_setup_logging()

VERSION = "v18.34.FORENSIC"

BANNER = f"""
╔══════════════════════════════════════════════════════════════════╗
║  FORECAST SCHEDULER  {VERSION}                            ║
║  Isolated Kalshi Macro Bridge: Forensic Traceability             ║
╚══════════════════════════════════════════════════════════════════╝
"""

def main():
    print(BANNER)
    import system_state
    from config import MARKET_TIMEZONE, ACCOUNT_SIZE, DB_PATH
    
    _db_path = DB_PATH
    system_state.state.set_mode("LIVE")
    
    # 📊 Metrics (Port 8001 for Forecast Isolation)
    from monitoring.metrics import start_metrics_server
    start_metrics_server(port=8001)

    from logging_db.trade_logger import log_event
    from runtime.runtime_state import upsert_lane_state

    # ── Forecast Execution Loop ──────────────────────────────────────────────
    import schedule as _sched_lib
    _s = _sched_lib.Scheduler()
    
    from forecast.db import init_forecast_db
    from data.kalshi_weather_monitor import start_weather_monitor
    from forecast.runner import (
        run_discovery_cycle,
        run_strategy_cycle,
        run_position_monitor,
        _get_broker,
        _get_harvester,
    )

    try:
        init_forecast_db()
        # v18.35: Start Weather Ensemble Pipeline
        start_weather_monitor()
        
        broker = _get_broker()
        _connected = broker.connect()
        
        upsert_lane_state(
            "forecast",
            db_path=_db_path,
            connected=int(_connected),
            active=1,
            readiness_state="BROKER_DISCONNECTED" if not _connected else "OK",
        )

        harvester = _get_harvester()
        harvester.start()
        
        # Initial run
        run_discovery_cycle()
        
        # Schedule
        _s.every(30).minutes.do(run_discovery_cycle)
        _s.every(5).minutes.do(lambda: run_strategy_cycle(100.0))
        _s.every(30).seconds.do(run_position_monitor)
        
        log_event("INFO", "ForecastMain", f"Forecast lane started on port 8001")
        print("   ForecastEx lane active. Monitoring Port 8001.")

        while True:
            _s.run_pending()
            time.sleep(1)
            
    except Exception as e:
        log_event("ERROR", "ForecastMain", f"Forecast lane crashed: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nShutdown complete.")
        sys.exit(0)
    except Exception as e:
        print(f"\n💥 Fatal error: {e}")
        traceback.print_exc()
        sys.exit(1)
