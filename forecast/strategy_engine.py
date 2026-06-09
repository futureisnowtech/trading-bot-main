"""
forecast/strategy_engine.py — Kalshi weather strategy families + economics gate + sizing.

Three strategy families (v1):
  continuation    — trend is likely to continue toward resolution
  mean_reversion  — overextended move expected to revert
  late_repricing  — mispricing before resolution; contract hasn't updated

Economics gate: real veto logic using all required inputs from spec.
Sizing: fractional Kelly, capped at 0.10 of bankroll.

Output for each candidate:
  StrategyResult(
    strategy_family, side, q_hat, ev, confidence, uncertainty_penalty,
    econ_approved, position_fraction, position_contracts, veto_reason, top_factors
  )
"""

import logging
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np

from config import (
    DB_PATH,
    MACRO_CACHE_FILE,
    KALSHI_EXPENSIVE_YES_MIN_NET_EDGE,
    KALSHI_EXPENSIVE_YES_SIZE_MULTIPLIER,
    KALSHI_EXPENSIVE_YES_THRESHOLD,
    KALSHI_KELLY_CAP,
    KALSHI_MAX_CONCURRENT_POSITIONS,
    KALSHI_MAX_DEPLOYED_PCT,
    KALSHI_MAX_RISK_PER_EVENT_PCT,
    KALSHI_SAME_EVENT_FAMILY_CAP,
    KALSHI_MIN_PRICE,
    KALSHI_MAX_SIGMA,
    KALSHI_MAX_QTY_PER_POSITION,
    KALSHI_MAX_SPREAD_RATIO,
    KALSHI_DATA_FRESHNESS_MINUTES,
    KALSHI_MAX_FEE_DRAG_PCT,
    KALSHI_MAX_USD_PER_POSITION,
    estimate_kalshi_fee_per_contract,
    estimate_kalshi_order_cost_usd,
    get_kalshi_hub_exposure_cap,
    get_kalshi_position_exposure_usd,
    max_kalshi_contracts_for_budget,
)
from forecast.market_snapshot import MarketSnapshot, build_market_snapshots
from forecast.weather_contracts import (
    is_hourly_weather_contract,
    probability_from_members,
    resolve_weather_contract,
    weather_mode_for_ticker,
)

logger = logging.getLogger(__name__)

# ── Gate thresholds ────────────────────────────────────────────────────────────

# EV must exceed this to pass the economics gate (positive edge requirement)
EV_THRESHOLD: float = 0.02  # 2 cents per $1 contract = 2% edge

# Overround hard cap — above this the house edge is too large
MAX_OVERROUND: float = 0.15  # Tightened from 0.25 to 0.15 for Kalshi

# Spread hard cap
MAX_SPREAD_DOLLARS: float = 0.12  # $0.12 per contract

# Time-to-resolution gates (v19.7: Horizon Pullback to 48h)
MIN_HOURS_TO_RES: float = 1.0
MAX_HOURS_TO_RES: float = 48.0

# v19.7: Sovereign Precision Calibration
# Raising the bar for Alpha to ensure Win-Rate Restoration.
EV_THRESHOLD: float = 0.025  # v19.9: Hardened 2.5% post-fee edge floor

# Longshot Bias Gate
MIN_IMPLIED_PROB_FOR_YES: float = 0.10  # refuse to buy YES below 10% probability

# Entropy saturation — don't trade near 0 or 1 (already resolved)
MAX_ENTROPY_FOR_ENTRY: float = 0.67  # H(p) = 0.67 nat ≈ p in [0.09, 0.91]
MIN_ENTROPY_FOR_ENTRY: float = 0.05  # don't trade if market already 95%+ certain

# Volatility cap — don't trade if log-odds are too noisy
MAX_SIGMA_T: float = 0.80

# Parity gap gate — G_t too large means pricing is internally inconsistent
MAX_PARITY_GAP_ABS: float = 0.05  # |G_t| ≤ 0.05

# Duplicate/correlated exposure penalty
SAME_EVENT_PENALTY: float = 0.50  # halve Kelly fraction if same event family open

# Late-repricing: look back this many hours for movement
LATE_REPRICING_LOOKBACK_HOURS: float = 24.0

# Sizing parameters (mapped to Sovereign config v18.33)
KELLY_CAP: float = KALSHI_KELLY_CAP
MAX_DEPLOYED_PCT: float = KALSHI_MAX_DEPLOYED_PCT
MAX_RISK_PER_EVENT_PCT: float = KALSHI_MAX_RISK_PER_EVENT_PCT
MAX_CONCURRENT_POSITIONS: int = KALSHI_MAX_CONCURRENT_POSITIONS

