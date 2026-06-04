"""
data/kalshi_weather_monitor.py — Asynchronous Weather Ensemble Pipeline.

Ingests 31-member GFS ensembles from Open-Meteo to calculate probabilistic edges
for Kalshi weather prediction markets. Decouples heavy network ops from the 
live execution loop via a low-latency shadow state dictionary.
"""

import asyncio
import logging
import threading
import time
from copy import deepcopy
from typing import Any, Dict, Optional

import numpy as np
import requests

logger = logging.getLogger("weather_monitor")

# ── Shadow State ──────────────────────────────────────────────────────────────
# O(1) read access for the strategy engine
_WEATHER_SHADOW_STATE: Dict[str, Any] = {}
WEATHER_STATE_TTL_SEC = 21600
_STATE_LOCK = threading.Lock()
_MONITOR_LOCK = threading.Lock()
_MONITOR_THREAD: Optional[threading.Thread] = None

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
    # v19.3: Institutional Expansion Universe
    "MSP": {"lat": 44.88, "lon": -93.22, "icao": "KMSP", "name": "Minneapolis-St. Paul", "tz": "America/Chicago", "series": ["KXHIGHMSP", "KXLOWMSP"]},
    "DET": {"lat": 42.21, "lon": -83.35, "icao": "KDTW", "name": "Detroit Metro", "tz": "America/New_York", "series": ["KXHIGHDET", "KXLOWDET"]},
    "SLC": {"lat": 40.79, "lon": -111.97, "icao": "KSLC", "name": "Salt Lake City Intl", "tz": "America/Denver", "series": ["KXHIGHSLC", "KXLOWSLC"]},
    "OKC": {"lat": 35.39, "lon": -97.60, "icao": "KOKC", "name": "Oklahoma City", "tz": "America/Chicago", "series": ["KXHIGHOKC", "KXLOWOKC"]},
    "PHL": {"lat": 39.87, "lon": -75.24, "icao": "KPHL", "name": "Philadelphia Intl", "tz": "America/New_York", "series": ["KXHIGHPHL", "KXLOWPHL", "KXRAINPHL"]},
    "MCI": {"lat": 39.30, "lon": -94.71, "icao": "KMCI", "name": "Kansas City Intl", "tz": "America/Chicago", "series": ["KXHIGHMCI", "KXLOWMCI"]},
    "ABQ": {"lat": 35.04, "lon": -106.61, "icao": "KABQ", "name": "Albuquerque Intl", "tz": "America/Denver", "series": ["KXHIGHABQ", "KXLOWABQ"]},
    "MSY": {"lat": 29.99, "lon": -90.26, "icao": "KMSY", "name": "New Orleans (Armstrong)", "tz": "America/Chicago", "series": ["KXHIGHMSY", "KXLOWMSY"]},
    "PDX": {"lat": 45.59, "lon": -122.60, "icao": "KPDX", "name": "Portland Intl", "tz": "America/Los_Angeles", "series": ["KXHIGHPDX", "KXLOWPDX"]},
    "MKE": {"lat": 42.95, "lon": -87.90, "icao": "KMKE", "name": "Milwaukee (Mitchell)", "tz": "America/Chicago", "series": ["KXHIGHMKE", "KXLOWMKE"]},
    "MCO": {"lat": 28.43, "lon": -81.33, "icao": "KMCO", "name": "Orlando Intl", "tz": "America/New_York", "series": ["KXHIGHMCO", "KXLOWMCO", "KXRAINMCO"]},
    "STL": {"lat": 38.75, "lon": -90.37, "icao": "KSTL", "name": "St. Louis (Lambert)", "tz": "America/Chicago", "series": ["KXHIGHSTL", "KXLOWSTL"]},
    "RDU": {"lat": 35.88, "lon": -78.79, "icao": "KRDU", "name": "Raleigh-Durham", "tz": "America/New_York", "series": ["KXHIGHRDU", "KXLOWRDU"]},
    "CLT": {"lat": 35.21, "lon": -80.94, "icao": "KCLT", "name": "Charlotte-Douglas", "tz": "America/New_York", "series": ["KXHIGHCLT", "KXLOWCLT"]},
    "OMA": {"lat": 41.30, "lon": -95.89, "icao": "KOMA", "name": "Omaha (Eppley Airfield)", "tz": "America/Chicago", "series": ["KXHIGHOMA", "KXLOWOMA"]},
    "CHS": {"lat": 32.89, "lon": -80.04, "icao": "KCHS", "name": "Charleston (SC)", "tz": "America/New_York", "series": ["KXHIGHCHS", "KXLOWCHS"]},
}
_SERIES_TO_CITY = {
    series: city_key
    for city_key, loc in STATIONS.items()
    for series in loc.get("series", [])
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
CACHE_EXPIRY_SEC = WEATHER_STATE_TTL_SEC  # 6 hours (weather ensembles are slow-moving)


def _resolve_weather_series(token: str) -> Optional[str]:
    value = str(token or "").upper()
    if not value:
        return None
    if value in _SERIES_TO_CITY:
        return value
    for series in _SERIES_TO_CITY:
        if value.startswith(series):
            return series
    return None


def _intraday_payload(city_key: str, metar: Dict[str, Any], hrrr: Dict[str, Any]) -> Dict[str, Any]:
    cur_temp = metar.get("temp_f")
    return {
        "city_key": city_key,
        "metar_temp": cur_temp,
        "daily_max": cur_temp,
        "daily_min": cur_temp,
        "metar_raw": metar.get("raw"),
        "hrrr_high": hrrr.get("hrrr_high"),
        "hrrr_trend": hrrr.get("hrrr_trend"),
        "ts": time.time(),
    }

async def fetch_open_meteo_ensemble(city_key: str, lat: float, lon: float) -> Dict[str, Any]:
    """
    v19.1.10: Sovereign Multi-Model Ingestion (GFS + ECMWF).
    Includes cloud_cover for TCDC overrides, max/min temps, and precip.
    """
    # v19.1.6: Coordinate-based caching
    cache_key = f"{lat:.2f}_{lon:.2f}"
    now = time.time()
    if cache_key in _COORDINATE_CACHE:
        cached = _COORDINATE_CACHE[cache_key]
        if now - cached["timestamp"] < CACHE_EXPIRY_SEC:
            return cached

    import os
    api_key = os.getenv("OPEN_METEO_API_KEY")
    base_url = "https://customer-api.open-meteo.com/v1/ensemble" if api_key else "https://ensemble-api.open-meteo.com/v1/ensemble"
    
    # v19.2: Sovereign Grand Ensemble (Institutional Blend)
    # GFS = 31, ECMWF = 51, GRAPHCAST (AI) = 1
    # Note: GraphCast is deterministic but highly accurate in the 24-48h window.
    models = ["gfs_seamless", "ecmwf_ifs025", "gfs_graphcast025"]
    results = {}
    
    for model in models:
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,cloud_cover,precipitation",
            "models": model,
            "timezone": "auto"
        }
        if api_key: params["apikey"] = api_key
        
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: requests.get(base_url, params=params, timeout=15))
            
            if resp.status_code == 429:
                logger.warning(f"Open-Meteo 429 (Rate Limit) for {city_key} [{model}]")
                continue

            if resp.status_code != 200: continue

            data = resp.json()
            hourly = data.get("hourly", {})
            if not hourly: continue

            window_size = 26 
            members_high, members_low, members_precip, cloud_members = [], [], [], []
            
            # Model member counts
            if "ecmwf" in model: max_members = 51
            elif "graphcast" in model: max_members = 1 # Deterministic AI model
            else: max_members = 31
            
            for i in range(max_members):
                # For GraphCast, key is just temperature_2m
                if "graphcast" in model:
                    temp_key = "temperature_2m"
                else:
                    temp_key = f"temperature_2m_member{i:02d}"
                    
                cloud_key = f"cloud_cover_member{i:02d}" if "graphcast" not in model else "cloud_cover"
                precip_key = f"precipitation_member{i:02d}" if "graphcast" not in model else "precipitation"
                
                if temp_key in hourly:
                    temps_c = hourly[temp_key][:window_size]
                    if temps_c:
                        temps_f = [(tc * 9/5) + 32 for tc in temps_c]
                        members_high.append(max(temps_f))
                        members_low.append(min(temps_f))
                
                if precip_key in hourly:
                    p_mm = hourly[precip_key][:window_size]
                    if p_mm: 
                        val = sum(p_mm) * 0.03937
                        members_precip.append(val)

                if cloud_key in hourly:
                    c_vals = hourly[cloud_key][11:17] # Peak heating 11 AM - 4 PM
                    if c_vals: cloud_members.append(np.mean(c_vals))

            if members_high:
                if "gfs_seamless" in model: m_type = "gfs"
                elif "ecmwf" in model: m_type = "ecmwf"
                elif "graphcast" in model: m_type = "aigefs"
                else: m_type = "other"
                
                results[m_type] = {
                    "members_high": members_high,
                    "members_low": members_low,
                    "members_precip": members_precip,
                    "mean_high": float(np.mean(members_high)),
                    "sigma_high": float(np.std(members_high)) if len(members_high) > 1 else 0.5,
                    "mean_low": float(np.mean(members_low)),
                    "sigma_low": float(np.std(members_low)) if len(members_low) > 1 else 0.5,
                    "peak_tcdc": float(np.mean(cloud_members)) if cloud_members else 0.0,
                    "timestamp": time.time()
                }
        except Exception as e:
            logger.debug(f"Fetch failed for {city_key} {model}: {e}")

    if not results: return {}
    
    # Unified City Record
    final_record = results.get("gfs", list(results.values())[0]).copy()
    final_record["ecmwf"] = results.get("ecmwf")
    final_record["aigefs"] = results.get("aigefs")
    
    # Update cache
    _COORDINATE_CACHE[cache_key] = final_record
    return final_record

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


