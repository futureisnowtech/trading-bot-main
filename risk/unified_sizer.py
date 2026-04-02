"""
risk/unified_sizer.py — Position sizing for v10.

Formula: notional_usd = (account × BASE_RISK_PCT × quality_mult) / stop_dist_pct
         Capped by portfolio heat and single-position max.

Three factors only:
  quality_mult   — from economics gate quality tier (A+/A/B)
  heat_factor    — reduces size when portfolio risk budget is filling up
  hard_cap       — single position never exceeds 25% of account notional

Drawdown halt (D factor from v9) is preserved: if drawdown_controller says halt,
return 0. That safety gate belongs here.

Everything else (time-of-day, Kelly, memory similarity, volatility regime,
edge monitor) has been removed. These added fragility without measurable edge
on a 24/7 perp system with insufficient trade history to calibrate them.
"""
import os
import sys
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import ACCOUNT_SIZE, PAPER_TRADING

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_RISK_PCT: float = 0.015        # risk 1.5% of account per trade
MAX_HEAT_PCT: float = 0.06          # max 6% of account in total stop-distance risk
MAX_SINGLE_NOTIONAL_PCT: float = 0.25  # single position <= 25% of account (pre-leverage)
MIN_NOTIONAL_USD: float = 20.0      # below this skip — fees eat the edge

_QUALITY_MULT = {
    'A+': 1.35,
    'A':  1.00,
    'B':  0.75,
}


def compute_size(
    account_balance: float,
    stop_dist_pct: float,       # stop distance as fraction (e.g. 0.015 = 1.5%)
    quality_tier: str = 'A',    # 'A+' | 'A' | 'B' — from economics_gate
    portfolio_heat: float = 0.0, # current total risk deployed (USD)
    leverage: int = 3,
    paper: bool = True,
) -> dict:
    """
    Returns notional position size in USD.

    Args:
        account_balance:  Current account equity in USD.
        stop_dist_pct:    ATR × 1.5 / price (e.g. 0.015).
        quality_tier:     From economics_gate.check() — drives size multiplier.
        portfolio_heat:   Sum of (notional × stop_pct) across all open positions.
        leverage:         Intended leverage (affects margin, not notional directly).
        paper:            Paper mode flag (no behaviour change currently).

    Returns dict with:
        notional_usd, dollar_risk, stop_dist_pct, quality_mult, heat_factor, leverage
    """
    if stop_dist_pct <= 0:
        stop_dist_pct = 0.015

    quality_mult = _QUALITY_MULT.get(quality_tier, 0.75)

    # Drawdown halt check — if heat controller says stop, return 0
    try:
        from risk.drawdown_controller import get_heat_level
        heat_data = get_heat_level(paper=paper)
        if heat_data.get('size_factor', 1.0) == 0.0:
            return _zero_result(stop_dist_pct, quality_mult)
    except Exception:
        pass

    # Base: dollar risk → notional
    dollar_risk = account_balance * BASE_RISK_PCT * quality_mult
    notional_raw = dollar_risk / stop_dist_pct

    # Portfolio heat cap
    remaining_budget = max(0.0, MAX_HEAT_PCT * account_balance - portfolio_heat)
    max_from_heat = remaining_budget / stop_dist_pct if stop_dist_pct > 0 else 0.0
    heat_factor = min(1.0, max_from_heat / notional_raw) if notional_raw > 0 else 0.0

    notional = notional_raw * heat_factor

    # Hard cap: single position notional
    hard_cap = account_balance * MAX_SINGLE_NOTIONAL_PCT
    notional = min(notional, hard_cap)

    if notional < MIN_NOTIONAL_USD:
        notional = 0.0

    return {
        'notional_usd':  round(notional, 2),
        'dollar_risk':   round(dollar_risk * heat_factor, 2),
        'stop_dist_pct': round(stop_dist_pct * 100, 3),
        'quality_mult':  quality_mult,
        'heat_factor':   round(heat_factor, 3),
        'leverage':      leverage,
    }


def _zero_result(stop_dist_pct: float, quality_mult: float) -> dict:
    return {
        'notional_usd': 0.0,
        'dollar_risk': 0.0,
        'stop_dist_pct': round(stop_dist_pct * 100, 3),
        'quality_mult': quality_mult,
        'heat_factor': 0.0,
        'leverage': 3,
    }


# ── Legacy shim — keeps v9 callers from breaking ───────────────────────────────
# get_position_size() is called by v9 scanner paths that may still be loaded.
# Delegates to the new compute_size() with sensible defaults.
def get_position_size(
    strategy: str,
    symbol: str,
    base_size: float,
    confidence: float,
    paper: Optional[bool] = None,
    current_price: float = 0.0,
    funding_rate: float = 0.0,
    regime: str = '',
) -> float:
    if paper is None:
        paper = PAPER_TRADING
    if base_size <= 0:
        return 0.0
    # Map confidence → quality tier
    if confidence >= 0.75:
        tier = 'A+'
    elif confidence >= 0.55:
        tier = 'A'
    else:
        tier = 'B'
    result = compute_size(
        account_balance=ACCOUNT_SIZE,
        stop_dist_pct=0.015,   # default 1.5% stop
        quality_tier=tier,
        paper=paper if paper is not None else PAPER_TRADING,
    )
    # Scale result to caller's requested base_size
    scale = base_size / ACCOUNT_SIZE if ACCOUNT_SIZE > 0 else 1.0
    return round(result['notional_usd'] * scale, 2)