_HARD_ECON_VETO_PREFIXES: tuple[str, ...] = (
    "MAX_CAPITAL_EXCEEDED",
    "RESOLUTION_HORIZON_TOO_SHORT",
    "too_far_from_resolution",
    "concurrent_cap_reached",
    "overround_too_high",
    "spread_too_wide",
    "spread_ratio_veto",
    "market_near_certainty",
    "entropy_too_high",
    "sigma_too_high",
    "parity_gap_too_large",
    "fee_drag_veto",
    "longshot_bias_gate",
)


def _estimated_fee_per_contract(price: float, *, rounded: bool = False) -> float:
    return estimate_kalshi_fee_per_contract(price, rounded=rounded)


def _weather_net_edge(contract_prob: float, ask_price: float) -> float | None:
    if ask_price <= 0.0:
        return None
    return (
        float(contract_prob)
        - float(ask_price)
        - _estimated_fee_per_contract(ask_price, rounded=False)
    )


def min_contract_price_for_mode(
    mode: str,
    *,
    ticker: str = "",
    contract_name: str = "",
) -> float:
    try:
        from learning.weather_rbi import get_weather_model_blend
        blend = get_weather_model_blend(mode)
        penny_floor = blend.get("penny_threshold")
        if penny_floor is not None:
            return max(0.01, float(penny_floor))
    except Exception as e:
        logger.warning(f"Failed to fetch dynamic penny floor for mode {mode}: {e}")

    mode_str = str(mode or "").upper()
    if mode_str in ("RAIN", "TEMP") or is_hourly_weather_contract(
        ticker,
        contract_name=contract_name,
    ):
        return 0.03
    return float(KALSHI_MIN_PRICE)


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


def _is_weather_ticker(ticker: str) -> bool:
    return weather_mode_for_ticker(str(ticker or "")) is not None


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


# ── Economics gate ─────────────────────────────────────────────────────────────


