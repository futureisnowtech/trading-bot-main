"""
forecast/primitives.py — lean probability and sizing helpers for Kalshi weather.

The weather lane now avoids legacy HFT momentum physics. Fair value is shaped by:
  - midpoint probability in log-odds space
  - weather uncertainty via sigma
  - market entropy
  - overround / house edge
  - ensemble agreement and RBI-driven blend bias

All functions are pure and side-effect free.
"""

from __future__ import annotations

import math

# Clipping bounds keep the logit math finite.
CLIP_LO: float = 0.01
CLIP_HI: float = 0.99

# Default q_hat coefficients.
DEFAULT_SIGMA_PENALTY: float = 0.25
DEFAULT_ENTROPY_PENALTY: float = 0.15
DEFAULT_OVERROUND_PENALTY: float = 0.50
DEFAULT_AGREEMENT_BONUS: float = 0.25
DEFAULT_ML_BIAS_WEIGHT: float = 0.20
DEFAULT_CONTEXT_WEIGHT: float = 0.10


def clip_prob(p: float) -> float:
    """Clip probability to the safe logit range."""
    return max(CLIP_LO, min(CLIP_HI, float(p)))


def log_odds(p: float) -> float:
    """Return the log-odds for a clipped probability."""
    p = clip_prob(p)
    return math.log(p / (1.0 - p))


def logistic(x: float) -> float:
    """Inverse-logit transform."""
    return 1.0 / (1.0 + math.exp(-x))


def entropy(p: float) -> float:
    """Binary entropy in nats."""
    p = clip_prob(p)
    return -(p * math.log(p) + (1.0 - p) * math.log(1.0 - p))


def overround(ask_yes: float, ask_no: float) -> float:
    """House edge implied by the paired ask prices."""
    return float(ask_yes) + float(ask_no) - 1.0


def parity_gap(mid_yes: float, mid_no: float) -> float:
    """Internal YES/NO midpoint skew."""
    return float(mid_yes) + float(mid_no) - 1.0


def compute_q_hat(
    p_mid: float,
    sigma_t: float,
    h_t: float | None = None,
    omega_t: float | None = None,
    ensemble_agreement: float = 0.0,
    ml_bias: float = 0.0,
    context_bias: float = 0.0,
    sigma_penalty: float = DEFAULT_SIGMA_PENALTY,
    entropy_penalty: float = DEFAULT_ENTROPY_PENALTY,
    overround_penalty: float = DEFAULT_OVERROUND_PENALTY,
    agreement_bonus: float = DEFAULT_AGREEMENT_BONUS,
    ml_bias_weight: float = DEFAULT_ML_BIAS_WEIGHT,
    context_weight: float = DEFAULT_CONTEXT_WEIGHT,
) -> float:
    """
    Fair YES probability with only weather-relevant penalties and blend bias.

    ``ensemble_agreement`` and ``ml_bias`` are bounded directional nudges.
    Positive values raise conviction; sigma, entropy, and overround reduce it.
    """
    midpoint = clip_prob(p_mid)
    entropy_term = h_t if h_t is not None else entropy(midpoint)
    overround_term = omega_t if omega_t is not None else 0.0

    agreement_term = max(-1.0, min(1.0, float(ensemble_agreement)))
    ml_term = max(-1.0, min(1.0, float(ml_bias)))
    context_term = max(-1.0, min(1.0, float(context_bias)))

    logit = (
        log_odds(midpoint)
        - sigma_penalty * max(0.0, float(sigma_t))
        - entropy_penalty * max(0.0, float(entropy_term))
        - overround_penalty * max(0.0, float(overround_term))
        + agreement_bonus * agreement_term
        + ml_bias_weight * ml_term
        + context_weight * context_term
    )
    return clip_prob(logistic(logit))


def compute_ev(q_hat: float, ask_yes: float, ask_no: float, fee_per_contract: float = 0.0) -> tuple[float, float]:
    """
    Net EV for YES and NO using only real exchange fee friction.

    EV_yes = q_hat - ask_yes - fee
    EV_no  = (1 - q_hat) - ask_no - fee
    """
    fee = max(0.0, float(fee_per_contract))
    ev_yes = float(q_hat) - float(ask_yes) - fee
    ev_no = (1.0 - float(q_hat)) - float(ask_no) - fee
    return ev_yes, ev_no


def kalshi_absolute_sizing(
    ask_price: float,
    bankroll: float,
    max_risk_pct: float = 0.015,
    max_deploy_pct: float = 0.05,
    fee_per_contract: float = 0.0,
    max_usd_cap: float | None = None,
) -> tuple[int, float]:
    """
    Size by absolute premium-at-risk plus fees.

    In Kalshi, the premium paid is the capital at risk for both YES and NO.
    """
    if ask_price <= 0 or bankroll <= 0:
        return 0, 0.0

    max_loss_usd = bankroll * max_risk_pct
    max_deploy_usd = bankroll * max_deploy_pct
    total_cash_per_contract = ask_price + max(0.0, fee_per_contract)
    if total_cash_per_contract <= 0:
        return 0, 0.0

    qty_by_risk = int(max_loss_usd / total_cash_per_contract)
    qty_by_capital = int(max_deploy_usd / total_cash_per_contract)

    if max_usd_cap is None:
        try:
            from config import KALSHI_MAX_USD_PER_POSITION

            max_usd_cap = float(KALSHI_MAX_USD_PER_POSITION)
        except Exception:
            max_usd_cap = None

    qty_by_hard_cap = (
        int(max_usd_cap / total_cash_per_contract)
        if max_usd_cap is not None and max_usd_cap > 0
        else 999999
    )

    final_qty = max(0, min(qty_by_risk, qty_by_capital, qty_by_hard_cap))
    return final_qty, final_qty * total_cash_per_contract


def fractional_kelly_fraction(
    q_side: float,
    p_cost: float,
    lambda_: float = 1.0,
    confidence_multiplier: float = 1.0,
    correlation_penalty: float = 1.0,
    kelly_cap: float = 0.10,
) -> float:
    """
    Convert a contract edge into a capped bankroll fraction.
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
    fee_per_contract: float = 0.0,
) -> int:
    """Convert a fraction-of-bankroll budget to whole contracts."""
    if p_cost <= 0 or bankroll <= 0:
        return 0

    per_event_dollars = min(fraction, per_event_cap_pct) * bankroll
    remaining_pct = max(0.0, max_deployed_pct - deployed_pct)
    headroom_dollars = remaining_pct * bankroll
    dollar_risk = min(per_event_dollars, headroom_dollars)
    if dollar_risk <= 0:
        return 0

    cost_per_contract = p_cost + max(0.0, fee_per_contract)
    if cost_per_contract <= 0:
        return 0

    return int(dollar_risk / cost_per_contract)
