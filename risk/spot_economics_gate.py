"""
risk/spot_economics_gate.py — Pre-trade economics veto gate for Coinbase spot trades.

Coinbase Advanced Trade charges ~0.6% taker per leg (1.2% round-trip) for small
accounts. This gate blocks spot entries where fee burden is prohibitive relative
to signal conviction.

Usage:
    from risk.spot_economics_gate import check_spot_economics

    result = check_spot_economics(
        symbol="BTC",
        size_usd=400.0,
        composite_score=72.0,
        paper=False,
    )
    if result["approved"]:
        # proceed with spot entry
        ...

Return dict keys (always present):
    approved   bool   — True if trade may proceed
    reason     str    — human-readable veto reason, or "approved"
    fee_usd    float  — estimated round-trip fee in USD
    edge_score float  — estimated edge pct minus round-trip fee pct (negative = losing)
"""

import logging
import os

logger = logging.getLogger(__name__)

# ── Config constants (overridable via environment) ────────────────────────────

# Coinbase Advanced Trade taker fee for small accounts (~<$10K 30-day volume)
SPOT_TAKER_FEE_PCT: float = float(
    os.getenv("SPOT_TAKER_FEE_PCT", "0.006")
)  # 0.6% per leg

# Minimum composite signal score to allow a spot entry at all
SPOT_MIN_COMPOSITE_SCORE: float = float(os.getenv("SPOT_MIN_COMPOSITE_SCORE", "55.0"))

# Edge must be at least this many times the round-trip fee to be viable
SPOT_MIN_EDGE_MULT: float = float(os.getenv("SPOT_MIN_EDGE_MULT", "2.0"))

# Edge mapping: score 50 → 0% edge, score 100 → 5% edge (linear)
_EDGE_SCALE: float = 0.05  # max edge pct at score=100


# ── Public gate function ──────────────────────────────────────────────────────


def check_spot_economics(
    symbol: str,
    size_usd: float,
    composite_score: float,
    paper: bool = False,
) -> dict:
    """
    Evaluate spot-trade economics before entry.

    Args:
        symbol:          Underlying symbol (e.g. "BTC", "ETH"). Used for logging only.
        size_usd:        Notional trade size in USD.
        composite_score: Two-tower composite signal score (0–100).
        paper:           If True, bypass all fee checks (paper trades are never blocked).

    Returns:
        {
            "approved":   bool,
            "reason":     str,   # "approved" | "below_min_composite" | "insufficient_edge_vs_fees"
            "fee_usd":    float, # estimated round-trip fee in USD
            "edge_score": float, # edge_pct - round_trip_fee_pct (positive = net positive EV)
        }
    """
    round_trip_pct: float = 2.0 * SPOT_TAKER_FEE_PCT
    fee_usd: float = size_usd * round_trip_pct

    # Edge estimate: linear map from composite_score.
    # score=50 → 0% edge, score=100 → +5% edge, score=0 → -5% edge.
    edge_pct: float = (composite_score - 50.0) / 50.0 * _EDGE_SCALE

    # Net edge after fees (positive = EV-positive trade)
    edge_score: float = edge_pct - round_trip_pct

    # Paper mode — never block, fees are not real
    if paper:
        logger.debug(
            "spot_econ paper=%s symbol=%s score=%.1f fee_usd=%.4f → approved (paper)",
            paper,
            symbol,
            composite_score,
            fee_usd,
        )
        return {
            "approved": True,
            "reason": "approved",
            "fee_usd": fee_usd,
            "edge_score": edge_score,
        }

    # Gate 1: minimum composite score
    if composite_score < SPOT_MIN_COMPOSITE_SCORE:
        logger.info(
            "spot_econ VETO symbol=%s score=%.1f < min=%.1f reason=below_min_composite",
            symbol,
            composite_score,
            SPOT_MIN_COMPOSITE_SCORE,
        )
        return {
            "approved": False,
            "reason": "below_min_composite",
            "fee_usd": fee_usd,
            "edge_score": edge_score,
        }

    # Gate 2: edge must clear the fee hurdle by the required multiplier.
    # During the defined intraday session we permit a slightly lower hurdle so
    # the lane can recycle more often without opening the door to off-session
    # churn.
    min_edge_mult = SPOT_MIN_EDGE_MULT
    try:
        from config import SPOT_SESSION_MIN_EDGE_MULT, SPOT_OFFSESSION_MIN_EDGE_MULT
        from runtime.spot_session import is_spot_entry_session_open

        min_edge_mult = (
            float(SPOT_SESSION_MIN_EDGE_MULT)
            if is_spot_entry_session_open()
            else float(SPOT_OFFSESSION_MIN_EDGE_MULT)
        )
    except Exception:
        min_edge_mult = SPOT_MIN_EDGE_MULT

    min_required_edge_pct: float = min_edge_mult * round_trip_pct
    if edge_pct < min_required_edge_pct:
        logger.info(
            "spot_econ VETO symbol=%s score=%.1f edge_pct=%.4f < required=%.4f "
            "(%.1f× round_trip=%.4f) reason=insufficient_edge_vs_fees",
            symbol,
            composite_score,
            edge_pct,
            min_required_edge_pct,
            min_edge_mult,
            round_trip_pct,
        )
        return {
            "approved": False,
            "reason": "insufficient_edge_vs_fees",
            "fee_usd": fee_usd,
            "edge_score": edge_score,
        }

    logger.debug(
        "spot_econ APPROVED symbol=%s score=%.1f edge_pct=%.4f fee_usd=%.4f edge_score=%.4f",
        symbol,
        composite_score,
        edge_pct,
        fee_usd,
        edge_score,
    )
    return {
        "approved": True,
        "reason": "approved",
        "fee_usd": fee_usd,
        "edge_score": edge_score,
    }


# ── Self-audit (run as script) ────────────────────────────────────────────────

if __name__ == "__main__":
    # Case 1: paper=True should always return approved regardless of score
    r1 = check_spot_economics("BTC", 400.0, 50.0, paper=True)
    assert r1["approved"] is True, f"FAIL case1 paper bypass: {r1}"
    assert r1["fee_usd"] > 0, f"FAIL case1 fee_usd missing: {r1}"
    print(f"case1 PASS — paper bypass: {r1}")

    # Case 2: composite_score=50 (zero edge) must be rejected in live mode
    r2 = check_spot_economics("ETH", 400.0, 50.0, paper=False)
    assert r2["approved"] is False, f"FAIL case2 score=50 should reject: {r2}"
    print(f"case2 PASS — score=50 rejected: {r2}")

    # Case 3: composite_score=80 with reasonable size should be approved
    # edge_pct = (80-50)/50 * 0.05 = 0.03 (3%)
    # round_trip = 1.2%, min_required = 2 × 1.2% = 2.4%
    # 3% > 2.4% → approved
    r3 = check_spot_economics("BTC", 500.0, 80.0, paper=False)
    assert r3["approved"] is True, f"FAIL case3 score=80 should approve: {r3}"
    print(f"case3 PASS — score=80 approved: {r3}")

    print("\nAll self-audit assertions passed.")
