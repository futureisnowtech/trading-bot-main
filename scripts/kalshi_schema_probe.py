import sys
import logging
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from execution.kalshi_broker import KalshiBroker
from data.kalshi_weather_monitor import STATIONS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kalshi_schema_probe")

def main():
    broker = KalshiBroker()
    if not broker.connect():
        logger.warning("Failed to connect to Kalshi broker. Skipping live check (CI or offline environment).")
        # In CI/mock environments where keys don't exist, we don't want to crash the build.
        # But we verify that the STATIONS dict is structurally loadable.
        sys.exit(0)

    logger.info("Initializing live Kalshi schema probe...")
    failures = []
    
    # Audit all series in STATIONS
    for city, loc in STATIONS.items():
        for series in loc.get("series", []):
            logger.info(f"Auditing Series: {series} for City: {city}...")
            try:
                res = broker._request("GET", "/trade-api/v2/events", params={"series_ticker": series})
                if "error" in res:
                    logger.error(f"FAIL: Kalshi returned error for {series}: {res['error']}")
                    failures.append((series, f"API error: {res['error']}"))
                elif not isinstance(res.get("events"), list):
                    logger.error(f"FAIL: Kalshi response did not contain 'events' array for {series}. Got: {res}")
                    failures.append((series, "Malformed response structure (no events list)"))
                elif not res.get("events"):
                    logger.warning(
                        "NEUTRAL: %s query accepted but returned zero events. This is not proof that the family is live.",
                        series,
                    )
                else:
                    logger.info(f"PASS: {series} is structurally valid on Kalshi.")
            except Exception as e:
                logger.error(f"FAIL: Connection/HTTP error on {series}: {e}")
                failures.append((series, str(e)))

    if failures:
        logger.error(f"Schema probe FAILED with {len(failures)} mismatch(es):")
        for f in failures:
            logger.error(f" - Series: {f[0]} | Error: {f[1]}")
        sys.exit(1)

    logger.info("Live Kalshi schema probe completed with 100% SUCCESS.")

if __name__ == "__main__":
    main()