def _economics_gate(
    ask_yes: float,
    ask_no: float,
    q_hat: float,
    omega_t: float,
    g_t: float,
    h_t: float,
    sigma_t: float,
    spread: float,
    hours_to_resolution: float,
    open_positions_count: int = 0,
    deployed_pct: float = 0.0,
    same_event_open: bool = False,
) -> tuple[bool, str, float, float]:
    """
    Multi-factor economics gate. No decorative checks — every factor can veto.

    Returns: (approved, veto_reason, ev_yes, ev_no)
    """
    yes_available = ask_yes > 0.0
    no_available = ask_no > 0.0

    if not yes_available and not no_available:
        return False, "missing_quotes", 0.0, 0.0

    # 0. Capital Partition (Sovereign Mandate v18.32)
    if deployed_pct >= KALSHI_MAX_DEPLOYED_PCT:
        return (
            False,
            "MAX_CAPITAL_EXCEEDED",
            0.0,
            0.0,
        )

    # 1. Minimum hours to resolution
    if hours_to_resolution < MIN_HOURS_TO_RES:
        return (
            False,
            "RESOLUTION_HORIZON_TOO_SHORT",
            0.0,
            0.0,
        )

    if hours_to_resolution > MAX_HOURS_TO_RES:
        return (
            False,
            f"too_far_from_resolution ({hours_to_resolution:.1f}h > {MAX_HOURS_TO_RES}h)",
            0.0,
            0.0,
        )

    if open_positions_count >= MAX_CONCURRENT_POSITIONS:
        return (
            False,
            f"concurrent_cap_reached ({open_positions_count}/{MAX_CONCURRENT_POSITIONS})",
            0.0,
            0.0,
        )

    # 2. Overround too high (house edge eats the edge)
    if omega_t > MAX_OVERROUND:
        return (
            False,
            f"overround_too_high (Ω={omega_t:.3f} > {MAX_OVERROUND})",
            0.0,
            0.0,
        )

    # 3. Spread too wide
    if spread > MAX_SPREAD_DOLLARS:
        return False, f"spread_too_wide ({spread:.3f} > {MAX_SPREAD_DOLLARS})", 0.0, 0.0

    # v19.5: Spread-to-Price Ratio Gate (Liquidity Veto)
    available_prices = [price for price in (ask_yes, ask_no) if price > 0.0]
    avg_price = sum(available_prices) / len(available_prices) if available_prices else 0.0
    if avg_price > 0:
        spread_ratio = spread / avg_price
        if spread_ratio > KALSHI_MAX_SPREAD_RATIO:
            return False, f"spread_ratio_veto ({spread_ratio:.1%} > {KALSHI_MAX_SPREAD_RATIO:.0%})", 0.0, 0.0

    # 4. Entropy gates: don't trade near certainty
    if h_t < MIN_ENTROPY_FOR_ENTRY:
        return (
            False,
            f"market_near_certainty (H={h_t:.3f} < {MIN_ENTROPY_FOR_ENTRY})",
            0.0,
            0.0,
        )
    if h_t > MAX_ENTROPY_FOR_ENTRY:
        return (
            False,
            f"entropy_too_high (H={h_t:.3f} > {MAX_ENTROPY_FOR_ENTRY})",
            0.0,
            0.0,
        )

    # 5. Volatility cap — don't trade during noisy repricing
    if sigma_t > MAX_SIGMA_T:
        return False, f"sigma_too_high (σ={sigma_t:.3f} > {MAX_SIGMA_T})", 0.0, 0.0

    # 6. Parity gap — internally inconsistent pricing
    if abs(g_t) > MAX_PARITY_GAP_ABS:
        return (
            False,
            f"parity_gap_too_large (|G|={abs(g_t):.3f} > {MAX_PARITY_GAP_ABS})",
            0.0,
            0.0,
        )

    # 7. Compute EV only for executable sides (using taker friction buffer)
    ev_yes = -1.0
    ev_no = -1.0
    if yes_available:
        fee_yes = _estimated_fee_per_contract(ask_yes, rounded=False)
        ev_yes = (
            q_hat
            - ask_yes
            - fee_yes
        )
    if no_available:
        fee_no = _estimated_fee_per_contract(ask_no, rounded=False)
        ev_no = (
            (1.0 - q_hat)
            - ask_no
            - fee_no
        )

    # 8. Neither side has positive EV
    available_evs = [ev for ev, available in ((ev_yes, yes_available), (ev_no, no_available)) if available]
    best_ev = max(available_evs) if available_evs else -1.0
    if best_ev < EV_THRESHOLD:
        return (
            False,
            f"LOW_CONVICTION_ALPHA (Net_EV={best_ev:.4f} < {EV_THRESHOLD})",
            ev_yes,
            ev_no,
        )

    # v19.5: Fee Drag Veto
    # If fees consume > 30% of gross gain, veto.
    best_side = "YES" if ev_yes >= ev_no else "NO"
    potential_gain = (1.0 - ask_yes) if best_side == "YES" else (1.0 - ask_no)
    if potential_gain > 0:
        fee_drag = _estimated_fee_per_contract(
            ask_yes if best_side == "YES" else ask_no,
            rounded=False,
        )
        drag = fee_drag / potential_gain
        if drag > KALSHI_MAX_FEE_DRAG_PCT:
            return False, f"fee_drag_veto (drag={drag:.1%} > {KALSHI_MAX_FEE_DRAG_PCT:.0%})", ev_yes, ev_no

    # 9. Longshot Bias Gate: refuse to buy YES below the probability threshold
    # Note: latest_prob is YES implied probability.
    # If the strategy wants to buy YES but p < 0.10, we veto.
    # (Checking here for EV passing YES but p too low)
    if yes_available and ev_yes >= EV_THRESHOLD and q_hat < MIN_IMPLIED_PROB_FOR_YES:
        # If EV is only positive for YES, we veto. 
        # If EV is positive for both, we might still allow NO if it's the better EV.
        if ev_yes >= ev_no:
            return (
                False,
                f"longshot_bias_gate (YES_p={q_hat:.3f} < {MIN_IMPLIED_PROB_FOR_YES})",
                ev_yes,
                ev_no,
            )

    # 10. Duplicate exposure penalty doesn't veto but is noted in sizing
    return True, "", ev_yes, ev_no


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

    if open_positions_count >= MAX_CONCURRENT_POSITIONS:
        return False, f"concurrent_cap_reached ({open_positions_count}/{MAX_CONCURRENT_POSITIONS})"

    max_spread_dollars = 0.22 if (mode == "TEMP" or hourly_contract) else MAX_SPREAD_DOLLARS
    if spread > max_spread_dollars:
        return False, f"spread_too_wide ({spread:.3f} > {max_spread_dollars})"

    available_prices = [price for price in (ask_yes, ask_no) if price > 0.0]
    avg_price = sum(available_prices) / len(available_prices) if available_prices else 0.0
    if avg_price > 0:
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
    prob_gfs = _probability_from_weather_record(w_data, semantics)
    prob_ecmwf = _probability_from_weather_record(w_data.get("ecmwf") or {}, semantics)

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


