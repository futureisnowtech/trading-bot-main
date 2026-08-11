"""
config.py — Single source of truth. All values from .env.
Never hardcode anything that belongs here.
"""

import os
import math
from decimal import Decimal, ROUND_CEILING
from datetime import time as dt_time
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv(dotenv_path: str | None = None) -> bool:
        """Minimal .env loader fallback for audit scripts on hosts without python-dotenv."""
        path = dotenv_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), ".env"
        )
        if not os.path.exists(path):
            return False
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                os.environ.setdefault(key, value)
        return True


load_dotenv()

# v19.1.12: Canonical Repository Root
_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = _ROOT_DIR

# Post-Paper Live Trading Era Invariant Boundary
POST_PAPER_START_DATE = "2026-07-24"


def _resolve_runtime_root() -> str:
    raw_value = os.getenv("ALGO_RUNTIME_DIR", "").strip()
    if not raw_value:
        return os.path.join(REPO_ROOT, "logs")

    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = Path(REPO_ROOT) / path
    return str(path)


RUNTIME_ROOT: str = _resolve_runtime_root()


def resolve_runtime_path(raw_path: str, *fallbacks: str) -> str:
    """Resolve a runtime path across host and container environments."""
    raw_value = (raw_path or "").strip()
    candidates: list[Path] = []

    if raw_value:
        expanded = Path(raw_value).expanduser()
        candidates.append(expanded)
        if expanded.is_absolute():
            candidates.append(Path(REPO_ROOT) / expanded.name)
        else:
            candidates.append(Path(REPO_ROOT) / expanded)

    for fallback in fallbacks:
        if fallback:
            candidates.append(Path(fallback).expanduser())

    seen: set[str] = set()
    for candidate in candidates:
        candidate_str = str(candidate)
        if candidate_str in seen:
            continue
        seen.add(candidate_str)
        if candidate.exists():
            return candidate_str

    return raw_value


def get_kalshi_private_key_path() -> str:
    return resolve_runtime_path(
        os.getenv("KALSHI_PRIVATE_KEY_PATH", "").strip(),
        "/run/secrets/kalshi_private_key.pem",
        os.path.join(REPO_ROOT, "kalshi_private_key.pem"),
    )


def _resolve_runtime_child(env_key: str, default_name: str) -> str:
    raw_value = os.getenv(env_key, "").strip()
    if not raw_value:
        return str(Path(RUNTIME_ROOT) / default_name)

    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = Path(RUNTIME_ROOT) / path
    return str(path)

# ════════════════════════════════════════════════════════════════════
# SYSTEM MODE
# ════════════════════════════════════════════════════════════════════
# v19.18: Paper lanes A and B retired. There is one live lane; the two ideas the
# paper lanes existed to test are now flags on that single path (see LANE FEATURES).
SHADOW_EXECUTION: bool = os.getenv("SHADOW_EXECUTION", "false").lower() == "true"

# ════════════════════════════════════════════════════════════════════
# LANE FEATURES
# ════════════════════════════════════════════════════════════════════
# Continuous physics delta overlay (formerly paper Lane A): correct model
# temperature for precip and wind before pricing. Rain caps the daytime high;
# wind mixes the boundary layer and lifts the overnight low.
PHYSICS_DELTA_ENABLED: bool = os.getenv("PHYSICS_DELTA_ENABLED", "true").lower() == "true"

# Maker entry (formerly paper Lane B): rest at the bid instead of crossing the
# ask. Maker fees are ~4x cheaper than taker and the spread is saved. Ships off;
# enable only after the physics-delta deploy is verified. If an order does not
# fill within the timeout it is cancelled and re-crossed, so a thin book degrades
# to today's taker behavior rather than silently halting entries.
MAKER_ENTRY_ENABLED: bool = os.getenv("MAKER_ENTRY_ENABLED", "true").lower() == "true"
MAKER_ENTRY_TIMEOUT_S: int = int(os.getenv("MAKER_ENTRY_TIMEOUT_S", "90"))


