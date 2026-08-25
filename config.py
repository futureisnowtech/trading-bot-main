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

# Canonical quality boundary for every historical trade read. Operational broker
# state remains unbounded so an older open position can never disappear from risk.
TRADE_DATA_START_DATE: str = os.getenv("TRADE_DATA_START_DATE", "2026-07-23")
POST_PAPER_START_DATE: str = TRADE_DATA_START_DATE


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

# The production entry lane is taker-only. Resting entry orders were retired by
# operator decision; generic cancellation remains solely as startup protection
# against externally or historically resting orders.
# Learning-loop cadence. These were read via getattr(config, ...) with inline
# defaults and never defined here, so setting them in .env did nothing and
# raised nothing -- a silent configuration no-op.
# Values match the inline defaults they replace, so behaviour is unchanged.
ML_RETRAIN_MIN_HOURS: float = float(os.getenv("ML_RETRAIN_MIN_HOURS", "24"))
ML_RETRAIN_MIN_NEW_CLEAN_TRADES: int = int(os.getenv("ML_RETRAIN_MIN_NEW_CLEAN_TRADES", "20"))
RBI_MIN_DAYS: float = float(os.getenv("RBI_MIN_DAYS", "7"))
RBI_MIN_NEW_CLEAN_TRADES: int = int(os.getenv("RBI_MIN_NEW_CLEAN_TRADES", "24"))
# Version the probability/evidence contract so a materially changed engine must
# collect a fresh learning window instead of training on incompatible history.
RBI_LEARNING_EPOCH: str = os.getenv(
    "RBI_LEARNING_EPOCH",
    "v19.20.0-deterministic-physics-path",
).strip()

# Session start: all performance stats (win rate, P&L, trade counts) are
# measured from this date forward.
TRADE_SESSION_START: str = TRADE_DATA_START_DATE

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
DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
REASONING_PROVIDER: str = os.getenv("REASONING_PROVIDER", "gemini").strip().lower()