def _get_adaptive_weather_model_blend(mode: str) -> dict:
    try:
        from learning.weather_rbi import get_weather_model_blend

        return get_weather_model_blend(mode)
    except Exception:
        return {
            "segment": "STATIC",
            "sample_size": 0,
            "effective_weight": 0.0,
            "gfs_brier": None,
            "ecmwf_brier": None,
            "gfs_weight": 0.60,
            "ecmwf_weight": 0.40,
            "shrinkage": 0.0,
            "lookback_days": 30,
        }


def _blend_weather_probabilities(
    *,
    prob_gfs: float,
    prob_ecmwf: float | None,
    mode: str,
) -> dict[str, float | bool]:
    blend = _get_adaptive_weather_model_blend(mode)
    gfs_weight = float(blend.get("gfs_weight") or 0.60)
    ecmwf_weight = float(blend.get("ecmwf_weight") or 0.40)

    if prob_ecmwf is None:
        return {
            "ensemble_prob": max(0.03, min(0.97, float(prob_gfs))),
            "gfs_weight": gfs_weight,
            "ecmwf_weight": ecmwf_weight,
            "convergence_multiplier": 1.0,
            "divergence_gap": 0.0,
            "divergence_size_multiplier": 1.0,
            "catastrophic_divergence": False,
        }

    ensemble_prob = (float(prob_gfs) * gfs_weight) + (float(prob_ecmwf) * ecmwf_weight)
    yes_agree = prob_gfs > 0.75 and prob_ecmwf > 0.75
    no_agree = prob_gfs < 0.25 and prob_ecmwf < 0.25
    convergence_multiplier = 1.5 if (yes_agree or no_agree) else 1.0
    divergence_gap = abs(float(prob_gfs) - float(prob_ecmwf))
    divergence_size_multiplier = 1.0
    catastrophic_divergence = divergence_gap > 0.70

    if divergence_gap > 0.20:
        confidence_scale = max(
            0.55,
            1.0 - min(0.45, (divergence_gap - 0.20) * 0.90),
        )
        divergence_size_multiplier = max(
            0.60,
            1.0 - min(0.40, (divergence_gap - 0.20) * 0.80),
        )
        ensemble_prob = 0.5 + ((ensemble_prob - 0.5) * confidence_scale)

    return {
        "ensemble_prob": max(0.03, min(0.97, ensemble_prob)),
        "gfs_weight": gfs_weight,
        "ecmwf_weight": ecmwf_weight,
        "convergence_multiplier": convergence_multiplier,
        "divergence_gap": divergence_gap,
        "divergence_size_multiplier": divergence_size_multiplier,
        "catastrophic_divergence": catastrophic_divergence,
    }


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

    semantics = resolve_weather_contract(
        ticker=ticker,
        contract_name=contract_name,
        strike=strike,
    )
    if semantics is None or semantics.ambiguous:
        return None

    prob_gfs, prob_ecmwf = _extract_weather_model_probabilities(w_data, semantics)
    if prob_gfs is None:
        return None

    blended = _blend_weather_probabilities(
        prob_gfs=prob_gfs,
        prob_ecmwf=prob_ecmwf,
        mode=semantics.mode,
    )
    return float(blended["ensemble_prob"])

def calculate_continuous_sizing(market_price: float, ensemble_prob: float, capital_base: float, multiplier: float = 1.0, cap_pct: float = 0.10, conv_tier: int = 3) -> int:
    """Continuous sigmoid sizing driven by post-fee edge."""
    ensemble_prob = max(0.01, min(0.99, ensemble_prob))
    market_price = max(0.01, min(0.99, market_price))
    fee_per_contract = _estimated_fee_per_contract(market_price, rounded=False)
    calculated_ev = ensemble_prob - market_price - fee_per_contract

    if calculated_ev <= 0.0 or capital_base <= 0:
        return 0

    scaling_factor = 1.0 / (1.0 + math.exp(-15.0 * (calculated_ev - 0.12)))
    capital_allowance = min(
        max(0.0, float(capital_base) * max(0.0, float(cap_pct))),
        float(KALSHI_MAX_USD_PER_POSITION),
    )
    deployed_budget = capital_allowance * scaling_factor * max(0.10, float(multiplier))
    per_contract_outlay = max(0.01, market_price + fee_per_contract)
    qty = int(deployed_budget / per_contract_outlay)
    qty = min(max(0, qty), KALSHI_MAX_QTY_PER_POSITION)

    return max(0, qty)

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