def get_dynamic_bool(key: str, default: bool) -> bool:
    """Boolean override from dynamic_system_config, set via update_system_parameter.

    forecast.strategy_engine.get_dynamic_param coerces non-string defaults with
    int(val)/float(val); since bool is a subclass of int, a bool default routes into
    int("True") and raises, so the override is silently swallowed by that function's
    try/except and never applies. Booleans get their own reader instead.
    """
    try:
        if os.path.exists(DB_PATH):
            import sqlite3

            conn = sqlite3.connect(DB_PATH, timeout=5.0)
            row = conn.execute(
                "SELECT param_value FROM dynamic_system_config WHERE param_key = ?",
                (key.upper(),),
            ).fetchone()
            conn.close()
            if row and row[0] is not None:
                return str(row[0]).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        pass
    return default

# Session start: all performance stats (win rate, P&L, trade counts) are
# measured from this date forward.
TRADE_SESSION_START: str = os.getenv("TRADE_SESSION_START", "2026-07-24")

# ════════════════════════════════════════════════════════════════════
# ACCOUNT
# ════════════════════════════════════════════════════════════════════
ACCOUNT_SIZE: float = float(os.getenv("ACCOUNT_SIZE", "100"))
MAX_DEPLOYED_PCT: float = 1.0
CASH_RESERVE_PCT: float = 0.0

# ════════════════════════════════════════════════════════════════════
# AI & INTELLIGENCE
# ════════════════════════════════════════════════════════════════════
GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

# AI Exit Settings
PM_LLM_TEMPERATURE: float = float(os.getenv("PM_LLM_TEMPERATURE", "0.3"))
PM_LLM_MAX_TOKENS: int = int(os.getenv("PM_LLM_MAX_TOKENS", "600"))
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# ════════════════════════════════════════════════════════════════════
# KALSHI (Weather Prediction Engine)
# ════════════════════════════════════════════════════════════════════
KALSHI_API_KEY_ID: str = os.getenv("KALSHI_API_KEY_ID", "").strip()
KALSHI_PRIVATE_KEY_PATH: str = get_kalshi_private_key_path()
KALSHI_ENABLED: bool = os.getenv("KALSHI_ENABLED", "true").lower() == "true"
FORECAST_LANE_ACTIVE: bool = (
    os.getenv("FORECAST_LANE_ACTIVE", "false").lower() == "true"
)
FORECAST_DASHBOARD_VISIBLE: bool = (
    os.getenv("FORECAST_DASHBOARD_VISIBLE", "true").lower() == "true"
)
FORECAST_AUTONOMOUS_ENABLED: bool = (
    os.getenv("FORECAST_AUTONOMOUS_ENABLED", "false").lower() == "true"
)
FORECAST_MANUAL_ENABLED: bool = (
    os.getenv("FORECAST_MANUAL_ENABLED", "true").lower() == "true"
)