# AI Exit Settings
PM_LLM_TEMPERATURE: float = float(os.getenv("PM_LLM_TEMPERATURE", "0.3"))
PM_LLM_MAX_TOKENS: int = int(os.getenv("PM_LLM_MAX_TOKENS", "600"))
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip() or "deepseek-v4-flash"
DEEPSEEK_BASE_URL: str = (
    os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
    or "https://api.deepseek.com"
)
DEEPSEEK_REASONING_EFFORT: str = (
    os.getenv("DEEPSEEK_REASONING_EFFORT", "high").strip() or "high"
)
DEEPSEEK_THINKING_MODE: str = (
    os.getenv("DEEPSEEK_THINKING_MODE", "enabled").strip().lower() or "enabled"
)

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
# Source defaults are the versioned fallback for a fresh runtime.  This revision
# was reconciled against the effective (config + env + SQLite overrides) NYC
# production posture on 2026-08-24.  Environment values may override it, but a
# missing variable must not silently create a different risk system.
NYC_LIVE_POLICY_REVISION: str = "2026-08-24"
KALSHI_MAX_DEPLOYED_PCT: float = float(os.getenv("KALSHI_MAX_DEPLOYED_PCT", "0.90"))
KALSHI_MAX_CONCURRENT_POSITIONS: int = int(os.getenv("KALSHI_MAX_CONCURRENT_POSITIONS", "20"))
KALSHI_SAME_EVENT_FAMILY_CAP: int = int(os.getenv("KALSHI_SAME_EVENT_FAMILY_CAP", "5"))
KALSHI_HIGH_PROB_THRESHOLD: float = float(os.getenv("KALSHI_HIGH_PROB_THRESHOLD", "0.80"))
KALSHI_ULTRA_HIGH_PROB_THRESHOLD: float = float(os.getenv("KALSHI_ULTRA_HIGH_PROB_THRESHOLD", "0.90"))
KALSHI_HIGH_PROB_POSITION_CAP_MULTIPLIER: float = float(
    os.getenv("KALSHI_HIGH_PROB_POSITION_CAP_MULTIPLIER", "1.50")
)
KALSHI_ULTRA_HIGH_PROB_POSITION_CAP_MULTIPLIER: float = float(
    os.getenv("KALSHI_ULTRA_HIGH_PROB_POSITION_CAP_MULTIPLIER", "2.00")
)
KALSHI_ULTRA_HIGH_PROB_NO_CONCURRENT_BONUS: int = int(
    os.getenv("KALSHI_ULTRA_HIGH_PROB_NO_CONCURRENT_BONUS", "2")
)
KALSHI_ULTRA_HIGH_PROB_NO_FAMILY_CAP_BONUS: int = int(
    os.getenv("KALSHI_ULTRA_HIGH_PROB_NO_FAMILY_CAP_BONUS", "1")
)
# Regional hub exposure ceiling: cap = max(MIN_USD, balance * PCT).
#
# These defaults deliberately mirror the effective values running in NYC
# production, not an aspirational or historical number.  The live config resolves
# to 0.40 / $20; CI and examples previously pinned 0.30 / $12 and therefore proved
# a different posture. Change these only with an explicit policy revision.
KALSHI_HUB_EXPOSURE_PCT: float = float(
    os.getenv("KALSHI_HUB_EXPOSURE_PCT", "0.40")
)
KALSHI_HUB_EXPOSURE_MIN_USD: float = float(
    os.getenv("KALSHI_HUB_EXPOSURE_MIN_USD", "20")
)
# Sovereign Salvage Delta: purge a position when its model probability falls
# below this. High-conviction entries get slightly more room before the bot
# abandons them, while sub-80% entries still use the full default delta.
SALVAGE_EXIT_DELTA: float = float(os.getenv("SALVAGE_EXIT_DELTA", "0.15"))
SALVAGE_EXIT_DELTA_HIGH_PROB: float = float(
    os.getenv("SALVAGE_EXIT_DELTA_HIGH_PROB", "0.12")
)
SALVAGE_EXIT_DELTA_ULTRA_HIGH_PROB: float = float(
    os.getenv("SALVAGE_EXIT_DELTA_ULTRA_HIGH_PROB", "0.10")
)
KALSHI_MAX_QTY_PER_POSITION: int = int(os.getenv("KALSHI_MAX_QTY_PER_POSITION", "15"))
KALSHI_MAX_USD_PER_POSITION: float = float(os.getenv("KALSHI_MAX_USD_PER_POSITION", "10.0"))  # Hard Ceiling
KALSHI_MAX_SIGMA: float = 2.8
KALSHI_MIN_MODEL_HEADROOM_F: float = float(
    os.getenv("KALSHI_MIN_MODEL_HEADROOM_F", "2.0")
)
KALSHI_MAX_SPREAD_RATIO: float = 0.35
KALSHI_DATA_FRESHNESS_MINUTES_HOURLY: int = 25   # SPEC §4.5
KALSHI_DATA_FRESHNESS_MINUTES_DAILY:  int = 90   # SPEC §4.5
KALSHI_DATA_FRESHNESS_MINUTES: int = 90  # Legacy fallback
KALSHI_TAKER_FEE_RATE: float = float(os.getenv("KALSHI_TAKER_FEE_RATE", "0.07"))
# Retrospective fill normalization only. Production has no maker route or knob.
_HISTORICAL_KALSHI_MAKER_FEE_RATE: float = 0.0175
KALSHI_FEE_PER_CONTRACT: float = float(
    os.getenv("KALSHI_FEE_PER_CONTRACT", str(KALSHI_TAKER_FEE_RATE))
)  # Legacy fallback only
KALSHI_MAX_SPREAD_DOLLARS: float = 0.12  # SPEC §5.4c — quote coherence gate (dollars, not ratio)
KALSHI_MAX_ENTRY_SLIPPAGE: float = float(
    os.getenv("KALSHI_MAX_ENTRY_SLIPPAGE", "0.02")
)
KALSHI_POSITION_SNAPSHOT_MAX_AGE_SEC: float = float(
    os.getenv("KALSHI_POSITION_SNAPSHOT_MAX_AGE_SEC", "60")
)
KALSHI_MIN_ENTRY_PRICE: float = float(os.getenv("KALSHI_MIN_ENTRY_PRICE", "0.34"))     # SPEC §2.6 — hard entry price floor; deletes 0.02/0.03 carve-outs
# Enforced sizing ceilings. KELLY_CAP limits each order's fee-inclusive capital
# fraction after conviction overrides. MAX_RISK_PER_EVENT_PCT limits aggregate
# contract-family exposure before broker submission.
KALSHI_KELLY_CAP: float = float(os.getenv("KALSHI_KELLY_CAP", "0.12"))
KALSHI_KELLY_FRACTION: float = float(os.getenv("KALSHI_KELLY_FRACTION", "0.25"))
KALSHI_MAX_RISK_PER_EVENT_PCT: float = float(os.getenv("KALSHI_MAX_RISK_PER_EVENT_PCT", "0.08"))

