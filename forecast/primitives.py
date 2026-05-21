"""
forecast/primitives.py — Log-odds mathematical primitives for event-contract signals.

All formulas operate in log-odds space where p_t is an implied probability
clipped to [CLIP_LO, CLIP_HI] before any log is taken.

Reference formulas (from system spec):
  x_t = log(p_t / (1 - p_t))                   — log-odds
  v_t = (x_t - x_{t-k}) / Δt                   — velocity (log-odds per unit time)
  a_t = (v_t - v_{t-k}) / Δt                   — acceleration
  σ_t = rolling_std(diff(x_t))                  — log-odds volatility
  H_t = -p_t·ln(p_t) - (1-p_t)·ln(1-p_t)      — binary entropy
  Ω_t = ask_yes_t + ask_no_t - 1                — overround
  G_t = mid_yes_t + mid_no_t - 1                — parity gap
  z_t = (x_t - EMA_n(x_t)) / std_n(x_t)        — z-score vs EMA

  q_hat = logistic(x_t + α·v_1h + β·a_30m
                   - γ·z_t - δ·σ_t - ε·H_t
                   - ζ·Ω_t + η·context_bias)    — fair-probability estimate

All functions are pure (no I/O, no DB).  They accept scalars or numpy arrays.
"""

import math
import numpy as np
from typing import Sequence

# Clipping bounds — prevents log(0) and log(inf)
CLIP_LO: float = 0.01
CLIP_HI: float = 0.99

# Default q_hat coefficients (interpretable starting point; tune via calibration)
DEFAULT_ALPHA: float = 0.40  # 1h velocity weight
DEFAULT_BETA: float = 0.20  # 30m acceleration weight
DEFAULT_GAMMA: float = 0.30  # z-score (overextension) penalty
DEFAULT_DELTA: float = 0.25  # volatility penalty
DEFAULT_EPSILON: float = 0.15  # entropy penalty
DEFAULT_ZETA: float = 0.50  # overround penalty
DEFAULT_ETA: float = 0.10  # context bias weight

# EMA / std window for z_t computation
Z_EMA_WINDOW: int = 20


# ---------------------------------------------------------------------------
# Core primitives
# ---------------------------------------------------------------------------


def clip_prob(p: float) -> float:
    """Clip probability to [CLIP_LO, CLIP_HI]."""
    return max(CLIP_LO, min(CLIP_HI, float(p)))


def log_odds(p: float) -> float:
    """x_t = log(p_t / (1 - p_t)).  Clips p first."""
    p = clip_prob(p)
    return math.log(p / (1.0 - p))


def logistic(x: float) -> float:
    """Inverse of log_odds: σ(x) = 1 / (1 + e^-x). Output in (0, 1)."""
    return 1.0 / (1.0 + math.exp(-x))


def entropy(p: float) -> float:
    """H_t = -p·ln(p) - (1-p)·ln(1-p).  Binary entropy in nats. Clips p first."""
    p = clip_prob(p)
    return -(p * math.log(p) + (1.0 - p) * math.log(1.0 - p))


def overround(ask_yes: float, ask_no: float) -> float:
    """Ω_t = ask_yes + ask_no - 1.  Positive means house edge; 0 = fair."""
    return ask_yes + ask_no - 1.0


def parity_gap(mid_yes: float, mid_no: float) -> float:
    """G_t = mid_yes + mid_no - 1.  Positive = YES premium; negative = NO premium."""
    return mid_yes + mid_no - 1.0


# ---------------------------------------------------------------------------
# Series-based primitives (require list/array of log-odds values)
# ---------------------------------------------------------------------------


def velocity(x_series: Sequence[float], k: int = 1, dt: float = 1.0) -> float:
    """v_t = (x_t - x_{t-k}) / Δt.  Returns last velocity.

    Args:
        x_series: sequence of log-odds values, most-recent last.
        k: look-back steps.
        dt: time step size (e.g. 1.0 for 1 bar, 4.0 for 4 bars → 1h in 15m bars).
    """
    if len(x_series) < k + 1:
        return 0.0
    return (x_series[-1] - x_series[-(k + 1)]) / max(dt, 1e-9)


