#!/usr/bin/env python3
"""Backfill gridded daily weather history at settlement-station coordinates.

The source is the keyless Open-Meteo historical archive, not NOAA/ASOS station
observations.  Rows live in the legacy-named ``noaa_daily_summaries`` table.
"""
import argparse
import os
import sys
import json
import sqlite3
import urllib.request
import urllib.parse
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Add parent dir to path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DB_PATH
from forecast.db import init_forecast_db
from data.kalshi_weather_monitor import STATIONS

HISTORY_SOURCE = "open_meteo_archive_grid_at_settlement_station_coordinates"


def _history_date_range(
    timezone_name: str,
    days_back: int,
    *,
    now_utc: datetime | None = None,
) -> tuple[str, str]:
    local_now = (now_utc or datetime.now(timezone.utc)).astimezone(
        ZoneInfo(timezone_name)
    )
    end_date = local_now.date() - timedelta(days=1)
    start_date = end_date - timedelta(days=max(1, int(days_back)) - 1)
    return start_date.isoformat(), end_date.isoformat()


def backfill_station_history(days_back: int = 120, db_path: str = DB_PATH) -> int:
    init_forecast_db(db_path=db_path)

    days_back = max(1, int(days_back))
    total_inserted = 0
    failed_stations: list[str] = []

    for city_key, loc in STATIONS.items():
        station = loc["icao"] # e.g. KNYC
        lat = loc["lat"]
        lon = loc["lon"]
        timezone_name = str(loc.get("tz") or "UTC")
        start_str, end_str = _history_date_range(timezone_name, days_back)

        print(
            f"Fetching history for {station} ({city_key}) at {lat}, {lon} "
            f"from {start_str} to {end_str} ({timezone_name})..."
        )

        # Build Open-Meteo Archive API URL
        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": start_str,
            "end_date": end_str,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
            "temperature_unit": "fahrenheit",
            "precipitation_unit": "inch",
            "timezone": timezone_name,
        }

        url = "https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(params)

        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "KalshiWeatherBackfiller/2.0"}
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

                daily = data.get("daily", {})
                times = daily.get("time", [])
                tmaxs = daily.get("temperature_2m_max", [])
                tmins = daily.get("temperature_2m_min", [])
                prcps = daily.get("precipitation_sum", [])

                lengths = {len(times), len(tmaxs), len(tmins), len(prcps)}
                if data.get("error") or not daily or len(lengths) != 1:
                    raise ValueError(
                        str(data.get("reason") or "incomplete historical archive payload")
                    )

                rows_to_insert = []
                for t, tmax, tmin, prcp in zip(times, tmaxs, tmins, prcps):
                    if t and tmax is not None and tmin is not None:
                        rows_to_insert.append((
                            station,
                            t,
                            float(tmax),
                            float(tmin),
                            float(prcp or 0.0),
                            HISTORY_SOURCE,
                        ))

                if not rows_to_insert:
                    raise ValueError("historical archive returned no complete daily rows")
                with sqlite3.connect(db_path) as conn:
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO noaa_daily_summaries
                        (station, date, temp_max, temp_min, precipitation, source)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(station, date) DO UPDATE SET
                            temp_max=excluded.temp_max,
                            temp_min=excluded.temp_min,
                            precipitation=excluded.precipitation,
                            source=excluded.source
                        WHERE noaa_daily_summaries.source = excluded.source
                        """,
                        rows_to_insert
                    )
                    conn.commit()
                print(f"  Inserted {len(rows_to_insert)} days of history for {station}.")
                total_inserted += len(rows_to_insert)
                break
            except Exception as e:
                print(f"  Attempt {attempt + 1} failed for {station}: {e}")
                time.sleep(2.0)
        else:
            failed_stations.append(station)

        time.sleep(0.3) # Rate limit friendly

    print(
        "Backfill completed. "
        f"Total records inserted: {total_inserted}; "
        f"failed stations: {','.join(failed_stations) if failed_stations else 'none'}"
    )
    if failed_stations:
        raise RuntimeError(
            "station history backfill failed for: " + ",".join(failed_stations)
        )
    return total_inserted

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-back", type=int, default=120)
    parser.add_argument("--db-path", default=DB_PATH)
    args = parser.parse_args()
    backfill_station_history(days_back=args.days_back, db_path=args.db_path)