# Kalshi Risk & Capital Partitioning
KALSHI_MAX_DEPLOYED_PCT: float = float(os.getenv("KALSHI_MAX_DEPLOYED_PCT", "0.90"))
KALSHI_MAX_CONCURRENT_POSITIONS: int = int(os.getenv("KALSHI_MAX_CONCURRENT_POSITIONS", "50"))
KALSHI_SAME_EVENT_FAMILY_CAP: int = int(os.getenv("KALSHI_SAME_EVENT_FAMILY_CAP", "5"))
KALSHI_HUB_EXPOSURE_PCT: float = float(
    # Default tracks the live .env so no environment can silently prove a
    # different risk posture than the one that trades.
    #
    # Operator-set to 1.20 on 2026-08-11 (raised from 0.30). Above ~0.90 this
    # ceiling sits over KALSHI_MAX_DEPLOYED_PCT, so the regional concentration
    # limit no longer binds: a single weather hub may hold everything the
    # engine is allowed to deploy. Per-position and total-deployment rails
    # (KALSHI_MAX_USD_PER_POSITION, KALSHI_MAX_DEPLOYED_PCT) still apply.
    os.getenv("KALSHI_HUB_EXPOSURE_PCT", "1.20")
)
KALSHI_HUB_EXPOSURE_MIN_USD: float = float(
    os.getenv("KALSHI_HUB_EXPOSURE_MIN_USD", "40")
)
# Sovereign Salvage Delta: purge a position when its model probability falls
# below this. Constant by contract -- see research_package/03_parameter_catalog.md
# (marked CONFIRMED) and 02_strategy_catalog.md section 4.
SALVAGE_EXIT_DELTA: float = float(os.getenv("SALVAGE_EXIT_DELTA", "0.15"))
KALSHI_MAX_QTY_PER_POSITION: int = int(os.getenv("KALSHI_MAX_QTY_PER_POSITION", "2500"))
KALSHI_MAX_USD_PER_POSITION: float = float(os.getenv("KALSHI_MAX_USD_PER_POSITION", "40.0"))  # Hard Ceiling
KALSHI_MIN_PRICE: float = 0.08
KALSHI_MAX_SIGMA: float = 2.8
KALSHI_MAX_SPREAD_RATIO: float = 0.35
KALSHI_DATA_FRESHNESS_MINUTES_HOURLY: int = 25   # SPEC §4.5
KALSHI_DATA_FRESHNESS_MINUTES_DAILY:  int = 90   # SPEC §4.5
KALSHI_DATA_FRESHNESS_MINUTES: int = 90  # Legacy fallback
KALSHI_TAKER_FEE_RATE: float = float(os.getenv("KALSHI_TAKER_FEE_RATE", "0.07"))
KALSHI_MAKER_FEE_RATE: float = float(os.getenv("KALSHI_MAKER_FEE_RATE", "0.0175"))
KALSHI_FEE_PER_CONTRACT: float = float(
    os.getenv("KALSHI_FEE_PER_CONTRACT", str(KALSHI_TAKER_FEE_RATE))
)  # Legacy fallback only
KALSHI_MAX_FEE_DRAG_PCT: float = 0.30
KALSHI_MAX_SPREAD_DOLLARS: float = 0.12  # SPEC §5.4c — quote coherence gate (dollars, not ratio)
KALSHI_MIN_ENTRY_PRICE: float = float(os.getenv("KALSHI_MIN_ENTRY_PRICE", "0.30"))     # SPEC §2.6 — hard entry price floor; deletes 0.02/0.03 carve-outs
KALSHI_KELLY_CAP: float = float(os.getenv("KALSHI_KELLY_CAP", "0.10"))
KALSHI_KELLY_FRACTION: float = float(os.getenv("KALSHI_KELLY_FRACTION", "0.25"))
KALSHI_MAX_RISK_PER_EVENT_PCT: float = float(os.getenv("KALSHI_MAX_RISK_PER_EVENT_PCT", "0.015"))


KALSHI_EXIT_MODEL_INVALIDATION_DELTA: float = float(
    os.getenv("KALSHI_EXIT_MODEL_INVALIDATION_DELTA", "0.10")
)
KALSHI_EXIT_REDEPLOY_EDGE: float = float(
    os.getenv("KALSHI_EXIT_REDEPLOY_EDGE", "0.03")
)
KALSHI_EXIT_TIME_DECAY_HOURS: float = float(
    os.getenv("KALSHI_EXIT_TIME_DECAY_HOURS", "24")
)
KALSHI_EXIT_TIME_DECAY_BID_FLOOR: float = float(
    os.getenv("KALSHI_EXIT_TIME_DECAY_BID_FLOOR", "0.70")
)
KALSHI_HOURLY_EXIT_INVALIDATION_HOURS: float = float(
    os.getenv("KALSHI_HOURLY_EXIT_INVALIDATION_HOURS", "1.50")
)
KALSHI_HOURLY_EXIT_REDEPLOY_HOURS: float = float(
    os.getenv("KALSHI_HOURLY_EXIT_REDEPLOY_HOURS", "0.75")
)
KALSHI_HOURLY_EXIT_BID_FLOOR: float = float(
    os.getenv("KALSHI_HOURLY_EXIT_BID_FLOOR", "0.05")
)
KALSHI_HOURLY_EXIT_REDEPLOY_EDGE: float = float(
    os.getenv("KALSHI_HOURLY_EXIT_REDEPLOY_EDGE", "0.00")
)
KALSHI_EXPENSIVE_YES_THRESHOLD: float = float(
    os.getenv("KALSHI_EXPENSIVE_YES_THRESHOLD", "0.70")
)
KALSHI_EXPENSIVE_YES_MIN_NET_EDGE: float = float(
    os.getenv("KALSHI_EXPENSIVE_YES_MIN_NET_EDGE", "0.01")
)
KALSHI_EXPENSIVE_YES_SIZE_MULTIPLIER: float = float(
    os.getenv("KALSHI_EXPENSIVE_YES_SIZE_MULTIPLIER", "1.00")
)
WEATHER_ACTIVE_CITY_REFRESH_SEC: int = int(
    os.getenv("WEATHER_ACTIVE_CITY_REFRESH_SEC", "300")
)
WEATHER_ENSEMBLE_COOLDOWN_SEC: int = int(
    os.getenv("WEATHER_ENSEMBLE_COOLDOWN_SEC", "1200")
)
WEATHER_ENSEMBLE_MODEL_PAUSE_SEC: float = float(
    os.getenv("WEATHER_ENSEMBLE_MODEL_PAUSE_SEC", "0.75")
)

