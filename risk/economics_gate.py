"""
risk/economics_gate.py — Pre-trade economics veto gate.

Runs before feature building and signal scoring.
Estimates expected net edge accounting for fees, funding, and spread.
Returns approved=True/False with a reason string and quality tier.

Quality tiers determine size multiplier:
  A+   (ev_pct >= 0.8%)  → 1.35× base size
  A    (ev_pct >= 0.4%)  → 1.00× base size
  B    (ev_pct >= 0.15%) → 0.75× base size   (0.25% in ranging markets)
  VETO (ev_pct < floor)  → 0×   (no trade)

  In ranging markets (is_ranging=True) the EV floor is tightened because
  expected moves are smaller relative to fees — a standard EV floor that
  would be profitable in a trending market is marginal in flat conditions.

Usage:
    from risk.economics_gate import check, batch_check

    result = check(
        symbol='BTCUSDT',
        direction='LONG',
        current_price=65000.0,
        atr_pct=0.018,
        funding_rate=0.0002,
        spread_pct=0.0003,
        volume_24h_usd=500_000_000,
        leverage=3,
    )
    if result['approved']:
        ...
"""

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# ── Fee constants (Coinbase nano perp-style futures, Advanced Trade API) ─────
TAKER_FEE_PCT = 0.0003  # 0.03% per side (taker) — Coinbase perp futures (promotional)
MAKER_REBATE_PCT = 0.0000  # 0.00% maker — Coinbase perp futures (promotional)
ROUND_TRIP_COST = TAKER_FEE_PCT * 2  # entry taker + exit taker = 0.060%

# ── Baseline win-rate assumption (conservative; calibrate as live data grows) ─
_BASELINE_WIN_RATE = 0.52  # 52% — used for EV calculation

# ── Veto thresholds ───────────────────────────────────────────────────────────
_MIN_STOP_DIST_PCT = (
    0.002  # ATR too small → fees consume stop distance (lowered from 0.4%)
)
_MAX_STOP_DIST_PCT = 0.05  # ATR too large → stop is a prayer
_MAX_FEE_TO_WIN_PCT = 0.35  # fees must not eat > 35% of gross target
_MIN_NET_RR = 1.2  # net R:R (after fees) must be ≥ 1.2
_MIN_VOLUME_USD = 2_500_000  # $2.5M — aligned with scanner floor (v13.2)
_MAX_SPREAD_PCT_GATE = 0.0025  # 25 bps — defense-in-depth spread ceiling
_MIN_NEAR_DEPTH_USD = 1_000.0  # $1K each side — minimum near-touch OB depth

# ── Quality tier thresholds ───────────────────────────────────────────────────
# Restored to original pre-v13 values. The v13 doubling was overly conservative
# and was vetoing trades with real edge. Minimal sizing enforces capital discipline.
_TIER_APLUS_EV = 0.008  # 0.8% net EV → A+
_TIER_A_EV = 0.004  # 0.4% net EV → A
_TIER_B_EV = (
    0.0005  # 0.05% net EV → B (lowered from 0.15% to allow marginal-positive trades)
)

# ── Edge score normaliser (EV % that maps to 1.0 on edge_score) ───────────────
_EDGE_SCORE_CAP_EV = 0.015  # 1.5% EV → edge_score = 1.0

# ── Size multipliers per tier ─────────────────────────────────────────────────
TIER_MULTIPLIERS = {
    "A+": 1.35,
    "A": 1.00,
    "B": 0.75,
    "VETO": 0.00,
}


