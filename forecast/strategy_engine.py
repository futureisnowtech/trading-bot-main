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
    ev_yes, ev_no = compute_ev(q_hat, ask_yes, ask_no, fee_buffer=KALSHI_FEE_BUFFER)

    # 8. Neither side has positive EV
    best_ev = max(ev_yes, ev_no)
    if best_ev < EV_THRESHOLD:
        return (
            False,
            "LOW_PROBABILITY_EDGE",
            ev_yes,
            ev_no,
        )

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

    # 10. Concurrent position cap
    if open_positions_count >= MAX_CONCURRENT_POSITIONS:
        return (
            False,
            f"concurrent_cap_reached ({open_positions_count}/{MAX_CONCURRENT_POSITIONS})",
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

def calculate_continuous_sizing(market_price: float, ensemble_prob: float, capital_base: float) -> int:
    """Logistic Sigmoid mapping for high-aggression position sizing (v19.1.4)."""
    edge = ensemble_prob - market_price
    if edge < 0.08:  # 8% Edge Floor Veto
        return 0

    # Logistic Sigmoid mapping for soft-veto position sizing
    # v19.1.4: High-aggression scaling (sharp sigmoid at 12% edge)
    scaling_factor = 1 / (1 + np.exp(-15 * (edge - 0.12)))

    # v19.1.4: Sovereign Cap — up to 25% of bankroll for weather
    allocated_capital = capital_base * 0.25 * scaling_factor

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
    Boundary Pinning & METAR Discrepancies Strategy (v19.1.6).
    Treats weather as an asymmetric information decay problem in the <48h window.
    Expanded to support High Temp, Low Temp, and Precipitation.
    """
    # Alpha Filter: 48-Hour Asymmetric Information Decay Window
    is_short_term = 1.5 <= hours_to_res <= 48.0

    w_data = get_weather_data(ticker)
    if not w_data:
        # Trace log for audit
        if "HIGH" in ticker or "LOW" in ticker:
            logger.info(f"TRACE: No weather data for {ticker}")
        return False, "", 0.0, ["no_weather_ensemble_data"], False

    threshold = _parse_weather_threshold(ticker)
    if threshold is None:
        return False, "", 0.0, [f"unparseable_ticker_threshold: {ticker}"], False

    # v19.1.6: Dynamic member selection based on contract type
    if "HIGH" in ticker:
        members = w_data.get("members_high", [])
        sd = w_data.get("std_high", 5.0)
        mode = "HIGH"
    elif "LOW" in ticker:
        members = w_data.get("members_low", [])
        sd = w_data.get("std_low", 5.0)
        mode = "LOW"
    elif "RAIN" in ticker or "PRECIP" in ticker:
        members = w_data.get("members_precip", [])
        sd = 0.1 # Default low SD for precip
        mode = "RAIN"
    else:
        return False, "", 0.0, ["unknown_weather_type"], False

    if not members:
        return False, "", 0.0, ["empty_ensemble_members"], False

    # v19.1.6: Boundary Pinning calculation
    # P(Condition >= Threshold) from 31-member GFS ensemble
    success_count = sum(1 for m in members if m >= threshold)
    ensemble_prob = success_count / len(members)

    # v19.1.5: Dynamic SD Shrinkage (Decay Capture)
    pin_strength = max(0.0, 1.0 - (sd / 5.0)) if is_short_term else 0.0

    # Apply 3% uncertainty floor/ceiling
    ensemble_prob = max(0.03, min(0.97, ensemble_prob))

    edge_yes = ensemble_prob - ask_yes
    edge_no = (1.0 - ensemble_prob) - ask_no
    
    # Forensic Audit Log
    logger.info(f"TRACE: {ticker} | p={ensemble_prob:.1%} Ask_Y={ask_yes:.2f} Edge_Y={edge_yes:.1%} Edge_N={edge_no:.1%}")

    # Guardrail 1: The Convective Cloud Cover Override (The "Sun Spike")
    # Only applies to HIGH temp YES contracts.
    peak_tcdc = w_data.get("peak_tcdc", 0.0)
    cloud_veto = (mode == "HIGH") and (peak_tcdc > 65.0)

    # v19.1.5: High-aggression edge detection (8% floor)
    # Guardrail 3: Taker-Override (Edge >= 22%)
    if edge_yes > 0.08:
        if cloud_veto:
            return False, "", 0.0, [f"cloud_cover_veto (TCDC={peak_tcdc:.1f}%)"], False

        is_taker = edge_yes >= 0.22 and is_short_term
        factors = [
            f"ensemble_p={ensemble_prob:.1%}",
            f"edge={edge_yes:.1%}",
            f"pin_strength={pin_strength:.2f}",
            f"TCDC={peak_tcdc:.1f}%",
            f"type={mode}"
        ]
        return True, "YES", ensemble_prob, factors, is_taker

    if edge_no > 0.08:
        is_taker = edge_no >= 0.22 and is_short_term
        factors = [
            f"ensemble_p={ensemble_prob:.1%}",
            f"edge={edge_no:.1%}",
            f"pin_strength={pin_strength:.2f}",
            f"type={mode}"
        ]
        return True, "NO", (1.0 - ensemble_prob), factors, is_taker

    return False, "", 0.0, [f"insufficient_weather_edge (p_yes={ensemble_prob:.2f})"], False

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

    Args:
        contract: from forecast_contracts table (has local_symbol, right, strike, etc.)
        bars_*: OHLC bar lists, most-recent last
        yes_quote / no_quote: most recent bid/ask/mid dicts
        bankroll: current account equity in USD
        deployed_pct: fraction of bankroll already in open positions
        open_positions_count: number of currently open positions
        same_event_open: True if an open position is in the same event family
    """
    ask_yes = float(yes_quote.get("ask") or 0.0)
    ask_no = float(no_quote.get("ask") or 0.0)
    mid_yes = float(yes_quote.get("mid") or 0.0)
    mid_no = float(no_quote.get("mid") or 0.0)
    spread = max(
        float(yes_quote.get("spread") or 0.0),
        float(no_quote.get("spread") or 0.0),
    )

    # ADVERSARY FIX #5: Data Freshness SLA (Veto if > 120s old)
    # v19.1.6: 300s buffer for weather markets to handle harvester congestion
    quote_ts_str = yes_quote.get("ts")
    if quote_ts_str:
        try:
            quote_ts = datetime.fromisoformat(quote_ts_str.replace("Z", "+00:00"))
            age_seconds = (datetime.now(timezone.utc) - quote_ts).total_seconds()
            
            # Weather alpha is slow moving; allow up to 300s. Others stay at 120s.
            is_weather = "KXHIGH" in str(contract.get("local_symbol")) or "KXLOW" in str(contract.get("local_symbol"))
            limit = 300 if is_weather else 120
            
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
    strategy_candidates: list[tuple[str, str, float, list[str], bool]] = []

    # v18.35: Weather Strategy (Ensemble-driven)
    ticker = contract.get("local_symbol", "")
    w_passes, w_side, w_conf, w_factors, w_is_taker = _strategy_weather(ticker, ask_yes, ask_no, hours_to_res)
    if w_passes:
        strategy_candidates.append(("weather_ensemble", w_side, w_conf, w_factors, w_is_taker))

    for name, fn in [
        ("continuation", _strategy_continuation),
        ("mean_reversion", _strategy_mean_reversion),
        ("late_repricing", _strategy_late_repricing),
    ]:
        try:
            passes, side, confidence, factors = fn(feats, hours_to_res)
            if passes:
                strategy_candidates.append((name, side, confidence, factors, False))
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
    best_family, best_side, best_confidence, best_factors, best_is_taker = strategy_candidates[0]

    # Determine EV for chosen side
    ev_chosen = ev_yes if best_side == "YES" else ev_no
    p_cost = ask_yes if best_side == "YES" else ask_no
    q_side = q_hat if best_side == "YES" else (1.0 - q_hat)

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
            # We use the confidence value as the ensemble probability for the formula
            n_contracts = calculate_continuous_sizing(
                market_price=p_cost,
                ensemble_prob=best_confidence, # In weather strategy, conf = ensemble_prob
                capital_base=bankroll
            )
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


def evaluate_all_contracts(
    active_contracts: list[dict],
    get_bars_fn,  # callable(contract_id, interval) -> list[dict]
    get_quotes_fn,  # callable(market_id, strike, last_trade_at) -> dict
    bankroll: float = 100.0,
    deployed_pct: float = 0.0,
    open_positions_count: int = 0,
    open_event_families: Optional[dict] = None,
    macro_context: Optional[dict] = None,
) -> list[dict]:
    """
    Evaluate all active contracts and return ranked list of approved entries.
    v18.34: Now anchored in real-time TradFi reality via macro_context.
    v18.35: Implements tick-aware concurrency capping for event families.
    """
    if open_event_families is None:
        open_event_families = {}
    
    # Local frequency map to track evaluations in the SAME tick
    current_tick_counts = open_event_families.copy()

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

            # Check same-event exposure (v18.35: Count-based capping)
            ticker = yc.get("local_symbol", "")
            family = ticker.split("_")[0]
            count = current_tick_counts.get(family, 0)

            if count >= KALSHI_SAME_EVENT_FAMILY_CAP:
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