# ════════════════════════════════════════════════════════════════════
# TELEGRAM (Mobile HUD)
# ════════════════════════════════════════════════════════════════════
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_POLLING_HOSTNAME: str = os.getenv("TELEGRAM_POLLING_HOSTNAME", "kalshi-weather-bot")

# ════════════════════════════════════════════════════════════════════
# MONITORING & INCIDENT MANAGEMENT
# ════════════════════════════════════════════════════════════════════
GRAFANA_INCIDENT_ENABLED: bool = os.getenv("GRAFANA_INCIDENT_ENABLED", "false").lower() == "true"
GRAFANA_URL: str = os.getenv("GRAFANA_URL", "").strip()
GRAFANA_TOKEN: str = os.getenv("GRAFANA_TOKEN", "").strip()
GRAFANA_SERVICE_ACCOUNT_ID: str = os.getenv("GRAFANA_SERVICE_ACCOUNT_ID", "").strip()
UPTIME_PING_URL: str = os.getenv("UPTIME_PING_URL", "")

# ════════════════════════════════════════════════════════════════════
# DATABASE & LOGGING
# ════════════════════════════════════════════════════════════════════
DB_USE_POSTGRES: bool = os.getenv("DB_USE_POSTGRES", "false").lower() == "true"
DB_PATH: str = _resolve_runtime_child("DB_PATH", "trades.db")
CSV_LOG_DIR: str = _resolve_runtime_child("CSV_LOG_DIR", "csv")
BOT_LOG_PATH: str = _resolve_runtime_child("BOT_LOG_PATH", "bot.log")
FORECAST_LOG_PATH: str = _resolve_runtime_child("FORECAST_LOG_PATH", "forecast.log")
WEATHER_CACHE_ROOT: str = os.getenv("WEATHER_CACHE_ROOT", RUNTIME_ROOT)
MACRO_CACHE_FILE: str = _resolve_runtime_child(
    "MACRO_CACHE_FILE", "cached_macro_regime.json"
)
MIN_FREE_DISK_MB: int = int(os.getenv("MIN_FREE_DISK_MB", "2048"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
MARKET_TIMEZONE: str = "America/New_York"


def get_kalshi_hub_exposure_cap(balance_usd: float) -> float:
    try:
        balance = float(balance_usd)
    except (TypeError, ValueError):
        balance = 0.0
    return max(KALSHI_HUB_EXPOSURE_MIN_USD, balance * KALSHI_HUB_EXPOSURE_PCT)


def get_kalshi_fee_rate(*, maker: bool = False, fee_rate: float | None = None) -> float:
    if fee_rate is not None:
        try:
            return max(0.0, float(fee_rate))
        except (TypeError, ValueError):
            return max(0.0, float(KALSHI_TAKER_FEE_RATE))
    return max(0.0, float(KALSHI_MAKER_FEE_RATE if maker else KALSHI_TAKER_FEE_RATE))


def _normalize_kalshi_price(price: float) -> float:
    try:
        return max(0.0, min(1.0, float(price)))
    except (TypeError, ValueError):
        return 0.0


def kalshi_raw_fee_per_contract(
    price: float,
    *,
    maker: bool = False,
    fee_rate: float | None = None,
) -> float:
    normalized_price = _normalize_kalshi_price(price)
    if normalized_price <= 0.0 or normalized_price >= 1.0:
        return 0.0

    rate = get_kalshi_fee_rate(maker=maker, fee_rate=fee_rate)
    return rate * normalized_price * (1.0 - normalized_price)


def estimate_kalshi_order_fee_usd(
    qty: float,
    price: float,
    *,
    maker: bool = False,
    fee_rate: float | None = None,
    round_up_cents: bool = True,
) -> float:
    try:
        contracts = max(0.0, float(qty))
    except (TypeError, ValueError):
        contracts = 0.0
    if contracts <= 0.0:
        return 0.0

    raw_total = contracts * kalshi_raw_fee_per_contract(
        price,
        maker=maker,
        fee_rate=fee_rate,
    )
    if raw_total <= 0.0:
        return 0.0
    if not round_up_cents:
        return raw_total
    rounded = (
        Decimal(str(raw_total))
        .quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    )
    return float(rounded)


def estimate_kalshi_fee_per_contract(
    price: float,
    *,
    qty: float = 1.0,
    maker: bool = False,
    fee_rate: float | None = None,
    round_up_cents: bool = True,
    rounded: bool | None = None,
) -> float:
    if rounded is not None:
        round_up_cents = bool(rounded)
    try:
        contracts = max(0.0, float(qty))
    except (TypeError, ValueError):
        contracts = 0.0
    if contracts <= 0.0:
        return 0.0
    total_fee = estimate_kalshi_order_fee_usd(
        contracts,
        price,
        maker=maker,
        fee_rate=fee_rate,
        round_up_cents=round_up_cents,
    )
    return total_fee / contracts if contracts > 0 else 0.0


def estimate_kalshi_order_cost_usd(
    qty: float,
    price: float,
    *,
    maker: bool = False,
    fee_rate: float | None = None,
    round_up_cents: bool = True,
) -> float:
    try:
        contracts = max(0.0, float(qty))
    except (TypeError, ValueError):
        contracts = 0.0
    normalized_price = _normalize_kalshi_price(price)
    if contracts <= 0.0 or normalized_price <= 0.0:
        return 0.0
    return (contracts * normalized_price) + estimate_kalshi_order_fee_usd(
        contracts,
        normalized_price,
        maker=maker,
        fee_rate=fee_rate,
        round_up_cents=round_up_cents,
    )


def max_kalshi_contracts_for_budget(
    price: float,
    budget_usd: float,
    *,
    maker: bool = False,
    fee_rate: float | None = None,
) -> int:
    normalized_price = _normalize_kalshi_price(price)
    try:
        budget = max(0.0, float(budget_usd))
    except (TypeError, ValueError):
        budget = 0.0
    if normalized_price <= 0.0 or budget <= 0.0:
        return 0

    high = max(1, int(budget / max(normalized_price, 0.01)) + 2)
    low = 0
    best = 0
    while low <= high:
        mid = (low + high) // 2
        total_cost = estimate_kalshi_order_cost_usd(
            mid,
            normalized_price,
            maker=maker,
            fee_rate=fee_rate,
        )
        if total_cost <= budget + 1e-9:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return best


def get_kalshi_position_exposure_usd(
    qty: float,
    entry_price: float,
    *,
    maker: bool = False,
    fee_rate: float | None = None,
) -> float:
    try:
        contracts = max(0.0, float(qty))
    except (TypeError, ValueError):
        contracts = 0.0
    price = _normalize_kalshi_price(entry_price)
    return estimate_kalshi_order_cost_usd(
        contracts,
        price,
        maker=maker,
        fee_rate=fee_rate,
    )


# ──────────────────────────────────────────────────────────────────────────────
# v19.10: Optional per-hub Gate 11 overrides
# ──────────────────────────────────────────────────────────────────────────────
# Loaded from config/hub_params.json. Hubs may optionally override the lane-aware
# Hard RBI Conviction Floor by setting {"hard_rbi_threshold": <0.50–0.95>}.
# If the file is absent, empty, or malformed, HUB_PARAMS is {} and the lane-aware
# defaults in forecast/strategy_engine.py apply unconditionally.
import json as _hub_json

_HUB_PARAMS_PATH = os.path.join(REPO_ROOT, "config", "hub_params.json")
try:
    with open(_HUB_PARAMS_PATH, "r", encoding="utf-8") as _hub_fh:
        HUB_PARAMS: dict = _hub_json.load(_hub_fh)
        if not isinstance(HUB_PARAMS, dict):
            HUB_PARAMS = {}
except (FileNotFoundError, _hub_json.JSONDecodeError, OSError):
    HUB_PARAMS = {}