def check(
    symbol: str,
    direction: str,
    current_price: float,
    atr_pct: float,
    funding_rate: float,
    spread_pct: float,
    volume_24h_usd: float,
    leverage: int = 3,
    account_balance: float = 5000.0,
    base_risk_pct: float = 0.015,
    is_ranging: bool = False,
    win_rate_estimate: float = 0.0,  # 0.0 = use default 0.52 baseline
    stop_multiplier: float = 1.5,  # v18.16: matches actual position stop (default 3.0 in v10_runner)
    bid_depth_usd: float = 0.0,  # near-touch bid-side OB depth in USD (0 = skip depth gate)
    ask_depth_usd: float = 0.0,  # near-touch ask-side OB depth in USD (0 = skip depth gate)
) -> dict:
    """
    Hard pre-trade economics veto gate.

    Parameters
    ----------
    symbol          : Instrument symbol (e.g. 'BTCUSDT') — used only for logging.
    direction       : 'LONG' or 'SHORT'.
    current_price   : Latest mark or last price.
    atr_pct         : ATR expressed as a fraction of price (e.g. 0.015 = 1.5%).
    funding_rate    : 8-hour funding rate as a signed decimal (e.g. 0.0003 = 0.03%).
                      Positive = longs pay shorts.
    spread_pct      : Bid-ask spread as a fraction of price (e.g. 0.0005 = 0.05%).
    volume_24h_usd  : 24-hour traded volume in USD.
    leverage        : Integer leverage applied to the position (default 3).
    account_balance : Current account equity in USD (used for context/logging only).
    base_risk_pct   : Fraction of account at risk per trade (used for context only).
    is_ranging      : When True (CHOP > 61.8), tighter EV floor and R:R minimum
                      are applied — flat markets have smaller expected moves relative
                      to fees, so the standard floor would be too permissive.

    Returns
    -------
    dict with keys:
        approved        : bool
        quality_tier    : 'A+' | 'A' | 'B' | 'VETO'
        ev_pct          : float  — net expected value as % of notional
        roi_on_margin   : float  — ev_pct * leverage
        fee_drag_pct    : float  — round-trip fees as % of notional
        funding_cost_pct: float  — estimated funding cost for expected hold period
        reject_reason   : str    — empty string if approved
        edge_score      : float  — 0.0-1.0 normalised score for position sizer
    """
    # ── Guard: basic sanity on inputs ─────────────────────────────────────────
    if current_price <= 0 or atr_pct <= 0:
        return _veto("invalid price or ATR", 0.0, 0.0, 0.0)

    direction = direction.upper()
    if direction not in ("LONG", "SHORT"):
        return _veto(f"unknown direction: {direction}", 0.0, 0.0, 0.0)

    # ── Step 1: Distance calculations ─────────────────────────────────────────
    # v13: use actual stop multiplier passed from v10_runner (3.0x ATR) instead of
    # hardcoded 1.5. Previously gate computed EV with half the actual stop distance,
    # making fee drag look 2x worse relative to the target than it actually is.
    # Both maintain 2:1 gross R:R (target = 2 * stop), so relative math is preserved.
    stop_dist_pct = atr_pct * stop_multiplier  # actual stop distance
    target_dist_pct = atr_pct * stop_multiplier * 2.0  # 2:1 gross R:R target

    # ── Step 2: Fee drag ──────────────────────────────────────────────────────
    # Round-trip taker cost + half of spread paid twice (entry + exit)
    fee_drag_pct = ROUND_TRIP_COST + abs(spread_pct)

    # ── Step 3: Funding cost for estimated hold period ────────────────────────
    # Assume ~12h average hold = 1.5 × 8h funding cycles.
    _hold_cycles = 1.5
    if direction == "LONG":
        # Positive funding = longs pay shorts (cost); negative = income.
        funding_cost_pct = funding_rate * _hold_cycles
    else:
        # SHORT: reversed — positive funding is income, negative is cost.
        funding_cost_pct = -funding_rate * _hold_cycles

    # ── Step 4: Net P&L on win and loss ───────────────────────────────────────
    # Funding cost only applies when it reduces the win or adds to the loss.
    net_win_pct = target_dist_pct - fee_drag_pct - max(0.0, funding_cost_pct)
    net_loss_pct = stop_dist_pct + fee_drag_pct + max(0.0, funding_cost_pct)

    # ── Step 5: Expected value ────────────────────────────────────────────────
    # Use caller-supplied win-rate estimate when available (e.g. from composite score).
    # Clamp to [0.40, 0.70] — never assume below 40% (no reason to trade) or above
    # 70% (overfit fantasy).  Fall back to baseline 0.52 when not supplied.
    if win_rate_estimate > 0.0:
        wr = float(max(0.40, min(0.70, win_rate_estimate)))
    else:
        wr = _BASELINE_WIN_RATE
    ev_pct = (wr * net_win_pct) - ((1.0 - wr) * net_loss_pct)

    # ── Step 6: ROI on margin (what the trader actually sees) ─────────────────
    roi_on_margin = ev_pct * leverage

    # ── Step 7: Veto checks (any single trigger kills the trade) ──────────────
    # In ranging markets the expected move is smaller relative to fees — tighten.
    # EV floor: max of static tier-B floor and 2× effective round-trip cost.
    # This ensures the gate always requires at least 2× cost coverage regardless of regime.
    _effective_cost = (
        ROUND_TRIP_COST + abs(spread_pct) / 2 + max(0.0, abs(funding_cost_pct))
    )
    _cost_floor = 1.0 * _effective_cost  # require 1× cost coverage (down from 2×)
    _static_floor = _TIER_B_EV  # same floor for trending and ranging regimes
    _ev_floor = max(_static_floor, _cost_floor)
    _rr_floor = _MIN_NET_RR * 1.25 if is_ranging else _MIN_NET_RR  # 1.5 vs 1.2

    reject_reason = ""

    if volume_24h_usd < _MIN_VOLUME_USD:
        reject_reason = (
            f"volume ${volume_24h_usd:,.0f} < ${_MIN_VOLUME_USD:,.0f} minimum"
        )
    elif spread_pct > _MAX_SPREAD_PCT_GATE:
        reject_reason = (
            f"spread {spread_pct * 100:.3f}% > {_MAX_SPREAD_PCT_GATE * 100:.2f}% limit"
        )
    elif bid_depth_usd > 0 and min(bid_depth_usd, ask_depth_usd) < _MIN_NEAR_DEPTH_USD:
        _thin_side = min(bid_depth_usd, ask_depth_usd)
        reject_reason = (
            f"near-depth ${_thin_side:,.0f} < ${_MIN_NEAR_DEPTH_USD:,.0f} minimum"
        )
    elif stop_dist_pct < _MIN_STOP_DIST_PCT:
        reject_reason = (
            f"stop distance {stop_dist_pct * 100:.3f}% < {_MIN_STOP_DIST_PCT * 100:.1f}% "
            f"floor — ATR too small, fees consume stop"
        )
    elif stop_dist_pct > _MAX_STOP_DIST_PCT:
        reject_reason = (
            f"stop distance {stop_dist_pct * 100:.2f}% > {_MAX_STOP_DIST_PCT * 100:.1f}% "
            f"ceiling — ATR too large"
        )
    elif target_dist_pct > 0 and (fee_drag_pct / target_dist_pct) > _MAX_FEE_TO_WIN_PCT:
        _fee_ratio = fee_drag_pct / target_dist_pct
        reject_reason = (
            f"fee drag {_fee_ratio * 100:.1f}% of gross target "
            f"> {_MAX_FEE_TO_WIN_PCT * 100:.0f}% ceiling"
        )
    elif net_loss_pct > 0 and (net_win_pct / net_loss_pct) < _rr_floor:
        _net_rr = net_win_pct / net_loss_pct if net_loss_pct > 0 else 0.0
        reject_reason = (
            f"net R:R {_net_rr:.2f} < {_rr_floor:.1f} minimum after fees"
            + (" (ranging)" if is_ranging else "")
        )

    # Also veto if EV is below the floor even without the structural veto checks.
    # _ev_floor is now cost-aware: max(static_tier_b, 2× effective_round_trip).
    if not reject_reason and ev_pct < _ev_floor:
        reject_reason = (
            f"net EV {ev_pct * 100:.3f}% < {_ev_floor * 100:.2f}% minimum — "
            f"trade not worth taking" + (" (ranging)" if is_ranging else "")
        )

    if reject_reason:
        logger.debug("[EconomicsGate] VETO %s %s: %s", direction, symbol, reject_reason)
        return _veto(reject_reason, fee_drag_pct, funding_cost_pct, ev_pct)

    # ── Step 8: Quality tier ──────────────────────────────────────────────────
    if ev_pct >= _TIER_APLUS_EV:
        tier = "A+"
    elif ev_pct >= _TIER_A_EV:
        tier = "A"
    else:
        tier = "B"

    # ── Step 9: Edge score (0.0-1.0) ─────────────────────────────────────────
    edge_score = min(1.0, max(0.0, ev_pct / _EDGE_SCORE_CAP_EV))

    logger.debug(
        "[EconomicsGate] APPROVED %s %s tier=%s ev=%.4f%% roi_margin=%.3f%% edge=%.3f%s",
        direction,
        symbol,
        tier,
        ev_pct * 100,
        roi_on_margin * 100,
        edge_score,
        " [ranging]" if is_ranging else "",
    )

    return {
        "approved": True,
        "quality_tier": tier,
        "ev_pct": round(ev_pct, 6),
        "roi_on_margin": round(roi_on_margin, 6),
        "fee_drag_pct": round(fee_drag_pct, 6),
        "funding_cost_pct": round(funding_cost_pct, 6),
        "reject_reason": "",
        "edge_score": round(edge_score, 4),
    }


