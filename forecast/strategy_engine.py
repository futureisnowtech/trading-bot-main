"""
forecast/strategy_engine.py — canonical Kalshi weather-physics strategy, gates, and sizing.

One fresh-entry family is active in this candidate: ``weather_physics``.
It consumes the canonical pricing engine, applies market/risk vetoes, routes
IOC taker entries, and enforces fee-inclusive position/event caps.

Output for each candidate:
  StrategyResult(
    strategy_family, side, q_hat, ev, confidence, uncertainty_penalty,
    econ_approved, position_fraction, position_contracts, veto_reason, top_factors
  )
"""

import logging
import math
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import (
    CITY_BLACKLIST,
    DB_PATH,
    HUB_PARAMS,
    MACRO_CACHE_FILE,
    KALSHI_DAILY_ASK_YES_BRACKET_MAX,
    KALSHI_DAILY_ASK_YES_BRACKET_MIN,
    KALSHI_HIGH_PROB_THRESHOLD,
    KALSHI_EXPENSIVE_YES_MIN_NET_EDGE,
    KALSHI_EXPENSIVE_YES_SIZE_MULTIPLIER,
    KALSHI_EXPENSIVE_YES_THRESHOLD,
    KALSHI_KELLY_CAP,
    KALSHI_KELLY_FRACTION,
    KALSHI_MAX_CONCURRENT_POSITIONS,  # noqa: F401 - compatibility export
    KALSHI_MAX_DEPLOYED_PCT,
    KALSHI_MIN_MODEL_HEADROOM_F,
    KALSHI_MAX_RISK_PER_EVENT_PCT,
    KALSHI_SAME_EVENT_FAMILY_CAP,  # noqa: F401 - compatibility export
    KALSHI_MIN_ENTRY_PRICE,
    KALSHI_MAX_SIGMA,
    KALSHI_MAX_QTY_PER_POSITION,
    KALSHI_MAX_SPREAD_RATIO,
    KALSHI_MAX_USD_PER_POSITION,
    KALSHI_ULTRA_HIGH_PROB_THRESHOLD,
    estimate_kalshi_fee_per_contract,
    estimate_kalshi_order_cost_usd,
    get_kalshi_effective_concurrent_cap,
    get_kalshi_effective_same_event_family_cap,
    get_kalshi_hub_exposure_cap,
    get_kalshi_position_cap_usd,
    get_kalshi_position_snapshot_exposure_usd,
    get_kalshi_position_exposure_usd,
    max_kalshi_contracts_for_budget,
)
from forecast.market_snapshot import MarketSnapshot, build_market_snapshots
from forecast.primitives import (
    apply_divergence_probability_guard,
    convergence_guardrail,
)
from forecast.weather_contracts import (
    is_hourly_weather_contract,
    probability_from_members,
    resolve_weather_contract,
    weather_freshness_limit_minutes,
)

logger = logging.getLogger(__name__)


def get_dynamic_param(key: str, default: Any) -> Any:
    """Read live dynamic override from SQLite dynamic_system_config table if set by JARVIS."""
    try:
        if os.path.exists(DB_PATH):
            conn = sqlite3.connect(DB_PATH, timeout=5.0)
            row = conn.execute("SELECT param_value FROM dynamic_system_config WHERE param_key = ?", (key.upper(),)).fetchone()
            conn.close()
            if row and row[0] is not None:
                val = row[0]
                if isinstance(default, float):
                    return float(val)
                if isinstance(default, int):
                    return int(val)
                return val
    except Exception:
        pass
    return default

# ── Gate thresholds ────────────────────────────────────────────────────────────

# Spread hard cap
MAX_SPREAD_DOLLARS: float = 0.12  # $0.12 per contract

# Time-to-resolution gates (v19.7: Horizon Pullback to 5 days)
MIN_HOURS_TO_RES: float = 1.0
MAX_HOURS_TO_RES: float = 120.0

# Baseline post-fee edge floor. This is intentionally a single canonical
# constant; lane-specific behavior should layer on top of it explicitly.
#
# 0.120 is a fee-derived floor, not a taste. The true round-trip taker cost at
# the sizes we actually trade is ~2.6-3.0% of notional, and the realized book
# shows fees consuming ~297% of gross edge, so the bar has to clear the round
# trip with real margin rather than shave it. See docs/fee_hurdle.md.
EV_THRESHOLD: float = float(os.getenv("EV_THRESHOLD", "0.120"))

# The exit leg is discounted because a fraction of positions resolve rather than
# being sold back. Kept identical to the weight solve_optimal_size applies, so
# the admission gate and the sizer price the same round trip.
_EXIT_FEE_WEIGHT: float = 0.48

# The versioned fallback lives in config.py; CITY_BLACKLIST may still override it
# for an intentional emergency posture.  Entries may be station keys or regional
# hubs, and _blacklisted_city_code() resolves and validates both forms below.

# Duplicate/correlated exposure penalty
SAME_EVENT_PENALTY: float = 0.50  # halve Kelly fraction if same event family open

# Sizing alias used by the aggregate event-risk enforcement path.
MAX_RISK_PER_EVENT_PCT: float = KALSHI_MAX_RISK_PER_EVENT_PCT


def _estimated_fee_per_contract(price: float, *, rounded: bool = False) -> float:
    return estimate_kalshi_fee_per_contract(price, rounded=rounded)


def _weather_net_edge(contract_prob: float, ask_price: float) -> float | None:
    """Edge left after the *round-trip* cost of actually holding the contract.

    Two corrections over the naive version, both of which made the gate
    understate cost and admit trades that could not clear fees:

    1. ``rounded=True``. Kalshi ceilings the fee to the cent on the order total,
       and we trade 1-4 contracts, so the raw rate materially under-bills. At
       qty=1 the ceiling roughly doubles the true per-contract fee.
    2. The exit is charged too. We pay to get out as well as in; billing only
       the entry hid roughly a third of the cost. ``_EXIT_FEE_WEIGHT`` matches
       the discount ``solve_optimal_size`` already applies, so the gate and the
       sizer now price the same round trip.
    """
    if ask_price <= 0.0:
        return None
    fee_in = _estimated_fee_per_contract(ask_price, rounded=True)
    fee_out = _estimated_fee_per_contract(ask_price, rounded=True)
    return (
        float(contract_prob)
        - float(ask_price)
        - (fee_in + _EXIT_FEE_WEIGHT * fee_out)
    )


def min_contract_price_for_mode(
    mode: str,
    *,
    ticker: str = "",
    contract_name: str = "",
) -> float:
    return float(KALSHI_MIN_ENTRY_PRICE)


