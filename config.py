"""
config.py — Single source of truth. All values from .env.
Never hardcode anything that belongs here.
"""

import os
import math
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
# v18.32: Ripped out paper trading and scalper mode switches.
# All systems are strictly LIVE.
SHADOW_EXECUTION: bool = os.getenv("SHADOW_EXECUTION", "false").lower() == "true"

# Session start: all performance stats (win rate, P&L, trade counts) are
# measured from this date forward.
TRADE_SESSION_START: str = os.getenv("TRADE_SESSION_START", "2026-03-28")

# ════════════════════════════════════════════════════════════════════
# ACCOUNT
# ════════════════════════════════════════════════════════════════════
ACCOUNT_SIZE: float = float(os.getenv("ACCOUNT_SIZE", "5000"))
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
    os.getenv("FORECAST_LANE_ACTIVE", "true").lower() == "true"
)
FORECAST_DASHBOARD_VISIBLE: bool = (
    os.getenv("FORECAST_DASHBOARD_VISIBLE", "true").lower() == "true"
)
FORECAST_AUTONOMOUS_ENABLED: bool = (
    os.getenv("FORECAST_AUTONOMOUS_ENABLED", "true").lower() == "true"
)
FORECAST_MANUAL_ENABLED: bool = (
    os.getenv("FORECAST_MANUAL_ENABLED", "true").lower() == "true"
)

# Kalshi Risk & Capital Partitioning
KALSHI_MAX_DEPLOYED_PCT: float = 0.90
KALSHI_MAX_CONCURRENT_POSITIONS: int = 15
KALSHI_SAME_EVENT_FAMILY_CAP: int = int(os.getenv("KALSHI_SAME_EVENT_FAMILY_CAP", "5"))
KALSHI_HUB_EXPOSURE_PCT: float = float(
    os.getenv("KALSHI_HUB_EXPOSURE_PCT", "0.30")
)
KALSHI_HUB_EXPOSURE_MIN_USD: float = float(
    os.getenv("KALSHI_HUB_EXPOSURE_MIN_USD", "40")
)
KALSHI_MAX_QTY_PER_POSITION: int = 200
KALSHI_MAX_USD_PER_POSITION: float = 10.0  # Hard Ceiling
KALSHI_MIN_PRICE: float = 0.15
KALSHI_MAX_SIGMA: float = 3.0
KALSHI_MAX_SPREAD_RATIO: float = 0.20
KALSHI_DATA_FRESHNESS_MINUTES: int = 180
KALSHI_TAKER_FEE_RATE: float = float(os.getenv("KALSHI_TAKER_FEE_RATE", "0.07"))
KALSHI_MAKER_FEE_RATE: float = float(os.getenv("KALSHI_MAKER_FEE_RATE", "0.0175"))
KALSHI_FEE_PER_CONTRACT: float = float(
    os.getenv("KALSHI_FEE_PER_CONTRACT", str(KALSHI_TAKER_FEE_RATE))
)  # Legacy fallback only
KALSHI_MAX_FEE_DRAG_PCT: float = 0.30
KALSHI_KELLY_CAP: float = 0.10
KALSHI_MAX_RISK_PER_EVENT_PCT: float = 0.015
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
KALSHI_EXPENSIVE_YES_THRESHOLD: float = float(
    os.getenv("KALSHI_EXPENSIVE_YES_THRESHOLD", "0.70")
)
KALSHI_EXPENSIVE_YES_MIN_NET_EDGE: float = float(
    os.getenv("KALSHI_EXPENSIVE_YES_MIN_NET_EDGE", "0.08")
)
KALSHI_EXPENSIVE_YES_SIZE_MULTIPLIER: float = float(
    os.getenv("KALSHI_EXPENSIVE_YES_SIZE_MULTIPLIER", "0.75")
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
    if normalized_price <= 0.0:
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
    return math.ceil(raw_total * 100.0 - 1e-12) / 100.0


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
