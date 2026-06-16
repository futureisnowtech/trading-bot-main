"""
data/kalshi_weather_monitor.py — Asynchronous Weather Ensemble Pipeline.

Ingests 31-member GFS ensembles from Open-Meteo to calculate probabilistic edges
for Kalshi weather prediction markets. Decouples heavy network ops from the 
live execution loop via a low-latency shadow state dictionary.
"""

import asyncio
import json
import logging
import os
import re
import threading
import time
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

import numpy as np
import pytz
import requests

logger = logging.getLogger("weather_monitor")

# ── Shadow State ──────────────────────────────────────────────────────────────
# O(1) read access for the strategy engine
_WEATHER_SHADOW_STATE: Dict[str, Any] = {}
WEATHER_STATE_TTL_SEC = 21600
WEATHER_REFRESH_TARGET_SEC = 9900
_STATE_LOCK = threading.Lock()
_MONITOR_LOCK = threading.Lock()
_MONITOR_THREAD: Optional[threading.Thread] = None
_ACTIVE_CITY_SCOPE_LOCK = threading.Lock()
_ENSEMBLE_FETCH_STATE_LOCK = threading.Lock()
_ENSEMBLE_GLOBAL_RATE_LIMIT_LOCK = threading.Lock()
_PROVIDER_NOTICE_LOCK = threading.Lock()
_WATERMARKS_FILE = ""
_WEATHER_SNAPSHOT_FILE = ""
_SNAPSHOT_FILE_LOCK = threading.Lock()
_LAST_SNAPSHOT_MTIME = 0.0
_ACTIVE_CITY_SCOPE_CACHE: Dict[str, Any] = {"timestamp": 0.0, "city_keys": []}
_ENSEMBLE_FETCH_STATE: Dict[str, Dict[str, Any]] = {}
_ENSEMBLE_GLOBAL_RATE_LIMIT: Dict[str, Any] = {"until": 0.0, "reason": "", "logged_at": 0.0}
_PROVIDER_NOTICES_EMITTED: set[str] = set()
_OBSERVED_HOURLY_CACHE: Dict[str, Dict[str, Any]] = {}
WEATHER_ACTIVE_CITY_REFRESH_SEC = 300
WEATHER_ENSEMBLE_COOLDOWN_SEC = 1200
WEATHER_ENSEMBLE_MODEL_PAUSE_SEC = 0.75

try:
    from config import (
        DB_PATH as _DB_PATH,
        KALSHI_DATA_FRESHNESS_MINUTES as _CFG_KALSHI_DATA_FRESHNESS_MINUTES,
        WEATHER_ACTIVE_CITY_REFRESH_SEC as _CFG_ACTIVE_CITY_REFRESH_SEC,
        WEATHER_ENSEMBLE_COOLDOWN_SEC as _CFG_ENSEMBLE_COOLDOWN_SEC,
        WEATHER_ENSEMBLE_MODEL_PAUSE_SEC as _CFG_ENSEMBLE_MODEL_PAUSE_SEC,
    )

    _WATERMARKS_FILE = os.path.join(os.path.dirname(_DB_PATH), "weather_watermarks.json")
    _WEATHER_SNAPSHOT_FILE = os.path.join(os.path.dirname(_DB_PATH), "weather_snapshot.json")
    WEATHER_STATE_TTL_SEC = max(300, int(float(_CFG_KALSHI_DATA_FRESHNESS_MINUTES) * 60))
    WEATHER_REFRESH_TARGET_SEC = max(300, WEATHER_STATE_TTL_SEC - 900)
    WEATHER_ACTIVE_CITY_REFRESH_SEC = int(_CFG_ACTIVE_CITY_REFRESH_SEC)
    WEATHER_ENSEMBLE_COOLDOWN_SEC = int(_CFG_ENSEMBLE_COOLDOWN_SEC)
    WEATHER_ENSEMBLE_MODEL_PAUSE_SEC = float(_CFG_ENSEMBLE_MODEL_PAUSE_SEC)
except Exception:
    _WATERMARKS_FILE = ""
    _WEATHER_SNAPSHOT_FILE = ""