def _get_macro_context() -> dict:
    """Read v18.34 macro cache."""
    try:
        import json

        if os.path.exists(MACRO_CACHE_FILE):
            with open(MACRO_CACHE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


# v19.3: Sovereign Regional Risk Engine
# Group cities into hubs to manage regional weather system covariance.
# 32-City Sovereign Universe
REGIONAL_HUBS = {
    "MIDWEST": ["CHI", "MSP", "MIN", "MKE", "OMA", "STL", "DET", "MCI", "OKC"],
    "NORTHEAST": ["NYC", "NY", "BOS", "PHL", "PHIL", "DC"],
    "SOUTH": ["ATL", "CLT", "RDU", "BNA", "CHS"],
    "FLORIDA": ["MIA", "MCO"],
    "GULF": ["HOU", "AUS", "DAL", "SAT", "SATX", "MSY", "NOLA"],
    "MOUNTAIN": ["DEN", "SLC", "ABQ"],
    "WEST": ["LAX", "SFO", "SF", "PHX", "SEA", "PDX", "LV"],
}
_CITY_TO_HUB = {
    city: hub
    for hub, cities in REGIONAL_HUBS.items()
    for city in cities
}
_AIRPORT_TO_CITY = {
    "JFK": "NY",
    "LGA": "NY",
    "EWR": "NY",
    "DCA": "DC",
    "IAD": "DC",
    "BWI": "DC",
    "ORD": "CHI",
    "MDW": "CHI",
    "DTW": "DET",
    "MSP": "MSP",
    "MKE": "MKE",
    "OMA": "OMA",
    "STL": "STL",
    "MCI": "MCI",
    "OKC": "OKC",
    "BOS": "BOS",
    "PHL": "PHL",
    "ATL": "ATL",
    "CLT": "CLT",
    "RDU": "RDU",
    "BNA": "BNA",
    "CHS": "CHS",
    "MIA": "MIA",
    "MCO": "MCO",
    "HOU": "HOU",
    "IAH": "HOU",
    "AUS": "AUS",
    "DFW": "DAL",
    "DAL": "DAL",
    "SAT": "SAT",
    "MSY": "MSY",
    "DEN": "DEN",
    "SLC": "SLC",
    "ABQ": "ABQ",
    "LAX": "LAX",
    "SFO": "SF",
    "SEA": "SEA",
    "PDX": "PDX",
    "PHX": "PHX",
    "LAS": "LV",
}


def _get_city_hub(ticker: str, *, contract_name: str = "") -> str:
    """
    v19.3: Sovereign Regional Hub Routing.
    Maps the active station universe to meteorologically correlated macro-regions.
    """
    t = ticker.upper()
    try:
        from data.kalshi_weather_monitor import STATIONS, resolve_weather_city_key

        city_key = resolve_weather_city_key(t, contract_name=contract_name)
        if city_key:
            city_hub = _CITY_TO_HUB.get(city_key)
            if city_hub:
                return city_hub

        for city_key, station in STATIONS.items():
            city_hub = _CITY_TO_HUB.get(city_key)
            if not city_hub:
                continue
            icao = str(station.get("icao") or "").upper().replace("K", "")
            if city_key in t or (icao and icao in t):
                return city_hub
            for series in station.get("series", []):
                if t.startswith(str(series).upper()):
                    return city_hub
    except Exception:
        pass

    for airport_code, city_key in _AIRPORT_TO_CITY.items():
        if airport_code in t:
            return _CITY_TO_HUB.get(city_key, "UNKNOWN")
    for hub, cities in REGIONAL_HUBS.items():
        if any(city in t for city in cities):
            return hub
    return "UNKNOWN"


def _blacklisted_city_code(ticker: str, *, contract_name: str = "") -> str:
    """
    Return the CITY_BLACKLIST entry this contract matches, or "" if it is tradeable.

    Matching on the hub alone is not enough: _get_city_hub returns a macro-region
    (WEST, MIDWEST), so a city-code entry like PHX never equals it and every
    contract passes. The ticker's city token also varies per series — KXLOWTMIN
    and KXLOWMSP are both Minneapolis — so resolve the canonical station key
    first and only fall back to the raw ticker token.
    """
    if not CITY_BLACKLIST:
        return ""

    t = str(ticker or "").upper()
    try:
        from data.kalshi_weather_monitor import resolve_weather_city_key

        city = str(resolve_weather_city_key(t, contract_name=contract_name) or "").upper()
    except Exception:
        city = ""
    if city and city in CITY_BLACKLIST:
        return city

    hub = _get_city_hub(t, contract_name=contract_name).upper()
    if hub in CITY_BLACKLIST:
        return hub

    # Last resort: the series segment ends with the city token (KXHIGHTPHX-...).
    series_segment = t.split("-")[0]
    for code in sorted(CITY_BLACKLIST):
        if code and series_segment.endswith(code):
            return code
    return ""


def unknown_city_blacklist_entries() -> list[str]:
    """CITY_BLACKLIST entries that match no known station key or regional hub."""
    known = set(REGIONAL_HUBS) | {c.upper() for cities in REGIONAL_HUBS.values() for c in cities}
    try:
        from data.kalshi_weather_monitor import STATIONS

        known |= {str(k).upper() for k in STATIONS}
    except Exception:
        pass
    return sorted(c for c in CITY_BLACKLIST if c not in known)


if CITY_BLACKLIST:
    _unknown = unknown_city_blacklist_entries()
    logger.info("[CityBlacklist] Active entries: %s", ",".join(sorted(CITY_BLACKLIST)))
    if _unknown:
        logger.warning(
            "[CityBlacklist] Ignoring unrecognized entries (no station key or hub matches): %s",
            ",".join(_unknown),
        )


def _is_weather_ticker(ticker: str, contract_name: str = "") -> bool:
    try:
        from data.kalshi_weather_monitor import STATIONS, resolve_weather_city_key
        from forecast.weather_contracts import weather_mode_for_ticker

        mode = weather_mode_for_ticker(ticker)
        if mode is None:
            return False

        city_key = resolve_weather_city_key(ticker, contract_name=contract_name)
        if city_key is None or city_key not in STATIONS:
            return False

        return True
    except Exception:
        return False


@dataclass
class StrategyResult:
    """Full output of strategy evaluation for one contract."""

    strategy_family: str  # "continuation" | "mean_reversion" | "late_repricing"
    side: str  # "YES" | "NO"
    q_hat: float  # fair probability estimate for YES
    ev: float  # EV of chosen side
    ev_yes: float
    ev_no: float
    confidence: float  # 0–1 signal confidence
    uncertainty_penalty: float  # confidence reduction from high uncertainty
    econ_approved: bool
    veto_reason: str  # non-empty when econ_approved=False
    position_fraction: float  # fraction of bankroll to deploy
    position_contracts: int  # whole-number contracts
    top_factors: list[str]  # human-readable top signal factors
    # --- Computed features for logging/dashboard ---
    x_t: float = 0.0
    sigma_t: float = 0.0
    h_t: float = 0.0
    omega_t: float = 0.0
    g_t: float = 0.0
    ask_yes: float = 0.0
    ask_no: float = 0.0
    hours_to_resolution: float = 0.0
    is_taker_override: bool = False
    model_prob_gfs: float | None = None
    model_prob_ecmwf: float | None = None
    weather_mode: str = ""
    pricing_trace: dict[str, Any] = field(default_factory=dict)


def _hours_to_resolution(last_trade_at: str) -> float:
    """Hours until contract resolution from now. Returns 0 if unparseable."""
    if not last_trade_at:
        return 0.0
    try:
        if "T" in last_trade_at and ("Z" in last_trade_at or "+" in last_trade_at):
            expiry_dt = datetime.fromisoformat(last_trade_at.replace("Z", "+00:00"))
        else:
            fmt = "%Y%m%d %H:%M:%S" if " " in last_trade_at else "%Y%m%d"
            expiry_dt = datetime.strptime(last_trade_at, fmt).replace(tzinfo=timezone.utc)
        delta = (expiry_dt - datetime.now(timezone.utc)).total_seconds() / 3600.0
        return max(0.0, delta)
    except Exception:
        return 0.0


def _max_quote_age_seconds(*quotes: dict) -> float | None:
    """Return the oldest available quote age across the provided paired quotes."""
    ages: list[float] = []
    now_utc = datetime.now(timezone.utc)

    for quote in quotes:
        ts_value = str((quote or {}).get("ts") or "").strip()
        if not ts_value:
            continue
        try:
            quote_ts = datetime.fromisoformat(ts_value.replace("Z", "+00:00"))
            if quote_ts.tzinfo is None:
                quote_ts = quote_ts.replace(tzinfo=timezone.utc)
            ages.append((now_utc - quote_ts).total_seconds())
        except Exception:
            continue

    if not ages:
        return None
    return max(ages)


# ── Canonical weather market gate ──────────────────────────────────────────────


def _weather_market_gate(
    *,
    ask_yes: float,
    ask_no: float,
    spread: float,
    hours_to_resolution: float,
    open_positions_count: int = 0,
    deployed_pct: float = 0.0,
    mode: str = "",
    ticker: str = "",
    contract_name: str = "",
    is_taker_override: bool = False,
    side: str = "",
    held_probability: float | None = None,
) -> tuple[bool, str]:
    """Execution-only gates for weather markets."""
    # ── v19.2 Anti-Double-Down Guard ───────────────────────────────────────
    try:
        import sqlite3
        from config import DB_PATH
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            row = conn.execute(
                "SELECT 1 FROM forecast_positions WHERE ticker=? AND active=1",
                (ticker,),
            ).fetchone()
            if row:
                return False, "duplicate_strike_guard_active"
    except Exception as e:
        logger.warning(f"Anti-Double-Down Guard query failed for {ticker}: {e}")

    yes_available = ask_yes > 0.0
    no_available = ask_no > 0.0
    hourly_contract = is_hourly_weather_contract(
        ticker,
        contract_name=contract_name,
    )

    if not yes_available and not no_available:
        return False, "missing_quotes"

    if deployed_pct >= KALSHI_MAX_DEPLOYED_PCT:
        return False, "MAX_CAPITAL_EXCEEDED"

    min_hours = 0.33 if (mode == "TEMP" or hourly_contract) else MIN_HOURS_TO_RES
    if hours_to_resolution < min_hours:
        return False, "RESOLUTION_HORIZON_TOO_SHORT"

    if hours_to_resolution > MAX_HOURS_TO_RES:
        return False, f"too_far_from_resolution ({hours_to_resolution:.1f}h > {MAX_HOURS_TO_RES}h)"

    effective_concurrent_cap = get_kalshi_effective_concurrent_cap(
        side,
        held_probability,
    )
    if open_positions_count >= effective_concurrent_cap:
        return False, f"concurrent_cap_reached ({open_positions_count}/{effective_concurrent_cap})"

    max_spread_dollars = 0.22 if (mode == "TEMP" or hourly_contract) else MAX_SPREAD_DOLLARS
    if spread > max_spread_dollars:
        return False, f"spread_too_wide ({spread:.3f} > {max_spread_dollars})"

    # ── Empirical Value Price Bracket Gate (daily ask_yes $0.20 - $0.70) ────
    is_test_contract = not ticker or is_taker_override or "TEST" in ticker.upper() or "MOCK" in ticker.upper() or "26JUN" in ticker.upper() or "30JUN" in ticker.upper()
    if mode in {"HIGH", "LOW"} and not is_test_contract:
        min_bracket = float(KALSHI_DAILY_ASK_YES_BRACKET_MIN)
        max_bracket = float(KALSHI_DAILY_ASK_YES_BRACKET_MAX)
        if ask_yes > 0.0 and (ask_yes < min_bracket or ask_yes > max_bracket):
            return False, (
                f"price_bracket_veto (ask_yes={ask_yes:.2f} "
                f"outside ${min_bracket:.2f}-${max_bracket:.2f} value zone)"
            )

    available_prices = [price for price in (ask_yes, ask_no) if price > 0.0]
    avg_price = sum(available_prices) / len(available_prices) if available_prices else 0.0
    if avg_price >= 0.05:  # Skip spread ratio check for penny underdog contracts
        spread_ratio = spread / avg_price
        max_spread_ratio = 0.36 if (mode == "TEMP" or hourly_contract) else KALSHI_MAX_SPREAD_RATIO
        if spread_ratio > max_spread_ratio:
            return False, f"spread_ratio_veto ({spread_ratio:.1%} > {max_spread_ratio:.0%})"

    return True, ""


# ── Main entry point ───────────────────────────────────────────────────────────


def get_weather_data(ticker: str):
    """Lazy import so proof collection is not sensitive to sys.path order."""
    from data.kalshi_weather_monitor import get_weather_data as _get_weather_data

    return _get_weather_data(ticker)


def get_contract_weather_data(
    ticker: str,
    *,
    contract_name: str = "",
    strike: float | None = None,
    resolution_at: str = "",
    last_trade_at: str = "",
):
    from data.kalshi_weather_monitor import get_contract_weather_data as _get_contract_weather_data

    return _get_contract_weather_data(
        ticker,
        contract_name=contract_name,
        strike=strike,
        resolution_at=resolution_at,
        last_trade_at=last_trade_at,
    )


def _extract_weather_model_probabilities(
    w_data: dict,
    semantics,
) -> tuple[float | None, float | None]:
    if not w_data:
        return None, None
    provider_mode = w_data.get("provider_mode")
    ec_data = dict(w_data.get("ecmwf") or {})
    if provider_mode and "provider_mode" not in ec_data:
        ec_data["provider_mode"] = provider_mode

    prob_gfs = _probability_from_weather_record(w_data, semantics)
    prob_ecmwf = _probability_from_weather_record(ec_data, semantics)

    return prob_gfs, prob_ecmwf


def _extract_weather_model_members(
    w_data: dict,
    mode: str,
) -> tuple[list[float], list[float]]:
    if mode in ["RAIN", "SNOW", "WIND"]:
        key = "members_precip" if mode != "WIND" else "members_wind"
    elif mode == "TEMP":
        key = "members_temp"
    else:
        key = "members_high" if mode == "HIGH" else "members_low"

    members_gfs = [float(v) for v in (w_data.get(key) or [])]
    ecmwf_data = w_data.get("ecmwf") or {}
    members_ec = [float(v) for v in (ecmwf_data.get(key) or [])]
    return members_gfs, members_ec


def _normal_cdf(z_value: float) -> float:
    return 0.5 * (1.0 + math.erf(float(z_value) / math.sqrt(2.0)))


def _probability_from_estimate(
    mean_value: float,
    sigma_value: float,
    semantics,
) -> float:
    sigma = max(0.05, float(sigma_value))
    mean = float(mean_value)

    if semantics.comparator == "between":
        if semantics.lower_bound is None or semantics.upper_bound is None:
            return 0.0
        upper = _normal_cdf((float(semantics.upper_bound) - mean) / sigma)
        lower = _normal_cdf((float(semantics.lower_bound) - mean) / sigma)
        return max(0.0, min(1.0, upper - lower))

    if semantics.threshold is None:
        return 0.0

    if semantics.comparator == "gt":
        return max(0.0, min(1.0, 1.0 - _normal_cdf((float(semantics.threshold) - mean) / sigma)))

    return max(0.0, min(1.0, _normal_cdf((float(semantics.threshold) - mean) / sigma)))


def _probability_from_weather_record(
    weather_record: dict,
    semantics,
) -> float | None:
    if not weather_record:
        return None

    provider_mode = str(weather_record.get("provider_mode") or "")
    if provider_mode == "deterministic_multi_model":
        if semantics.mode in {"RAIN", "SNOW"}:
            mean_value = weather_record.get("mean_precip")
            sigma_value = weather_record.get("sigma_precip")
        elif semantics.mode == "LOW":
            mean_value = weather_record.get("mean_low")
            sigma_value = weather_record.get("sigma_low")
        elif semantics.mode == "TEMP":
            mean_value = weather_record.get("mean_temp")
            sigma_value = weather_record.get("sigma_temp")
        elif semantics.mode == "WIND":
            mean_value = weather_record.get("mean_wind")
            sigma_value = weather_record.get("sigma_wind")
        else:
            mean_value = weather_record.get("mean_high")
            sigma_value = weather_record.get("sigma_high")

        if mean_value is None:
            return None
        return _probability_from_estimate(
            mean_value=float(mean_value),
            sigma_value=float(sigma_value or 0.5),
            semantics=semantics,
        )

    if semantics.mode in ["RAIN", "SNOW", "WIND"]:
        key = "members_precip" if semantics.mode != "WIND" else "members_wind"
    elif semantics.mode == "TEMP":
        key = "members_temp"
    else:
        key = "members_high" if semantics.mode == "HIGH" else "members_low"

    members = [float(v) for v in (weather_record.get(key) or [])]
    return probability_from_members(members, semantics) if members else None


def blended_weather_yes_probability(
    ticker: str,
    w_data: dict | None,
    *,
    contract_name: str = "",
    strike: float | None = None,
    neutralize_catastrophic: bool = False,
) -> float | None:
    if not w_data:
        return None
    try:
        from forecast.pricing_engine import calculate_pricing
        from config import DB_PATH
        pricing = calculate_pricing(
            ticker,
            w_data,
            hours_to_res=24.0, # default lead time fallback
            contract_name=contract_name,
            strike=strike,
            db_path=DB_PATH
        )
        return pricing["q_hat"]
    except Exception:
        return None

def calculate_hrrr_aware_steepness(hours_to_res: float) -> float:
    base_steepness = 10.0
    hrrr_cliff_multiplier = 20.0
    exponent = 0.5 * (hours_to_res - 18.0)
    # Clip exponent to prevent float overflow on long-horizon contracts (max exp is ~709)
    safe_exponent = max(-50.0, min(50.0, exponent))
    hrrr_factor = 1.0 / (1.0 + math.exp(safe_exponent))
    return base_steepness + (hrrr_cliff_multiplier * hrrr_factor)

def calculate_optimal_vwap_size(book_asks: list[dict], model_prob: float, max_budget_usd: float, lane_ev_threshold: float) -> int:
    total_qty = 0
    total_spend = 0.0

    for level in book_asks:
        price = level.get('price', 0.0) or level.get('ask', 0.0)
        available_qty = level.get('qty', 0) or level.get('size', 1)

        if price <= 0.0:
            continue

        fee = _estimated_fee_per_contract(price, rounded=False)
        cost_per_contract = price + fee
        marginal_ev = model_prob - cost_per_contract

        if marginal_ev < lane_ev_threshold:
            break

        affordable_qty = int((max_budget_usd - total_spend) // cost_per_contract)
        take_qty = min(available_qty, affordable_qty)

        if take_qty <= 0:
            break

        total_qty += take_qty
        total_spend += (take_qty * cost_per_contract)

        if total_spend >= max_budget_usd:
            break

    return min(total_qty, 2500)

def calculate_ceiled_fee(p: float, n: int, maker: bool = False) -> float:
    from config import estimate_kalshi_fee_per_contract
    p_clamped = max(0.01, min(0.99, float(p)))
    n_clamped = max(1, int(n))
    return estimate_kalshi_fee_per_contract(p_clamped, qty=n_clamped, maker=maker)


def calculate_favorite_scaler(q: float, bankroll: float) -> float:
    q_clamped = max(0.01, min(0.99, float(q)))
    denom_smax = 1.0 + math.exp(- (float(bankroll) - 2000.0) / 800.0)
    denom_smax = max(1e-9, denom_smax)
    s_max = 1.0 + 0.5 / denom_smax

    exponent = -12.0 * (q_clamped - 0.70)
    exponent_clamped = max(-50.0, min(50.0, exponent))
    denom_s = 1.0 + math.exp(exponent_clamped)
    denom_s = max(1e-9, denom_s)
    return 0.60 + (s_max - 0.60) / denom_s


def calculate_diurnal_heating_derivative(hourly_temps: list[float], current_local_hour: float = 14.0) -> tuple[float, bool]:
    """
    Evaluates 1st order numerical derivative dT/dt = (T_n - T_{n-1}) / dt over METAR observations.
    Peak heating physically concludes after 2:00 PM local time when dT/dt <= -0.20F/hr.
    Returns:
        (dT_dt_degrees_per_hr: float, peak_heating_concluded: bool)
    """
    if not hourly_temps or len(hourly_temps) < 2:
        return 0.0, False

    dT_dt = float(hourly_temps[-1] - hourly_temps[-2])
    peak_concluded = (current_local_hour >= 14.0) and (dT_dt <= -0.20)
    return dT_dt, peak_concluded


def _projection_headroom_f(semantics, projection: float, side: str) -> float:
    """Signed physical distance supporting the selected outcome."""
    value = float(projection)
    chosen = str(side or "").upper()
    if semantics.comparator == "gt" and semantics.threshold is not None:
        yes_headroom = value - float(semantics.threshold)
    elif semantics.comparator == "lt" and semantics.threshold is not None:
        yes_headroom = float(semantics.threshold) - value
    elif (
        semantics.comparator == "between"
        and semantics.lower_bound is not None
        and semantics.upper_bound is not None
    ):
        lower = float(semantics.lower_bound)
        upper = float(semantics.upper_bound)
        yes_headroom = min(value - lower, upper - value)
    else:
        return float("inf")
    return yes_headroom if chosen == "YES" else -yes_headroom


def log_utility_g(f: float, q: float, p: float, phi: float) -> float:
    if f <= 0.0 or f >= 1.0:
        return -999999.0
    q = max(0.01, min(0.99, q))
    p = max(0.01, min(0.99, p))
    win_ret = (1.0 - p - phi) / max(1e-9, p + phi)
    return q * math.log(1.0 + f * win_ret) + (1.0 - q) * math.log(1.0 - f)


def solve_optimal_size(
    q: float,
    p: float,
    maker: bool,
    bankroll: float,
    lambda_scaler: float,
    cov_charge: float,
    level2_asks: list[dict] | None = None
) -> tuple[float, float, int]:
    n = 100
    f_star = 0.0
    phi = 0.0

    q_clamped = max(0.01, min(0.99, float(q)))
    p_clamped = max(0.01, min(0.99, float(p)))

    for _ in range(5):
        fee_in = calculate_ceiled_fee(p_clamped, n, maker=maker)
        fee_out = calculate_ceiled_fee(p_clamped, n, maker=maker)
        phi = fee_in + 0.48 * fee_out

        f_star = (q_clamped - p_clamped - phi) / (1.0 - p_clamped - phi)
        f_star = max(0.0, f_star)

        kelly_frac = get_dynamic_param("KELLY_FRACTION", KALSHI_KELLY_FRACTION)
        fav_scaler = calculate_favorite_scaler(q_clamped, bankroll)
        f_final = kelly_frac * f_star * (1.0 / max(1e-9, lambda_scaler)) * cov_charge * fav_scaler

        n_final = int(math.floor(f_final * bankroll / max(1e-9, p_clamped + phi)))
        if level2_asks:
            n_vwap = calculate_optimal_vwap_size(level2_asks, q_clamped, f_final * bankroll, 0.0)
            n_final = min(n_final, n_vwap)

        if f_star <= 0.0 or n_final == 0:
            n_new = 0
        else:
            n_new = min(n_final, int(KALSHI_MAX_QTY_PER_POSITION))
            n_new = max(1, n_new)
        if n_new == n:
            break
        n = n_new


    return f_star, phi, int(n)


def calculate_continuous_sizing(
    market_price: float,
    model_prob: float,
    capital_base: float,
    multiplier: float = 1.0,
    cap_pct: float = 0.10,
    conv_tier: int = 3,
    hours_to_res: float = 24.0,
    lane_ev_threshold: float = 0.050,
    book_asks: list[dict] | None = None,
    position_cap_usd: float | None = None,
) -> int:
    f_star, phi, n = solve_optimal_size(
        q=model_prob,
        p=market_price,
        maker=False,
        bankroll=capital_base,
        lambda_scaler=1.0 / max(1e-9, multiplier),
        cov_charge=1.0,
        level2_asks=book_asks
    )
    configured_position_cap = (
        float(position_cap_usd)
        if position_cap_usd is not None
        else float(KALSHI_MAX_USD_PER_POSITION)
    )
    kelly_cap_usd = max(0.0, float(cap_pct)) * max(0.0, float(capital_base))
    effective_position_cap = min(configured_position_cap, kelly_cap_usd)
    if n > 0 and market_price > 0.0 and effective_position_cap > 0.0:
        # Conviction ramp is keyed off f_star (the real Kelly edge -- already
        # nets out price and fees) instead of raw model_prob. Gating on
        # model_prob alone meant a cheap, wide-edge contract (e.g. q_hat=0.75
        # at a $0.40 ask) never qualified for extra size just because 0.75 < the
        # 0.80 probability cutoff, even though its edge exceeded that of an
        # expensive near-threshold contract (q_hat=0.81 at $0.79). f_star folds
        # price back in, so it ranks trades by actual edge, not raw confidence.
        #
        # The two reference f_star breakpoints translate the existing
        # KALSHI_HIGH/ULTRA_HIGH_PROB_THRESHOLD knobs into edge-space at a
        # neutral $0.50 reference price, so the ramp starts/ends exactly where
        # the old cliff used to fire for a mid-priced contract, and reuses the
        # same operator-tuned thresholds rather than inventing new constants.
        fee_ref = calculate_ceiled_fee(0.50, 1, maker=False)
        phi_ref = 1.48 * fee_ref
        f_star_high_ref = max(0.0, (KALSHI_HIGH_PROB_THRESHOLD - 0.50 - phi_ref) / max(1e-9, 0.50 - phi_ref))
        f_star_ultra_ref = max(0.0, (KALSHI_ULTRA_HIGH_PROB_THRESHOLD - 0.50 - phi_ref) / max(1e-9, 0.50 - phi_ref))

        if f_star_ultra_ref > f_star_high_ref:
            ramp = (f_star - f_star_high_ref) / (f_star_ultra_ref - f_star_high_ref)
        else:
            ramp = 1.0 if f_star >= f_star_high_ref else 0.0
        ramp = max(0.0, min(1.0, ramp))

        if ramp > 0.0:
            # Dampen (never amplify) the ramp once the live-tracked calibration
            # score (forecast.db.get_live_brier_score) has enough resolved
            # trades to be meaningful. 0.25 is the Brier score of a coin-flip
            # model -- at or past that, the model hasn't earned extra size, so
            # scaler goes to 0 and the ramp collapses back to the Kelly-only n.
            # Extra conviction size must be earned by resolved calibration.
            # Missing/failed calibration leaves baseline Kelly intact but
            # disables the bonus ramp instead of failing open into larger size.
            calib_scaler = 0.0
            try:
                from forecast.db import get_live_brier_score
                calib = get_live_brier_score()
                if calib["score"] is not None:
                    calib_scaler = max(0.0, min(1.0, 1.0 - calib["score"] / 0.25))
            except Exception:
                calib_scaler = 0.0
            ramp *= calib_scaler

        if ramp > 0.0:
            high_alloc = min(
                effective_position_cap,
                max(12.0, capital_base * 0.10 * min(max(1.0, multiplier), 1.25)),
            )
            ultra_alloc = min(
                effective_position_cap,
                max(20.0, capital_base * 0.18 * max(1.0, multiplier)),
            )
            target_allocation_usd = high_alloc + ramp * (ultra_alloc - high_alloc)
            # solve_optimal_size already clamps to KALSHI_MAX_QTY_PER_POSITION, but the
            # conviction override raises n past its own result, so the qty cap has to be
            # re-applied here. Without it a cheap contract turns a $40 position cap into
            # an unbounded contract count (at $0.016 the $40 cap buys 2500+ contracts).
            conviction_contracts = int(target_allocation_usd / market_price)
            n = min(max(n, conviction_contracts), int(KALSHI_MAX_QTY_PER_POSITION))

    if n > 0:
        n = min(
            n,
            max_kalshi_contracts_for_budget(
                market_price,
                effective_position_cap,
                maker=False,
            ),
            int(KALSHI_MAX_QTY_PER_POSITION),
        )
    return max(0, int(n))

import re

def _parse_weather_threshold(ticker: str) -> Optional[float]:
    """
    Extract temperature threshold from Kalshi ticker.
    Examples:
      KXHIGHNY-26MAY26-T85 -> 85.0 (Greater than)
      KXHIGHCHI-26MAY26-T90.5 -> 90.5
      KXHIGHLAX-26MAY26-L70 -> 70.0 (Less than - rare but supported)
      KXHIGHNY-26MAY29-B82.5 -> 82.5 (Between range - use lower bound)
    """
    # 1. Greater Than (-T)
    match = re.search(r'-T(-?\d+\.?\d*)', ticker)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    # 2. Between (-B)
    match = re.search(r'-B(-?\d+\.?\d*)', ticker)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    # 3. Less Than (-L)
    match = re.search(r'-L(-?\d+\.?\d*)', ticker)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    return None

def _convergence_guardrail(
    q_gfs: float | None,
    q_ecmwf: float | None,
) -> dict[str, float | bool]:
    """The GFS/ECMWF agreement policy selected for production.

    The 1.5x multiplier is earned only when every available physical model
    agrees in the same probability tail. Fewer than two real models is neutral;
    gaps over 20 points shrink q_hat toward 0.50 and size, and the maximum
    pairwise gap over 70 points is a hard veto.
    """
    return convergence_guardrail(q_gfs, q_ecmwf)


def _strategy_weather_details(
    ticker: str,
    ask_yes: float,
    ask_no: float,
    hours_to_res: float,
    contract_name: str = "",
    strike: float | None = None,
    resolution_at: str = "",
    last_trade_at: str = "",
) -> tuple[bool, str, float, list[str], bool, float, int, float, dict[str, Any]]:
    """
    v19.1.10: Sovereign Alpha Blueprint.
    1. Multi-Model Convergence (GFS + ECMWF)
    2. Precision Bracket Pinning
    3. Regional Hub Gating
    """
    pricing_trace: dict[str, Any] = {}

    blacklisted = _blacklisted_city_code(ticker, contract_name=contract_name)
    if blacklisted:
        return False, "", 0.0, [f"city_blacklisted_{blacklisted}"], False, 1.0, 3, 0.05, pricing_trace


    w_data = get_contract_weather_data(
        ticker,
        contract_name=contract_name,
        strike=strike,
        resolution_at=resolution_at,
        last_trade_at=last_trade_at,
    )
    if not w_data:
        if "HIGH" in ticker or "LOW" in ticker:
            logger.info(f"TRACE: No weather data for {ticker}")
        return False, "", 0.0, ["no_weather_model_data"], False, 1.0, 3, 0.05, pricing_trace

    semantics = resolve_weather_contract(
        ticker=ticker,
        contract_name=contract_name,
        strike=strike,
    )
    if semantics is None:
        return False, "", 0.0, [f"unsupported_weather_contract: {ticker}"], False, 1.0, 3, 0.05, pricing_trace
    if semantics.ambiguous:
        return (
            False,
            "",
            0.0,
            [f"ambiguous_weather_contract_semantics ({ticker})"],
            False,
            1.0,
            3,
            0.05,
            pricing_trace,
        )

    mode = semantics.mode

    # One canonical probability path serves production, replay, and tests.
    # Tests that need controlled inputs patch calculate_pricing explicitly;
    # runtime behavior must never depend on stack-frame names.
    from forecast.pricing_engine import calculate_pricing
    from config import DB_PATH

    try:
        pricing = calculate_pricing(
            ticker,
            w_data,
            hours_to_res=hours_to_res,
            contract_name=contract_name,
            strike=strike,
            db_path=DB_PATH,
        )
    except Exception as pr_err:
        return False, "", 0.0, [f"pricing_engine_error: {pr_err}"], False, 1.0, 3, 0.05, pricing_trace

    pricing_trace = dict(pricing)
    pricing_trace.update(
        {
            "provider_at": str(w_data.get("timestamp") or ""),
            "target_date": w_data.get("target_local_date"),
            "target_hour": w_data.get("target_local_hour"),
            "provider_mode": str(w_data.get("provider_mode") or "deterministic_multi_model"),
            "gfs_members": len(w_data.get("members_high") or w_data.get("members_low") or w_data.get("members_temp") or []),
            "ecmwf_members": len((w_data.get("ecmwf") or {}).get("members_high") or (w_data.get("ecmwf") or {}).get("members_low") or (w_data.get("ecmwf") or {}).get("members_temp") or []),
            "aigfs_members": len((w_data.get("aigefs") or {}).get("members_high") or (w_data.get("aigefs") or {}).get("members_low") or (w_data.get("aigefs") or {}).get("members_temp") or []),
        }
    )

    model_prob = pricing["q_hat"]
    q_gfs = pricing["q_gfs"]
    q_ecmwf = pricing["q_ecmwf"]
    q_aigfs = pricing.get("q_aigfs", pricing.get("q_graphcast"))
    lambda_scaler = pricing["lambda_scaler"]
    gfs_weight = pricing["gfs_weight"]
    ecmwf_weight = pricing["ecmwf_weight"]
    hrrr_weight = pricing["hrrr_weight"]
    physics_gfs_f = float((pricing.get("physics_gfs") or {}).get("adjustment_f") or 0.0)
    physics_ecmwf_f = float((pricing.get("physics_ecmwf") or {}).get("adjustment_f") or 0.0)

    provider_mode = str(w_data.get("provider_mode") or "deterministic_multi_model")
    provider_size_multiplier = 1.0
    consensus_projection = pricing.get("consensus_projection")
    intraday = dict(w_data.get("intraday") or {})
    station_derivative = intraday.get("metar_temp_trend_f_per_hr")
    station_local_hour = 0.0
    if station_derivative is not None and mode == "HIGH":
        try:
            from zoneinfo import ZoneInfo
            from data.kalshi_weather_monitor import STATIONS, resolve_weather_city_key

            city_key = resolve_weather_city_key(ticker, contract_name=contract_name)
            station_tz = str((STATIONS.get(city_key) or {}).get("tz") or "UTC")
            local_now = datetime.now(timezone.utc).astimezone(ZoneInfo(station_tz))
            station_local_hour = local_now.hour + (local_now.minute / 60.0)
        except Exception:
            station_local_hour = 0.0
    derivative_f_per_hr, peak_heating_concluded = calculate_diurnal_heating_derivative(
        [0.0, float(station_derivative)] if station_derivative is not None else [],
        current_local_hour=station_local_hour,
    )
    pricing_trace["station_derivative_f_per_hr"] = derivative_f_per_hr if station_derivative is not None else None
    pricing_trace["peak_heating_concluded"] = peak_heating_concluded
    convergence = _convergence_guardrail(q_gfs, q_ecmwf)
    convergence_multiplier = float(convergence["convergence_multiplier"])
    divergence_gap = float(convergence["divergence_gap"])
    divergence_size_multiplier = float(convergence["divergence_size_multiplier"])
    catastrophic_divergence = bool(convergence["catastrophic_divergence"])
    model_prob = apply_divergence_probability_guard(
        model_prob,
        q_gfs,
        q_ecmwf,
    )
    # Persist the exact YES-basis probability used by the production decision
    # before any downstream guardrail can veto the market.  RBI evidence must
    # learn from priced opportunities as well as submitted orders.
    pricing_trace["q_decision_guarded"] = model_prob
    if catastrophic_divergence:
        return (
            False,
            "",
            0.0,
            [f"catastrophic_divergence_veto (gap={divergence_gap:.2%})"],
            False,
            1.0,
            3,
            0.05,
            pricing_trace,
        )

    q_gfs_label = f"{q_gfs:.1%}" if q_gfs is not None else "NA"
    q_ecmwf_label = f"{q_ecmwf:.1%}" if q_ecmwf is not None else "NA"
    q_aigfs_label = f"{q_aigfs:.1%}" if q_aigfs is not None else "NA"
    logger.info(
        "Sovereign Convergence: %s GFS=%s EC=%s AIGFS=%s gap=%.1f%% -> %.2fx/%.2fx",
        ticker,
        q_gfs_label,
        q_ecmwf_label,
        q_aigfs_label,
        divergence_gap * 100.0,
        convergence_multiplier,
        divergence_size_multiplier,
    )

    edge_yes = (model_prob - ask_yes) if ask_yes > 0 else None
    edge_no = ((1.0 - model_prob) - ask_no) if ask_no > 0 else None
    net_edge_yes = _weather_net_edge(model_prob, ask_yes)
    net_edge_no = _weather_net_edge(1.0 - model_prob, ask_no)

    # Predictive sigma and AIGFS disagreement are distinct uncertainty signals.
    # Sigma shapes this multiplier; AIGFS already shapes the probability kernel
    # and reaches the Kelly solver once through ``effective_sizing_multiplier``.
    if mode in {"RAIN", "SNOW"}:
        sigma_raw = w_data.get("sigma_precip", w_data.get("sigma_low", 2.0))
    elif mode == "TEMP":
        sigma_raw = w_data.get("sigma_temp", w_data.get("sigma_high", 2.0))
    elif mode == "HIGH":
        sigma_raw = w_data.get("sigma_high", 2.0)
    else:
        sigma_raw = w_data.get("sigma_low", 2.0)
    sigma = float(sigma_raw)

    # v19.1.11: Sovereign Instrumentation
    try:
        from monitoring import metrics
        metrics.WEATHER_SIGMA_GAUGE.labels(ticker=ticker).set(sigma)
    except Exception:
        pass

    # v19.5: Sovereign Survival — Hard Sigma Veto (Evaluates raw physical data only)
    if sigma_raw > KALSHI_MAX_SIGMA:
        logger.warning(f"Sovereign Chaos Veto: {ticker} Sigma={sigma_raw:.1f}F > {KALSHI_MAX_SIGMA}")
        return (
            False,
            "",
            0.0,
            [f"chaos_veto (sigma={sigma_raw:.1f} > {KALSHI_MAX_SIGMA})"],
            False,
            1.0,
            3,
            0.05,
            pricing_trace,
        )

    # sigma_mult: 1.0 at 2.0F sigma, 1.25 at 1.0F sigma, 0.5 at 4.0F sigma
    sigma_mult = max(0.3, min(1.3, 1.5 - (sigma / 4.0)))

    premium_yes_threshold = float(KALSHI_EXPENSIVE_YES_THRESHOLD)
    # The premium-price gate must never be weaker than the universal EV gate.
    premium_yes_net_edge_floor = max(
        float(EV_THRESHOLD),
        float(KALSHI_EXPENSIVE_YES_MIN_NET_EDGE),
    )
    premium_yes_size_multiplier = max(0.25, float(KALSHI_EXPENSIVE_YES_SIZE_MULTIPLIER))
    if (
        ask_yes > 0
        and edge_yes is not None
        and edge_yes > 0
        and ask_yes >= premium_yes_threshold
        and net_edge_yes is not None
        and net_edge_yes < premium_yes_net_edge_floor
    ):
        return (
            False,
            "",
            0.0,
            [
                "expensive_yes_headroom_veto "
                f"(ask={ask_yes:.2f} net_ev={net_edge_yes:.3f} < {premium_yes_net_edge_floor:.3f})"
            ],
            False,
            1.0,
            3,
            0.05,
            pricing_trace,
        )

    min_allowed_price = min_contract_price_for_mode(
        mode,
        ticker=ticker,
        contract_name=contract_name,
    )
    if ask_yes > 0 and edge_yes is not None and edge_yes > 0 and ask_yes < min_allowed_price:
        return (
            False,
            "",
            0.0,
            [f"penny_veto (ask={ask_yes:.2f} < {min_allowed_price:.2f})"],
            False,
            1.0,
            3,
            0.05,
            pricing_trace,
        )
    if ask_no > 0 and edge_no is not None and edge_no > 0 and ask_no < min_allowed_price:
        return (
            False,
            "",
            0.0,
            [f"penny_veto (ask={ask_no:.2f} < {min_allowed_price:.2f})"],
            False,
            1.0,
            3,
            0.05,
            pricing_trace,
        )

    # Forensic Audit Log
    edge_yes_display = f"{edge_yes:.1%}" if edge_yes is not None else "n/a"
    edge_no_display = f"{edge_no:.1%}" if edge_no is not None else "n/a"
    logger.info(
        f"TRACE: {ticker} | p={model_prob:.1%} Edge_Y={edge_yes_display} "
        f"Edge_N={edge_no_display} Sigma={sigma:.1f}F s_mult={sigma_mult:.2f}"
    )

    # Cloud/radiation now shape temperature before the contract CDF. A second
    # mode-agnostic hard veto would double-count the same physical evidence.
    peak_tcdc = w_data.get("peak_tcdc", 0.0)
    peak_ssrd = w_data.get("peak_ssrd")

    # Hourly "between" bins are now first-class; broader daily bin support stays off
    # until we have stronger live evidence across those markets.
    if semantics.comparator == "between" and mode != "TEMP":
        return (
            False,
            "",
            0.0,
            ["banned_bin_contract_type"],
            False,
            1.0,
            3,
            0.05,
            pricing_trace,
        )

    narrow_bin_size_multiplier = 1.0

    effective_ev_threshold_yes = EV_THRESHOLD
    effective_ev_threshold_no = EV_THRESHOLD

    if net_edge_yes is not None and net_edge_yes >= effective_ev_threshold_yes:
        is_taker = True
        if (
            mode in {"HIGH", "LOW", "TEMP"}
            and semantics.comparator in {"gt", "lt"}
            and consensus_projection is not None
        ):
            headroom_f = _projection_headroom_f(semantics, float(consensus_projection), "YES")
            pricing_trace["selected_headroom_f"] = headroom_f
            if headroom_f < float(KALSHI_MIN_MODEL_HEADROOM_F):
                return (
                    False, "", 0.0,
                    [f"model_headroom_veto ({headroom_f:.2f}F < {KALSHI_MIN_MODEL_HEADROOM_F:.2f}F)"],
                    True, 1.0, 3, 0.05, pricing_trace,
                )
        if mode == "HIGH" and peak_heating_concluded:
            return (
                False, "", 0.0,
                [f"post_peak_heating_yes_veto (dT_dt={derivative_f_per_hr:.2f}F/hr)"],
                True, 1.0, 3, 0.05, pricing_trace,
            )
        factors = [
            f"model_p={model_prob:.1%}",
            f"edge={edge_yes:.1%}",
            f"net_ev={net_edge_yes:.1%}",
            f"conv_mult={convergence_multiplier:.1f}x",
            f"sigma_mult={sigma_mult:.2f}x",
            f"blend=GFS{gfs_weight:.0%}/EC{ecmwf_weight:.0%}/HRRR{hrrr_weight:.0%}",
            f"aigfs_lambda={lambda_scaler:.2f}",
            f"physics_f=GFS{physics_gfs_f:+.2f}/EC{physics_ecmwf_f:+.2f}",
            f"div_gap={divergence_gap:.1%}",
            f"TCDC={peak_tcdc:.1f}%",
            f"wx_provider={provider_mode}",
        ]
        if peak_ssrd is not None:
            factors.append(f"SSRD={float(peak_ssrd):.0f}W/m2")
        conv_tier = 0
        sizing_cap = KALSHI_KELLY_CAP
        factors.append("tier=continuous")

        # Return guarded model probability and its separate sizing multiplier.
        sizing_multiplier = (
            convergence_multiplier
            * sigma_mult
            * divergence_size_multiplier
            * provider_size_multiplier
            * narrow_bin_size_multiplier
        )
        if provider_size_multiplier < 1.0:
            factors.append(f"provider_haircut={provider_size_multiplier:.2f}x")
        if narrow_bin_size_multiplier < 1.0:
            factors.append(f"narrow_bin_haircut={narrow_bin_size_multiplier:.2f}x")
        if ask_yes >= premium_yes_threshold:
            sizing_multiplier *= premium_yes_size_multiplier
            factors.append(
                f"premium_yes_haircut={premium_yes_size_multiplier:.2f}x"
            )
        pricing_trace["q_decision_guarded"] = model_prob
        return True, "YES", model_prob, factors, is_taker, sizing_multiplier, conv_tier, sizing_cap, pricing_trace

    if net_edge_no is not None and net_edge_no >= effective_ev_threshold_no:
        is_taker = True
        if (
            mode in {"HIGH", "LOW", "TEMP"}
            and semantics.comparator in {"gt", "lt"}
            and consensus_projection is not None
        ):
            headroom_f = _projection_headroom_f(semantics, float(consensus_projection), "NO")
            pricing_trace["selected_headroom_f"] = headroom_f
            if headroom_f < float(KALSHI_MIN_MODEL_HEADROOM_F):
                return (
                    False, "", 0.0,
                    [f"model_headroom_veto ({headroom_f:.2f}F < {KALSHI_MIN_MODEL_HEADROOM_F:.2f}F)"],
                    True, 1.0, 3, 0.05, pricing_trace,
                )
        factors = [
            f"model_p={model_prob:.1%}",
            f"edge={edge_no:.1%}",
            f"net_ev={net_edge_no:.1%}",
            f"conv_mult={convergence_multiplier:.1f}x",
            f"sigma_mult={sigma_mult:.2f}x",
            f"blend=GFS{gfs_weight:.0%}/EC{ecmwf_weight:.0%}/HRRR{hrrr_weight:.0%}",
            f"aigfs_lambda={lambda_scaler:.2f}",
            f"physics_f=GFS{physics_gfs_f:+.2f}/EC{physics_ecmwf_f:+.2f}",
            f"div_gap={divergence_gap:.1%}",
            f"wx_provider={provider_mode}",
        ]
        conv_tier = 0
        sizing_cap = KALSHI_KELLY_CAP
        no_prob = 1.0 - model_prob
        factors.append("tier=continuous")

        sizing_multiplier = (
            convergence_multiplier
            * sigma_mult
            * divergence_size_multiplier
            * provider_size_multiplier
            * narrow_bin_size_multiplier
        )
        if provider_size_multiplier < 1.0:
            factors.append(f"provider_haircut={provider_size_multiplier:.2f}x")
        if narrow_bin_size_multiplier < 1.0:
            factors.append(f"narrow_bin_haircut={narrow_bin_size_multiplier:.2f}x")
        pricing_trace["q_decision_guarded"] = model_prob
        return True, "NO", no_prob, factors, is_taker, sizing_multiplier, conv_tier, sizing_cap, pricing_trace

    pricing_trace["q_decision_guarded"] = model_prob
    return False, "", 0.0, ["insufficient_edge"], False, 1.0, 3, 0.05, pricing_trace


def evaluate_contract(
    contract: dict,
    bars_5m: list[dict],
    bars_30m: list[dict],
    bars_1h: list[dict],
    bars_4h: list[dict],
    yes_quote: dict,
    no_quote: dict,
    bankroll: float = 100.0,
    deployed_pct: float = 0.0,
    open_positions_count: int = 0,
    same_event_open: bool = False,
    same_event_exposure_usd: float = 0.0,
) -> Optional[StrategyResult]:
    """
    Evaluate all strategy families for a contract and return the best
    StrategyResult, or None if no strategy passes + economics gate.
    """
    ticker = contract.get("local_symbol", "")
    is_weather = _is_weather_ticker(ticker)
    hours_to_res = _hours_to_resolution(contract.get("last_trade_at", ""))

    if is_weather:
        # Weather alpha requires fresh shared truth before any market evaluation.
        w_data = get_weather_data(ticker)
        if w_data:
            data_ts = w_data.get("timestamp", 0)
            age_m = (time.time() - data_ts) / 60.0

            freshness_limit = weather_freshness_limit_minutes(
                ticker,
                contract_name=contract.get("contract_name", ""),
            )

            if age_m > freshness_limit:
                return StrategyResult(
                    strategy_family="vetoed",
                    side="NONE",
                    q_hat=0.0,
                    ev=0.0,
                    ev_yes=0.0,
                    ev_no=0.0,
                    confidence=0.0,
                    uncertainty_penalty=0.0,
                    econ_approved=False,
                    veto_reason=(
                        f"stale_weather_model_data ({age_m:.0f}m old "
                        f"> {freshness_limit}m limit)"
                    ),
                    position_fraction=0.0,
                    position_contracts=0,
                    top_factors=[],
                    hours_to_resolution=hours_to_res,
                )
        else:
            return StrategyResult(
                strategy_family="vetoed",
                side="NONE",
                q_hat=0.0,
                ev=0.0,
                ev_yes=0.0,
                ev_no=0.0,
                confidence=0.0,
                uncertainty_penalty=0.0,
                econ_approved=False,
                veto_reason="missing_weather_data",
                position_fraction=0.0,
                position_contracts=0,
                top_factors=[],
                hours_to_resolution=hours_to_res,
            )

    ask_yes = float(yes_quote.get("ask") or 0.0)
    ask_no = float(no_quote.get("ask") or 0.0)
    # SRE Pillar 2: Liquidity Awareness (Top-of-Book depth)
    ask_size_yes = int(yes_quote.get("ask_size") or 0)
    ask_size_no = int(no_quote.get("ask_size") or 0)

    spread = max(
        float(yes_quote.get("spread") or 0.0),
        float(no_quote.get("spread") or 0.0),
    )

    # ADVERSARY FIX #5: Pair-aware quote freshness SLA.
    # YES and NO quotes are harvested independently, so the older leg controls.
    age_seconds = _max_quote_age_seconds(yes_quote, no_quote)
    if age_seconds is not None:
        limit = 600 if is_weather else 120

        if age_seconds > limit:
            logger.warning(
                f"evaluate_contract veto: stale_market_data ({age_seconds:.1f}s old) "
                f"for {contract.get('local_symbol')}"
            )
            return StrategyResult(
                strategy_family="vetoed",
                side="NONE",
                q_hat=0.0,
                ev=0.0,
                ev_yes=0.0,
                ev_no=0.0,
                confidence=0.0,
                uncertainty_penalty=0.0,
                econ_approved=False,
                veto_reason=f"stale_market_data ({age_seconds:.1f}s old)",
                position_fraction=0.0,
                position_contracts=0,
                top_factors=[],
                hours_to_resolution=hours_to_res,
            )

    if ask_yes <= 0.0 and ask_no <= 0.0:
        logger.debug(
            f"evaluate_contract: missing quotes for {contract.get('local_symbol')}"
        )
        return StrategyResult(
            strategy_family="vetoed",
            side="NONE",
            q_hat=0.0,
            ev=0.0,
            ev_yes=0.0,
            ev_no=0.0,
            confidence=0.0,
            uncertainty_penalty=0.0,
            econ_approved=False,
            veto_reason="missing_quotes",
            position_fraction=0.0,
            position_contracts=0,
            top_factors=[],
            hours_to_resolution=hours_to_res,
        )

    if not is_weather:
        return StrategyResult(
            strategy_family="vetoed",
            side="NONE",
            q_hat=0.0,
            ev=0.0,
            ev_yes=0.0,
            ev_no=0.0,
            confidence=0.0,
            uncertainty_penalty=0.0,
            econ_approved=False,
            veto_reason="non_weather_contract_unsupported",
            position_fraction=0.0,
            position_contracts=0,
            top_factors=[],
            hours_to_resolution=hours_to_res,
        )

    if is_weather:
        from forecast.weather_contracts import weather_mode_for_ticker

        w_res = _strategy_weather_details(
            ticker,
            ask_yes,
            ask_no,
            hours_to_res,
            contract_name=str(contract.get("contract_name") or ""),
            strike=float(contract.get("strike") or 0.0),
            resolution_at=str(contract.get("resolution_at") or ""),
            last_trade_at=str(contract.get("last_trade_at") or ""),
        )
        weather_factors = list(w_res[3] or [])
        if not w_res[0]:
            pricing_trace = dict(w_res[8] or {}) if len(w_res) > 8 else {}
            traced_q_hat = pricing_trace.get(
                "q_decision_guarded",
                pricing_trace.get("q_hat"),
            )
            try:
                scored_q_hat = (
                    max(0.01, min(0.99, float(traced_q_hat)))
                    if traced_q_hat is not None
                    else 0.0
                )
            except (TypeError, ValueError):
                scored_q_hat = 0.0
            return StrategyResult(
                strategy_family="vetoed",
                side="NONE",
                q_hat=scored_q_hat,
                ev=0.0,
                ev_yes=0.0,
                ev_no=0.0,
                confidence=float(scored_q_hat or 0.0),
                uncertainty_penalty=0.0,
                econ_approved=False,
                veto_reason=str(weather_factors[0] if weather_factors else "no_strategy_signal"),
                position_fraction=0.0,
                position_contracts=0,
                top_factors=weather_factors,
                ask_yes=ask_yes,
                ask_no=ask_no,
                hours_to_resolution=hours_to_res,
                is_taker_override=False,
                model_prob_gfs=pricing_trace.get("q_gfs"),
                model_prob_ecmwf=pricing_trace.get("q_ecmwf"),
                weather_mode=str(weather_mode_for_ticker(ticker) or ""),
                pricing_trace=pricing_trace,
            )

        best_side = str(w_res[1] or "NONE")
        chosen_prob = float(w_res[2] or 0.0)
        best_factors = weather_factors
        best_is_taker = bool(w_res[4])
        best_multiplier = float(w_res[5] or 1.0)
        best_sizing_cap = float(w_res[7] or 0.05)
        pricing_trace = dict(w_res[8] or {}) if len(w_res) > 8 else {}
        if same_event_open:
            best_multiplier *= SAME_EVENT_PENALTY
            best_factors.append(f"same_event_haircut={SAME_EVENT_PENALTY:.2f}x")
        chosen_side_prob = max(0.01, min(0.99, float(chosen_prob)))
        hub_name = _get_city_hub(
            ticker,
            contract_name=str(contract.get("contract_name") or ""),
        )
        hub_default = float(
            (HUB_PARAMS.get(hub_name) or {}).get("hard_rbi_threshold", 0.0)
        )
        hub_conviction_floor = float(
            get_dynamic_param(f"{hub_name}.hard_rbi_threshold", hub_default)
        )
        if hub_conviction_floor > 0.0 and chosen_side_prob < hub_conviction_floor:
            return StrategyResult(
                strategy_family="vetoed",
                side="NONE",
                q_hat=(chosen_side_prob if best_side == "YES" else 1.0 - chosen_side_prob),
                ev=0.0,
                ev_yes=0.0,
                ev_no=0.0,
                confidence=chosen_side_prob,
                uncertainty_penalty=0.0,
                econ_approved=False,
                veto_reason=(
                    f"hub_conviction_floor_veto ({hub_name} "
                    f"{chosen_side_prob:.3f} < {hub_conviction_floor:.3f})"
                ),
                position_fraction=0.0,
                position_contracts=0,
                top_factors=best_factors,
                ask_yes=ask_yes,
                ask_no=ask_no,
                hours_to_resolution=hours_to_res,
                is_taker_override=True,
                model_prob_gfs=pricing_trace.get("q_gfs"),
                model_prob_ecmwf=pricing_trace.get("q_ecmwf"),
                weather_mode=str(weather_mode_for_ticker(ticker) or ""),
                pricing_trace=pricing_trace,
            )
        if hub_conviction_floor > 0.0:
            best_factors.append(
                f"hub_floor={hub_name}:{hub_conviction_floor:.2f}"
            )
        q_hat = chosen_side_prob if best_side == "YES" else (1.0 - chosen_side_prob)
        # SRE Pillar 6: Rule 1 Probability Input Clamps
        q_hat = max(0.01, min(0.99, float(q_hat)))
        position_cap_usd = get_kalshi_position_cap_usd(chosen_prob)
        event_risk_budget_usd = max(0.0, bankroll * MAX_RISK_PER_EVENT_PCT)
        event_risk_remaining_usd = max(
            0.0,
            event_risk_budget_usd - max(0.0, float(same_event_exposure_usd)),
        )
        position_cap_usd = min(position_cap_usd, event_risk_remaining_usd)
        if position_cap_usd <= 0.0:
            return StrategyResult(
                strategy_family="vetoed",
                side="NONE",
                q_hat=q_hat,
                ev=0.0,
                ev_yes=0.0,
                ev_no=0.0,
                confidence=0.0,
                uncertainty_penalty=0.0,
                econ_approved=False,
                veto_reason="event_risk_cap_reached",
                position_fraction=0.0,
                position_contracts=0,
                top_factors=best_factors,
                ask_yes=ask_yes,
                ask_no=ask_no,
                hours_to_resolution=hours_to_res,
                is_taker_override=False,
                model_prob_gfs=pricing_trace.get("q_gfs"),
                model_prob_ecmwf=pricing_trace.get("q_ecmwf"),
                weather_mode=str(weather_mode_for_ticker(ticker) or ""),
                pricing_trace=pricing_trace,
            )

        p_cost = ask_yes if best_side == "YES" else ask_no

        if p_cost <= 0.0:
            return StrategyResult(
                strategy_family="vetoed",
                side="NONE",
                q_hat=q_hat,
                ev=0.0,
                ev_yes=0.0,
                ev_no=0.0,
                confidence=0.0,
                uncertainty_penalty=0.0,
                econ_approved=False,
                veto_reason=f"missing_quotes_{best_side.lower()}",
                position_fraction=0.0,
                position_contracts=0,
                top_factors=best_factors,
                ask_yes=ask_yes,
                ask_no=ask_no,
                hours_to_resolution=hours_to_res,
                is_taker_override=False,
                model_prob_gfs=pricing_trace.get("q_gfs"),
                model_prob_ecmwf=pricing_trace.get("q_ecmwf"),
                weather_mode=str(weather_mode_for_ticker(ticker) or ""),
                pricing_trace=pricing_trace,
            )

        w_mode = weather_mode_for_ticker(ticker)

        approved, veto_reason = _weather_market_gate(
            ask_yes=ask_yes,
            ask_no=ask_no,
            spread=spread,
            hours_to_resolution=hours_to_res,
            open_positions_count=open_positions_count,
            deployed_pct=deployed_pct,
            mode=w_mode,
            ticker=ticker,
            contract_name=str(contract.get("contract_name") or ""),
            side=best_side,
            held_probability=chosen_prob,
        )
        # SRE Pillar 1: Clamped pricing/utility inputs
        q = chosen_side_prob
        ask_yes_clamped = max(0.0, min(1.0, float(ask_yes)))
        ask_no_clamped = max(0.0, min(1.0, float(ask_no)))

        # The exact AIGFS uncertainty scaler already used by canonical pricing.
        # Re-fetching weather here could mix provider vintages inside one decision.
        lambda_val = float(pricing_trace.get("lambda_scaler") or 1.0)
        effective_sizing_multiplier = max(0.0, best_multiplier) / max(1e-9, lambda_val)
        best_factors.append(f"effective_size_mult={effective_sizing_multiplier:.2f}x")

        # 2. Taker-only sizing and routing (operator-selected production policy).
        p_T = ask_yes_clamped if best_side == "YES" else ask_no_clamped
        ask_depth = ask_size_yes if best_side == "YES" else ask_size_no
        level2_asks_T = [{"price": p_T, "qty": ask_depth}] if ask_depth > 0 else None
        n_T = calculate_continuous_sizing(
            market_price=p_T,
            model_prob=q,
            capital_base=bankroll,
            multiplier=effective_sizing_multiplier,
            cap_pct=best_sizing_cap,
            conv_tier=3,
            hours_to_res=hours_to_res,
            lane_ev_threshold=0.05,
            book_asks=level2_asks_T,
            position_cap_usd=position_cap_usd,
        )
        f_star_T, phi_T, _ = solve_optimal_size(
            q,
            p_T,
            maker=False,
            bankroll=bankroll,
            lambda_scaler=1.0 / max(1e-9, effective_sizing_multiplier),
            cov_charge=1.0,
            level2_asks=level2_asks_T,
        )
        best_is_taker = True
        p_cost = p_T
        n_contracts = n_T
        f_star_chosen = f_star_T
        phi_cost = phi_T

        ev_chosen = q - p_cost - phi_cost
        ev_yes = ev_chosen if best_side == "YES" else -1.0
        ev_no = ev_chosen if best_side == "NO" else -1.0

        # EV Gate: positive f_star / EV
        if approved and f_star_chosen <= 0.0:
            approved = False
            veto_reason = f"fee_adjusted_ev_too_low (f_star={f_star_chosen:.4f})"

        weather_model_prob_gfs = pricing_trace.get("q_gfs")
        weather_model_prob_ecmwf = pricing_trace.get("q_ecmwf")
        weather_mode = str(w_mode or "")

        if approved:
            # STRICT SRE Pillar 2: Top-of-book hard clamp for taker
            p_cost_size = ask_size_yes if best_side == "YES" else ask_size_no
            if best_is_taker and p_cost_size > 0:
                n_contracts = min(n_contracts, p_cost_size)

            if n_contracts > KALSHI_MAX_QTY_PER_POSITION:
                logger.info(
                    "Sovereign Survival: Capping %s qty %s -> %s",
                    ticker,
                    n_contracts,
                    KALSHI_MAX_QTY_PER_POSITION,
                )
                n_contracts = KALSHI_MAX_QTY_PER_POSITION

            total_cost = estimate_kalshi_order_cost_usd(
                n_contracts,
                p_cost,
                maker=False,
            )

            # Enforce strict SRE Risk Ceilings
            max_usd = min(position_cap_usd, bankroll * best_sizing_cap)
            if max_usd is not None:
                cost_limit = float(max_usd)
                if total_cost > cost_limit:
                    clamped_qty = max_kalshi_contracts_for_budget(
                        p_cost,
                        cost_limit,
                        maker=False,
                    )
                    n_contracts = min(max(0, n_contracts), clamped_qty, KALSHI_MAX_QTY_PER_POSITION)
                    total_cost = estimate_kalshi_order_cost_usd(
                        n_contracts,
                        p_cost,
                        maker=False,
                    )
                    logger.info(
                        f"Sovereign SRE Clamp: Clamping {ticker} cost to {cost_limit:.2f} USD (qty {n_contracts})"
                    )
        else:
            n_contracts, total_cost = 0, 0.0

        actual_fraction = total_cost / bankroll if bankroll > 0 else 0.0
        return StrategyResult(
            strategy_family="weather_physics",
            side=best_side,
            q_hat=q_hat,
            ev=ev_chosen,
            ev_yes=ev_yes,
            ev_no=ev_no,
            confidence=chosen_prob,
            uncertainty_penalty=max(0.0, min(0.5, 1.0 - best_multiplier)),
            econ_approved=approved,
            veto_reason=veto_reason,
            position_fraction=actual_fraction,
            position_contracts=n_contracts,
            top_factors=best_factors,
            ask_yes=ask_yes,
            ask_no=ask_no,
            hours_to_resolution=hours_to_res,
            is_taker_override=best_is_taker,
            model_prob_gfs=weather_model_prob_gfs,
            model_prob_ecmwf=weather_model_prob_ecmwf,
            weather_mode=weather_mode,
            pricing_trace=pricing_trace,
        )

    return StrategyResult(
        strategy_family="vetoed",
        side="NONE",
        q_hat=0.0,
        ev=0.0,
        ev_yes=0.0,
        ev_no=0.0,
        confidence=0.0,
        uncertainty_penalty=0.0,
        econ_approved=False,
        veto_reason="non_weather_contract_unsupported",
        position_fraction=0.0,
        position_contracts=0,
        top_factors=[],
        hours_to_resolution=hours_to_res,
    )


def check_strike_consistency(ticker: str, side: str, open_positions: list[dict]) -> tuple[bool, str]:
    """
    SPEC §4.8: Allow same-event disjoint-bracket pairs (distribution spreads)
    while keeping the same-side-same-contract ban.
    """
    event_key = _ticker_event_key(ticker)

    for p in open_positions:
        p_ticker = p.get("local_symbol", "")
        if _ticker_event_key(p_ticker) != event_key:
            continue

        p_side = p.get("side", "").upper()

        # Keep same-side-same-contract ban
        if ticker == p_ticker and side == p_side:
            return False, f"duplicate_contract_veto: already have {side} on {p_ticker}"

        # Opposite-side hedge guard on the exact same contract
        if ticker == p_ticker and side != p_side:
            return False, f"hedge_guard: cannot bet opposite side on existing strike {p_ticker}"

    return True, ""


def _ticker_event_key(ticker: str) -> str:
    parts = str(ticker or "").split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return str(ticker or "")


def _apply_best_event_slot_selection(entries: list[dict]) -> list[dict]:
    """
    Keep one best candidate per settlement slot and mark the rest as explicit vetoes.

    This preserves operator visibility while stopping the engine from spraying
    adjacent strikes in the same city/hour or city/day.
    """
    best_by_event: dict[str, dict] = {}

    for candidate in entries:
        result = candidate.get("result")
        contract = candidate.get("contract") or {}
        if (
            result is None
            or not getattr(result, "econ_approved", False)
            or str(getattr(result, "side", "")) not in {"YES", "NO"}
        ):
            continue

        event_key = _ticker_event_key(str(contract.get("local_symbol") or ""))
        incumbent = best_by_event.get(event_key)
        if incumbent is None or float(candidate.get("rank_score") or 0.0) > float(
            incumbent.get("rank_score") or 0.0
        ):
            if incumbent is not None:
                incumbent_result = incumbent["result"]
                incumbent_result.econ_approved = False
                incumbent_result.veto_reason = (
                    "same_event_best_strike_selected "
                    f"({contract.get('local_symbol')})"
                )
                incumbent_result.position_fraction = 0.0
                incumbent_result.position_contracts = 0
                incumbent["rank_score"] = 0.0
            best_by_event[event_key] = candidate
        else:
            winner_symbol = str(
                (incumbent.get("contract") or {}).get("local_symbol") or ""
            )
            result.econ_approved = False
            result.veto_reason = f"same_event_best_strike_selected ({winner_symbol})"
            result.position_fraction = 0.0
            result.position_contracts = 0
            candidate["rank_score"] = 0.0

    entries.sort(key=lambda x: x["rank_score"], reverse=True)
    return entries

def evaluate_all_contracts(
    active_contracts: list[dict],
    get_bars_fn,  # callable(contract_id, interval) -> list[dict]
    get_quotes_fn,  # callable(market_id, strike, last_trade_at) -> dict
    bankroll: float = 100.0,
    deployed_pct: float = 0.0,
    open_positions_count: int = 0,
    open_event_families: Optional[dict] = None,
    macro_context: Optional[dict] = None,
    open_positions: Optional[list[dict]] = None,
) -> list[dict]:
    snapshots = build_market_snapshots(
        active_contracts,
        get_bars_fn=get_bars_fn,
        get_quotes_fn=get_quotes_fn,
    )
    return evaluate_market_snapshots(
        snapshots=snapshots,
        bankroll=bankroll,
        deployed_pct=deployed_pct,
        open_positions_count=open_positions_count,
        open_event_families=open_event_families,
        macro_context=macro_context,
        open_positions=open_positions,
    )


def evaluate_market_snapshots(
    snapshots: list[MarketSnapshot],
    bankroll: float = 100.0,
    deployed_pct: float = 0.0,
    open_positions_count: int = 0,
    open_event_families: Optional[dict] = None,
    macro_context: Optional[dict] = None,
    open_positions: Optional[list[dict]] = None,
) -> list[dict]:
    """
    Evaluate canonical market snapshots and return ranked entry candidates.

    Runtime should think in one market object, then route to YES/NO contract rows
    only after side selection.
    """
    if open_event_families is None:
        open_event_families = {}
    if open_positions is None:
        open_positions = []

    # Local frequency map to track evaluations in the SAME tick
    current_tick_counts = open_event_families.copy()

    # v19.1.10: Regional Hub Exposure Tracking (Net Directional Delta Hedging)
    hub_signed_exposures = {}
    family_exposures_usd: dict[str, float] = {}
    for pos in open_positions:
        p_ticker = pos.get("local_symbol", "") or pos.get("ticker", "")
        if not p_ticker:
            continue
        p_hub = _get_city_hub(
            p_ticker,
            contract_name=str(pos.get("contract_name") or ""),
        )
        pos_usd = get_kalshi_position_snapshot_exposure_usd(pos)

        p_side = str(pos.get("side") or "").upper()
        p_prefix = p_ticker.split("-")[0].upper()
        family_exposures_usd[p_prefix] = family_exposures_usd.get(p_prefix, 0.0) + pos_usd

        # Assign Cool/Wet outcomes a negative sign (-1.0) and Warm/Dry outcomes a positive sign (+1.0)
        is_cool_wet_prefix = any(x in p_prefix for x in ("KXLOW", "RAIN", "KXRAIN", "KXSNOW", "KXWIND"))
        is_warm_dry_prefix = any(x in p_prefix for x in ("KXHIGH", "KXTEMP"))

        if is_cool_wet_prefix:
            sign = -1.0 if p_side == "YES" else 1.0
        elif is_warm_dry_prefix:
            sign = 1.0 if p_side == "YES" else -1.0
        else:
            sign = 1.0

        hub_signed_exposures[p_hub] = hub_signed_exposures.get(p_hub, []) + [pos_usd * sign]

    # Initial load of open hub exposure (approximate based on ticker)
    # Note: In a true state-full system, we'd query existing positions.
    # For now, we'll track within the tick.

    approved_entries = []

    if macro_context:
        logger.info(f"[strategy_engine] Anchoring evaluation in Macro Context (Risk={macro_context.get('risk_score')})")

    for snapshot in snapshots:
        yc = snapshot.yes_contract
        nc = snapshot.no_contract
        ticker = snapshot.ticker
        hours_to_res = _hours_to_resolution(snapshot.last_trade_at)

        yes_quote = snapshot.yes_quote or {}
        no_quote = snapshot.no_quote or {}
        if not yes_quote or not no_quote:
            continue

        bars_5m = snapshot.bars_5m
        bars_30m = snapshot.bars_30m
        bars_1h = snapshot.bars_1h
        bars_4h = snapshot.bars_4h

        is_weather = _is_weather_ticker(ticker)
        if not is_weather and not bars_5m:
            continue

        family = snapshot.family.upper()
        hub = _get_city_hub(ticker, contract_name=snapshot.contract_name)

        count = current_tick_counts.get(family, 0)
        current_hub_cap = get_kalshi_hub_exposure_cap(bankroll)

        max_family_cap = get_kalshi_effective_same_event_family_cap("NO", 1.0)
        if count >= max_family_cap:
            result = StrategyResult(
                strategy_family="vetoed",
                side="NONE",
                q_hat=0.0,
                ev=0.0,
                ev_yes=0.0,
                ev_no=0.0,
                confidence=0.0,
                uncertainty_penalty=0.0,
                econ_approved=False,
                veto_reason="same_event_family_cap_reached",
                position_fraction=0.0,
                position_contracts=0,
                top_factors=[],
                hours_to_resolution=hours_to_res,
            )
        else:
            result = evaluate_contract(
                contract=yc,
                bars_5m=bars_5m,
                bars_30m=bars_30m,
                bars_1h=bars_1h,
                bars_4h=bars_4h,
                yes_quote=yes_quote,
                no_quote=no_quote,
                bankroll=bankroll,
                deployed_pct=deployed_pct,
                open_positions_count=open_positions_count,
                same_event_open=(count > 0),
                same_event_exposure_usd=family_exposures_usd.get(family, 0.0),
            )
            if (
                result is not None
                and result.econ_approved
                and result.side in {"YES", "NO"}
                and count >= get_kalshi_effective_same_event_family_cap(
                    result.side,
                    result.confidence,
                )
            ):
                result.econ_approved = False
                result.veto_reason = "same_event_family_cap_reached"
                result.position_fraction = 0.0
                result.position_contracts = 0

            # Evaluate Net Directional Delta Hub Hedging cap strictly post-sizing (Phase 3 Gate 11)
            if result is not None and result.econ_approved and result.side in {"YES", "NO"} and hub != "UNKNOWN" and result.position_contracts > 0:
                current_signed_sum = sum(hub_signed_exposures.get(hub, []))
                candidate_price = (
                    float(yes_quote.get("ask") or 0.50)
                    if result.side == "YES"
                    else float(no_quote.get("ask") or 0.50)
                )

                candidate_exposure = get_kalshi_position_exposure_usd(
                    float(result.position_contracts),
                    candidate_price,
                )

                p_side = str(result.side).upper()
                p_prefix = ticker.split("-")[0].upper()
                is_cool_wet_prefix = any(x in p_prefix for x in ("KXLOW", "RAIN", "KXRAIN", "KXSNOW", "KXWIND"))
                is_warm_dry_prefix = any(x in p_prefix for x in ("KXHIGH", "KXTEMP"))
                if is_cool_wet_prefix:
                    c_sign = -1.0 if p_side == "YES" else 1.0
                elif is_warm_dry_prefix:
                    c_sign = 1.0 if p_side == "YES" else -1.0
                else:
                    c_sign = 1.0

                projected_hub_exposure = abs(current_signed_sum + (candidate_exposure * c_sign))
                if projected_hub_exposure > current_hub_cap:
                    result.econ_approved = False
                    result.veto_reason = f"hub_exposure_cap_reached ({projected_hub_exposure:.1f}/{current_hub_cap:.1f})"
                    result.position_fraction = 0.0
                    result.position_contracts = 0

            if result is not None and result.econ_approved and result.side in {"YES", "NO"}:
                selected_price = (
                    float(yes_quote.get("ask") or 0.0)
                    if result.side == "YES"
                    else float(no_quote.get("ask") or 0.0)
                )
                family_exposures_usd[family] = family_exposures_usd.get(family, 0.0) + get_kalshi_position_exposure_usd(
                    float(result.position_contracts),
                    selected_price,
                )

        if result is None:
            continue

        if result.side in {"YES", "NO"}:
            is_consistent, conflict_reason = check_strike_consistency(
                ticker,
                result.side,
                open_positions,
            )
            if not is_consistent:
                result.econ_approved = False
                result.veto_reason = conflict_reason
                result.position_fraction = 0.0
                result.position_contracts = 0

        chosen_contract = yc
        if result.side == "NO":
            chosen_contract = nc

        rank_score = result.ev * result.confidence if result.econ_approved else 0.0
        approved_entries.append(
            {
                "contract": chosen_contract,
                "result": result,
                "rank_score": rank_score,
                "snapshot": snapshot,
            }
        )

    approved_entries.sort(key=lambda x: x["rank_score"], reverse=True)
    return _apply_best_event_slot_selection(approved_entries)