def acceleration(
    x_series: Sequence[float],
    k: int = 1,
    dt: float = 1.0,
) -> float:
    """a_t = (v_t - v_{t-k}) / Δt.  Requires at least 2k+1 elements."""
    if len(x_series) < 2 * k + 1:
        return 0.0
    v_now = velocity(x_series[-(k + 1) :], k=k, dt=dt)
    v_prev = velocity(x_series[-(2 * k + 1) : -(k)], k=k, dt=dt)
    return (v_now - v_prev) / max(dt, 1e-9)


def log_odds_vol(x_series: Sequence[float], window: int = 20) -> float:
    """σ_t = rolling_std(diff(x_t)) over last `window` diffs."""
    xs = list(x_series)
    if len(xs) < 2:
        return 0.0
    diffs = np.diff(xs[-window:]) if len(xs) >= window else np.diff(xs)
    return float(np.std(diffs)) if len(diffs) > 0 else 0.0


def z_score(
    x_series: Sequence[float],
    window: int = Z_EMA_WINDOW,
) -> float:
    """z_t = (x_t - EMA_n(x_t)) / std_n(x_t).

    Uses exponentially-weighted mean and std for the EMA component.
    Returns 0 if series is too short or std is near zero.
    """
    xs = np.array(list(x_series), dtype=float)
    if len(xs) < 4:
        return 0.0
    span = min(window, len(xs))
    # EMA via pandas-style ewm (α = 2/(span+1))
    alpha = 2.0 / (span + 1)
    ema = xs[0]
    for val in xs[1:]:
        ema = alpha * val + (1.0 - alpha) * ema
    std = float(np.std(xs[-span:]))
    if std < 1e-9:
        return 0.0
    return (xs[-1] - ema) / std


# ---------------------------------------------------------------------------
# Fair probability estimate
# ---------------------------------------------------------------------------


def compute_q_hat(
    p_mid: float,
    v_1h: float,
    a_30m: float,
    sigma_t: float,
    h_t: float | None = None,
    omega_t: float | None = None,
    context_bias: float = 0.0,
    z_t: float | None = None,
    alpha: float = DEFAULT_ALPHA,
    beta: float = DEFAULT_BETA,
    gamma: float = DEFAULT_GAMMA,
    delta: float = DEFAULT_DELTA,
    epsilon: float = DEFAULT_EPSILON,
    zeta: float = DEFAULT_ZETA,
    eta: float = DEFAULT_ETA,
) -> float:
    """q_hat = logistic(x_t + α·v_1h + β·a_30m - γ·z_t - δ·σ_t - ε·H_t - ζ·Ω_t + η·bias)

    Args:
        p_mid: current midpoint probability (will be clipped).
        v_1h: 1h log-odds velocity.
        a_30m: 30m log-odds acceleration.
        sigma_t: log-odds volatility (σ_t).
        h_t: binary entropy (computed from p_mid if None).
        omega_t: overround Ω_t (set to 0 if None — conservative).
        context_bias: external bias term (e.g. from macro data).
        z_t: z-score deviation (computed to 0 if None — no mean-reversion signal).
        alpha..eta: coefficient overrides.
    Returns:
        q_hat in (CLIP_LO, CLIP_HI) — fair probability estimate for YES.
    """
    x_t = log_odds(p_mid)
    _h = h_t if h_t is not None else entropy(p_mid)
    _om = omega_t if omega_t is not None else 0.0
    _z = z_t if z_t is not None else 0.0

    logit = (
        x_t
        + alpha * v_1h
        + beta * a_30m
        - gamma * _z
        - delta * sigma_t
        - epsilon * _h
        - zeta * _om
        + eta * context_bias
    )
    return clip_prob(logistic(logit))


# ---------------------------------------------------------------------------
# EV calculation
# ---------------------------------------------------------------------------


