"""
verification/replay.py — Deterministic pipeline replay harness.

Runs a single candidate through the full v10 pipeline without touching the
live scanner or exchange APIs.  Used by the proof suite and the nightly audit
to verify that the signal → economics → sizing → risk → attribution chain
works end-to-end in a controlled environment.

Usage
-----
    from verification.replay import run_replay
    result = run_replay(candidate=cand, features=feats, ...)

Returns
-------
    {
      "signal":     {approved, score_dict},
      "economics":  {approved, gate_dict},
      "sizing":     {position_usd, size_dict},
      "risk":       {approved, reason},
      "staged":     bool,
      "attribution":  {attr_id} | None,
    }
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


def run_replay(
    candidate: dict,
    features: dict,
    account_balance: float = 5_000.0,
    current_balance: float = 5_000.0,
    deployed_usd: float = 0.0,
    margin_usd: float = 0.0,
    live_trade_days: int = 0,
    kelly_fraction: float = 0.33,
    exit_price: Optional[float] = None,
    exit_reason: str = "target_hit",
) -> Dict[str, Any]:
    """
    Run a single candidate through the full entry + attribution pipeline.

    Parameters
    ----------
    candidate       : Scanner candidate dict (see tests/proof/support.build_candidate).
    features        : 57-feature dict (see tests/proof/support.build_features).
    account_balance : Starting account equity for sizing.
    current_balance : Current balance (may differ after open positions).
    deployed_usd    : USD already deployed in open positions.
    margin_usd      : Margin locked by open positions.
    live_trade_days : Number of live trading days (drives ML weight schedule).
    kelly_fraction  : Kelly fraction override (passed through to sizing).
    exit_price      : If provided, a simulated trade close is attributed.
    exit_reason     : Reason string for the simulated close.
    """
    direction = candidate.get("direction", "LONG").upper()
    price = float(candidate.get("price", 100.0))
    atr_15m = float(candidate.get("atr_15m", price * 0.015))
    atr_pct = atr_15m / price if price > 0 else 0.015

    # ── 1. Signal engine ──────────────────────────────────────────────────────
    import signal_engine

    score_dict = signal_engine.score(
        features,
        direction=direction,
        regime=features.get("regime", "UNKNOWN"),
        live_trade_days=live_trade_days,
    )
    signal_approved = bool(score_dict.get("should_enter", False))

    # ── 2. Economics gate ─────────────────────────────────────────────────────
    from risk.economics_gate import check as econ_check

    # Unit conversions matching v10_runner conventions:
    #   funding_rate  — candidate stores annualized decimal; gate wants per-8h fraction
    #   spread_pct    — candidate stores percent units (e.g. 0.05 = 0.05%); gate wants fraction
    _funding_8h = float(candidate.get("funding_rate", 0.0)) / (365 * 3)
    _spread_frac = float(candidate.get("spread_pct", 0.1)) / 100.0

    gate_dict = econ_check(
        symbol=candidate.get("symbol", "BTCUSDT"),
        direction=direction,
        current_price=price,
        atr_pct=atr_pct,
        funding_rate=_funding_8h,
        spread_pct=_spread_frac,
        volume_24h_usd=float(candidate.get("volume_24h_usd", 0.0)),
        leverage=3,
        account_balance=account_balance,
        win_rate_estimate=float(candidate.get("win_rate_estimate", 0.54)),
        stop_multiplier=float(candidate.get("stop_multiplier", 3.0)),
        bid_depth_usd=float(candidate.get("bid_depth_usd", 0.0)),
        ask_depth_usd=float(candidate.get("ask_depth_usd", 0.0)),
    )
    econ_approved = bool(gate_dict.get("approved", False))

    # Early-out: economics vetoed — no sizing, no risk check, no attribution
    if not econ_approved:
        return {
            "signal": {"approved": signal_approved, "score_dict": score_dict},
            "economics": {"approved": False, "gate_dict": gate_dict},
            "sizing": {"position_usd": 0.0, "size_dict": {}},
            "risk": {"approved": False, "reason": "economics_veto"},
            "staged": False,
            "attribution": None,
        }

    # ── 3. Position sizing ────────────────────────────────────────────────────
    from position_manager import compute_position_size

    size_dict = compute_position_size(
        account_balance=account_balance,
        current_price=price,
        atr_7=atr_15m,
        stop_multiplier=float(candidate.get("stop_multiplier", 3.0)),
        vol_regime=int(candidate.get("vol_regime", 2)),
        ml_score=float(score_dict.get("ml_score", 50.0)),
        fg_current=float(candidate.get("fg_current", 50.0)),
        composite_score=float(score_dict.get("composite_score", 65.0)),
        correlation_penalty=float(candidate.get("correlation_penalty", 1.0)),
        edge_score=float(gate_dict.get("edge_score", 0.5)),
        cascade_risk_score=float(candidate.get("cascade_risk_score", 0.0)),
        deployed_usd=deployed_usd,
        paper=True,
    )
    position_usd = float(size_dict.get("position_usd", 0.0))

    # ── 4. Risk gate ──────────────────────────────────────────────────────────
    import risk_engine

    risk_engine.update_balances(
        current_balance=current_balance,
        deployed_usd=deployed_usd,
        margin_usd=margin_usd,
    )
    risk_allowed, risk_reason = risk_engine.can_open_new_position()

    if not risk_allowed or position_usd <= 0:
        return {
            "signal": {"approved": signal_approved, "score_dict": score_dict},
            "economics": {"approved": econ_approved, "gate_dict": gate_dict},
            "sizing": {"position_usd": position_usd, "size_dict": size_dict},
            "risk": {"approved": False, "reason": risk_reason},
            "staged": False,
            "attribution": None,
        }

    # ── 5. Attribution (simulate close) ──────────────────────────────────────
    attribution_result: Optional[Dict[str, Any]] = None

    if exit_price is not None:
        try:
            from learning.signal_performance import record_trade_attribution

            pnl_usd = (
                (exit_price - price) * (position_usd / price)
                if direction == "LONG"
                else (price - exit_price) * (position_usd / price)
            )
            pnl_pct = pnl_usd / position_usd if position_usd > 0 else 0.0
            fee_usd = position_usd * 0.00065 * 2  # round-trip Kraken taker fee

            now = datetime.now(timezone.utc).isoformat()
            active_signals = {
                k: bool(v)
                for k, v in features.items()
                if isinstance(v, (int, float)) and v
            }

            attr_id = record_trade_attribution(
                symbol=candidate.get("symbol", "BTCUSDT"),
                strategy="replay_harness",
                regime=features.get("regime", "UNKNOWN"),
                signals=active_signals,
                won=pnl_usd > 0,
                pnl_usd=round(pnl_usd, 4),
                pnl_pct=round(pnl_pct, 6),
                fee_usd=round(fee_usd, 4),
                conviction=float(score_dict.get("composite_score", 65.0)),
                entry_price=price,
                exit_price=exit_price,
                entry_ts=now,
                exit_ts=now,
                exit_reason=exit_reason,
                hold_minutes=60.0,
                source="replay_harness",
                paper=True,
                composite_score=float(score_dict.get("composite_score", 65.0)),
            )
            attribution_result = {"attr_id": attr_id}
        except Exception as exc:
            attribution_result = {"attr_id": 0, "error": str(exc)}

    return {
        "signal": {"approved": signal_approved, "score_dict": score_dict},
        "economics": {"approved": econ_approved, "gate_dict": gate_dict},
        "sizing": {"position_usd": position_usd, "size_dict": size_dict},
        "risk": {"approved": risk_allowed, "reason": risk_reason},
        "staged": True,
        "attribution": attribution_result,
    }
