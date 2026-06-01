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
# Refined v19.1.10: Official ASOS Settlement Stations
STATIONS = {
    "NY": {"lat": 40.78, "lon": -73.97, "icao": "KNYC", "name": "New York (Central Park)", "tz": "America/New_York", "series": ["KXHIGHNY", "KXLOWNY", "KXRAINNY"]},
    "CHI": {"lat": 41.78, "lon": -87.75, "icao": "KMDW", "name": "Chicago (Midway)", "tz": "America/Chicago", "series": ["KXHIGHCHI", "KXLOWCHI", "KXRAINCHI"]},
    "MIA": {"lat": 25.79, "lon": -80.29, "icao": "KMIA", "name": "Miami International", "tz": "America/New_York", "series": ["KXHIGHMIA", "KXLOWMIA", "KXRAINMIA"]},
    "LAX": {"lat": 33.94, "lon": -118.41, "icao": "KLAX", "name": "Los Angeles Intl", "tz": "America/Los_Angeles", "series": ["KXHIGHLAX", "KXLOWLAX", "KXRAINLAX"]},
    "DEN": {"lat": 39.86, "lon": -104.67, "icao": "KDEN", "name": "Denver International", "tz": "America/Denver", "series": ["KXHIGHDEN", "KXLOWDEN", "KXRAINDEN"]},
    "AUS": {"lat": 30.20, "lon": -97.67, "icao": "KAUS", "name": "Austin-Bergstrom", "tz": "America/Chicago", "series": ["KXHIGHAUS", "KXLOWAUS", "KXRAINAUS"]},
    "PHX": {"lat": 33.43, "lon": -112.01, "icao": "KPHX", "name": "Phoenix Sky Harbor", "tz": "America/Phoenix", "series": ["KXHIGHTPHX", "KXLOWTPHX"]},
    "SEA": {"lat": 47.45, "lon": -122.31, "icao": "KSEA", "name": "Seattle-Tacoma", "tz": "America/Los_Angeles", "series": ["KXHIGHSEA", "KXLOWSEA", "KXRAINSEA"]},
    "DAL": {"lat": 32.90, "lon": -97.04, "icao": "KDFW", "name": "Dallas/Fort Worth", "tz": "America/Chicago", "series": ["KXHIGHDAL", "KXLOWDAL"]},
    "ATL": {"lat": 33.64, "lon": -84.43, "icao": "KATL", "name": "Hartsfield-Jackson", "tz": "America/New_York", "series": ["KXHIGHTATL", "KXLOWTATL"]},
    "HOU": {"lat": 29.65, "lon": -95.28, "icao": "KHOU", "name": "Houston Hobby", "tz": "America/Chicago", "series": ["KXHIGHTHOU", "KXLOWTHOU"]},
    "BOS": {"lat": 42.36, "lon": -71.01, "icao": "KBOS", "name": "Boston Logan", "tz": "America/New_York", "series": ["KXHIGHBOS", "KXLOWBOS"]},
    "DC": {"lat": 38.85, "lon": -77.04, "icao": "KDCA", "name": "Reagan National", "tz": "America/New_York", "series": ["KXHIGHDC", "KXLOWDC", "KXRAINDC"]},
    "SF": {"lat": 37.62, "lon": -122.37, "icao": "KSFO", "name": "San Francisco Intl", "tz": "America/Los_Angeles", "series": ["KXHIGHSF", "KXLOWSF", "KXRAINSF"]},
    "LV": {"lat": 36.08, "lon": -115.15, "icao": "KLAS", "name": "Las Vegas (Harry Reid)", "tz": "America/Los_Angeles", "series": ["KXHIGHTLV", "KXLOWTLV"]},
}

# ── Intraday Ground Truth ───────────────────────────────────────────────────

def _parse_t_group(metar_raw: str) -> Optional[float]:
    """Parse the T-group from METAR remarks for 0.1C precision."""
    import re
    # Pattern: T followed by 8 digits. First 4 are temp, last 4 are dew point.
    # T snnn snnn where s is sign (0=pos, 1=neg) and nnn is tenths of Celsius.
    match = re.search(r' T([01])(\d{3})', metar_raw)
    if match:
        sign = 1 if match.group(1) == '0' else -1
        val = int(match.group(2)) / 10.0
        temp_c = sign * val
        temp_f = (temp_c * 9/5) + 32
        return round(temp_f, 2)
    return None

