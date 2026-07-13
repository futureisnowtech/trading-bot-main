#!/usr/bin/env python3
"""
SPEC §4.4: Backfill NOAA daily summaries for all registered stations.
Fetches from the Open-Meteo Archive API (unauthenticated, public domain station archive)
and stores in the noaa_daily_summaries table in logs/trades.db.
"""
import os
import sys
import json
import sqlite3
import urllib.request
import urllib.parse
import time
from datetime import datetime, timedelta, timezone

# Add parent dir to path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DB_PATH
from forecast.db import init_forecast_db
from data.kalshi_weather_monitor import STATIONS

def backfill_station_history(days_back: int = 120, db_path: str = DB_PATH) -> int:
    init_forecast_db(db_path=db_path)
    
    end_date = datetime.now(timezone.utc) - timedelta(days=1)
    start_date = end_date - timedelta(days=days_back)
    
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")
    
    print(f"Backfilling station history from {start_str} to {end_str}...")
    
    total_inserted = 0
    
    for city_key, loc in STATIONS.items():
        station = loc["icao"] # e.g. KNYC
        lat = loc["lat"]
        lon = loc["lon"]
        
        print(f"Fetching history for {station} ({city_key}) at {lat}, {lon}...")
        
        # Build Open-Meteo Archive API URL
        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": start_str,
            "end_date": end_str,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
            "temperature_unit": "fahrenheit",
            "precipitation_unit": "inch",
            "timezone": "UTC"
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
                
                rows_to_insert = []
                for t, tmax, tmin, prcp in zip(times, tmaxs, tmins, prcps):
                    if t and tmax is not None and tmin is not None:
                        rows_to_insert.append((
                            station,
                            t,
                            float(tmax),
                            float(tmin),
                            float(prcp or 0.0)
                        ))
                        
                if rows_to_insert:
                    with sqlite3.connect(db_path) as conn:
                        conn.executemany(
                            """
                            INSERT OR REPLACE INTO noaa_daily_summaries
                            (station, date, temp_max, temp_min, precipitation)
                            VALUES (?, ?, ?, ?, ?)
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
        
        time.sleep(0.3) # Rate limit friendly
        
    print(f"Backfill completed. Total records inserted: {total_inserted}")
    return total_inserted

if __name__ == "__main__":
    backfill_station_history()