# Kalshi Station Mappings (Lat/Lon)
# Refined v19.1.10: Official ASOS Settlement Stations.
# Repo truth: the active station universe currently contains 32 cities.
def _series(*tokens: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        value = str(token or "").strip().upper()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


STATIONS = {
    "NY": {"lat": 40.78, "lon": -73.97, "icao": "KNYC", "name": "New York (Central Park)", "tz": "America/New_York", "series": _series("HIGHNY", "KXHIGHNY", "KXHIGHNYD", "KXLOWNY", "KXLOWNYC", "KXLOWTNYC", "KXRAINNY", "KXRAINNYC", "KXSNOWNY", "KXSNOWNYC", "KXTEMPNYCH", "KXWINDNY", "KXWINDNYC", "RAINNY", "RAINNYC")},
    "CHI": {"lat": 41.78, "lon": -87.75, "icao": "KMDW", "name": "Chicago (Midway)", "tz": "America/Chicago", "series": _series("HIGHCHI", "KXHIGHCHI", "KXLOWCHI", "KXLOWTCHI", "KXRAINCHI", "KXRAINCHIM", "KXSNOWCHI", "KXTEMPCHIH", "KXWINDCHI")},
    "MIA": {"lat": 25.79, "lon": -80.29, "icao": "KMIA", "name": "Miami International", "tz": "America/New_York", "series": _series("HIGHMIA", "KXHIGHMIA", "KXLOWMIA", "KXLOWTMIA", "KXRAINMIA", "KXRAINMIAM", "KXSNOWMIA", "KXTEMPMIAH", "KXWINDMIA", "RAINMIA")},
    "LAX": {"lat": 33.94, "lon": -118.41, "icao": "KLAX", "name": "Los Angeles Intl", "tz": "America/Los_Angeles", "series": _series("KXHIGHLAX", "KXLOWLAX", "KXLOWTLAX", "KXRAINLAX", "KXRAINLAXM", "KXSNOWLAX", "KXTEMPLAXH", "KXWINDLAX")},
    "DEN": {"lat": 39.86, "lon": -104.67, "icao": "KDEN", "name": "Denver International", "tz": "America/Denver", "series": _series("KXHIGHDEN", "KXLOWDEN", "KXLOWTDEN", "KXRAINDEN", "KXRAINDENM", "KXSNOWDEN", "KXWINDDEN")},
    "AUS": {"lat": 30.20, "lon": -97.67, "icao": "KAUS", "name": "Austin-Bergstrom", "tz": "America/Chicago", "series": _series("HIGHAUS", "KXHIGHAUS", "KXLOWAUS", "KXLOWTAUS", "KXRAINAUS", "KXRAINAUSM", "KXSNOWAUS", "KXWINDAUS")},
    "PHX": {"lat": 33.43, "lon": -112.01, "icao": "KPHX", "name": "Phoenix Sky Harbor", "tz": "America/Phoenix", "series": _series("KXHIGHTPHX", "KXLOWTPHX", "KXRAINPHX", "KXSNOWPHX", "KXWINDPHX")},
    "SEA": {"lat": 47.45, "lon": -122.31, "icao": "KSEA", "name": "Seattle-Tacoma", "tz": "America/Los_Angeles", "series": _series("KXHIGHSEA", "KXHIGHTSEA", "KXLOWSEA", "KXLOWTSEA", "KXRAINSEA", "KXRAINSEAM", "KXSNOWSEA", "KXWINDSEA", "RAINSEA")},
    "DAL": {"lat": 32.90, "lon": -97.04, "icao": "KDFW", "name": "Dallas/Fort Worth", "tz": "America/Chicago", "series": _series("KXHIGHDAL", "KXHIGHTDAL", "KXLOWDAL", "KXLOWTDAL", "KXRAINDAL", "KXRAINDALM", "KXSNOWDAL", "KXWINDDAL")},
    "ATL": {"lat": 33.64, "lon": -84.43, "icao": "KATL", "name": "Hartsfield-Jackson", "tz": "America/New_York", "series": _series("KXHIGHTATL", "KXLOWTATL", "KXRAINATL", "KXSNOWATL", "KXWINDATL")},
    "HOU": {"lat": 29.65, "lon": -95.28, "icao": "KHOU", "name": "Houston Hobby", "tz": "America/Chicago", "series": _series("KXHIGHHOU", "KXHIGHOU", "KXHIGHTHOU", "KXLOWTHOU", "KXRAINHOU", "KXRAINHOUM", "KXSNOWHOU", "KXWINDHOU", "RAINHOU")},
    "BOS": {"lat": 42.36, "lon": -71.01, "icao": "KBOS", "name": "Boston Logan", "tz": "America/New_York", "series": _series("KXHIGHBOS", "KXHIGHTBOS", "KXLOWBOS", "KXLOWTBOS", "KXRAINBOS", "KXSNOWBOS", "KXTEMPBOSH", "KXWINDBOS")},
    "DC": {"lat": 38.85, "lon": -77.04, "icao": "KDCA", "name": "Reagan National", "tz": "America/New_York", "series": _series("KXHIGHDC", "KXHIGHTDC", "KXLOWDC", "KXLOWTDC", "KXRAINDC", "KXSNOWDC", "KXTEMPDCH", "KXWINDDC")},
    "SF": {"lat": 37.62, "lon": -122.37, "icao": "KSFO", "name": "San Francisco Intl", "tz": "America/Los_Angeles", "series": _series("KXHIGHSF", "KXHIGHTSFO", "KXLOWSF", "KXLOWTSFO", "KXRAINSF", "KXRAINSFOM", "KXSNOWSF", "KXSNOWSFO", "KXWINDSF", "KXWINDSFO")},
    "LV": {"lat": 36.08, "lon": -115.15, "icao": "KLAS", "name": "Las Vegas (Harry Reid)", "tz": "America/Los_Angeles", "series": _series("KXHIGHTLV", "KXLOWTLV", "KXRAINLV", "KXSNOWLV", "KXWINDLV")},
    # v19.3: Institutional Expansion Universe
    "MSP": {"lat": 44.88, "lon": -93.22, "icao": "KMSP", "name": "Minneapolis-St. Paul", "tz": "America/Chicago", "series": _series("KXHIGHMSP", "KXHIGHTMIN", "KXLOWMSP", "KXLOWTMIN", "KXRAINMIN", "KXRAINMSP", "KXSNOWMIN", "KXSNOWMSP", "KXWINDMIN", "KXWINDMSP")},
    "DET": {"lat": 42.21, "lon": -83.35, "icao": "KDTW", "name": "Detroit Metro", "tz": "America/New_York", "series": _series("KXHIGHDET", "KXLOWDET", "KXRAINDET", "KXSNOWDET", "KXWINDDET")},
    "SLC": {"lat": 40.79, "lon": -111.97, "icao": "KSLC", "name": "Salt Lake City Intl", "tz": "America/Denver", "series": _series("KXHIGHSLC", "KXLOWSLC", "KXRAINSLC", "KXSNOWSLC", "KXWINDSLC")},
    "OKC": {"lat": 35.39, "lon": -97.60, "icao": "KOKC", "name": "Oklahoma City", "tz": "America/Chicago", "series": _series("KXHIGHOKC", "KXHIGHTOKC", "KXLOWOKC", "KXLOWTOKC", "KXRAINOKC", "KXSNOWOKC", "KXWINDOKC")},
    "PHL": {"lat": 39.87, "lon": -75.24, "icao": "KPHL", "name": "Philadelphia Intl", "tz": "America/New_York", "series": _series("KXHIGHPHIL", "KXHIGHPHL", "KXLOWPHIL", "KXLOWPHL", "KXLOWTPHIL", "KXRAINPHIL", "KXRAINPHL", "KXSNOWPHIL", "KXSNOWPHL", "KXWINDPHIL", "KXWINDPHL")},
    "MCI": {"lat": 39.30, "lon": -94.71, "icao": "KMCI", "name": "Kansas City Intl", "tz": "America/Chicago", "series": _series("KXHIGHMCI", "KXLOWMCI", "KXRAINMCI", "KXSNOWMCI", "KXWINDMCI")},
    "ABQ": {"lat": 35.04, "lon": -106.61, "icao": "KABQ", "name": "Albuquerque Intl", "tz": "America/Denver", "series": _series("KXHIGHABQ", "KXLOWABQ", "KXRAINABQ", "KXSNOWABQ", "KXWINDABQ")},
    "MSY": {"lat": 29.99, "lon": -90.26, "icao": "KMSY", "name": "New Orleans (Armstrong)", "tz": "America/Chicago", "series": _series("KXHIGHMSY", "KXHIGHTNOLA", "KXLOWMSY", "KXLOWTNOLA", "KXRAINNO", "KXRAINMSY", "KXSNOWNO", "KXSNOWMSY", "KXWINDNO", "KXWINDMSY")},
    "PDX": {"lat": 45.59, "lon": -122.60, "icao": "KPDX", "name": "Portland Intl", "tz": "America/Los_Angeles", "series": _series("KXHIGHPDX", "KXLOWPDX", "KXRAINPDX", "KXSNOWPDX", "KXWINDPDX")},
    "MKE": {"lat": 42.95, "lon": -87.90, "icao": "KMKE", "name": "Milwaukee (Mitchell)", "tz": "America/Chicago", "series": _series("KXHIGHMKE", "KXLOWMKE", "KXRAINMKE", "KXSNOWMKE", "KXWINDMKE")},
    "MCO": {"lat": 28.43, "lon": -81.33, "icao": "KMCO", "name": "Orlando Intl", "tz": "America/New_York", "series": _series("KXHIGHMCO", "KXLOWMCO", "KXRAINMCO", "KXSNOWMCO", "KXWINDMCO")},
    "STL": {"lat": 38.75, "lon": -90.37, "icao": "KSTL", "name": "St. Louis (Lambert)", "tz": "America/Chicago", "series": _series("KXHIGHSTL", "KXLOWSTL", "KXRAINSTL", "KXSNOWSTL", "KXWINDSTL")},
    "RDU": {"lat": 35.88, "lon": -78.79, "icao": "KRDU", "name": "Raleigh-Durham", "tz": "America/New_York", "series": _series("KXHIGHRDU", "KXLOWRDU", "KXRAINRDU", "KXSNOWRDU", "KXWINDRDU")},
    "CLT": {"lat": 35.21, "lon": -80.94, "icao": "KCLT", "name": "Charlotte-Douglas", "tz": "America/New_York", "series": _series("KXHIGHCLT", "KXLOWCLT", "KXRAINCLT", "KXSNOWCLT", "KXWINDCLT")},
    "OMA": {"lat": 41.30, "lon": -95.89, "icao": "KOMA", "name": "Omaha (Eppley Airfield)", "tz": "America/Chicago", "series": _series("KXHIGHOMA", "KXLOWOMA", "KXRAINOMA", "KXSNOWOMA", "KXWINDOMA")},
    "CHS": {"lat": 32.89, "lon": -80.04, "icao": "KCHS", "name": "Charleston (SC)", "tz": "America/New_York", "series": _series("KXHIGHCHS", "KXLOWCHS", "KXRAINCHS", "KXSNOWCHS", "KXWINDCHS")},
    "SAT": {"lat": 29.53, "lon": -98.47, "icao": "KSAT", "name": "San Antonio Intl", "tz": "America/Chicago", "series": _series("KXHIGHTSATX", "KXLOWTSATX", "KXRAINSATX", "KXSNOWSATX", "KXWINDSATX")},
}
_SERIES_TO_CITY = {
    series: city_key
    for city_key, loc in STATIONS.items()
    for series in loc.get("series", [])
}
_WEATHER_PREFIXES = (
    "KXTEMP",
    "KXHIGHT",
    "KXLOWT",
    "KXHIGH",
    "KXLOW",
    "KXRAIN",
    "RAIN",
    "HIGH",
    "LOW",
    "KXWIND",
    "KXSNOW",
)
_REGISTRY_HOURLY_PREFIXES = (
    "KXTEMP",
    "KXHIGHT",
    "KXLOWT",
)
_CITY_TITLE_ALIASES = (
    ("NEW YORK CITY", "NY"),
    ("NEW YORK", "NY"),
    ("NYC", "NY"),
    ("LOS ANGELES", "LAX"),
    ("CHICAGO", "CHI"),
    ("WASHINGTON, DC", "DC"),
    ("WASHINGTON DC", "DC"),
    ("MIAMI", "MIA"),
    ("BOSTON", "BOS"),
)


def _canonical_series_for_city(city_key: str) -> Optional[str]:
    station = STATIONS.get(city_key) or {}
    series_list = [str(series).upper() for series in station.get("series", []) if str(series).strip()]
    if not series_list:
        return None
    for prefix in ("KXTEMP", "KXHIGHT", "KXLOWT", "KXHIGH", "KXLOW", "KXRAIN"):
        for series in series_list:
            if series.startswith(prefix):
                return series
    return series_list[0]


def _is_registry_hourly_series(series: str) -> bool:
    token = str(series or "").upper()
    return any(token.startswith(prefix) for prefix in _REGISTRY_HOURLY_PREFIXES)


def _city_key_from_contract_name(contract_name: str) -> Optional[str]:
    title = re.sub(r"\s+", " ", str(contract_name or "").replace("*", " ")).strip().upper()
    if not title:
        return None
    for alias, city_key in _CITY_TITLE_ALIASES:
        if alias in title:
            return city_key
    return None


def _series_suffix_aliases(series: str) -> set[str]:
    token = str(series or "").upper()
    aliases: set[str] = set()
    for prefix in _WEATHER_PREFIXES:
        if not token.startswith(prefix):
            continue
        suffix = token[len(prefix):].strip()
        if len(suffix) >= 2:
            aliases.add(suffix)
        break
    return aliases


def _build_hourly_temp_alias_map() -> dict[str, str]:
    alias_map: dict[str, str] = {}
    for city_key, station in STATIONS.items():
        aliases: set[str] = set()
        for series in station.get("series", []):
            if not str(series).upper().startswith("KXTEMP"):
                continue
            aliases.update(_series_suffix_aliases(str(series)))
        for alias in sorted(aliases, key=len, reverse=True):
            alias_map.setdefault(alias, str(city_key).upper())
    return alias_map


_HOURLY_TEMP_ALIAS_TO_CITY = _build_hourly_temp_alias_map()
_HOURLY_TEMP_ALIAS_ORDER = tuple(
    sorted(_HOURLY_TEMP_ALIAS_TO_CITY.keys(), key=len, reverse=True)
)


def _build_short_cadence_alias_map() -> dict[str, str]:
    alias_map: dict[str, str] = {}
    for city_key, station in STATIONS.items():
        aliases: set[str] = set()
        for series in station.get("series", []):
            if not _is_registry_hourly_series(str(series)):
                continue
            aliases.update(_series_suffix_aliases(str(series)))
        for alias in sorted(aliases, key=len, reverse=True):
            alias_map.setdefault(alias, str(city_key).upper())
    return alias_map


_SHORT_CADENCE_ALIAS_TO_CITY = _build_short_cadence_alias_map()


def _resolve_hourly_temp_city_key(token: str) -> Optional[str]:
    value = str(token or "").upper()
    if not value.startswith("KXTEMP"):
        return None
    head = value.split("-", 1)[0]
    suffix = head[len("KXTEMP") :].strip()
    if not suffix:
        return None
    for alias in _HOURLY_TEMP_ALIAS_ORDER:
        if suffix == alias:
            return _HOURLY_TEMP_ALIAS_TO_CITY.get(alias)
    return None


def _resolve_generic_prefix_city_key(token: str) -> Optional[str]:
    value = str(token or "").upper()
    for prefix in ("KXWIND", "KXSNOW", "KXRAIN", "KXHIGH", "KXLOW", "RAIN"):
        if value.startswith(prefix):
            head = value.split("-", 1)[0]
            suffix = head[len(prefix):].strip()
            if suffix in _SHORT_CADENCE_ALIAS_TO_CITY:
                return _SHORT_CADENCE_ALIAS_TO_CITY.get(suffix)
    return None


def resolve_weather_city_key(token: str, *, contract_name: str = "") -> Optional[str]:
    city_key = _city_key_from_contract_name(contract_name)
    if city_key is not None:
        return city_key
    series = _resolve_weather_series(token)
    if series is not None:
        return _SERIES_TO_CITY.get(series)
    return None


def get_hourly_city_support_summary() -> dict[str, Any]:
    registry_hourly = sorted(
        city_key
        for city_key, station in STATIONS.items()
        if any(_is_registry_hourly_series(str(series)) for series in station.get("series", []))
    )
    resolver_ready = sorted(set(_SHORT_CADENCE_ALIAS_TO_CITY.values()))
    verified_hourly_series = sorted(
        str(series).upper()
        for station in STATIONS.values()
        for series in station.get("series", [])
        if _is_registry_hourly_series(str(series))
    )
    return {
        "support_basis": "local_series_registry",
        "universe_city_count": len(STATIONS),
        "resolver_ready_city_count": len(resolver_ready),
        "explicit_hourly_series_city_count": len(registry_hourly),
        "resolver_ready_cities": resolver_ready,
        "explicit_hourly_series_cities": registry_hourly,
        "exchange_verified_city_count": len(registry_hourly),
        "exchange_verified_cities": registry_hourly,
        "verified_hourly_series_count": len(verified_hourly_series),
        "verified_hourly_series": verified_hourly_series,
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
CACHE_EXPIRY_SEC = WEATHER_REFRESH_TARGET_SEC
_OBSERVED_DAILY_CACHE: Dict[str, Dict[str, Any]] = {}
OBSERVED_DAILY_CACHE_TTL_SEC = 6 * 3600


def _series_record_is_fresh(record: Dict[str, Any] | None, *, max_age_sec: int = WEATHER_STATE_TTL_SEC) -> bool:
    if not record:
        return False
    try:
        ts = float(record.get("timestamp") or 0.0)
    except Exception:
        return False
    return (time.time() - ts) <= max_age_sec


def _persist_weather_snapshot() -> None:
    """Persist the current weather shadow state for read-only sidecars."""
    global _LAST_SNAPSHOT_MTIME

    if not _WEATHER_SNAPSHOT_FILE:
        return

    with _STATE_LOCK:
        snapshot_state = deepcopy(_WEATHER_SHADOW_STATE)

    payload = {
        "written_at": time.time(),
        "series_count": len(snapshot_state),
        "state": snapshot_state,
    }
    tmp_path = f"{_WEATHER_SNAPSHOT_FILE}.tmp"

    try:
        os.makedirs(os.path.dirname(_WEATHER_SNAPSHOT_FILE), exist_ok=True)
        with _SNAPSHOT_FILE_LOCK:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
            os.replace(tmp_path, _WEATHER_SNAPSHOT_FILE)
            _LAST_SNAPSHOT_MTIME = os.path.getmtime(_WEATHER_SNAPSHOT_FILE)
    except Exception as exc:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        logger.debug("Weather snapshot persist failed: %s", exc)


def _load_weather_snapshot(*, force: bool = False) -> Dict[str, Any]:
    """Load persisted shared weather truth into the in-process shadow state."""
    global _LAST_SNAPSHOT_MTIME

    if not _WEATHER_SNAPSHOT_FILE or not os.path.exists(_WEATHER_SNAPSHOT_FILE):
        return {"loaded_series": 0}

    try:
        file_mtime = os.path.getmtime(_WEATHER_SNAPSHOT_FILE)
    except Exception:
        return {"loaded_series": 0}

    if not force and file_mtime <= _LAST_SNAPSHOT_MTIME:
        return {"loaded_series": 0, "cached": True}

    try:
        with _SNAPSHOT_FILE_LOCK:
            with open(_WEATHER_SNAPSHOT_FILE, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
    except Exception as exc:
        logger.debug("Weather snapshot load failed: %s", exc)
        return {"loaded_series": 0, "error": str(exc)}

    state = payload.get("state") if isinstance(payload, dict) else {}
    if not isinstance(state, dict):
        return {"loaded_series": 0}

    loaded_series = 0
    with _STATE_LOCK:
        for series, record in state.items():
            if not isinstance(record, dict):
                continue
            current = _WEATHER_SHADOW_STATE.get(series)
            current_ts = float((current or {}).get("timestamp") or 0.0)
            incoming_ts = float(record.get("timestamp") or 0.0)
            if current and current_ts > incoming_ts:
                continue
            _WEATHER_SHADOW_STATE[str(series)] = record
            loaded_series += 1

    _LAST_SNAPSHOT_MTIME = file_mtime
    return {
        "loaded_series": loaded_series,
        "written_at": payload.get("written_at") if isinstance(payload, dict) else None,
    }


def _cached_ensemble_record(cache_key: str, *, max_age_sec: int = CACHE_EXPIRY_SEC) -> Dict[str, Any]:
    cached = _COORDINATE_CACHE.get(cache_key)
    if not cached:
        return {}
    ts = float(cached.get("timestamp") or 0.0)
    if time.time() - ts > max_age_sec:
        return {}
    return cached


def _global_ensemble_rate_limit_active() -> bool:
    with _ENSEMBLE_GLOBAL_RATE_LIMIT_LOCK:
        return float(_ENSEMBLE_GLOBAL_RATE_LIMIT.get("until") or 0.0) > time.time()


def _activate_global_ensemble_rate_limit(*, reason: str) -> bool:
    tomorrow_utc = (
        datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        + 86400
    )
    with _ENSEMBLE_GLOBAL_RATE_LIMIT_LOCK:
        already_active = float(_ENSEMBLE_GLOBAL_RATE_LIMIT.get("until") or 0.0) > time.time()
        _ENSEMBLE_GLOBAL_RATE_LIMIT["until"] = tomorrow_utc
        _ENSEMBLE_GLOBAL_RATE_LIMIT["reason"] = reason
        if not already_active:
            _ENSEMBLE_GLOBAL_RATE_LIMIT["logged_at"] = time.time()
        return not already_active


def _claim_ensemble_fetch_slot(cache_key: str) -> str:
    now = time.time()
    with _ENSEMBLE_FETCH_STATE_LOCK:
        state = _ENSEMBLE_FETCH_STATE.setdefault(cache_key, {})
        cooldown_until = float(state.get("cooldown_until") or 0.0)
        if cooldown_until > now:
            return "cooldown"
        if state.get("inflight"):
            return "wait"
        state["inflight"] = True
        return "leader"


def _release_ensemble_fetch_slot(cache_key: str) -> None:
    with _ENSEMBLE_FETCH_STATE_LOCK:
        state = _ENSEMBLE_FETCH_STATE.setdefault(cache_key, {})
        state["inflight"] = False


def _enter_ensemble_cooldown(cache_key: str, *, city_key: str, model: str) -> bool:
    now = time.time()
    with _ENSEMBLE_FETCH_STATE_LOCK:
        state = _ENSEMBLE_FETCH_STATE.setdefault(cache_key, {})
        prior = float(state.get("cooldown_until") or 0.0)
        state["cooldown_until"] = now + max(60, WEATHER_ENSEMBLE_COOLDOWN_SEC)
        state["last_429_city"] = city_key
        state["last_429_model"] = model
        return prior <= now


async def _await_inflight_ensemble(cache_key: str) -> Dict[str, Any]:
    deadline = time.time() + 20.0
    while time.time() < deadline:
        cached = _cached_ensemble_record(cache_key)
        if cached:
            return cached
        with _ENSEMBLE_FETCH_STATE_LOCK:
            inflight = bool(_ENSEMBLE_FETCH_STATE.get(cache_key, {}).get("inflight"))
        if not inflight:
            break
        await asyncio.sleep(0.25)
    return _cached_ensemble_record(cache_key)


def _active_weather_city_keys() -> list[str]:
    now = time.time()
    with _ACTIVE_CITY_SCOPE_LOCK:
        cached_ts = float(_ACTIVE_CITY_SCOPE_CACHE.get("timestamp") or 0.0)
        cached_city_keys = list(_ACTIVE_CITY_SCOPE_CACHE.get("city_keys") or [])
        if cached_city_keys and (now - cached_ts) <= WEATHER_ACTIVE_CITY_REFRESH_SEC:
            return cached_city_keys

    city_keys: list[str] = []

    try:
        from forecast.db import get_active_contracts

        city_keys = sorted(
            {
                city_key
                for contract in get_active_contracts()
                for city_key in [
                    resolve_weather_city_key(
                        contract.get("local_symbol") or "",
                        contract_name=str(contract.get("contract_name") or ""),
                    )
                ]
                if city_key is not None
            }
        )
    except Exception as exc:
        logger.debug("Active weather scope lookup failed: %s", exc)

    if not city_keys:
        with _STATE_LOCK:
            city_keys = sorted(
                {
                    _SERIES_TO_CITY[series]
                    for series in _WEATHER_SHADOW_STATE
                    if series in _SERIES_TO_CITY
                }
            )

    with _ACTIVE_CITY_SCOPE_LOCK:
        _ACTIVE_CITY_SCOPE_CACHE["timestamp"] = now
        _ACTIVE_CITY_SCOPE_CACHE["city_keys"] = city_keys

    return city_keys


def _emit_provider_notice_once(notice_key: str, message: str) -> None:
    with _PROVIDER_NOTICE_LOCK:
        if notice_key in _PROVIDER_NOTICES_EMITTED:
            return
        _PROVIDER_NOTICES_EMITTED.add(notice_key)
    logger.info(message)


def _weather_model_key(model: str) -> str:
    model_name = str(model or "").lower()
    if "ecmwf" in model_name:
        return "ecmwf"
    if "graphcast" in model_name:
        return "aigefs"
    if "gfs" in model_name:
        return "gfs"
    return "other"


def _deterministic_temp_sigma_floor(model_key: str, horizon_days: int = 0) -> float:
    base = {
        "gfs": 2.1,
        "ecmwf": 1.8,
        "aigefs": 1.6,
    }.get(model_key, 2.0)
    return min(5.0, base + (max(0, int(horizon_days)) * 0.45))


def _deterministic_precip_sigma(mean_precip: float, horizon_days: int = 0) -> float:
    return min(
        2.0,
        max(0.08, 0.06 + (abs(float(mean_precip)) * 0.55) + (max(0, int(horizon_days)) * 0.04)),
    )


def _c_to_f(value: float | None) -> float | None:
    if value is None:
        return None
    return (float(value) * 9.0 / 5.0) + 32.0


def _mm_to_inches(value: float | None) -> float | None:
    if value is None:
        return None
    return float(value) * 0.03937


def _nws_cli_location(station: dict | None) -> str:
    if not station:
        return ""
    explicit = str(station.get("cli_location") or "").strip().upper()
    if explicit:
        return explicit
    icao = str(station.get("icao") or "").strip().upper()
    if len(icao) == 4 and icao.startswith("K"):
        return icao[1:]
    return icao


def _parse_cli_report_date(product_text: str) -> date | None:
    match = re.search(
        r"CLIMATE SUMMARY FOR ([A-Z]+ \d{1,2} \d{4})",
        str(product_text or "").upper(),
    )
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%B %d %Y").date()
    except Exception:
        return None


def _parse_cli_numeric_token(token: str, *, trace_value: float | None = None) -> float | None:
    value = str(token or "").strip().upper()
    if not value or value in {"MM", "M"}:
        return None
    if value == "T":
        return trace_value
    try:
        return float(value)
    except Exception:
        return None


def _parse_nws_cli_product_text(
    product_text: str,
    *,
    target_date: date,
) -> Dict[str, Any]:
    report_date = _parse_cli_report_date(product_text)
    if report_date != target_date:
        return {}

    text = str(product_text or "")
    temperature_section = ""
    precipitation_section = ""

    temp_match = re.search(
        r"TEMPERATURE \(F\)(.*?)(?:\n\s*PRECIPITATION \(IN\)|\Z)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if temp_match:
        temperature_section = temp_match.group(1)

    precip_match = re.search(
        r"PRECIPITATION \(IN\)(.*?)(?:\n\s*SNOWFALL \(IN\)|\n\s*DEGREE DAYS|\Z)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if precip_match:
        precipitation_section = precip_match.group(1)

    observed_high = None
    observed_low = None
    observed_precip = None

    if temperature_section:
        max_match = re.search(
            r"^\s*MAXIMUM\s+(-?\d+(?:\.\d+)?)\b",
            temperature_section,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        min_match = re.search(
            r"^\s*MINIMUM\s+(-?\d+(?:\.\d+)?)\b",
            temperature_section,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        if max_match:
            observed_high = float(max_match.group(1))
        if min_match:
            observed_low = float(min_match.group(1))

    if precipitation_section:
        yesterday_match = re.search(
            r"^\s*YESTERDAY\s+([0-9.]+|T|MM)\b",
            precipitation_section,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        if yesterday_match:
            observed_precip = _parse_cli_numeric_token(
                yesterday_match.group(1),
                trace_value=0.001,
            )

    if observed_high is None and observed_low is None and observed_precip is None:
        return {}

    return {
        "observed_high": observed_high,
        "observed_low": observed_low,
        "observed_precip": observed_precip,
    }


def _fetch_nws_cli_daily_summary(
    city_key: str,
    station: dict,
    target_date: date,
) -> Dict[str, Any]:
    location = _nws_cli_location(station)
    if not location:
        return {}

    headers = {"User-Agent": "kalshi-weather-engine/1.0"}
    try:
        index_resp = requests.get(
            f"https://api.weather.gov/products/types/CLI/locations/{location}",
            headers=headers,
            timeout=15,
        )
        index_resp.raise_for_status()
        products = list((index_resp.json() or {}).get("@graph") or [])
    except Exception as exc:
        logger.debug(
            "NWS CLI index fetch failed for %s %s: %s",
            city_key,
            target_date.isoformat(),
            exc,
        )
        return {}

    for product in products[:10]:
        product_url = str(product.get("@id") or "").strip()
        if not product_url:
            continue
        try:
            product_resp = requests.get(product_url, headers=headers, timeout=15)
            product_resp.raise_for_status()
            product_payload = product_resp.json() or {}
            parsed = _parse_nws_cli_product_text(
                str(product_payload.get("productText") or ""),
                target_date=target_date,
            )
            if parsed:
                return {
                    "city_key": city_key,
                    "target_local_date": target_date.isoformat(),
                    **parsed,
                    "source": "nws_cli_daily",
                    "cached_at": time.time(),
                }
        except Exception as exc:
            logger.debug(
                "NWS CLI product fetch failed for %s %s via %s: %s",
                city_key,
                target_date.isoformat(),
                product_url,
                exc,
            )
    return {}


def _build_weather_record_from_hourly(
    hourly: dict[str, Any],
    model: str,
    *,
    deterministic: bool,
    forecast_source: str,
) -> Dict[str, Any]:
    if not hourly:
        return {}

    window_size = 26
    hourly_time = list(hourly.get("time", []))
    members_high, members_low, members_precip, cloud_members = [], [], [], []
    ssrd_members, members_wind = [], []
    hourly_members_temp_f = {}
    hourly_members_precip_in = {}
    hourly_members_cloud = {}
    hourly_members_ssrd = {}
    hourly_members_wind = {}

    model_key = _weather_model_key(model)
    member_slots = [0] if deterministic else range(51 if model_key == "ecmwf" else (1 if model_key == "aigefs" else 31))

    for i in member_slots:
        member_label = f"member_{i:02d}"
        if deterministic:
            temp_key = "temperature_2m"
            cloud_key = "cloud_cover"
            precip_key = "precipitation"
            ssrd_key = "shortwave_radiation"
            wind_key = "wind_speed_10m"
        else:
            temp_key = "temperature_2m" if model_key == "aigefs" else f"temperature_2m_member{i:02d}"
            cloud_key = "cloud_cover" if model_key == "aigefs" else f"cloud_cover_member{i:02d}"
            precip_key = "precipitation" if model_key == "aigefs" else f"precipitation_member{i:02d}"
            ssrd_key = "shortwave_radiation" if model_key == "aigefs" else f"shortwave_radiation_member{i:02d}"
            wind_key = "wind_speed_10m" if model_key == "aigefs" else f"wind_speed_10m_member{i:02d}"

        if temp_key in hourly:
            all_temps_c = hourly[temp_key]
            temps_c = all_temps_c[:window_size]
            if temps_c:
                temps_f = [(float(tc) * 9 / 5) + 32 for tc in temps_c]
                members_high.append(max(temps_f))
                members_low.append(min(temps_f))
            if all_temps_c:
                hourly_members_temp_f[member_label] = [
                    (float(tc) * 9 / 5) + 32 for tc in all_temps_c
                ]

        if precip_key in hourly:
            all_precip_mm = hourly[precip_key]
            p_mm = all_precip_mm[:window_size]
            if p_mm:
                members_precip.append(sum(float(mm) for mm in p_mm) * 0.03937)
            if all_precip_mm:
                hourly_members_precip_in[member_label] = [
                    float(mm) * 0.03937 for mm in all_precip_mm
                ]

        if wind_key in hourly:
            all_wind_kmh = hourly[wind_key]
            w_kmh = all_wind_kmh[:window_size]
            if w_kmh:
                members_wind.append(max(float(kmh) for kmh in w_kmh) * 0.621371)
            if all_wind_kmh:
                hourly_members_wind[member_label] = [
                    float(kmh) * 0.621371 for kmh in all_wind_kmh
                ]

        if cloud_key in hourly:
            all_cloud = hourly[cloud_key]
            c_vals = all_cloud[11:17]
            if c_vals:
                cloud_members.append(float(np.mean(c_vals)))
            if all_cloud:
                hourly_members_cloud[member_label] = [float(val) for val in all_cloud]

        if ssrd_key in hourly:
            all_ssrd = hourly[ssrd_key]
            s_vals = all_ssrd[11:17]
            if s_vals:
                ssrd_members.append(float(np.mean(s_vals)))
            if all_ssrd:
                hourly_members_ssrd[member_label] = [float(val) for val in all_ssrd]

    if not members_high:
        return {}

    sigma_high = (
        float(np.std(members_high))
        if len(members_high) > 1
        else _deterministic_temp_sigma_floor(model_key, horizon_days=0)
    )
    sigma_low = (
        float(np.std(members_low))
        if len(members_low) > 1
        else _deterministic_temp_sigma_floor(model_key, horizon_days=0)
    )
    mean_precip = float(np.mean(members_precip)) if members_precip else 0.0
    sigma_precip = (
        float(np.std(members_precip))
        if len(members_precip) > 1
        else _deterministic_precip_sigma(mean_precip, horizon_days=0)
    )
    mean_wind = float(np.mean(members_wind)) if members_wind else 0.0
    sigma_wind = (
        float(np.std(members_wind))
        if len(members_wind) > 1
        else 0.5
    )

    return {
        "members_high": members_high,
        "members_low": members_low,
        "members_precip": members_precip,
        "members_wind": members_wind,
        "mean_high": float(np.mean(members_high)),
        "sigma_high": sigma_high,
        "mean_low": float(np.mean(members_low)),
        "sigma_low": sigma_low,
        "mean_precip": mean_precip,
        "sigma_precip": sigma_precip,
        "mean_wind": mean_wind,
        "sigma_wind": sigma_wind,
        "peak_tcdc": float(np.mean(cloud_members)) if cloud_members else 0.0,
        "peak_ssrd": float(np.mean(ssrd_members)) if ssrd_members else None,
        "timestamp": time.time(),
        "hourly_time": hourly_time,
        "hourly_members_temp_f": hourly_members_temp_f,
        "hourly_members_precip_in": hourly_members_precip_in,
        "hourly_members_cloud": hourly_members_cloud,
        "hourly_members_ssrd": hourly_members_ssrd,
        "hourly_members_wind": hourly_members_wind,
        "provider_mode": "deterministic_multi_model" if deterministic else "ensemble_members",
        "forecast_source": forecast_source,
        "model_name": model,
    }


async def _fetch_open_meteo_deterministic_multimodel(
    city_key: str,
    lat: float,
    lon: float,
) -> Dict[str, Any]:
    model_specs = [
        ("gfs_seamless", "https://api.open-meteo.com/v1/gfs"),
        ("ecmwf_ifs025", "https://api.open-meteo.com/v1/ecmwf"),
        ("gfs_graphcast025", "https://api.open-meteo.com/v1/gfs"),
    ]
    results: dict[str, dict[str, Any]] = {}

    for idx, (model, url) in enumerate(model_specs):
        if idx and WEATHER_ENSEMBLE_MODEL_PAUSE_SEC > 0:
            await asyncio.sleep(WEATHER_ENSEMBLE_MODEL_PAUSE_SEC)

        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,cloud_cover,precipitation,shortwave_radiation",
            "models": model,
            "timezone": "auto",
            "forecast_days": 8,
        }
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: requests.get(url, params=params, timeout=15),
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            record = _build_weather_record_from_hourly(
                data.get("hourly", {}),
                model,
                deterministic=True,
                forecast_source="open_meteo_forecast",
            )
            if record:
                results[_weather_model_key(model)] = record
        except Exception as exc:
            logger.debug("Deterministic weather fetch failed for %s %s: %s", city_key, model, exc)

    if not results:
        return {}

    final_record = results.get("gfs", list(results.values())[0]).copy()
    final_record["ecmwf"] = results.get("ecmwf")
    final_record["aigefs"] = results.get("aigefs")
    final_record["provider_mode"] = "deterministic_multi_model"
    final_record["forecast_source"] = "open_meteo_forecast"
    final_record["city_key"] = city_key
    final_record["timestamp"] = time.time()
    return final_record


def _resolve_weather_series(token: str) -> Optional[str]:
    value = str(token or "").upper()
    if not value:
        return None
    value_head = value.split("-", 1)[0]
    if value in _SERIES_TO_CITY:
        return value
    for series in sorted(_SERIES_TO_CITY, key=len, reverse=True):
        if value.startswith("KXTEMP") and str(series).startswith("KXTEMP") and value_head != str(series):
            continue
        if value.startswith(series):
            return series
    city_key = _resolve_hourly_temp_city_key(value)
    if city_key is None:
        city_key = _resolve_generic_prefix_city_key(value)
    if city_key is not None:
        return _canonical_series_for_city(city_key)
    return None


def _station_for_series(series: str, *, contract_name: str = "") -> Optional[dict]:
    city_key = resolve_weather_city_key(series, contract_name=contract_name)
    if city_key is None:
        city_key = _SERIES_TO_CITY.get(series)
    if city_key is None:
        return None
    return STATIONS.get(city_key)


def _parse_contract_local_date(
    ticker: str,
    *,
    station: Optional[dict] = None,
    resolution_at: str = "",
    last_trade_at: str = "",
) -> Optional[date]:
    target_dt = _parse_contract_local_datetime(
        ticker,
        station=station,
        resolution_at=resolution_at,
        last_trade_at=last_trade_at,
    )
    return target_dt.date() if target_dt is not None else None


def _parse_contract_local_datetime(
    ticker: str,
    *,
    station: Optional[dict] = None,
    resolution_at: str = "",
    last_trade_at: str = "",
) -> Optional[datetime]:
    symbol = str(ticker or "").upper()
    tz_name = (station or {}).get("tz", "UTC")
    local_tz = pytz.timezone(tz_name)

    match = re.search(r"-(\d{2}[A-Z]{3}\d{2})(\d{2})?(?:-|$)", symbol)
    if match:
        parsed_date = None
        for fmt in ("%y%B%d", "%y%b%d"):
            try:
                parsed_date = datetime.strptime(match.group(1), fmt).date()
                break
            except ValueError:
                continue
        if parsed_date is not None:
            hour_token = match.group(2)
            hour_value = int(hour_token) if hour_token is not None else 0
            hour_value = max(0, min(23, hour_value))
            naive = datetime(
                parsed_date.year,
                parsed_date.month,
                parsed_date.day,
                hour_value,
                0,
                0,
            )
            return local_tz.localize(naive)

    for raw in (resolution_at, last_trade_at):
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            if "T" in text:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=pytz.UTC)
                return dt.astimezone(local_tz)
            if " " in text:
                dt = datetime.strptime(text, "%Y%m%d %H:%M:%S").replace(tzinfo=pytz.UTC)
                return dt.astimezone(local_tz)
            naive = datetime.strptime(text, "%Y%m%d")
            return local_tz.localize(naive)
        except Exception:
            continue
    return None


def _contract_has_explicit_local_hour(ticker: str) -> bool:
    return bool(re.search(r"-(\d{2}[A-Z]{3}\d{2})(\d{2})(?:-|$)", str(ticker or "").upper()))


def _parse_hourly_local_datetime(raw_time: str, timezone_name: str) -> datetime | None:
    text = str(raw_time or "").strip()
    if not text:
        return None

    local_tz = pytz.timezone(timezone_name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return local_tz.localize(parsed)
        return parsed.astimezone(local_tz)
    except Exception:
        pass

    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return local_tz.localize(datetime.strptime(text, fmt))
        except Exception:
            continue
    return None


def _daily_settlement_start_hour(target_date: date, timezone_name: str) -> int:
    local_tz = pytz.timezone(timezone_name)
    noon_local = local_tz.localize(
        datetime(target_date.year, target_date.month, target_date.day, 12, 0, 0)
    )
    return 1 if bool(noon_local.dst()) else 0


def _station_settlement_date(timezone_name: str) -> date:
    local_tz = pytz.timezone(timezone_name)
    now_local = datetime.now(local_tz)
    start_hour = _daily_settlement_start_hour(now_local.date(), timezone_name)
    if start_hour > 0 and now_local.hour < start_hour:
        return (now_local - timedelta(days=1)).date()
    return now_local.date()


def _target_day_indices(hourly_time: list[str], target_date) -> list[int]:
    indices = []
    target_label = target_date.isoformat()
    for idx, raw_time in enumerate(hourly_time or []):
        if str(raw_time).startswith(target_label):
            indices.append(idx)
    return indices


def _target_hour_indices(
    hourly_time: list[str],
    target_date,
    target_hour: int,
) -> list[int]:
    indices = []
    for idx, raw_time in enumerate(hourly_time or []):
        text = str(raw_time or "").strip()
        if not text:
            continue
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.date() == target_date and parsed.hour == target_hour:
                indices.append(idx)
            continue
        except Exception:
            pass

        if text.startswith(target_date.isoformat()):
            hour_match = re.search(r"T(\d{2})", text)
            if hour_match and int(hour_match.group(1)) == target_hour:
                indices.append(idx)
    return indices


def _reduce_member_projection(
    member_series: dict[str, list[float]],
    indices: list[int],
    reducer,
) -> list[float]:
    values: list[float] = []
    if not indices:
        return values
    for series in (member_series or {}).values():
        bucket = [series[idx] for idx in indices if idx < len(series)]
        if bucket:
            values.append(float(reducer(bucket)))
    return values


def _project_contract_record(
    record: dict,
    target_date,
    *,
    target_hour: int | None = None,
    timezone_name: str = "UTC",
) -> dict:
    if not record:
        return {}

    hourly_time = list(record.get("hourly_time") or [])
    hour_indices: list[int] = []
    if target_hour is None:
        local_tz = pytz.timezone(timezone_name)
        settlement_start_hour = _daily_settlement_start_hour(target_date, timezone_name)
        settlement_start = local_tz.localize(
            datetime(target_date.year, target_date.month, target_date.day, settlement_start_hour, 0, 0)
        )
        settlement_end = settlement_start + timedelta(days=1)
        indices = []
        for idx, raw_time in enumerate(hourly_time or []):
            parsed = _parse_hourly_local_datetime(raw_time, timezone_name)
            if parsed is None:
                continue
            if settlement_start <= parsed < settlement_end:
                indices.append(idx)
    else:
        hour_indices = _target_hour_indices(hourly_time, target_date, int(target_hour))
        indices = hour_indices
    if not indices:
        return {}

    members_temp = record.get("hourly_members_temp_f") or {}
    members_precip = record.get("hourly_members_precip_in") or {}
    members_cloud = record.get("hourly_members_cloud") or {}
    members_ssrd = record.get("hourly_members_ssrd") or {}
    members_wind = record.get("hourly_members_wind") or {}

    members_high = _reduce_member_projection(members_temp, indices, max)
    members_low = _reduce_member_projection(members_temp, indices, min)
    members_precip_total = _reduce_member_projection(members_precip, indices, sum)
    members_wind_max = _reduce_member_projection(members_wind, indices, max)
    midday_indices = [
        idx
        for idx, raw_time in enumerate(hourly_time or [])
        if (
            (parsed := _parse_hourly_local_datetime(raw_time, timezone_name)) is not None
            and parsed.date() == target_date
            and 11 <= parsed.hour <= 16
        )
    ]
    weather_window_indices = hour_indices or midday_indices or indices
    cloud_peaks = _reduce_member_projection(members_cloud, weather_window_indices, max)
    ssrd_means = _reduce_member_projection(members_ssrd, weather_window_indices, np.mean)
    members_temp_instant = _reduce_member_projection(
        members_temp,
        hour_indices or indices,
        lambda bucket: bucket[0],
    )

    projected = {
        "members_high": members_high,
        "members_low": members_low,
        "members_temp": members_temp_instant,
        "members_precip": members_precip_total,
        "members_wind": members_wind_max,
        "mean_high": float(np.mean(members_high)) if members_high else record.get("mean_high", 0.0),
        "sigma_high": float(np.std(members_high)) if len(members_high) > 1 else record.get("sigma_high", 0.5),
        "mean_low": float(np.mean(members_low)) if members_low else record.get("mean_low", 0.0),
        "sigma_low": float(np.std(members_low)) if len(members_low) > 1 else record.get("sigma_low", 0.5),
        "mean_temp": float(np.mean(members_temp_instant)) if members_temp_instant else None,
        "sigma_temp": float(np.std(members_temp_instant)) if len(members_temp_instant) > 1 else None,
        "mean_precip": float(np.mean(members_precip_total)) if members_precip_total else record.get("mean_precip", 0.0),
        "sigma_precip": float(np.std(members_precip_total)) if len(members_precip_total) > 1 else record.get("sigma_precip", 0.08),
        "peak_tcdc": float(np.mean(cloud_peaks)) if cloud_peaks else float(record.get("peak_tcdc") or 0.0),
        "peak_ssrd": float(np.mean(ssrd_means)) if ssrd_means else record.get("peak_ssrd"),
        "timestamp": record.get("timestamp", time.time()),
        "target_local_date": target_date.isoformat(),
        "target_local_hour": target_hour,
        "settlement_start_hour": _daily_settlement_start_hour(target_date, timezone_name),
        "hourly_time": hourly_time,
        "hourly_members_temp_f": members_temp,
        "hourly_members_precip_in": members_precip,
        "hourly_members_cloud": members_cloud,
        "hourly_members_ssrd": members_ssrd,
        "hourly_members_wind": members_wind,
        "provider_mode": record.get("provider_mode", "ensemble_members"),
        "forecast_source": record.get("forecast_source", ""),
        "model_name": record.get("model_name", ""),
    }

    if projected["provider_mode"] == "deterministic_multi_model":
        base_date = None
        if hourly_time:
            try:
                base_date = datetime.fromisoformat(str(hourly_time[0]).replace("Z", "+00:00")).date()
            except Exception:
                try:
                    base_date = datetime.strptime(str(hourly_time[0])[:10], "%Y-%m-%d").date()
                except Exception:
                    base_date = None
        horizon_days = max(0, (target_date - base_date).days) if base_date else 0
        model_key = _weather_model_key(projected.get("model_name", ""))
        projected["sigma_high"] = _deterministic_temp_sigma_floor(model_key, horizon_days=horizon_days)
        projected["sigma_low"] = _deterministic_temp_sigma_floor(model_key, horizon_days=horizon_days)
        if projected["mean_temp"] is not None:
            projected["sigma_temp"] = _deterministic_temp_sigma_floor(
                model_key,
                horizon_days=horizon_days,
            )
        projected["sigma_precip"] = _deterministic_precip_sigma(
            projected.get("mean_precip", 0.0),
            horizon_days=horizon_days,
        )
        projected["target_horizon_days"] = horizon_days

    nested_ecmwf = record.get("ecmwf")
    if nested_ecmwf:
        projected["ecmwf"] = _project_contract_record(
            nested_ecmwf,
            target_date,
            target_hour=target_hour,
            timezone_name=timezone_name,
        )
    else:
        projected["ecmwf"] = None

    nested_aigefs = record.get("aigefs")
    if nested_aigefs:
        projected["aigefs"] = _project_contract_record(
            nested_aigefs,
            target_date,
            target_hour=target_hour,
            timezone_name=timezone_name,
        )
    else:
        projected["aigefs"] = None

    return projected


def _watermark_storage_path() -> str:
    return _WATERMARKS_FILE


def _load_watermarks() -> dict[str, float]:
    path = _watermark_storage_path()
    if not path or not os.path.exists(path):
        return {}
    try:
        import json

        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        return {str(k): float(v) for k, v in dict(raw).items()}
    except Exception:
        return {}


def _persist_watermarks(watermarks: dict[str, float]) -> None:
    path = _watermark_storage_path()
    if not path:
        return
    try:
        import json

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(watermarks, handle, indent=2, sort_keys=True)
    except Exception as exc:
        logger.debug("Watermark persist failed: %s", exc)


def _station_local_day(city_key: str) -> str:
    station = STATIONS.get(city_key, {})
    tz_name = station.get("tz", "UTC")
    return _station_settlement_date(tz_name).isoformat()


def _parse_metar_observation_key(metar_raw: str) -> float | None:
    match = re.search(r"\b(\d{6})Z\b", str(metar_raw or ""))
    if not match:
        return None
    try:
        return float(match.group(1))
    except Exception:
        return None


def _parse_metar_hourly_precip_inches(metar_raw: str) -> float | None:
    match = re.search(r"\bP(\d{4})\b", str(metar_raw or ""))
    if not match:
        return None
    try:
        return int(match.group(1)) / 100.0
    except Exception:
        return None


def _cached_observed_daily_record(cache_key: str) -> Dict[str, Any]:
    cached = _OBSERVED_DAILY_CACHE.get(cache_key)
    if not cached:
        return {}
    ts = float(cached.get("cached_at") or 0.0)
    if time.time() - ts > OBSERVED_DAILY_CACHE_TTL_SEC:
        return {}
    return dict(cached)


def _cached_observed_hourly_record(cache_key: str) -> Dict[str, Any]:
    cached = _OBSERVED_HOURLY_CACHE.get(cache_key)
    if not cached:
        return {}
    ts = float(cached.get("cached_at") or 0.0)
    if time.time() - ts > OBSERVED_DAILY_CACHE_TTL_SEC:
        return {}
    return dict(cached)


def _fetch_open_meteo_archive_daily_summary(
    city_key: str,
    lat: float,
    lon: float,
    target_date: date,
    *,
    timezone_name: str,
) -> Dict[str, Any]:
    cache_key = f"{city_key}|{target_date.isoformat()}"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": target_date.isoformat(),
        "end_date": target_date.isoformat(),
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone": timezone_name,
    }

    try:
        resp = requests.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        daily = payload.get("daily") or {}
        times = list(daily.get("time") or [])
        if not times:
            return {}

        observed = {
            "city_key": city_key,
            "target_local_date": target_date.isoformat(),
            "observed_high": (
                _c_to_f(float(daily["temperature_2m_max"][0]))
                if (daily.get("temperature_2m_max") or [None])[0] is not None
                else None
            ),
            "observed_low": (
                _c_to_f(float(daily["temperature_2m_min"][0]))
                if (daily.get("temperature_2m_min") or [None])[0] is not None
                else None
            ),
            "observed_precip": (
                _mm_to_inches(float(daily["precipitation_sum"][0]))
                if (daily.get("precipitation_sum") or [None])[0] is not None
                else None
            ),
            "source": "open_meteo_archive_daily",
            "cached_at": time.time(),
        }
        _OBSERVED_DAILY_CACHE[cache_key] = observed
        return dict(observed)
    except Exception as exc:
        logger.debug(
            "Observed daily fetch failed for %s %s: %s",
            city_key,
            target_date.isoformat(),
            exc,
        )
        return {}


def _fetch_open_meteo_archive_hourly_temp(
    city_key: str,
    lat: float,
    lon: float,
    target_date: date,
    target_hour: int,
    *,
    timezone_name: str,
) -> Dict[str, Any]:
    cache_key = f"{city_key}|{target_date.isoformat()}|{int(target_hour):02d}"
    cached = _cached_observed_hourly_record(cache_key)
    if cached:
        return cached

    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": target_date.isoformat(),
        "end_date": target_date.isoformat(),
        "hourly": "temperature_2m",
        "timezone": timezone_name,
    }

    try:
        resp = requests.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        hourly = payload.get("hourly") or {}
        times = list(hourly.get("time") or [])
        temps = list(hourly.get("temperature_2m") or [])
        for idx, raw_time in enumerate(times):
            try:
                parsed = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
            except Exception:
                continue
            if parsed.date() != target_date or parsed.hour != int(target_hour):
                continue
            observed = {
                "city_key": city_key,
                "target_local_date": target_date.isoformat(),
                "target_local_hour": int(target_hour),
                "observed_temp": (
                    _c_to_f(float(temps[idx]))
                    if idx < len(temps) and temps[idx] is not None
                    else None
                ),
                "source": "open_meteo_archive_hourly",
                "cached_at": time.time(),
            }
            _OBSERVED_HOURLY_CACHE[cache_key] = observed
            return dict(observed)
    except Exception as exc:
        logger.debug(
            "Observed hourly fetch failed for %s %s@%02d: %s",
            city_key,
            target_date.isoformat(),
            int(target_hour),
            exc,
        )
    return {}


def _fetch_observed_daily_summary(
    city_key: str,
    lat: float,
    lon: float,
    target_date: date,
    *,
    timezone_name: str,
    station: dict | None = None,
) -> Dict[str, Any]:
    cache_key = f"{city_key}|{target_date.isoformat()}"
    cached = _cached_observed_daily_record(cache_key)
    if cached:
        return cached

    cli_observed = _fetch_nws_cli_daily_summary(city_key, station or {}, target_date)
    if cli_observed:
        _OBSERVED_DAILY_CACHE[cache_key] = cli_observed
        return dict(cli_observed)

    return _fetch_open_meteo_archive_daily_summary(
        city_key,
        lat,
        lon,
        target_date,
        timezone_name=timezone_name,
    )


def _intraday_payload(
    city_key: str,
    metar: Dict[str, Any],
    hrrr: Dict[str, Any],
    *,
    watermarks: Optional[dict[str, float]] = None,
) -> Dict[str, Any]:
    cur_temp = metar.get("temp_f")
    daily_max = cur_temp
    daily_min = cur_temp
    daily_precip = None

    if watermarks is not None:
        today_str = _station_local_day(city_key)
        max_key = f"{city_key}|{today_str}|max"
        min_key = f"{city_key}|{today_str}|min"
        precip_total_key = f"{city_key}|{today_str}|precip_total"
        precip_obs_key = f"{city_key}|{today_str}|precip_obs_key"
        if cur_temp is not None:
            watermarks[max_key] = max(cur_temp, watermarks.get(max_key, cur_temp))
            watermarks[min_key] = min(cur_temp, watermarks.get(min_key, cur_temp))
        daily_max = watermarks.get(max_key, cur_temp)
        daily_min = watermarks.get(min_key, cur_temp)

        metar_raw = str(metar.get("raw") or "")
        obs_key = _parse_metar_observation_key(metar_raw)
        hourly_precip = _parse_metar_hourly_precip_inches(metar_raw)
        last_obs_key = watermarks.get(precip_obs_key, -1.0)
        if (
            obs_key is not None
            and hourly_precip is not None
            and float(obs_key) > float(last_obs_key)
        ):
            watermarks[precip_total_key] = round(
                float(watermarks.get(precip_total_key, 0.0)) + float(hourly_precip),
                4,
            )
            watermarks[precip_obs_key] = float(obs_key)
        if metar_raw:
            daily_precip = float(watermarks.get(precip_total_key, 0.0))

    return {
        "city_key": city_key,
        "metar_temp": cur_temp,
        "daily_max": daily_max,
        "daily_min": daily_min,
        "daily_precip": daily_precip,
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
    cached = _cached_ensemble_record(cache_key)
    if cached:
        return cached
    if _global_ensemble_rate_limit_active():
        _emit_provider_notice_once(
            "deterministic_due_to_quota",
            "Weather provider running on deterministic fallback because Open-Meteo ensemble quota is exhausted.",
        )
        fallback = await _fetch_open_meteo_deterministic_multimodel(city_key, lat, lon)
        if fallback:
            _COORDINATE_CACHE[cache_key] = fallback
        return fallback

    slot_status = _claim_ensemble_fetch_slot(cache_key)
    if slot_status == "cooldown":
        return {}
    if slot_status == "wait":
        return await _await_inflight_ensemble(cache_key)

    import os
    api_key = os.getenv("OPEN_METEO_API_KEY")
    if not api_key:
        _emit_provider_notice_once(
            "deterministic_due_to_missing_key",
            "OPEN_METEO_API_KEY absent; weather provider running on deterministic GFS/ECMWF/GraphCast fallback.",
        )
        try:
            fallback = await _fetch_open_meteo_deterministic_multimodel(city_key, lat, lon)
            if fallback:
                _COORDINATE_CACHE[cache_key] = fallback
            return fallback
        finally:
            _release_ensemble_fetch_slot(cache_key)

    base_url = "https://customer-api.open-meteo.com/v1/ensemble"
    
    # v19.2: Sovereign Grand Ensemble (Institutional Blend)
    # GFS = 31, ECMWF = 51, GRAPHCAST (AI) = 1
    # Note: GraphCast is deterministic but highly accurate in the 24-48h window.
    models = ["gfs_seamless", "ecmwf_ifs025", "gfs_graphcast025"]
    results = {}

    try:
        for idx, model in enumerate(models):
            if idx and WEATHER_ENSEMBLE_MODEL_PAUSE_SEC > 0:
                await asyncio.sleep(WEATHER_ENSEMBLE_MODEL_PAUSE_SEC)

            params = {
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m,cloud_cover,precipitation,shortwave_radiation",
                "models": model,
                "timezone": "auto",
                "forecast_days": 8,
            }
            if api_key:
                params["apikey"] = api_key

            try:
                loop = asyncio.get_event_loop()
                resp = await loop.run_in_executor(
                    None,
                    lambda: requests.get(base_url, params=params, timeout=15),
                )

                if resp.status_code == 429:
                    response_text = (resp.text or "").strip()
                    if "Daily API request limit exceeded" in response_text:
                        if _activate_global_ensemble_rate_limit(reason=response_text):
                            logger.warning(
                                "Open-Meteo daily ensemble limit exhausted; pausing all ensemble fetches until tomorrow UTC."
                            )
                            try:
                                from logging_db.trade_logger import log_event

                                log_event(
                                    "WARNING",
                                    "WeatherMonitor",
                                    "Open-Meteo daily ensemble limit exhausted; pausing all ensemble fetches until tomorrow UTC.",
                                )
                            except Exception:
                                pass
                        fallback = await _fetch_open_meteo_deterministic_multimodel(city_key, lat, lon)
                        if fallback:
                            _COORDINATE_CACHE[cache_key] = fallback
                        return fallback
                    should_log = _enter_ensemble_cooldown(
                        cache_key,
                        city_key=city_key,
                        model=model,
                    )
                    if should_log:
                        logger.warning(
                            "Open-Meteo 429 for %s [%s]; cooling city for %ss.",
                            city_key,
                            model,
                            WEATHER_ENSEMBLE_COOLDOWN_SEC,
                        )
                        try:
                            from logging_db.trade_logger import log_event

                            log_event(
                                "WARNING",
                                "WeatherMonitor",
                                (
                                    f"Open-Meteo 429 (Rate Limit) for {city_key} [{model}] "
                                    f"cooldown={WEATHER_ENSEMBLE_COOLDOWN_SEC}s"
                                ),
                            )
                        except Exception:
                            pass
                    fallback = await _fetch_open_meteo_deterministic_multimodel(city_key, lat, lon)
                    if fallback:
                        _COORDINATE_CACHE[cache_key] = fallback
                    return fallback

                if resp.status_code != 200:
                    continue

                data = resp.json()
                record = _build_weather_record_from_hourly(
                    data.get("hourly", {}),
                    model,
                    deterministic=False,
                    forecast_source="open_meteo_ensemble",
                )
                if record:
                    results[_weather_model_key(model)] = record
            except Exception as e:
                logger.debug(f"Fetch failed for {city_key} {model}: {e}")

        if not results:
            fallback = await _fetch_open_meteo_deterministic_multimodel(city_key, lat, lon)
            if fallback:
                _COORDINATE_CACHE[cache_key] = fallback
            return fallback

        # Unified City Record
        final_record = results.get("gfs", list(results.values())[0]).copy()
        final_record["ecmwf"] = results.get("ecmwf")
        final_record["aigefs"] = results.get("aigefs")
        final_record["provider_mode"] = "ensemble_members"
        final_record["forecast_source"] = "open_meteo_ensemble"

        # Update cache
        _COORDINATE_CACHE[cache_key] = final_record
        return final_record
    finally:
        _release_ensemble_fetch_slot(cache_key)

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
    concurrency: int = 1,
) -> Dict[str, Any]:
    """Refresh the weather shadow state once for selected series or the whole universe."""
    watermarks = _load_watermarks() if include_intraday else None

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
                intraday_payload = _intraday_payload(
                    city_key,
                    metar,
                    hrrr,
                    watermarks=watermarks,
                )

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
    if include_intraday and watermarks is not None:
        _persist_watermarks(watermarks)
    if updated_series > 0:
        _persist_weather_snapshot()
    logger.info("Weather one-shot hydration summary: %s", summary)
    return summary


def ensure_weather_data(
    tickers_or_series: list[str],
    *,
    include_intraday: bool = True,
    max_age_sec: int = WEATHER_REFRESH_TARGET_SEC,
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

    snapshot_restore = _load_weather_snapshot(force=not bool(_WEATHER_SHADOW_STATE))
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
            "snapshot_loaded": int(snapshot_restore.get("loaded_series") or 0),
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
        "snapshot_loaded": int(snapshot_restore.get("loaded_series") or 0),
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
                city_keys = _active_weather_city_keys()
                if not city_keys:
                    logger.info("Weather Ensemble sync skipped: no active weather cities.")
                    await asyncio.sleep(900)
                    continue
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
                    _persist_weather_snapshot()
                    logger.info(
                        "Weather Ensemble synced: %s series across %s active cities",
                        len(new_state),
                        len(city_keys),
                    )
            except Exception as e:
                logger.error(f"Ensemble sync failure: {e}")
            await asyncio.sleep(WEATHER_REFRESH_TARGET_SEC)

    # ── Cycle 2: Fast Intraday Precinct (15 Minutes) ───────────────────────
    async def run_intraday_sync():
        # v19.8: Day-High/Low Watermarks
        # Key: (city_key, YYYY-MM-DD) -> float
        watermarks = _load_watermarks()
        
        while True:
            try:
                # v19.1.10: Precision Ground Truth (METAR + HRRR)
                city_keys = _active_weather_city_keys()
                if not city_keys:
                    logger.info("Weather Intraday sync skipped: no active weather cities.")
                    await asyncio.sleep(900)
                    continue

                for city_key in city_keys:
                    loc = STATIONS[city_key]
                    metar = await fetch_metar_observation(loc["icao"])
                    hrrr = await fetch_hrrr_forecast(city_key, loc["lat"], loc["lon"])
                    intraday_payload = _intraday_payload(
                        city_key,
                        metar,
                        hrrr,
                        watermarks=watermarks,
                    )
                    
                    for s_ticker in loc.get("series", []):
                        if s_ticker in _WEATHER_SHADOW_STATE:
                            with _STATE_LOCK:
                                _WEATHER_SHADOW_STATE[s_ticker]["intraday"] = intraday_payload
                _persist_watermarks(watermarks)
                _persist_weather_snapshot()
                
                logger.info(
                    "Weather Intraday Precinct synced (METAR/HRRR/Watermarks) for %s active cities.",
                    len(city_keys),
                )
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
    if not _series_record_is_fresh(data):
        _load_weather_snapshot(force=not bool(data))
        data = _WEATHER_SHADOW_STATE.get(series)
    if _series_record_is_fresh(data):
        return data
    
    # Fallback pattern matching
    for series_list in [loc.get("series", []) for loc in STATIONS.values()]:
        for s in series_list:
            if str(ticker_prefix).upper().startswith(s):
                data = _WEATHER_SHADOW_STATE.get(s)
                if not _series_record_is_fresh(data):
                    _load_weather_snapshot(force=not bool(data))
                    data = _WEATHER_SHADOW_STATE.get(s)
                if _series_record_is_fresh(data):
                    return data
    return {}


def get_contract_weather_data(
    ticker: str,
    *,
    contract_name: str = "",
    strike: float | None = None,
    resolution_at: str = "",
    last_trade_at: str = "",
) -> Dict[str, Any]:
    """Project cached weather state onto the contract's local settlement day."""
    city_key = resolve_weather_city_key(ticker, contract_name=contract_name)
    series = _canonical_series_for_city(city_key) if city_key else (_resolve_weather_series(ticker) or ticker)
    base = get_weather_data(series)
    if not base:
        return {}

    station = STATIONS.get(city_key) if city_key else _station_for_series(series, contract_name=contract_name)
    if station is None:
        return base

    target_date = _parse_contract_local_date(
        ticker,
        station=station,
        resolution_at=resolution_at,
        last_trade_at=last_trade_at,
    )
    if target_date is None:
        return base
    target_hour = None
    if _contract_has_explicit_local_hour(ticker):
        target_dt = _parse_contract_local_datetime(
            ticker,
            station=station,
            resolution_at=resolution_at,
            last_trade_at=last_trade_at,
        )
        target_hour = target_dt.hour if target_dt is not None else None

    projected = _project_contract_record(
        base,
        target_date,
        target_hour=target_hour,
        timezone_name=station.get("tz", "UTC"),
    )
    if not projected:
        return {}

    local_today = _station_settlement_date(station.get("tz", "UTC"))
    if target_date == local_today:
        projected["intraday"] = dict(base.get("intraday") or {})
    else:
        projected["intraday"] = {}

    projected["series"] = series
    projected["station_tz"] = station.get("tz", "UTC")
    projected["contract_name"] = contract_name
    projected["strike"] = strike
    return projected


def get_contract_observed_weather_data(
    ticker: str,
    *,
    contract_name: str = "",
    strike: float | None = None,
    resolution_at: str = "",
    last_trade_at: str = "",
) -> Dict[str, Any]:
    """Return contract-date observed truth for resolution ingestion."""
    city_key = resolve_weather_city_key(ticker, contract_name=contract_name)
    series = _canonical_series_for_city(city_key) if city_key else (_resolve_weather_series(ticker) or ticker)
    base = get_weather_data(series)
    station = STATIONS.get(city_key) if city_key else _station_for_series(series, contract_name=contract_name)
    if station is None:
        return {}

    target_date = _parse_contract_local_date(
        ticker,
        station=station,
        resolution_at=resolution_at,
        last_trade_at=last_trade_at,
    )
    if target_date is None:
        return {}
    target_hour = None
    if _contract_has_explicit_local_hour(ticker):
        target_dt = _parse_contract_local_datetime(
            ticker,
            station=station,
            resolution_at=resolution_at,
            last_trade_at=last_trade_at,
        )
        target_hour = target_dt.hour if target_dt is not None else None

    timezone_name = station.get("tz", "UTC")
    local_today = _station_settlement_date(timezone_name)
    city_key = city_key or _SERIES_TO_CITY.get(series, "")
    if target_hour is not None:
        if target_date > local_today:
            return {}

        if target_date == local_today:
            intraday = dict(base.get("intraday") or {})
            now_local = datetime.now(pytz.timezone(timezone_name))
            if (
                intraday.get("metar_temp") is not None
                and now_local.hour == target_hour
            ):
                return {
                    "city_key": city_key,
                    "target_local_date": target_date.isoformat(),
                    "target_local_hour": target_hour,
                    "observed_temp": intraday.get("metar_temp"),
                    "source": "metar_hourly",
                }

        return _fetch_open_meteo_archive_hourly_temp(
            city_key,
            float(station["lat"]),
            float(station["lon"]),
            target_date,
            target_hour,
            timezone_name=timezone_name,
        )

    if target_date == local_today:
        intraday = dict(base.get("intraday") or {})
        if not intraday:
            return {}
        return {
            "city_key": city_key,
            "target_local_date": target_date.isoformat(),
            "observed_high": intraday.get("daily_max"),
            "observed_low": intraday.get("daily_min"),
            "observed_precip": intraday.get("daily_precip"),
            "source": "metar_watermark",
        }

    if target_date > local_today:
        return {}

    return _fetch_observed_daily_summary(
        city_key,
        float(station["lat"]),
        float(station["lon"]),
        target_date,
        timezone_name=timezone_name,
        station=station,
    )

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