# Version-controlled fallback for the city firewall.  NYC previously carried
# this only in its untracked .env, so a rebuild or new operator checkout could
# silently trade 27 cities that production intentionally blocks.  The effective
# default leaves CHI, DEN, LAX, OKC, and SAT enabled.  CITY_BLACKLIST remains an
# intentional emergency override surface, including support for regional hubs.
CITY_BLACKLIST_POLICY_REVISION: str = "2026-08-24.nyc-live"
DEFAULT_CITY_BLACKLIST: frozenset[str] = frozenset(
    {
        "ABQ", "ATL", "AUS", "BOS", "CHS", "CLT", "DAL", "DC", "DET",
        "HOU", "LV", "MCI", "MCO", "MIA", "MKE", "MSP", "MSY", "NY",
        "OMA", "PDX", "PHL", "PHX", "RDU", "SEA", "SF", "SLC", "STL",
    }
)
_city_blacklist_env = os.getenv(
    "CITY_BLACKLIST", ",".join(sorted(DEFAULT_CITY_BLACKLIST))
).strip()
CITY_BLACKLIST: frozenset[str] = frozenset(
    code.strip().upper()
    for code in _city_blacklist_env.split(",")
    if code.strip()
)


def _bounded_probability(value: float | None) -> float | None:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def get_kalshi_conviction_bucket(held_probability: float | None) -> str:
    prob = _bounded_probability(held_probability)
    if prob is None:
        return "base"
    if prob >= KALSHI_ULTRA_HIGH_PROB_THRESHOLD:
        return "ultra"
    if prob >= KALSHI_HIGH_PROB_THRESHOLD:
        return "high"
    return "base"


def get_kalshi_position_cap_multiplier(held_probability: float | None) -> float:
    bucket = get_kalshi_conviction_bucket(held_probability)
    if bucket == "ultra":
        return KALSHI_ULTRA_HIGH_PROB_POSITION_CAP_MULTIPLIER
    if bucket == "high":
        return KALSHI_HIGH_PROB_POSITION_CAP_MULTIPLIER
    return 1.0


def get_kalshi_position_cap_usd(held_probability: float | None) -> float:
    return float(KALSHI_MAX_USD_PER_POSITION) * float(
        get_kalshi_position_cap_multiplier(held_probability)
    )


def get_kalshi_effective_concurrent_cap(
    side: str | None,
    held_probability: float | None,
) -> int:
    cap = int(KALSHI_MAX_CONCURRENT_POSITIONS)
    if (
        str(side or "").upper() == "NO"
        and get_kalshi_conviction_bucket(held_probability) == "ultra"
    ):
        cap += int(KALSHI_ULTRA_HIGH_PROB_NO_CONCURRENT_BONUS)
    return cap


