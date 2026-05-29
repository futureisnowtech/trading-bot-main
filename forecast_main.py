"""
forecast_main.py — Isolated orchestrator for the Kalshi Forecast lane.
v19.1.6: Refactored to use Sovereign start_forecast_lane for optimized execution.
"""

import sys, os, time, traceback, logging, threading
from datetime import datetime

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

VERSION = "v19.1.6"

BANNER = f"""
╔══════════════════════════════════════════════════════════════════╗
║  FORECAST SCHEDULER  {VERSION} (SOVEREIGN)                 ║
║  Isolated Kalshi Macro Bridge: Forensic Traceability             ║
╚══════════════════════════════════════════════════════════════════╝
"""

def main():
    print(BANNER)
    import system_state
    from config import ACCOUNT_SIZE
    
    system_state.state.set_mode("LIVE")
    
    # 📊 Metrics (Port 8001 for Forecast Isolation)
    from monitoring.metrics import start_metrics_server
    start_metrics_server(port=8001)

    # v19.1.6: Start Weather Ensemble Pipeline
    from data.kalshi_weather_monitor import start_weather_monitor
    start_weather_monitor()

    # v19.1.6: Use optimized Sovereign runner
    from forecast.runner import start_forecast_lane
    import schedule

    # We don't block here; start_forecast_lane registers jobs in the default 'schedule' instance
    start_forecast_lane(bankroll=float(ACCOUNT_SIZE))
    
    print("   ForecastEx lane active. Monitoring Port 8001.")

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutdown complete.")
        sys.exit(0)
    except Exception as e:
        from logging_db.trade_logger import log_event
        log_event("ERROR", "ForecastMain", f"Forecast lane crashed: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