def batch_check(candidates: List[dict]) -> List[dict]:
    """
    Run check() on a list of candidate dicts and return only approved ones.

    Each candidate dict must have these keys:
        symbol, direction, price, atr_pct, funding_rate, spread_pct, volume_24h_usd

    Optional keys (with defaults):
        leverage (default 3), account_balance (default 5000.0), base_risk_pct (default 0.015),
        is_ranging (default False) — pass True when CHOP > 61.8 to tighten EV floor.

    Approved candidates are returned with 'quality_tier' and 'edge_score' merged in.
    Vetoed candidates are dropped entirely.
    """
    approved = []
    for candidate in candidates:
        symbol = candidate.get("symbol", "UNKNOWN")
        result = check(
            symbol=symbol,
            direction=candidate.get("direction", "LONG"),
            current_price=float(candidate.get("price", 0.0)),
            atr_pct=float(candidate.get("atr_pct", 0.0)),
            funding_rate=float(candidate.get("funding_rate", 0.0)),
            spread_pct=float(candidate.get("spread_pct", 0.0005)),
            volume_24h_usd=float(candidate.get("volume_24h_usd", 0.0)),
            leverage=int(candidate.get("leverage", 3)),
            account_balance=float(candidate.get("account_balance", 5000.0)),
            base_risk_pct=float(candidate.get("base_risk_pct", 0.015)),
            is_ranging=bool(candidate.get("is_ranging", False)),
        )
        if result["approved"]:
            enriched = dict(candidate)
            enriched["quality_tier"] = result["quality_tier"]
            enriched["edge_score"] = result["edge_score"]
            enriched["ev_pct"] = result["ev_pct"]
            enriched["roi_on_margin"] = result["roi_on_margin"]
            approved.append(enriched)
        else:
            logger.debug(
                "[EconomicsGate] batch_check dropped %s: %s",
                symbol,
                result["reject_reason"],
            )
    return approved


# ── Internal helper ───────────────────────────────────────────────────────────


def _veto(reason: str, fee_drag: float, funding_cost: float, ev_pct: float) -> dict:
    """Build a standardised VETO response."""
    return {
        "approved": False,
        "quality_tier": "VETO",
        "ev_pct": round(ev_pct, 6),
        "roi_on_margin": 0.0,
        "fee_drag_pct": round(fee_drag, 6),
        "funding_cost_pct": round(funding_cost, 6),
        "reject_reason": reason,
        "edge_score": 0.0,
    }