async def fetch_metar_observation(icao: str) -> Dict[str, Any]:
    """Fetch real-time METAR ground truth from NOAA ADDS."""
    url = f"https://aviationweather.gov/cgi-bin/data/metar.php?ids={icao}&format=raw"
    try:
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, lambda: requests.get(url, timeout=10))
        if resp.status_code == 200 and resp.text:
            raw = resp.text.strip()
            # Basic temp parsing as fallback
            # (e.g. 15/M02)
            import re
            temp_match = re.search(r' (M?\d{2})/(M?\d{2}) ', raw)
            temp_f = None
            if temp_match:
                tc_raw = temp_match.group(1).replace('M', '-')
                temp_c = float(tc_raw)
                temp_f = round((temp_c * 9/5) + 32, 1)
            
            # High-precision T-group override
            t_group_f = _parse_t_group(raw)
            if t_group_f is not None:
                temp_f = t_group_f

            return {
                "icao": icao,
                "temp_f": temp_f,
                "raw": raw,
                "timestamp": time.time()
            }
    except Exception as e:
        logger.debug(f"METAR fetch failed for {icao}: {e}")
    return {}

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


async def fetch_hrrr_forecast(city_key: str, lat: float, lon: float) -> Dict[str, Any]:
    """Fetch hourly HRRR 3km high-resolution forecast for intraday shifts."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,cloud_cover,precipitation",
        "models": "ncep_hrrr_conus",
        "timezone": "auto",
        "forecast_days": 1
    }
    try:
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, lambda: requests.get(url, params=params, timeout=10))
        if resp.status_code == 200:
            data = resp.json()
            hourly = data.get("hourly", {})
            if "temperature_2m" in hourly:
                temps_f = [(tc * 9/5) + 32 for tc in hourly["temperature_2m"][:12]] # Next 12h
                return {
                    "hrrr_high": max(temps_f),
                    "hrrr_trend": "rising" if temps_f[-1] > temps_f[0] else "falling",
                    "hrrr_timestamp": time.time()
                }
    except Exception as e:
        logger.debug(f"HRRR fetch failed for {city_key}: {e}")
    return {}

async def update_weather_shadow_state():
    """Background loop polling weather data (Ensembles + Intraday METAR/HRRR)."""
    global _WEATHER_SHADOW_STATE
    logger.info("Weather shadow state pipeline active.")
    
    # ── Cycle 1: Heavy Ensemble Loop (3 Hours) ─────────────────────────────
    async def run_ensemble_sync():
        while True:
            try:
                new_state = {}
                import random
                city_keys = list(STATIONS.keys())
                random.shuffle(city_keys)
                
                for city_key in city_keys:
                    loc = STATIONS[city_key]
                    result = await fetch_open_meteo_ensemble(city_key, loc["lat"], loc["lon"])
                    if result:
                        for s_ticker in loc.get("series", []):
                            # Initialize or preserve intraday data
                            existing = _WEATHER_SHADOW_STATE.get(s_ticker, {})
                            result["intraday"] = existing.get("intraday", {})
                            new_state[s_ticker] = result
                    await asyncio.sleep(random.uniform(2, 5))
                
                if new_state:
                    _WEATHER_SHADOW_STATE.update(new_state)
                    logger.info(f"Weather Ensemble synced: {len(new_state)} series")
            except Exception as e:
                logger.error(f"Ensemble sync failure: {e}")
            await asyncio.sleep(10800)

    # ── Cycle 2: Fast Intraday Precinct (15 Minutes) ───────────────────────
    async def run_intraday_sync():
        while True:
            try:
                # v19.1.10: Precision Ground Truth (METAR + HRRR)
                for city_key, loc in STATIONS.items():
                    metar = await fetch_metar_observation(loc["icao"])
                    hrrr = await fetch_hrrr_forecast(city_key, loc["lat"], loc["lon"])
                    
                    intraday_payload = {
                        "metar_temp": metar.get("temp_f"),
                        "metar_raw": metar.get("raw"),
                        "hrrr_high": hrrr.get("hrrr_high"),
                        "hrrr_trend": hrrr.get("hrrr_trend"),
                        "ts": time.time()
                    }
                    
                    for s_ticker in loc.get("series", []):
                        if s_ticker in _WEATHER_SHADOW_STATE:
                            _WEATHER_SHADOW_STATE[s_ticker]["intraday"] = intraday_payload
                
                logger.info("Weather Intraday Precinct synced (METAR/HRRR).")
            except Exception as e:
                logger.error(f"Intraday sync failure: {e}")
            await asyncio.sleep(900)

    # Launch concurrent loops
    await asyncio.gather(run_ensemble_sync(), run_intraday_sync())

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