def get_kalshi_effective_same_event_family_cap(
    side: str | None,
    held_probability: float | None,
) -> int:
    cap = int(KALSHI_SAME_EVENT_FAMILY_CAP)
    if (
        str(side or "").upper() == "NO"
        and get_kalshi_conviction_bucket(held_probability) == "ultra"
    ):
        cap += int(KALSHI_ULTRA_HIGH_PROB_NO_FAMILY_CAP_BONUS)
    return cap


def get_salvage_exit_delta_for_entry(held_probability: float | None) -> float:
    bucket = get_kalshi_conviction_bucket(held_probability)
    if bucket == "ultra":
        return float(SALVAGE_EXIT_DELTA_ULTRA_HIGH_PROB)
    if bucket == "high":
        return float(SALVAGE_EXIT_DELTA_HIGH_PROB)
    return float(SALVAGE_EXIT_DELTA)


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
KALSHI_DAILY_ASK_YES_BRACKET_MIN: float = float(
    os.getenv("KALSHI_DAILY_ASK_YES_BRACKET_MIN", "0.20")
)
KALSHI_DAILY_ASK_YES_BRACKET_MAX: float = float(
    os.getenv("KALSHI_DAILY_ASK_YES_BRACKET_MAX", "0.70")
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
WEATHER_PROVIDER_COOLDOWN_SEC: int = int(
    os.getenv("WEATHER_PROVIDER_COOLDOWN_SEC", "1200")
)
WEATHER_MODEL_PAUSE_SEC: float = float(
    os.getenv("WEATHER_MODEL_PAUSE_SEC", "0.75")
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
    return max(
        0.0,
        float(_HISTORICAL_KALSHI_MAKER_FEE_RATE if maker else KALSHI_TAKER_FEE_RATE),
    )


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


def kalshi_held_price_from_yes_leg(yes_price: float, side: str) -> float:
    """Translate Kalshi's canonical YES-book price into the held outcome price."""
    price = _normalize_kalshi_price(yes_price)
    if str(side or "YES").upper() == "NO":
        price = 1.0 - price
    return max(0.0, min(1.0, price))


def get_kalshi_position_held_price(position: dict) -> float:
    """Return the actual cost per held contract from a broker position snapshot.

    Live V2 order/fill accounting is YES-denominated even for a NO holding.  The
    official market exposure is therefore preferred, followed by an explicit
    held-side field, and only then the legacy YES-leg ``entry_price``.
    """
    qty = max(0.0, float(position.get("qty") or 0.0))
    for key in ("market_exposure_usd", "market_exposure_dollars"):
        raw = position.get(key)
        if raw not in (None, "") and qty > 0.0:
            try:
                exposure = abs(float(raw))
                if exposure > 0.0:
                    return max(0.0, min(1.0, exposure / qty))
            except (TypeError, ValueError):
                pass

    explicit = position.get("held_side_entry_price")
    if explicit not in (None, ""):
        try:
            return max(0.0, min(1.0, float(explicit)))
        except (TypeError, ValueError):
            pass

    yes_leg = position.get("yes_leg_entry_price")
    if yes_leg in (None, ""):
        yes_leg = position.get("entry_price", position.get("entry", 0.0))
    return kalshi_held_price_from_yes_leg(
        float(yes_leg or 0.0),
        str(position.get("side") or ("NO" if position.get("right") == "P" else "YES")),
    )


def get_kalshi_position_snapshot_exposure_usd(
    position: dict,
    *,
    include_estimated_fee: bool = False,
) -> float:
    """Economic dollars at risk for one cached position, side-correct for NO."""
    qty = max(0.0, float(position.get("qty") or 0.0))
    price = get_kalshi_position_held_price(position)
    if not include_estimated_fee:
        return qty * price
    return get_kalshi_position_exposure_usd(qty, price)


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
