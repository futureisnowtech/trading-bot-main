"""
data/kalshi_weather_monitor.py — Asynchronous Weather Ensemble Pipeline.

Ingests 31-member GFS ensembles from Open-Meteo to calculate probabilistic edges
for Kalshi weather prediction markets. Decouples heavy network ops from the 
live execution loop via a low-latency shadow state dictionary.
"""

import asyncio
import logging
import time
import requests
import numpy as np
from typing import Dict, Any

logger = logging.getLogger("weather_monitor")

# ── Shadow State ──────────────────────────────────────────────────────────────
# O(1) read access for the strategy engine
_WEATHER_SHADOW_STATE: Dict[str, Any] = {}

# Kalshi Station Mappings (Lat/Lon)
# Expanded v19.1.6 to cover horizontal expansion cities
STATIONS = {
    "NY": {"lat": 40.71, "lon": -74.01, "name": "New York", "series": ["KXHIGHNY", "KXLOWNY", "KXRAINNY"]},
    "CHI": {"lat": 41.88, "lon": -87.63, "name": "Chicago", "series": ["KXHIGHCHI", "KXLOWCHI", "KXRAINCHI"]},
    "MIA": {"lat": 25.76, "lon": -80.19, "name": "Miami", "series": ["KXHIGHMIA", "KXLOWMIA", "KXRAINMIA"]},
    "LAX": {"lat": 34.05, "lon": -118.24, "name": "Los Angeles", "series": ["KXHIGHLAX", "KXLOWLAX", "KXRAINLAX"]},
    "DEN": {"lat": 39.74, "lon": -104.99, "name": "Denver", "series": ["KXHIGHDEN", "KXLOWDEN", "KXRAINDEN"]},
    "AUS": {"lat": 30.27, "lon": -97.74, "name": "Austin", "series": ["KXHIGHAUS", "KXLOWAUS", "KXRAINAUS"]},
    "PHX": {"lat": 33.45, "lon": -112.07, "name": "Phoenix", "series": ["KXHIGHTPHX", "KXLOWTPHX"]},
    "SEA": {"lat": 47.61, "lon": -122.33, "name": "Seattle", "series": ["KXHIGHSEA", "KXLOWSEA", "KXRAINSEA"]},
    "DAL": {"lat": 32.78, "lon": -96.80, "name": "Dallas", "series": ["KXHIGHDAL", "KXLOWDAL"]},
    "ATL": {"lat": 33.75, "lon": -84.39, "name": "Atlanta", "series": ["KXHIGHTATL", "KXLOWTATL"]},
    "HOU": {"lat": 29.76, "lon": -95.37, "name": "Houston", "series": ["KXHIGHTHOU", "KXLOWTHOU"]},
    "BOS": {"lat": 42.36, "lon": -71.06, "name": "Boston", "series": ["KXHIGHBOS", "KXLOWBOS"]},
    "DC": {"lat": 38.91, "lon": -77.04, "name": "Washington DC", "series": ["KXHIGHDC", "KXLOWDC", "KXRAINDC"]},
    "SF": {"lat": 37.77, "lon": -122.42, "name": "San Francisco", "series": ["KXHIGHSF", "KXLOWSF", "KXRAINSF"]},
    "LV": {"lat": 36.17, "lon": -115.14, "name": "Las Vegas", "series": ["KXHIGHTLV", "KXLOWTLV"]},
}

# ── Cache ───────────────────────────────────────────────────────────────────
_COORDINATE_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_EXPIRY_SEC = 21600  # 6 hours (weather ensembles are slow-moving)

