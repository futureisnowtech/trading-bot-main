"""
forecast/strategy_engine.py — ForecastEx strategy families + economics gate + sizing.

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
    KALSHI_FEE_BUFFER,
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
    KALSHI_FEE_PER_CONTRACT,
    KALSHI_MAX_FEE_DRAG_PCT,
)
from forecast.primitives import (
    DEFAULT_ALPHA,
    DEFAULT_BETA,
    DEFAULT_DELTA,
    DEFAULT_EPSILON,
    DEFAULT_ETA,
    DEFAULT_GAMMA,
    DEFAULT_ZETA,
    acceleration,
    clip_prob,
    compute_ev,
    compute_q_hat,
    contracts_from_fraction,
    entropy,
    kalshi_absolute_sizing,
    log_odds,
    log_odds_vol,
    overround,
    parity_gap,
    velocity,
    z_score,
)

logger = logging.getLogger(__name__)

# ── Gate thresholds ────────────────────────────────────────────────────────────

# EV must exceed this to pass the economics gate (positive edge requirement)
EV_THRESHOLD: float = 0.02  # 2 cents per $1 contract = 2% edge

# Overround hard cap — above this the house edge is too large
MAX_OVERROUND: float = 0.15  # Tightened from 0.25 to 0.15 for Kalshi

# Spread hard cap
MAX_SPREAD_DOLLARS: float = 0.12  # $0.12 per contract

# Time-to-resolution gates (v19.1.4: Strict 72h Gate)
MIN_HOURS_TO_RES: float = 1.0
MAX_HOURS_TO_RES: float = 72.0

# Longshot Bias Gate
MIN_IMPLIED_PROB_FOR_YES: float = 0.10  # refuse to buy YES below 10% probability

# Entropy saturation — don't trade near 0 or 1 (already resolved)
MAX_ENTROPY_FOR_ENTRY: float = 0.67  # H(p) = 0.67 nat ≈ p in [0.09, 0.91]
MIN_ENTROPY_FOR_ENTRY: float = 0.05  # don't trade if market already 95%+ certain

# Volatility cap — don't trade if log-odds are too noisy
MAX_SIGMA_T: float = 0.80

# z-score cap for mean_reversion entries — must be overextended enough
MIN_ABS_Z_CONTINUATION: float = 0.0  # continuation doesn't require z
MIN_ABS_Z_MEAN_REVERSION: float = 1.5  # must be ≥1.5 std devs from EMA

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

MACRO_CACHE_FILE = "logs/cached_macro_regime.json"


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
# 31-City Sovereign Universe
REGIONAL_HUBS = {
    "MIDWEST": ["CHI", "MSP", "MKE", "OMA", "STL", "DET", "MCI", "OKC"],
    "NORTHEAST": ["NYC", "NY", "BOS", "PHL", "DC"],
    "SOUTH": ["ATL", "CLT", "RDU", "BNA", "CHS"],
    "FLORIDA": ["MIA", "MCO"],
    "GULF": ["HOU", "AUS", "DAL", "SAT", "MSY"],
    "MOUNTAIN": ["DEN", "SLC", "ABQ"],
    "WEST": ["LAX", "SFO", "SF", "PHX", "SEA", "PDX", "LV"],
}
# v19.4: HUB_CAP is now a percentage of balance (20%) calculated in-loop.


def _get_city_hub(ticker: str) -> str:
    """
    v19.3: Sovereign Regional Hub Routing.
    Maps 31 cities to meteorologically correlated macro-regions.
    """
    t = ticker.upper()
    for hub, cities in REGIONAL_HUBS.items():
        if any(city in t for city in cities):
            return hub
    return "UNKNOWN"


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
    v_1h: float = 0.0
    a_30m: float = 0.0
    sigma_t: float = 0.0
    h_t: float = 0.0
    omega_t: float = 0.0
    g_t: float = 0.0
    z_t: float = 0.0
    ask_yes: float = 0.0
    ask_no: float = 0.0
    hours_to_resolution: float = 0.0
    is_taker_override: bool = False


def _extract_log_odds_series(bars: list[dict]) -> list[float]:
    """
    Extract a time-ordered list of log-odds values from bar dicts.
    Uses bar['c'] (close mid) as implied probability.
    Clips to [0.01, 0.99] before log.
    """
    xs = []
    for b in bars:
        mid = b.get("c") or b.get("mid_mean")
        if mid is None:
            continue
        try:
            xs.append(log_odds(float(mid)))
        except Exception:
            pass
    return xs


def _hours_to_resolution(last_trade_at: str) -> float:
    """Hours until contract resolution from now. Returns 0 if unparseable."""
    if not last_trade_at:
        return 0.0
    try:
        fmt = "%Y%m%d %H:%M:%S" if " " in last_trade_at else "%Y%m%d"
        expiry_dt = datetime.strptime(last_trade_at, fmt).replace(tzinfo=timezone.utc)
        delta = (expiry_dt - datetime.now(timezone.utc)).total_seconds() / 3600.0
        return max(0.0, delta)
    except Exception:
        return 0.0


def _compute_features(
    bars_5m: list[dict],
    bars_30m: list[dict],
    bars_1h: list[dict],
    bars_4h: list[dict],
    ask_yes: float,
    ask_no: float,
    mid_yes: float,
    mid_no: float,
) -> dict:
    """
    Compute all log-odds features from multi-timeframe bars.

    Returns dict with: x_t, v_1h, a_30m, sigma_t, h_t, omega_t, g_t, z_t,
    v_4h, velocity_30m, latest_prob
    """
    # Primary price series — use 1h bars for most features
    xs_1h = _extract_log_odds_series(bars_1h)
    xs_30m = _extract_log_odds_series(bars_30m)
    xs_5m = _extract_log_odds_series(bars_5m)
    xs_4h = _extract_log_odds_series(bars_4h)

    # Current log-odds from most recent bar (prefer 5m for freshness)
    latest_prob = None
    for bar_list in [bars_5m, bars_30m, bars_1h]:
        if bar_list:
            latest_prob = bar_list[-1].get("c") or bar_list[-1].get("mid_mean")
            if latest_prob:
                break
    if latest_prob is None:
        latest_prob = mid_yes if mid_yes else 0.50
    latest_prob = clip_prob(float(latest_prob))

    x_t = log_odds(latest_prob)

    # Velocities: Δt = 1 bar for each timeframe
    v_1h = velocity(xs_1h, k=1, dt=1.0) if len(xs_1h) >= 2 else 0.0
    v_4h = velocity(xs_4h, k=1, dt=1.0) if len(xs_4h) >= 2 else 0.0
    v_30m_series = velocity(xs_30m, k=1, dt=1.0) if len(xs_30m) >= 2 else 0.0

    # Acceleration: 30m bars, 1 step
    a_30m = acceleration(xs_30m, k=1, dt=1.0) if len(xs_30m) >= 3 else 0.0

    # Volatility: std of diffs over last 20 5m bars
    sigma_t = log_odds_vol(xs_5m, window=20) if xs_5m else 0.0

    # Entropy
    h_t = entropy(latest_prob)

    # Overround and parity gap (require both sides)
    omega_t = overround(ask_yes, ask_no) if ask_yes and ask_no else 0.0
    g_t = parity_gap(mid_yes, mid_no) if mid_yes and mid_no else 0.0

    # Z-score: deviation from EMA using 1h bars (more stable than 5m)
    z_t = z_score(xs_1h, window=20) if len(xs_1h) >= 5 else 0.0

    return {
        "x_t": x_t,
        "v_1h": v_1h,
        "v_4h": v_4h,
        "a_30m": a_30m,
        "sigma_t": sigma_t,
        "h_t": h_t,
        "omega_t": omega_t,
        "g_t": g_t,
        "z_t": z_t,
        "latest_prob": latest_prob,
        "velocity_30m": v_30m_series,
    }


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
    avg_price = (ask_yes + ask_no) / 2.0
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

    # 7. Compute EV for both sides (using taker friction buffer)
    ev_yes_raw, ev_no_raw = compute_ev(q_hat, ask_yes, ask_no, fee_buffer=KALSHI_FEE_BUFFER)

    # v19.5: Institutional Fee-Aware EV (Net of $0.07)
    # Expected Profit = (Edge * $1.00) - Fee
    # We only care if Net EV is still > threshold and fees aren't dominant.
    ev_yes = ev_yes_raw - KALSHI_FEE_PER_CONTRACT
    ev_no = ev_no_raw - KALSHI_FEE_PER_CONTRACT

    # 8. Neither side has positive EV
    best_ev = max(ev_yes, ev_no)
    if best_ev < EV_THRESHOLD:
        return (
            False,
            f"LOW_PROBABILITY_EDGE (Net_EV={best_ev:.4f})",
            ev_yes,
            ev_no,
        )

    # v19.5: Fee Drag Veto
    # If fees consume > 30% of gross gain, veto.
    potential_gain = (1.0 - ask_yes) if ev_yes >= ev_no else (1.0 - ask_no)
    if potential_gain > 0:
        drag = KALSHI_FEE_PER_CONTRACT / potential_gain
        if drag > KALSHI_MAX_FEE_DRAG_PCT:
            return False, f"fee_drag_veto (drag={drag:.1%} > {KALSHI_MAX_FEE_DRAG_PCT:.0%})", ev_yes, ev_no

    # 9. Longshot Bias Gate: refuse to buy YES below the probability threshold
    # Note: latest_prob is YES implied probability.
    # If the strategy wants to buy YES but p < 0.10, we veto.
    # (Checking here for EV passing YES but p too low)
    if ev_yes >= EV_THRESHOLD and q_hat < MIN_IMPLIED_PROB_FOR_YES:
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


# ── Strategy families ──────────────────────────────────────────────────────────


def _strategy_continuation(
    features: dict, hours_to_res: float
) -> tuple[bool, str, float, list[str]]:
    """
    continuation strategy:
      - positive 1h AND 4h log-odds slope (trend in one direction)
      - non-negative 30m acceleration (trend not decelerating)
      - low/moderate overround
      - no extreme overextension (|z_t| < 2.5)
      - sufficient time to resolution

    Returns: (passes, side, confidence, top_factors)
    """
    v_1h = features["v_1h"]
    v_4h = features["v_4h"]
    a_30m = features["a_30m"]
    z_t = features["z_t"]
    omega_t = features["omega_t"]

    factors = []

    # Slope agreement: both 1h and 4h must agree on direction
    if v_1h > 0 and v_4h > 0:
        side = "YES"
        factors.append(f"1h_vel=+{v_1h:.3f} 4h_vel=+{v_4h:.3f}")
    elif v_1h < 0 and v_4h < 0:
        side = "NO"
        factors.append(f"1h_vel={v_1h:.3f} 4h_vel={v_4h:.3f}")
    else:
        return False, "", 0.0, ["slope_disagreement"]

    # Acceleration: 30m must be non-negative in direction of trade
    if side == "YES" and a_30m < -0.05:
        return False, "", 0.0, ["30m_deceleration_against_YES"]
    if side == "NO" and a_30m > 0.05:
        return False, "", 0.0, ["30m_acceleration_against_NO"]
    factors.append(f"a_30m={a_30m:.3f}")

    # Not overextended (mean reversion risk)
    if abs(z_t) > 2.5:
        return False, "", 0.0, [f"overextended z={z_t:.2f}"]
    factors.append(f"z_t={z_t:.2f}")

    # Enough time for continuation play
    if hours_to_res < 4.0:
        return False, "", 0.0, ["insufficient_time_for_continuation"]

    # Confidence: higher when slope is large, acceleration is aligned, z is moderate
    slope_mag = min(abs(v_1h) + abs(v_4h), 2.0) / 2.0
    accel_boost = (
        0.10 if (side == "YES" and a_30m > 0) or (side == "NO" and a_30m < 0) else 0.0
    )
    confidence = min(0.90, 0.50 + slope_mag * 0.30 + accel_boost)

    return True, side, confidence, factors


def _strategy_mean_reversion(
    features: dict, hours_to_res: float
) -> tuple[bool, str, float, list[str]]:
    """
    mean_reversion strategy:
      - large absolute z_t (contract is overextended vs EMA)
      - acceleration rolling over against the current move
      - entropy not extreme
      - enough time left for reversion

    Returns: (passes, side, confidence, top_factors)
    """
    z_t = features["z_t"]
    a_30m = features["a_30m"]
    h_t = features["h_t"]
    v_1h = features["v_1h"]

    factors = []

    # Must be overextended enough
    if abs(z_t) < MIN_ABS_Z_MEAN_REVERSION:
        return (
            False,
            "",
            0.0,
            [f"z_not_extreme enough ({abs(z_t):.2f} < {MIN_ABS_Z_MEAN_REVERSION})"],
        )

    # Determine reversion side (fade the overextension)
    if z_t > 0:
        # Overextended toward YES → trade NO (expect mean reversion downward)
        side = "NO"
        # Acceleration should be rolling over (turning negative)
        if a_30m > 0.05:
            return False, "", 0.0, ["still_accelerating_against_NO_reversion"]
        factors.append(f"z_t=+{z_t:.2f} (extended toward YES, fade to NO)")
    else:
        # Overextended toward NO → trade YES
        side = "YES"
        if a_30m < -0.05:
            return False, "", 0.0, ["still_accelerating_against_YES_reversion"]
        factors.append(f"z_t={z_t:.2f} (extended toward NO, fade to YES)")

    factors.append(f"a_30m={a_30m:.3f}")

    # Entropy: market must still have uncertainty (not already resolved)
    if h_t < MIN_ENTROPY_FOR_ENTRY:
        return False, "", 0.0, [f"entropy_too_low (H={h_t:.3f})"]

    # Time: need enough hours for the reversion to happen
    if hours_to_res < 6.0:
        return False, "", 0.0, ["insufficient_time_for_mean_reversion"]

    # Confidence: driven by z magnitude and acceleration confirmation
    z_magnitude = min(abs(z_t) / 3.0, 1.0)
    accel_confirm = (
        0.10
        if ((side == "NO" and a_30m <= 0) or (side == "YES" and a_30m >= 0))
        else 0.0
    )
    confidence = min(0.85, 0.45 + z_magnitude * 0.30 + accel_confirm)

    return True, side, confidence, factors


def _strategy_late_repricing(
    features: dict, hours_to_res: float
) -> tuple[bool, str, float, list[str]]:
    """
    late_repricing strategy:
      - meaningful movement in the 4h–24h window before resolution
      - low parity distortion (G_t near zero)
      - contract not close to 0/1 saturation
      - event-quality and liquidity thresholds met
      - time window: 2h–72h before resolution

    Returns: (passes, side, confidence, top_factors)
    """
    v_4h = features["v_4h"]
    g_t = features["g_t"]
    h_t = features["h_t"]
    z_t = features["z_t"]
    sigma_t = features["sigma_t"]
    latest_prob = features["latest_prob"]

    factors = []

    # Must have significant recent movement
    if abs(v_4h) < 0.10:
        return False, "", 0.0, [f"insufficient_4h_movement (v4h={v_4h:.3f})"]

    # Time window: specifically for late repricing (2h–72h window)
    if hours_to_res > 72.0 or hours_to_res < MIN_HOURS_TO_RES:
        return False, "", 0.0, [f"outside_late_repricing_window ({hours_to_res:.1f}h)"]

    # Parity distortion must be low
    if abs(g_t) > 0.03:
        return False, "", 0.0, [f"parity_distorted (|G|={abs(g_t):.3f})"]

    # Not already saturated (not near 0 or 1)
    if latest_prob > 0.92 or latest_prob < 0.08:
        return False, "", 0.0, [f"near_saturation (p={latest_prob:.3f})"]

    # Side: follow the recent 4h movement
    side = "YES" if v_4h > 0 else "NO"
    factors.append(
        f"v_4h={'+' if v_4h > 0 else ''}{v_4h:.3f} (late move toward {side})"
    )
    factors.append(f"g_t={g_t:.4f} (parity OK)")
    factors.append(f"H_t={h_t:.3f} (entropy OK)")

    # Confidence: recent movement magnitude, low sigma (stable repricing not chaotic)
    move_conf = min(abs(v_4h) / 0.5, 1.0) * 0.40
    sigma_penalty = max(0.0, sigma_t - 0.20) * 0.50
    confidence = min(0.80, 0.50 + move_conf - sigma_penalty)

    return True, side, confidence, factors


# ── Main entry point ───────────────────────────────────────────────────────────


from data.kalshi_weather_monitor import get_weather_data

def calculate_continuous_sizing(market_price: float, ensemble_prob: float, capital_base: float, multiplier: float = 1.0, cap_pct: float = 0.10) -> int:
    """Logistic Sigmoid mapping for high-aggression position sizing (v19.1.4)."""
    # Ensure prob is clipped
    ensemble_prob = max(0.01, min(0.99, ensemble_prob))
    edge = ensemble_prob - market_price
    if edge < 0.08:  # 8% Edge Floor Veto
        return 0

    # Logistic Sigmoid mapping for soft-veto position sizing
    # v19.1.4: High-aggression scaling (sharp sigmoid at 12% edge)
    scaling_factor = 1 / (1 + np.exp(-15 * (edge - 0.12)))

    # v19.1.12: Tightened Sovereign Cap — default 10% of bankroll for weather
    # v19.6: Dynamic Cap — 10%, 15%, or 20% based on Conviction Tier
    # Multiplier (Convergence/Sigma) applied to final allocated capital
    allocated_capital = capital_base * cap_pct * scaling_factor * multiplier

    qty = int(allocated_capital / market_price) if market_price > 0 else 0
    return qty

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

def _strategy_weather(ticker: str, ask_yes: float, ask_no: float, hours_to_res: float) -> tuple[bool, str, float, list[str], bool]:
    """
    v19.1.10: Sovereign Alpha Blueprint.
    1. Multi-Model Convergence (GFS + ECMWF)
    2. Precision Bracket Pinning
    3. Regional Hub Gating
    """
    # Alpha Filter: 48-Hour Asymmetric Information Decay Window
    is_short_term = 1.5 <= hours_to_res <= 48.0

    w_data = get_weather_data(ticker)
    if not w_data:
        if "HIGH" in ticker or "LOW" in ticker:
            logger.info(f"TRACE: No weather data for {ticker}")
        return False, "", 0.0, ["no_weather_ensemble_data"], False

    threshold = _parse_weather_threshold(ticker)
    if threshold is None:
        return False, "", 0.0, [f"unparseable_ticker_threshold: {ticker}"], False

    mode = "HIGH" if "HIGH" in ticker else "LOW" if "LOW" in ticker else "RAIN"
    
    # ── Phase 1: GFS Analysis (Primary Trigger) ─────────────────────────────
    members_gfs = w_data.get("members_high" if mode == "HIGH" else "members_low", [])
    if not members_gfs: return False, "", 0.0, ["missing_gfs_members"], False
    
    success_count_gfs = sum(1 for m in members_gfs if (m >= threshold if mode == "HIGH" else m <= threshold))
    prob_gfs = success_count_gfs / len(members_gfs)
    
    # ── Phase 2: ECMWF Analysis (Convergence Anchor) ────────────────────────
    ecmwf_data = w_data.get("ecmwf")
    prob_ecmwf = None
    
    if ecmwf_data:
        members_ec = ecmwf_data.get("members_high" if mode == "HIGH" else "members_low", [])
        if members_ec:
            success_count_ec = sum(1 for m in members_ec if (m >= threshold if mode == "HIGH" else m <= threshold))
            prob_ecmwf = success_count_ec / len(members_ec)

    # ── Phase 3: AI/GraphCast Analysis (Precision Anchor) ──────────────────
    aigefs_data = w_data.get("aigefs")
    prob_aigefs = None
    if aigefs_data:
        members_ai = aigefs_data.get("members_high" if mode == "HIGH" else "members_low", [])
        if members_ai:
            # Deterministic AI model returns 1 member (0 or 1 prob)
            success_count_ai = sum(1 for m in members_ai if (m >= threshold if mode == "HIGH" else m <= threshold))
            prob_aigefs = success_count_ai / len(members_ai)

    # ── Final Probability & Edge (v19.2 Institutional Blend) ────────────────
    # Blend: 40% GFS | 30% ECMWF | 30% AI-GraphCast
    
    # Start with GFS
    total_weight = 0.4
    ensemble_prob = prob_gfs * 0.4
    
    if prob_ecmwf is not None:
        ensemble_prob += prob_ecmwf * 0.3
        total_weight += 0.3
        
    if prob_aigefs is not None:
        ensemble_prob += prob_aigefs * 0.3
        total_weight += 0.3
        
    # Re-normalize if some models missing
    ensemble_prob = ensemble_prob / total_weight
    
    convergence_multiplier = 1.0
    if prob_ecmwf is not None and prob_aigefs is not None:
        # High Conviction: All 3 agree (>70%)
        if prob_gfs > 0.7 and prob_ecmwf > 0.7 and prob_aigefs > 0.7:
            convergence_multiplier = 1.6 # Upgraded from 1.5
            logger.info(f"Sovereign Grand Convergence: {ticker} GFS={prob_gfs:.1%} EC={prob_ecmwf:.1%} AI={prob_aigefs:.1%} -> 1.6x")
        # Veto if catastrophic divergence (>50% gap between any two)
        gap_1 = abs(prob_gfs - prob_ecmwf)
        gap_2 = abs(prob_gfs - (prob_aigefs if prob_aigefs is not None else prob_gfs))
        if gap_1 > 0.5 or gap_2 > 0.5:
             logger.warning(f"Sovereign Divergence: {ticker} GFS={prob_gfs:.1%} EC={prob_ecmwf:.1%} AI={prob_aigefs:.1%} -> VETO")
             return False, "", 0.0, ["model_catastrophic_divergence"], False, 1.0

    ensemble_prob = max(0.03, min(0.97, ensemble_prob))
    edge_yes = ensemble_prob - ask_yes
    edge_no = (1.0 - ensemble_prob) - ask_no
    
    # v19.1.11: The Sigma Lever (Volatility Sizing)
    # High sigma (spread) in members = uncertainty.
    sigma = w_data.get("sigma_high" if mode == "HIGH" else "sigma_low", 2.0)

    # v19.1.11: Sovereign Instrumentation
    try:
        from monitoring import metrics
        metrics.WEATHER_SIGMA_GAUGE.labels(ticker=ticker).set(sigma)
    except Exception:
        pass

    # v19.5: Sovereign Survival — Hard Sigma Veto
    if sigma > KALSHI_MAX_SIGMA:
        logger.warning(f"Sovereign Chaos Veto: {ticker} Sigma={sigma:.1f}F > {KALSHI_MAX_SIGMA}")
        return False, "", 0.0, [f"chaos_veto (sigma={sigma:.1f} > {KALSHI_MAX_SIGMA})"], False, 1.0

    # sigma_mult: 1.0 at 2.0F sigma, 1.25 at 1.0F sigma, 0.5 at 4.0F sigma
    sigma_mult = max(0.3, min(1.3, 1.5 - (sigma / 4.0)))
    
    # v19.5: Sovereign Survival — Institutional Price Floor ($0.15)
    # Never trade penny longshots.
    if ask_yes < KALSHI_MIN_PRICE and edge_yes > 0:
        return False, "", 0.0, [f"penny_veto (ask={ask_yes:.2f} < {KALSHI_MIN_PRICE})"], False, 1.0
    if ask_no < KALSHI_MIN_PRICE and edge_no > 0:
        return False, "", 0.0, [f"penny_veto (ask={ask_no:.2f} < {KALSHI_MIN_PRICE})"], False, 1.0

    # Forensic Audit Log
    logger.info(f"TRACE: {ticker} | p={ensemble_prob:.1%} Edge_Y={edge_yes:.1%} Sigma={sigma:.1f}F s_mult={sigma_mult:.2f}")

    # Guardrail 1: The "Sun Spike" Veto
    peak_tcdc = w_data.get("peak_tcdc", 0.0)
    cloud_veto = (mode == "HIGH") and (peak_tcdc > 65.0)

    # v19.1.9: Narrow Bin Veto
    if "-B" in ticker:
        if mode != "RAIN" and edge_yes < 0.25:
             return False, "", 0.0, [f"narrow_bin_trap_edge_too_low ({edge_yes:.2f})"], False

    if edge_yes > 0.08:
        if cloud_veto:
            return False, "", 0.0, [f"cloud_cover_veto (TCDC={peak_tcdc:.1f}%)"], False

        is_taker = edge_yes >= 0.22 and is_short_term
        factors = [
            f"ensemble_p={ensemble_prob:.1%}",
            f"edge={edge_yes:.1%}",
            f"conv_mult={convergence_multiplier:.1f}x",
            f"sigma_mult={sigma_mult:.2f}x",
            f"TCDC={peak_tcdc:.1f}%"
        ]
        # v19.6: Sovereign Escalation — Tier Identification
        conv_tier = 3
        sizing_cap = 0.10
        
        # TIER 1: AMAZING (Grand Slam)
        if ensemble_prob > 0.92 and sigma < 1.0:
            conv_tier = 1
            sizing_cap = 0.20
        # TIER 2: HIGH
        elif ensemble_prob > 0.85 and sigma < 1.5:
            conv_tier = 2
            sizing_cap = 0.15
            
        factors.append(f"tier={conv_tier}")
        
        # v19.1.12: Return raw ensemble_prob + sizing_multiplier separately
        sizing_multiplier = convergence_multiplier * sigma_mult
        return True, "YES", ensemble_prob, factors, is_taker, sizing_multiplier, conv_tier, sizing_cap

    if edge_no > 0.08:
        is_taker = edge_no >= 0.22 and is_short_term
        factors = [
            f"ensemble_p={ensemble_prob:.1%}",
            f"edge={edge_no:.1%}",
            f"conv_mult={convergence_multiplier:.1f}x",
            f"sigma_mult={sigma_mult:.2f}x"
        ]
        # v19.6: Sovereign Escalation — Tier Identification (NO Side)
        conv_tier = 3
        sizing_cap = 0.10
        no_prob = 1.0 - ensemble_prob
        if no_prob > 0.92 and sigma < 1.0:
            conv_tier = 1
            sizing_cap = 0.20
        elif no_prob > 0.85 and sigma < 1.5:
            conv_tier = 2
            sizing_cap = 0.15
            
        factors.append(f"tier={conv_tier}")
        
        sizing_multiplier = convergence_multiplier * sigma_mult
        return True, "NO", no_prob, factors, is_taker, sizing_multiplier, conv_tier, sizing_cap

    return False, "", 0.0, ["insufficient_edge"], False, 1.0, 3, 0.10

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
    
    # v19.5: Sovereign Survival — Data Freshness Veto
    # Ensure ensemble data isn't stale before evaluating.
    from data.kalshi_weather_monitor import get_weather_data
    w_data = get_weather_data(ticker)
    if w_data:
        data_ts = w_data.get("timestamp", 0)
        age_m = (time.time() - data_ts) / 60.0
        if age_m > KALSHI_DATA_FRESHNESS_MINUTES:
             return StrategyResult(
                strategy_family="vetoed", side="NONE", q_hat=0.0, ev=0.0, ev_yes=0.0, ev_no=0.0,
                confidence=0.0, uncertainty_penalty=0.0, econ_approved=False,
                veto_reason=f"stale_ensemble_data ({age_m:.0f}m old)", position_fraction=0.0,
                position_contracts=0, top_factors=[], 
                hours_to_resolution=_hours_to_resolution(contract.get("last_trade_at", ""))
            )
    else:
        # Weather alpha requires data; veto if missing
        is_weather = "KXHIGH" in ticker or "KXLOW" in ticker or "KXRAIN" in ticker
        if is_weather:
            return StrategyResult(
                strategy_family="vetoed", side="NONE", q_hat=0.0, ev=0.0, ev_yes=0.0, ev_no=0.0,
                confidence=0.0, uncertainty_penalty=0.0, econ_approved=False,
                veto_reason="missing_weather_data", position_fraction=0.0,
                position_contracts=0, top_factors=[], 
                hours_to_resolution=_hours_to_resolution(contract.get("last_trade_at", ""))
            )

    ask_yes = float(yes_quote.get("ask") or 0.0)
    ask_no = float(no_quote.get("ask") or 0.0)
    mid_yes = float(yes_quote.get("mid") or 0.0)
    mid_no = float(no_quote.get("mid") or 0.0)
    spread = max(
        float(yes_quote.get("spread") or 0.0),
        float(no_quote.get("spread") or 0.0),
    )

    # ADVERSARY FIX #5: Data Freshness SLA (Veto if > 120s old)
    # v19.1.6: 600s buffer for weather markets to handle harvester congestion
    quote_ts_str = yes_quote.get("ts")
    if quote_ts_str:
        try:
            quote_ts = datetime.fromisoformat(quote_ts_str.replace("Z", "+00:00"))
            age_seconds = (datetime.now(timezone.utc) - quote_ts).total_seconds()
            
            # v19.1.6: Weather alpha is slow moving; allow up to 600s. 
            # High-frequency crypto stays at 120s.
            is_weather = "KXHIGH" in str(contract.get("local_symbol")) or "KXLOW" in str(contract.get("local_symbol")) or "KXRAIN" in str(contract.get("local_symbol"))
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
                    veto_reason="stale_market_data",
                    position_fraction=0.0,
                    position_contracts=0,
                    top_factors=[],
                    hours_to_resolution=_hours_to_resolution(contract.get("last_trade_at", ""))
                )
        except Exception as e:
            logger.warning(f"Error checking quote freshness for {contract.get('local_symbol')}: {e}")

    if not ask_yes or not ask_no:
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
            hours_to_resolution=_hours_to_resolution(contract.get("last_trade_at", ""))
        )

    # v18.34: Macro Context Risk Gate
    macro = _get_macro_context()
    risk_score = float(macro.get("risk_score", 0))
    if risk_score >= 8:
        logger.info(
            f"Sovereign Veto: MACRO_RISK_OVERLOAD (score={risk_score}) for {contract.get('local_symbol')}"
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
            veto_reason="macro_risk_overload",
            position_fraction=0.0,
            position_contracts=0,
            top_factors=[],
            hours_to_resolution=_hours_to_resolution(contract.get("last_trade_at", ""))
        )

    last_trade_at = contract.get("last_trade_at", "")
    hours_to_res = _hours_to_resolution(last_trade_at)

    # Compute all log-odds features
    try:
        feats = _compute_features(
            bars_5m, bars_30m, bars_1h, bars_4h, ask_yes, ask_no, mid_yes, mid_no
        )
    except Exception:
        logger.exception(f"Feature computation failed for {contract.get('local_symbol')}")
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
            veto_reason="feature_computation_failed",
            position_fraction=0.0,
            position_contracts=0,
            top_factors=[],
            hours_to_resolution=hours_to_res
        )

    x_t = feats["x_t"]
    v_1h = feats["v_1h"]
    a_30m = feats["a_30m"]
    sigma_t = feats["sigma_t"]
    h_t = feats["h_t"]
    omega_t = feats["omega_t"]
    g_t = feats["g_t"]
    z_t = feats["z_t"]

    # Compute q_hat (fair probability for YES)
    q_hat = compute_q_hat(
        p_mid=feats["latest_prob"],
        v_1h=v_1h,
        a_30m=a_30m,
        sigma_t=sigma_t,
        h_t=h_t,
        omega_t=omega_t,
        z_t=z_t,
        context_bias=0.0,
    )

    # Run economics gate
    approved, veto_reason, ev_yes, ev_no = _economics_gate(
        ask_yes=ask_yes,
        ask_no=ask_no,
        q_hat=q_hat,
        omega_t=omega_t,
        g_t=g_t,
        h_t=h_t,
        sigma_t=sigma_t,
        spread=spread,
        hours_to_resolution=hours_to_res,
        open_positions_count=open_positions_count,
        deployed_pct=deployed_pct,
        same_event_open=same_event_open,
    )

    # Evaluate all strategy families
    # v19.6: tuple = (passes, side, ensemble_prob, factors, is_taker, multiplier, tier, sizing_cap)
    strategy_candidates: list[tuple[str, str, float, list[str], bool, float, int, float]] = []

    # v18.35: Weather Strategy (Ensemble-driven)
    ticker = contract.get("local_symbol", "")
    w_res = _strategy_weather(ticker, ask_yes, ask_no, hours_to_res)
    if w_res[0]: # w_passes
        # w_res = (passes, side, ensemble_prob, factors, is_taker, multiplier, tier, sizing_cap)
        strategy_candidates.append(("weather_ensemble", w_res[1], w_res[2], w_res[3], w_res[4], w_res[5], w_res[6], w_res[7]))

    for name, fn in [
        ("continuation", _strategy_continuation),
        ("mean_reversion", _strategy_mean_reversion),
        ("late_repricing", _strategy_late_repricing),
    ]:
        try:
            passes, side, confidence, factors = fn(feats, hours_to_res)
            if passes:
                strategy_candidates.append((name, side, confidence, factors, False, 1.0, 3, 0.10))
        except Exception as e:
            logger.debug(f"Strategy {name} error: {e}")

    if not strategy_candidates:
        # No strategy signal
        return StrategyResult(
            strategy_family="vetoed",
            side="NONE",
            q_hat=q_hat,
            ev=0.0,
            ev_yes=ev_yes,
            ev_no=ev_no,
            confidence=0.0,
            uncertainty_penalty=0.0,
            econ_approved=False,
            veto_reason="no_strategy_signal",
            position_fraction=0.0,
            position_contracts=0,
            top_factors=[],
            hours_to_resolution=hours_to_res,
            is_taker_override=False
        )

    # Pick highest-confidence strategy
    strategy_candidates.sort(key=lambda x: x[2], reverse=True)
    best_family, best_side, best_confidence, best_factors, best_is_taker, best_multiplier, best_tier, best_sizing_cap = strategy_candidates[0]

    # v19.6: Sovereign Conviction — Tiered Resolution Windows
    # T3: 72h max | T2: 120h (5d) max | T1: 168h (7d) max
    res_limit = 72.0
    if best_tier == 1: res_limit = 168.0
    elif best_tier == 2: res_limit = 120.0
    
    if hours_to_res > res_limit:
        return StrategyResult(
            strategy_family="vetoed", side="NONE", q_hat=q_hat, ev=0.0, ev_yes=0.0, ev_no=0.0,
            confidence=0.0, uncertainty_penalty=0.0, econ_approved=False,
            veto_reason=f"conviction_time_veto (Tier {best_tier} requires < {res_limit:.0f}h, got {hours_to_res:.1f}h)",
            position_fraction=0.0, position_contracts=0, top_factors=best_factors,
            hours_to_resolution=hours_to_res, is_taker_override=False
        )

    # v19.1.6: Sovereign Weather Override
    # Weather alpha is probabilistic arbitrage, NOT technical price action.
    # If weather passes, we override technical economics vetoes.
    if best_family == "weather_ensemble":
        approved = True
        veto_reason = ""
        # Recalculate EV based on ensemble probability, not q_hat
        p_cost = ask_yes if best_side == "YES" else ask_no
        # best_confidence is now raw ensemble_prob
        ev_chosen = best_confidence - p_cost
    else:
        # Determine EV for chosen side (Technical)
        ev_chosen = ev_yes if best_side == "YES" else ev_no
        p_cost = ask_yes if best_side == "YES" else ask_no

    # Uncertainty penalty from sigma and entropy
    uncertainty_penalty = min(0.40, sigma_t * 0.30 + max(0.0, h_t - 0.60) * 0.20)
    adj_confidence = max(0.0, best_confidence - uncertainty_penalty)

    # v18.34: Forensic Veto for Hedge Spaghetti (RETIRED v18.35 per user mandate)
    # if same_event_open:
    #     ...

    # Capital Lockup Penalty (Velocity scaling)
    # Scale fraction down exponentially the further away the resolution is.
    # Penalty = exp(-0.5 * (hours / 48)) -> ~0.6 at 48h, ~0.17 at 168h
    time_penalty = np.exp(-0.5 * (hours_to_res / 48.0)) if hours_to_res > 0 else 1.0

    # Sizing (v18.35 Unrestricted Pivot)
    if approved:
        if best_family == "weather_ensemble":
            # Use continuous sizing for weather
            n_contracts = calculate_continuous_sizing(
                market_price=p_cost,
                ensemble_prob=best_confidence,
                capital_base=bankroll,
                multiplier=best_multiplier,
                cap_pct=best_sizing_cap
            )
            # v19.5: Institutional Quantity Cap (Prevent Penny Spree)
            if n_contracts > KALSHI_MAX_QTY_PER_POSITION:
                logger.info(f"Sovereign Survival: Capping {ticker} qty {n_contracts} -> {KALSHI_MAX_QTY_PER_POSITION}")
                n_contracts = KALSHI_MAX_QTY_PER_POSITION

            total_cost = n_contracts * p_cost
        else:
            n_contracts, total_cost = kalshi_absolute_sizing(
                ask_price=p_cost,
                bankroll=bankroll,
                max_risk_pct=KALSHI_MAX_RISK_PER_EVENT_PCT,
                max_deploy_pct=0.10, # Individual event capital limit (increased to 10%)
            )
    else:
        n_contracts, total_cost = 0, 0.0

    # Actual deployed fraction for logging
    actual_fraction = total_cost / bankroll if bankroll > 0 else 0.0

    return StrategyResult(
        strategy_family=best_family,
        side=best_side,
        q_hat=q_hat,
        ev=ev_chosen,
        ev_yes=ev_yes,
        ev_no=ev_no,
        confidence=best_confidence,
        uncertainty_penalty=uncertainty_penalty,
        econ_approved=approved,
        veto_reason=veto_reason,
        position_fraction=actual_fraction,
        position_contracts=n_contracts,
        top_factors=best_factors,
        x_t=x_t,
        v_1h=v_1h,
        a_30m=a_30m,
        sigma_t=sigma_t,
        h_t=h_t,
        omega_t=omega_t,
        g_t=g_t,
        z_t=z_t,
        ask_yes=ask_yes,
        ask_no=ask_no,
        hours_to_resolution=hours_to_res,
        is_taker_override=best_is_taker,
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
    """
    Evaluate all active contracts and return ranked list of approved entries.
    v18.34: Now anchored in real-time TradFi reality via macro_context.
    v18.35: Implements tick-aware concurrency capping for event families.
    v19.2: Implements strike consistency logic.
    """
    if open_event_families is None:
        open_event_families = {}
    if open_positions is None:
        open_positions = []
    
    # Local frequency map to track evaluations in the SAME tick
    current_tick_counts = open_event_families.copy()
    
    # v19.1.10: Regional Hub Exposure Tracking
    hub_exposure = {} # {HUB: current_usd}
    
    # Initial load of open hub exposure (approximate based on ticker)
    # Note: In a true state-full system, we'd query existing positions.
    # For now, we'll track within the tick.

    approved_entries = []
    
    if macro_context:
        logger.info(f"[strategy_engine] Anchoring evaluation in Macro Context (Risk={macro_context.get('risk_score')})")

    # Group contracts by market for YES/NO pairing
    market_contracts: dict[int, list[dict]] = {}
    for c in active_contracts:
        mid = c.get("market_id") or c.get("id")
        market_contracts.setdefault(mid, []).append(c)

    for market_id, contracts in market_contracts.items():
        yes_contracts = [c for c in contracts if c.get("right") == "C"]
        no_contracts = [c for c in contracts if c.get("right") == "P"]

        for yc in yes_contracts:
            ticker = yc.get("local_symbol", "")
            # Find matching NO contract (same strike + expiry)
            nc = next(
                (
                    n
                    for n in no_contracts
                    if n.get("strike") == yc.get("strike")
                    and n.get("last_trade_at") == yc.get("last_trade_at")
                ),
                None,
            )
            if not nc:
                continue

            strike = yc.get("strike", 0.0)
            last_trade = yc.get("last_trade_at", "")
            
            # v19.1.10: Calculate hours_to_res before evaluation
            hours_to_res = 0.0
            if last_trade:
                try:
                    expiry = datetime.fromisoformat(last_trade.replace("Z", "+00:00"))
                    hours_to_res = (expiry - datetime.now(timezone.utc)).total_seconds() / 3600.0
                except: pass

            # Fetch quotes for both sides
            try:
                pair = get_quotes_fn(market_id, strike, last_trade)
                yes_quote = pair.get("yes_quote") or {}
                no_quote = pair.get("no_quote") or {}
            except Exception:
                continue

            if not yes_quote or not no_quote:
                continue

            # Fetch bars (Optional for weather, required for others)
            bars_5m, bars_30m, bars_1h, bars_4h = [], [], [], []
            yes_id = yc.get("id") or yc.get("contract_id")
            if yes_id:
                try:
                    bars_5m = get_bars_fn(yes_id, "5m")
                    bars_30m = get_bars_fn(yes_id, "30m")
                    bars_1h = get_bars_fn(yes_id, "1h")
                    bars_4h = get_bars_fn(yes_id, "4h")
                except Exception:
                    pass
            
            # v19.1.6: Only weather strategies are unblocked if bars are missing.
            is_weather = "KXHIGH" in ticker or "KXLOW" in ticker or "KXRAIN" in ticker
            if not is_weather and not bars_5m:
                continue

            # Check hub exposure (v19.1.10)
            ticker = yc.get("local_symbol", "")
            family = ticker.split("-")[0]
            hub = _get_city_hub(ticker)
            
            count = current_tick_counts.get(family, 0)
            current_hub_usd = hub_exposure.get(hub, 0.0)
            
            # v19.4 Sovereign Balance: Dynamic Hub Scaling (20% of Equity)
            current_hub_cap = max(20.0, bankroll * 0.20)
            
            # v19.2: Logic consistency gate
            is_consistent, conflict_reason = check_strike_consistency(ticker, "YES", open_positions)

            if not is_consistent:
                result = StrategyResult(
                    strategy_family="vetoed", side="NONE", q_hat=0.0, ev=0.0, ev_yes=0.0, ev_no=0.0,
                    confidence=0.0, uncertainty_penalty=0.0, econ_approved=False,
                    veto_reason=conflict_reason, position_fraction=0.0,
                    position_contracts=0, top_factors=[], 
                    hours_to_resolution=hours_to_res
                )
            elif hub != "UNKNOWN" and current_hub_usd >= current_hub_cap:
                result = StrategyResult(
                    strategy_family="vetoed", side="NONE", q_hat=0.0, ev=0.0, ev_yes=0.0, ev_no=0.0,
                    confidence=0.0, uncertainty_penalty=0.0, econ_approved=False,
                    veto_reason=f"hub_exposure_cap_reached ({current_hub_usd:.1f}/{current_hub_cap:.1f})", 
                    position_fraction=0.0,
                    position_contracts=0, top_factors=[], 
                    hours_to_resolution=hours_to_res
                )
            elif count >= KALSHI_SAME_EVENT_FAMILY_CAP:
                # Veto immediately if family cap reached
                result = StrategyResult(
                    strategy_family="vetoed", side="NONE", q_hat=0.0, ev=0.0, ev_yes=0.0, ev_no=0.0,
                    confidence=0.0, uncertainty_penalty=0.0, econ_approved=False,
                    veto_reason="same_event_family_cap_reached", position_fraction=0.0,
                    position_contracts=0, top_factors=[], 
                    hours_to_resolution=hours_to_res
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

            # Update count for the rest of the tick if approved
            if result.econ_approved and result.position_contracts > 0:
                current_tick_counts[family] = count + 1
                hub_exposure[hub] = current_hub_usd + (result.position_contracts * (yes_quote.get("ask") or 0.50))

            rank_score = result.ev * result.confidence if result.econ_approved else 0.0
            approved_entries.append(
                {
                    "contract": yc,
                    "result": result,
                    "rank_score": rank_score,
                }
            )

    approved_entries.sort(key=lambda x: x["rank_score"], reverse=True)
    return approved_entries