async def hydrate_weather_shadow_state(
    *,
    series_filter: Optional[set[str]] = None,
    include_intraday: bool = True,
    concurrency: int = 4,
) -> Dict[str, Any]:
    """Refresh the weather shadow state once for selected series or the whole universe."""
    if series_filter:
        city_keys = sorted({_SERIES_TO_CITY[s] for s in series_filter if s in _SERIES_TO_CITY})
    else:
        city_keys = sorted(STATIONS.keys())

    if not city_keys:
        return {"requested_cities": 0, "updated_series": 0}

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _hydrate_city(city_key: str) -> int:
        loc = STATIONS[city_key]
        async with semaphore:
            ensemble = await fetch_open_meteo_ensemble(city_key, loc["lat"], loc["lon"])
            if not ensemble:
                return 0

            intraday_payload = None
            if include_intraday:
                metar, hrrr = await asyncio.gather(
                    fetch_metar_observation(loc["icao"]),
                    fetch_hrrr_forecast(city_key, loc["lat"], loc["lon"]),
                )
                intraday_payload = _intraday_payload(city_key, metar, hrrr)

            updated = 0
            with _STATE_LOCK:
                for s_ticker in loc.get("series", []):
                    payload = deepcopy(ensemble)
                    existing = _WEATHER_SHADOW_STATE.get(s_ticker, {})
                    if intraday_payload:
                        payload["intraday"] = intraday_payload
                    else:
                        payload["intraday"] = existing.get("intraday", {})
                    _WEATHER_SHADOW_STATE[s_ticker] = payload
                    updated += 1
            return updated

    results = await asyncio.gather(*(_hydrate_city(city_key) for city_key in city_keys), return_exceptions=True)

    updated_series = 0
    errors = 0
    for result in results:
        if isinstance(result, Exception):
            errors += 1
            logger.warning("Weather hydration task failed: %s", result)
            continue
        updated_series += int(result or 0)

    summary = {
        "requested_cities": len(city_keys),
        "updated_series": updated_series,
        "errors": errors,
    }
    logger.info("Weather one-shot hydration summary: %s", summary)
    return summary