async def fetch_open_meteo_ensemble(city_key: str, lat: float, lon: float) -> Dict[str, Any]:
    """
    Fetch 31-member GFS ensemble for a specific coordinate.
    Includes cloud_cover for TCDC overrides, max/min temps, and precip.
    """
    # v19.1.6: Coordinate-based caching to avoid hammering API
    cache_key = f"{lat:.2f}_{lon:.2f}"
    now = time.time()
    if cache_key in _COORDINATE_CACHE:
        cached = _COORDINATE_CACHE[cache_key]
        if now - cached["timestamp"] < CACHE_EXPIRY_SEC:
            return cached

    # v19.1.6: Immediate failure on 429 to avoid retry loops
    try:
        import os
        api_key = os.getenv("OPEN_METEO_API_KEY")
        url = "https://customer-api.open-meteo.com/v1/ensemble" if api_key else "https://ensemble-api.open-meteo.com/v1/ensemble"
        
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,cloud_cover,precipitation",
            "models": "gfs_seamless",
            "timezone": "auto"
        }
        if api_key:
            params["apikey"] = api_key
        
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, lambda: requests.get(url, params=params, timeout=10))
        
        if resp.status_code == 429:
            logger.warning(f"Open-Meteo 429 (Rate Limit) for {city_key}. Aborting cycle to cool down.")
            return {}

        if resp.status_code != 200:
            logger.error(f"Open-Meteo error {resp.status_code} for {city_key}")
            return {}

        data = resp.json()
        hourly = data.get("hourly", {})
        
        # Guardrail 2: The Midnight Boundary Isolation Loop
        window_size = 26 
        
        members_high = []
        members_low = []
        members_precip = []
        cloud_members = []
        
        for i in range(31):
            temp_key = f"temperature_2m_member{i:02d}"
            cloud_key = f"cloud_cover_member{i:02d}"
            precip_key = f"precipitation_member{i:02d}"
            
            if temp_key in hourly:
                temps_c = hourly[temp_key][:window_size]
                if temps_c:
                    temps_f = [(tc * 9/5) + 32 for tc in temps_c]
                    members_high.append(float(max(temps_f)))
                    members_low.append(float(min(temps_f)))
            
            if precip_key in hourly:
                precip_mm = hourly[precip_key][:window_size]
                if precip_mm:
                    # Convert mm to inches
                    total_precip_in = sum(precip_mm) * 0.0393701
                    members_precip.append(float(total_precip_in))
                
            if cloud_key in hourly:
                # Guardrail 1: The Convective Cloud Cover Override (The "Sun Spike")
                clouds = hourly[cloud_key][11:17]
                if clouds:
                    cloud_members.append(float(np.mean(clouds)))
        
        if not members_high:
            return {}

        peak_tcdc = float(np.mean(cloud_members)) if cloud_members else 0.0

        result = {
            "members_high": members_high,
            "members_low": members_low,
            "members_precip": members_precip,
            "mean_high": float(np.mean(members_high)),
            "std_high": float(np.std(members_high)),
            "mean_low": float(np.mean(members_low)),
            "std_low": float(np.std(members_low)),
            "peak_tcdc": peak_tcdc,
            "timestamp": now
        }
        _COORDINATE_CACHE[cache_key] = result
        return result
    except Exception as e:
        logger.error(f"Weather fetch failed for {city_key}: {e}")
        return {}

def inject_weather_ensemble(ticker_prefix: str, members: list[float], tcdc: float = 0.0):
    """v19.1.5: Force-inject an ensemble for live verification/testing."""
    global _WEATHER_SHADOW_STATE
    _WEATHER_SHADOW_STATE[ticker_prefix] = {
        "members": members,
        "mean": float(np.mean(members)),
        "std": float(np.std(members)),
        "peak_tcdc": tcdc,
        "timestamp": time.time()
    }
    logger.info(f"VERIFICATION: Injected weather ensemble for {ticker_prefix}")


async def update_weather_shadow_state():
    """Background loop polling weather data every 15 minutes."""
    global _WEATHER_SHADOW_STATE
    logger.info("Weather shadow state pipeline active.")
    
    while True:
        try:
            # v19.1.6: Jumpstart mechanism to bypass IP bans
            import os, json
            jumpstart_path = "/app/logs/weather_jumpstart.json"
            if os.path.exists(jumpstart_path):
                try:
                    with open(jumpstart_path, 'r') as f:
                        data = json.load(f)
                    if data:
                        _WEATHER_SHADOW_STATE.update(data)
                        logger.info(f"Sovereign Jumpstart: Injected weather for {list(data.keys())}")
                    os.remove(jumpstart_path)
                except Exception as je:
                    logger.error(f"Jumpstart failed: {je}")

            new_state = {}
            # v19.1.6: Staggered fetch to avoid 429 detection
            city_keys = list(STATIONS.keys())
            import random
            random.shuffle(city_keys)
            
            for city_key in city_keys:
                loc = STATIONS[city_key]
                result = await fetch_open_meteo_ensemble(city_key, loc["lat"], loc["lon"])
                if result:
                    for s_ticker in loc.get("series", []):
                        new_state[s_ticker] = result
                
                # Jittered delay between cities (10-15s)
                await asyncio.sleep(random.uniform(10, 15))
            
            if new_state:
                _WEATHER_SHADOW_STATE.update(new_state)
                logger.info(f"Weather state synced: {list(new_state.keys())}")
        except Exception as e:
            logger.error(f"Weather pipeline sync failure: {e}")
        
        # v19.1.6: Synchronize with meteorological updates (3 hours)
        # GFS updates every 6h; polling every 15m was wasteful and caused 429s.
        await asyncio.sleep(10800)

def get_weather_data(ticker_prefix: str) -> Dict[str, Any]:
    """Retrieve cached weather data for a ticker prefix (e.g. 'KXHIGHNY')."""
    # v19.1.6: Direct lookup now that shadow state is keyed by series ticker
    data = _WEATHER_SHADOW_STATE.get(ticker_prefix)
    if data:
        # v19.1.6: Increase cache expiry to 6h to match polling cadence
        if time.time() - data["timestamp"] > 21600:
            return {}
        return data
    
    # Fallback pattern matching
    for series_list in [loc.get("series", []) for loc in STATIONS.values()]:
        for s in series_list:
            if ticker_prefix.startswith(s):
                data = _WEATHER_SHADOW_STATE.get(s)
                # v19.1.6: Increase cache expiry to 6h
                if data and time.time() - data["timestamp"] <= 21600:
                    return data
    return {}

def start_weather_monitor():
    """Start the weather daemon in a background thread."""
    import threading
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(update_weather_shadow_state())
    
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