def compute_ev(
    q_hat: float,
    ask_yes: float,
    ask_no: float,
    fee_buffer: float = 0.02,
) -> tuple[float, float]:
    """
    Sovereign Net EV (v18.32): Evaluates against the Ask (worst-case fill)
    and injects a conservative friction buffer.

    EV_yes = q_hat - ask_yes - fee_buffer
    EV_no  = (1 - q_hat) - ask_no - fee_buffer

    Returns (ev_yes, ev_no).
    """
    ev_yes = q_hat - ask_yes - fee_buffer
    ev_no = (1.0 - q_hat) - ask_no - fee_buffer
    return ev_yes, ev_no


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------


def kalshi_absolute_sizing(
    ask_price: float,
    bankroll: float,
    max_risk_pct: float = 0.015,
    max_deploy_pct: float = 0.05,
) -> tuple[int, float]:
    """
    Binary Risk Sizing (v18.32): Sizes by Absolute Loss to Zero.
    
    Ensures:
    1. Total loss if contract -> $0.00 is capped at max_risk_pct of bankroll.
    2. Total capital locked in one event is capped at max_deploy_pct of bankroll.
    
    Returns: (n_contracts, total_cost_usd)
    """
    if ask_price <= 0 or bankroll <= 0:
        return 0, 0.0

    max_loss_usd = bankroll * max_risk_pct
    max_deploy_usd = bankroll * max_deploy_pct

    # qty_by_risk = max_loss_usd / cost_per_loss_unit
    # In Kalshi, cost_per_loss_unit is exactly the ask_price.
    qty_by_risk = int(max_loss_usd / ask_price)

    # qty_by_capital = max_deploy_usd / ask_price
    qty_by_capital = int(max_deploy_usd / ask_price)

    final_qty = max(0, min(qty_by_risk, qty_by_capital))
    return final_qty, final_qty * ask_price


def fractional_kelly_fraction(
    q_side: float,
    p_cost: float,
    lambda_: float = 1.0,
    confidence_multiplier: float = 1.0,
    correlation_penalty: float = 1.0,
    kelly_cap: float = 0.10,
) -> float:
    """
    f_raw = max(0, (q_side - p_cost) / max(1 - p_cost, 0.01))
    f     = min(kelly_cap, λ · f_raw · confidence · correlation_penalty)

    Args:
        q_side: our belief probability for the chosen side.
        p_cost: ask price of the chosen side contract.
        lambda_: fractional Kelly multiplier (default 1.0 = full edge; use <1 for caution).
        confidence_multiplier: scales down when signal confidence is low.
        correlation_penalty: scales down when correlated positions exist.
        kelly_cap: hard cap on fraction (default 0.10 per spec).
    Returns:
        Final fraction of bankroll to risk (0.0 to kelly_cap).
    """
    denom = max(1.0 - p_cost, 0.01)
    f_raw = max(0.0, (q_side - p_cost) / denom)
    f = min(kelly_cap, lambda_ * f_raw * confidence_multiplier * correlation_penalty)
    return f


def contracts_from_fraction(
    fraction: float,
    bankroll: float,
    p_cost: float,
    per_event_cap_pct: float = 0.10,
    deployed_pct: float = 0.0,
    max_deployed_pct: float = 0.35,
) -> int:
    """Convert a Kelly fraction to whole-number contracts.

    Applies per-event and total-deployment caps:
        max_dollar_risk = min(fraction, per_event_cap_pct) × bankroll
        also bounded by remaining deployment headroom

    Returns 0 if no room or cost is zero/negative.
    """
    if p_cost <= 0 or bankroll <= 0:
        return 0

    # Per-event cap
    per_event_dollars = min(fraction, per_event_cap_pct) * bankroll

    # Deployment headroom
    remaining_pct = max(0.0, max_deployed_pct - deployed_pct)
    headroom_dollars = remaining_pct * bankroll

    dollar_risk = min(per_event_dollars, headroom_dollars)
    if dollar_risk <= 0:
        return 0

    # Each contract costs p_cost × 100 (ForecastEx contracts are $0–$1 per share,
    # 100 shares per contract).  For tiny bankroll, allow fractional-share thinking
    # but always round DOWN to whole contracts.
    cost_per_contract = p_cost * 100.0
    if cost_per_contract <= 0:
        return 0

    return int(dollar_risk / cost_per_contract)