def _strategy_weather_details(
    ticker: str,
    ask_yes: float,
    ask_no: float,
    hours_to_res: float,
    contract_name: str = "",
    strike: float | None = None,
    resolution_at: str = "",
    last_trade_at: str = "",
) -> tuple[bool, str, float, list[str], bool, float, int, float]:
    """
    v19.1.10: Sovereign Alpha Blueprint.
    1. Multi-Model Convergence (GFS + ECMWF)
    2. Precision Bracket Pinning
    3. Regional Hub Gating
    """
    # Alpha Filter: 48-Hour Asymmetric Information Decay Window
    is_short_term = 1.5 <= hours_to_res <= 48.0

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
        return False, "", 0.0, ["no_weather_ensemble_data"], False, 1.0, 3, 0.05

    semantics = resolve_weather_contract(
        ticker=ticker,
        contract_name=contract_name,
        strike=strike,
    )
    if semantics is None:
        return False, "", 0.0, [f"unsupported_weather_contract: {ticker}"], False, 1.0, 3, 0.05
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
        )

    mode = semantics.mode
    members_gfs, members_ec = _extract_weather_model_members(w_data, mode)
    prob_gfs, prob_ecmwf = _extract_weather_model_probabilities(w_data, semantics)
    if prob_gfs is None:
        return False, "", 0.0, ["missing_gfs_members"], False, 1.0, 3, 0.05
    provider_mode = str(w_data.get("provider_mode") or "ensemble_members")
    provider_size_multiplier = 0.85 if provider_mode == "deterministic_multi_model" else 1.0

    # ── Phase 3: AI/GraphCast Analysis (Sovereign Sigma Scaler) ────────────
    # v19.8: Move AI from Prob Blend to Sigma Scaler (Bayesian Confirmer)
    aigefs_data = w_data.get("aigefs")
    ai_multiplier = 1.0
    if aigefs_data:
        if mode in ["RAIN", "SNOW", "WIND"]:
            members_ai = aigefs_data.get("members_precip" if mode != "WIND" else "members_wind", [])
        elif mode == "TEMP":
            members_ai = aigefs_data.get("members_temp", [])
        else:
            members_ai = aigefs_data.get("members_high" if mode == "HIGH" else "members_low", [])
            
        if members_ai:
            ai_val = members_ai[0] # Deterministic
            # If AI value is on the wrong side of our bet, increase Sigma (uncertainty)
            # If AI is on our side, tighten Sigma (conviction)
            ensemble_member_values = members_gfs + members_ec
            ensemble_mean = float(np.mean(ensemble_member_values)) if ensemble_member_values else ai_val
            ai_divergence = abs(ai_val - ensemble_mean)
            
            # Scale uncertainty based on AI disagreement
            # v19.9: Precip divergence threshold is smaller (0.1 in)
            disagree_thresh = 1.5 if mode not in ["RAIN", "SNOW"] else 0.1
            if ai_divergence > disagree_thresh:
                ai_multiplier = 1.3 # Increase Sigma/Chaos
            elif ai_divergence < (disagree_thresh / 3.0):
                ai_multiplier = 0.8 # Compress Sigma/Conviction

    # ── Final Probability & Edge (adaptive blend + bounded divergence) ─────
    blend_state = _blend_weather_probabilities(
        prob_gfs=prob_gfs,
        prob_ecmwf=prob_ecmwf,
        mode=mode,
    )
    ensemble_prob = float(blend_state["ensemble_prob"])
    gfs_weight = float(blend_state["gfs_weight"])
    ecmwf_weight = float(blend_state["ecmwf_weight"])
    convergence_multiplier = float(blend_state["convergence_multiplier"])
    divergence_gap = float(blend_state["divergence_gap"])
    divergence_size_multiplier = float(blend_state["divergence_size_multiplier"])
    if prob_ecmwf is not None and convergence_multiplier > 1.0:
        logger.info(
            f"Sovereign Convergence: {ticker} GFS={prob_gfs:.1%} EC={prob_ecmwf:.1%} -> 1.5x"
        )

    edge_yes = (ensemble_prob - ask_yes) if ask_yes > 0 else None
    edge_no = ((1.0 - ensemble_prob) - ask_no) if ask_no > 0 else None
    net_edge_yes = _weather_net_edge(ensemble_prob, ask_yes)
    net_edge_no = _weather_net_edge(1.0 - ensemble_prob, ask_no)
    
    # v19.1.11: The Sigma Lever (Volatility Sizing)
    # v19.8: AI Multiplier now inflates/deflates Sigma directly
    if mode in {"RAIN", "SNOW"}:
        sigma_raw = w_data.get("sigma_precip", w_data.get("sigma_low", 2.0))
    elif mode == "TEMP":
        sigma_raw = w_data.get("sigma_temp", w_data.get("sigma_high", 2.0))
    elif mode == "HIGH":
        sigma_raw = w_data.get("sigma_high", 2.0)
    else:
        sigma_raw = w_data.get("sigma_low", 2.0)
    sigma = sigma_raw * ai_multiplier

    # v19.1.11: Sovereign Instrumentation
    try:
        from monitoring import metrics
        metrics.WEATHER_SIGMA_GAUGE.labels(ticker=ticker).set(sigma)
    except Exception:
        pass

    # v19.5: Sovereign Survival — Hard Sigma Veto
    if sigma > KALSHI_MAX_SIGMA:
        logger.warning(f"Sovereign Chaos Veto: {ticker} Sigma={sigma:.1f}F > {KALSHI_MAX_SIGMA}")
        return (
            False,
            "",
            0.0,
            [f"chaos_veto (sigma={sigma:.1f} > {KALSHI_MAX_SIGMA})"],
            False,
            1.0,
            3,
            0.05,
        )

    # sigma_mult: 1.0 at 2.0F sigma, 1.25 at 1.0F sigma, 0.5 at 4.0F sigma
    sigma_mult = max(0.3, min(1.3, 1.5 - (sigma / 4.0)))

    premium_yes_threshold = float(KALSHI_EXPENSIVE_YES_THRESHOLD)
    premium_yes_net_edge_floor = float(KALSHI_EXPENSIVE_YES_MIN_NET_EDGE)
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
        )
    
    hourly_contract = is_hourly_weather_contract(
        ticker,
        contract_name=contract_name,
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
        )

    # Forensic Audit Log
    edge_yes_display = f"{edge_yes:.1%}" if edge_yes is not None else "n/a"
    edge_no_display = f"{edge_no:.1%}" if edge_no is not None else "n/a"
    logger.info(
        f"TRACE: {ticker} | p={ensemble_prob:.1%} Edge_Y={edge_yes_display} "
        f"Edge_N={edge_no_display} Sigma={sigma:.1f}F s_mult={sigma_mult:.2f}"
    )

    # Guardrail 1: The "Sun Spike" Veto
    peak_tcdc = w_data.get("peak_tcdc", 0.0)
    peak_ssrd = w_data.get("peak_ssrd")
    cloud_veto = (mode == "HIGH") and (peak_tcdc > 65.0)
    narrow_bin_size_multiplier = 1.0
    if semantics.comparator == "between" and mode != "RAIN":
        narrow_bin_size_multiplier = 0.85

    effective_ev_threshold = 0.003 if (mode == "TEMP" or hourly_contract) else EV_THRESHOLD

    if net_edge_yes is not None and net_edge_yes >= effective_ev_threshold:
        if cloud_veto:
            if peak_ssrd is not None:
                return (
                    False,
                    "",
                    0.0,
                    [f"cloud_cover_veto (TCDC={peak_tcdc:.1f}% SSRD={float(peak_ssrd):.0f}W/m2)"],
                    False,
                    1.0,
                    3,
                    0.05,
                )
            return False, "", 0.0, [f"cloud_cover_veto (TCDC={peak_tcdc:.1f}%)"], False, 1.0, 3, 0.05

        is_taker = edge_yes >= 0.22 and is_short_term
        factors = [
            f"ensemble_p={ensemble_prob:.1%}",
            f"edge={edge_yes:.1%}",
            f"net_ev={net_edge_yes:.1%}",
            f"conv_mult={convergence_multiplier:.1f}x",
            f"sigma_mult={sigma_mult:.2f}x",
            f"blend=GFS{gfs_weight:.0%}/EC{ecmwf_weight:.0%}",
            f"div_gap={divergence_gap:.1%}",
            f"TCDC={peak_tcdc:.1f}%",
            f"wx_provider={provider_mode}",
        ]
        if peak_ssrd is not None:
            factors.append(f"SSRD={float(peak_ssrd):.0f}W/m2")
        conv_tier = 0
        sizing_cap = KALSHI_KELLY_CAP
        factors.append("tier=continuous")

        # v19.1.12: Return raw ensemble_prob + sizing_multiplier separately
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
        return True, "YES", ensemble_prob, factors, is_taker, sizing_multiplier, conv_tier, sizing_cap

    if net_edge_no is not None and net_edge_no >= effective_ev_threshold:
        is_taker = edge_no >= 0.22 and is_short_term
        factors = [
            f"ensemble_p={ensemble_prob:.1%}",
            f"edge={edge_no:.1%}",
            f"net_ev={net_edge_no:.1%}",
            f"conv_mult={convergence_multiplier:.1f}x",
            f"sigma_mult={sigma_mult:.2f}x",
            f"blend=GFS{gfs_weight:.0%}/EC{ecmwf_weight:.0%}",
            f"div_gap={divergence_gap:.1%}",
            f"wx_provider={provider_mode}",
        ]
        conv_tier = 0
        sizing_cap = KALSHI_KELLY_CAP
        no_prob = 1.0 - ensemble_prob
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
        return True, "NO", no_prob, factors, is_taker, sizing_multiplier, conv_tier, sizing_cap

    return False, "", 0.0, ["insufficient_edge"], False, 1.0, 3, 0.05