def ensure_weather_data(
    tickers_or_series: list[str],
    *,
    include_intraday: bool = True,
    max_age_sec: int = WEATHER_STATE_TTL_SEC,
) -> Dict[str, Any]:
    """Backfill only the missing or stale weather series needed by the current cycle."""
    needed_series = {
        series
        for token in tickers_or_series
        for series in [_resolve_weather_series(token)]
        if series is not None
    }
    if not needed_series:
        return {"requested_series": 0, "refreshed_series": 0, "requested_cities": 0, "errors": 0}

    stale_series = set()
    now = time.time()
    for series in needed_series:
        data = _WEATHER_SHADOW_STATE.get(series)
        if not data or now - float(data.get("timestamp") or 0) > max_age_sec:
            stale_series.add(series)

    if not stale_series:
        return {
            "requested_series": len(needed_series),
            "refreshed_series": 0,
            "requested_cities": 0,
            "errors": 0,
        }

    summary = asyncio.run(
        hydrate_weather_shadow_state(
            series_filter=stale_series,
            include_intraday=include_intraday,
        )
    )
    refreshed_series = 0
    refreshed_now = time.time()
    for series in stale_series:
        data = _WEATHER_SHADOW_STATE.get(series)
        if data and refreshed_now - float(data.get("timestamp") or 0) <= max_age_sec:
            refreshed_series += 1
    return {
        "requested_series": len(needed_series),
        "refreshed_series": refreshed_series,
        **summary,
    }

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
                            existing = _WEATHER_SHADOW_STATE.get(s_ticker, {})
                            payload = deepcopy(result)
                            payload["intraday"] = existing.get("intraday", {})
                            new_state[s_ticker] = payload
                    await asyncio.sleep(random.uniform(2, 5))
                
                if new_state:
                    with _STATE_LOCK:
                        _WEATHER_SHADOW_STATE.update(new_state)
                    logger.info(f"Weather Ensemble synced: {len(new_state)} series")
            except Exception as e:
                logger.error(f"Ensemble sync failure: {e}")
            await asyncio.sleep(10800)

    # ── Cycle 2: Fast Intraday Precinct (15 Minutes) ───────────────────────
    async def run_intraday_sync():
        # v19.8: Day-High/Low Watermarks
        # Key: (city_key, YYYY-MM-DD) -> float
        watermarks = {}
        
        while True:
            try:
                # v19.1.10: Precision Ground Truth (METAR + HRRR)
                from datetime import datetime
                today_str = datetime.now().strftime("%Y-%m-%d")
                
                for city_key, loc in STATIONS.items():
                    metar = await fetch_metar_observation(loc["icao"])
                    hrrr = await fetch_hrrr_forecast(city_key, loc["lat"], loc["lon"])
                    
                    cur_temp = metar.get("temp_f")
                    
                    # Update Watermarks
                    if cur_temp is not None:
                        # Max
                        max_key = (city_key, today_str, "max")
                        watermarks[max_key] = max(cur_temp, watermarks.get(max_key, cur_temp))
                        # Min
                        min_key = (city_key, today_str, "min")
                        watermarks[min_key] = min(cur_temp, watermarks.get(min_key, cur_temp))

                    intraday_payload = {
                        "metar_temp": cur_temp,
                        "daily_max": watermarks.get((city_key, today_str, "max")),
                        "daily_min": watermarks.get((city_key, today_str, "min")),
                        "metar_raw": metar.get("raw"),
                        "hrrr_high": hrrr.get("hrrr_high"),
                        "hrrr_trend": hrrr.get("hrrr_trend"),
                        "ts": time.time()
                    }
                    
                    for s_ticker in loc.get("series", []):
                        if s_ticker in _WEATHER_SHADOW_STATE:
                            with _STATE_LOCK:
                                _WEATHER_SHADOW_STATE[s_ticker]["intraday"] = intraday_payload
                
                logger.info("Weather Intraday Precinct synced (METAR/HRRR/Watermarks).")
            except Exception as e:
                logger.error(f"Intraday sync failure: {e}")
            await asyncio.sleep(900)

    # Launch concurrent loops
    await asyncio.gather(run_ensemble_sync(), run_intraday_sync())