def _strategy_weather(
    ticker: str,
    ask_yes: float,
    ask_no: float,
    hours_to_res: float,
    contract_name: str = "",
    strike: float | None = None,
) -> tuple[bool, str, float, list[str], bool]:
    """
    Legacy five-field wrapper kept for proof compatibility.

    Runtime sizing and tier metadata now live in ``_strategy_weather_details``.
    """
    passes, side, ensemble_prob, factors, is_taker, sizing_multiplier, _tier, _cap = (
        _strategy_weather_details(
            ticker,
            ask_yes,
            ask_no,
            hours_to_res,
            contract_name=contract_name,
            strike=strike,
        )
    )
    legacy_confidence = ensemble_prob * sizing_multiplier if passes else 0.0
    return passes, side, legacy_confidence, factors, is_taker

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
            if age_m > KALSHI_DATA_FRESHNESS_MINUTES:
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
                    veto_reason=f"stale_ensemble_data ({age_m:.0f}m old)",
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
    mid_yes = float(yes_quote.get("mid") or 0.0)
    mid_no = float(no_quote.get("mid") or 0.0)
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
                veto_reason=str(weather_factors[0] if weather_factors else "no_strategy_signal"),
                position_fraction=0.0,
                position_contracts=0,
                top_factors=weather_factors,
                hours_to_resolution=hours_to_res,
                is_taker_override=False,
            )

        best_side = str(w_res[1] or "NONE")
        chosen_prob = float(w_res[2] or 0.0)
        best_factors = weather_factors
        best_is_taker = bool(w_res[4])
        best_multiplier = float(w_res[5] or 1.0)
        best_tier = int(w_res[6] or 3)
        best_sizing_cap = float(w_res[7] or 0.05)
        q_hat = chosen_prob if best_side == "YES" else (1.0 - chosen_prob)
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
                hours_to_resolution=hours_to_res,
                is_taker_override=False,
            )

        from forecast.weather_contracts import weather_mode_for_ticker
        w_mode = weather_mode_for_ticker(ticker)

        hourly_contract = is_hourly_weather_contract(
            ticker,
            contract_name=str(contract.get("contract_name") or ""),
        )
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
        )
        ev_yes = (
            q_hat
            - ask_yes
            - _estimated_fee_per_contract(ask_yes, rounded=False)
            if ask_yes > 0.0
            else -1.0
        )
        ev_no = (
            (1.0 - q_hat)
            - ask_no
            - _estimated_fee_per_contract(ask_no, rounded=False)
            if ask_no > 0.0
            else -1.0
        )
        ev_chosen = ev_yes if best_side == "YES" else ev_no
        effective_ev_threshold = 0.003 if (w_mode == "TEMP" or hourly_contract) else EV_THRESHOLD
        if approved and ev_chosen < effective_ev_threshold:
            approved = False
            veto_reason = f"fee_adjusted_ev_too_low ({ev_chosen:.4f} < {effective_ev_threshold})"

        weather_model_prob_gfs = None
        weather_model_prob_ecmwf = None
        weather_mode = ""
        weather_semantics = resolve_weather_contract(
            ticker=contract.get("local_symbol", ""),
            contract_name=str(contract.get("contract_name") or ""),
            strike=float(contract.get("strike") or 0.0),
        )
        if weather_semantics is not None and not weather_semantics.ambiguous:
            projected_weather = get_contract_weather_data(
                contract.get("local_symbol", ""),
                contract_name=str(contract.get("contract_name") or ""),
                strike=float(contract.get("strike") or 0.0),
                resolution_at=str(contract.get("resolution_at") or ""),
                last_trade_at=str(contract.get("last_trade_at") or ""),
            )
            if projected_weather:
                weather_model_prob_gfs, weather_model_prob_ecmwf = (
                    _extract_weather_model_probabilities(projected_weather, weather_semantics)
                )
                weather_mode = weather_semantics.mode

        if approved:
            # Model Entropy of predicted q_hat
            model_entropy = -(q_hat * math.log(q_hat) + (1.0 - q_hat) * math.log(1.0 - q_hat)) if 0.0 < q_hat < 1.0 else 0.0

            # Asymmetric Fast-Lane (Surge Mode)
            is_surge = (0.03 <= p_cost <= 0.15) and (model_entropy < 0.05) and (ev_chosen >= 0.10)
            
            n_contracts = calculate_continuous_sizing(
                market_price=p_cost,
                ensemble_prob=q_hat,
                capital_base=bankroll,
                multiplier=best_multiplier * (3.5 if is_surge else 1.0),
                cap_pct=max(best_sizing_cap, 0.10) if is_surge else best_sizing_cap,
                conv_tier=best_tier,
            )
            if n_contracts > KALSHI_MAX_QTY_PER_POSITION:
                logger.info(
                    "Sovereign Survival: Capping %s qty %s -> %s",
                    ticker,
                    n_contracts,
                    KALSHI_MAX_QTY_PER_POSITION,
                )
                n_contracts = KALSHI_MAX_QTY_PER_POSITION
                
            # Enforce strict SRE Risk Ceilings (Surge Mode scales to KALSHI_MAX_USD_PER_POSITION)
            cost_limit = min(bankroll * 0.25, float(KALSHI_MAX_USD_PER_POSITION) if is_surge else 20.00)
            current_est_cost = estimate_kalshi_order_cost_usd(n_contracts, p_cost)
            if current_est_cost > cost_limit:
                n_contracts = int(cost_limit / (p_cost + _estimated_fee_per_contract(p_cost, rounded=False)))
                n_contracts = min(max(0, n_contracts), KALSHI_MAX_QTY_PER_POSITION)
                logger.info(
                    f"Sovereign SRE Clamp: Clamping {ticker} cost to {cost_limit:.2f} USD (qty {n_contracts})"
                )
                
            total_cost = estimate_kalshi_order_cost_usd(n_contracts, p_cost)
        else:
            n_contracts, total_cost = 0, 0.0

        actual_fraction = total_cost / bankroll if bankroll > 0 else 0.0
        return StrategyResult(
            strategy_family="weather_ensemble",
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
    v19.4 Sovereign Balance: Institutional Logical Exclusivity.
    Ensures only ONE active strike per city per side (YES/NO).
    """
    family = ticker.split("-")[0]
    
    for p in open_positions:
        p_ticker = p.get("local_symbol", "")
        if family not in p_ticker: continue
        
        p_side = p.get("side", "").upper()
        
        # 1. Mutual Exclusivity: Veto if we already have a position on this SIDE for this city
        if side == "YES" and p_side == "YES":
            return False, f"bracket_overlap_veto: already have YES on {p_ticker}"
        
        if side == "NO" and p_side == "NO":
            return False, f"bracket_overlap_veto: already have NO on {p_ticker}"

        # 2. Opposite Side Conflict (Hedge Guard)
        if side == "NO" and p_side == "YES" and ticker == p_ticker:
            return False, "hedge_guard: cannot bet NO on existing YES strike"

    return True, ""

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
    
    # v19.1.10: Regional Hub Exposure Tracking
    hub_exposure = {} 
    for pos in open_positions:
        p_ticker = pos.get("local_symbol", "")
        p_hub = _get_city_hub(
            p_ticker,
            contract_name=str(pos.get("contract_name") or ""),
        )  # Ensure _get_city_hub uses airport codes (e.g. 'ORD', 'JFK', 'DEN')
        entry_price = float(pos.get("entry_price") or pos.get("entry") or 0.0)
        pos_usd = get_kalshi_position_exposure_usd(
            float(pos.get("qty", 0)),
            entry_price,
        )
        hub_exposure[p_hub] = hub_exposure.get(p_hub, 0.0) + pos_usd
    
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

        family = snapshot.family
        hub = _get_city_hub(ticker, contract_name=snapshot.contract_name)

        count = current_tick_counts.get(family, 0)
        current_hub_usd = hub_exposure.get(hub, 0.0)
        current_hub_cap = get_kalshi_hub_exposure_cap(bankroll)

        if hub != "UNKNOWN" and current_hub_usd >= current_hub_cap:
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
                veto_reason=f"hub_exposure_cap_reached ({current_hub_usd:.1f}/{current_hub_cap:.1f})",
                position_fraction=0.0,
                position_contracts=0,
                top_factors=[],
                hours_to_resolution=hours_to_res,
            )
        elif count >= KALSHI_SAME_EVENT_FAMILY_CAP:
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
    return approved_entries