def get_weather_data(ticker_prefix: str) -> Dict[str, Any]:
    """Retrieve cached weather data for a ticker prefix (e.g. 'KXHIGHNY')."""
    # v19.1.6: Direct lookup now that shadow state is keyed by series ticker
    series = _resolve_weather_series(ticker_prefix) or ticker_prefix
    data = _WEATHER_SHADOW_STATE.get(series)
    if data:
        # v19.1.6: Increase cache expiry to 6h to match polling cadence
        if time.time() - data["timestamp"] > WEATHER_STATE_TTL_SEC:
            return {}
        return data
    
    # Fallback pattern matching
    for series_list in [loc.get("series", []) for loc in STATIONS.values()]:
        for s in series_list:
            if str(ticker_prefix).upper().startswith(s):
                data = _WEATHER_SHADOW_STATE.get(s)
                # v19.1.6: Increase cache expiry to 6h
                if data and time.time() - data["timestamp"] <= WEATHER_STATE_TTL_SEC:
                    return data
    return {}

def start_weather_monitor():
    """Start the weather daemon in a background thread."""
    global _MONITOR_THREAD
    with _MONITOR_LOCK:
        if _MONITOR_THREAD and _MONITOR_THREAD.is_alive():
            return _MONITOR_THREAD

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(update_weather_shadow_state())

        _MONITOR_THREAD = threading.Thread(target=_run, daemon=True, name="WeatherShadowMonitor")
        _MONITOR_THREAD.start()
        return _MONITOR_THREAD
