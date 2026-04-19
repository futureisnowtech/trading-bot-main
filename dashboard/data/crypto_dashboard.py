"""
dashboard/data/crypto_dashboard.py — Data readers for the CRYPTO page.

Three functions:
  get_crypto_header()           — lane health summary row
  get_crypto_opportunity_board()— recent scan_candidates with tradeability fields
  get_crypto_failure_summary()  — execution failures + top policy blocks
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

_DASH_DIR = os.path.dirname(os.path.abspath(__file__))
_DASHBOARD_DIR = os.path.dirname(_DASH_DIR)
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

from db import _q, _q1

_TS_NORM = "datetime(replace(substr(ts,1,19),'T',' '))"


def _cutoff(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )


def get_crypto_header() -> dict:
    """
    Returns:
      lane_health, spot_active, perp_active, mode_label,
      buying_power, spot_deployed_pct, perp_deployed_pct, open_count
    """
    result: dict = {
        "lane_health": "UNKNOWN",
        "spot_active": False,
        "perp_active": False,
        "mode_label": "UNKNOWN",
        "buying_power": 0.0,
        "spot_deployed_pct": 0.0,
        "perp_deployed_pct": 0.0,
        "open_count": 0,
    }

    # Lane runtime state
    lane = _q1(
        "SELECT health, active FROM lane_runtime_state WHERE lane_id='crypto' ORDER BY id DESC LIMIT 1"
    )
    result["lane_health"] = lane.get("health") or "UNKNOWN"
    result["perp_active"] = bool(lane.get("active"))

    # Spot lane active flag from config
    try:
        from config import SPOT_LANE_ACTIVE

        result["spot_active"] = bool(SPOT_LANE_ACTIVE)
    except Exception:
        result["spot_active"] = False

    # Runtime mode
    try:
        from db import _runtime_paper_flag

        result["mode_label"] = "PAPER" if _runtime_paper_flag() else "LIVE"
    except Exception:
        pass

    # Balance / buying power
    try:
        from data.balance import get_coinbase_balance

        bal = get_coinbase_balance()
        result["buying_power"] = float(bal.get("balance") or 0.0)
    except Exception:
        pass

    # Open position count
    try:
        from db import _runtime_paper_flag

        paper = _runtime_paper_flag()
        r = _q1("SELECT COUNT(*) AS n FROM open_positions WHERE paper=?", (paper,))
        result["open_count"] = int(r.get("n") or 0)
    except Exception:
        pass

    return result


def get_crypto_opportunity_board(hours: int = 24) -> list[dict]:
    """
    Recent scan_candidates with tradeability fields, ordered newest first.

    Each row:
      symbol, underlying, direction, recommended_lane, status, auto_executable,
      manual_executable, score, econ_approved, expected_profit, stop_pct,
      trade_blocked_reason, trade_size_block_reason, trade_source_reason, ts, decision
    """
    cutoff = _cutoff(hours)
    rows = _q(
        f"""
        SELECT
            COALESCE(symbol, '') AS symbol,
            COALESCE(underlying, symbol, '') AS underlying,
            COALESCE(direction, '') AS direction,
            COALESCE(recommended_lane, '') AS recommended_lane,
            COALESCE(tradeability_status, 'not_evaluated') AS status,
            COALESCE(auto_executable, 0) AS auto_executable,
            COALESCE(manual_executable, 0) AS manual_executable,
            COALESCE(composite_score, 0.0) AS score,
            COALESCE(econ_approved, 0) AS econ_approved,
            COALESCE(expected_profit, 0.0) AS expected_profit,
            COALESCE(stop_pct, 0.0) AS stop_pct,
            COALESCE(trade_blocked_reason, '') AS trade_blocked_reason,
            COALESCE(trade_size_block_reason, '') AS trade_size_block_reason,
            COALESCE(trade_source_reason, '') AS trade_source_reason,
            ts,
            COALESCE(decision, '') AS decision
        FROM scan_candidates
        WHERE {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' '))
        ORDER BY ts DESC
        LIMIT 200
        """,
        (cutoff,),
    )
    return rows


def get_crypto_failure_summary(hours: int = 24) -> dict:
    """
    Returns:
      execution_failures   — list of recent execution_failed rows
      top_policy_blocks    — list of {reason, count} for policy blocks
      top_bug_flags        — list of {reason, count} for bug/data failures
    """
    cutoff = _cutoff(hours)

    execution_failures = _q(
        f"""
        SELECT symbol, direction, COALESCE(entry_block_reason, decision) AS reason, ts
        FROM scan_candidates
        WHERE {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' '))
          AND decision = 'execution_failed'
        ORDER BY ts DESC
        LIMIT 20
        """,
        (cutoff,),
    )

    top_policy_blocks = _q(
        f"""
        SELECT
            COALESCE(NULLIF(trade_blocked_reason,''), NULLIF(entry_block_reason,''), decision) AS reason,
            COUNT(*) AS n
        FROM scan_candidates
        WHERE {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' '))
          AND decision IN (
            'dual_exposure_block','cooldown_block','risk_block',
            'research_only_block','not_autonomous_live_eligible','sizing_zero'
          )
        GROUP BY 1
        ORDER BY n DESC
        LIMIT 8
        """,
        (cutoff,),
    )

    top_bug_flags = _q(
        f"""
        SELECT
            COALESCE(NULLIF(entry_block_reason,''), decision) AS reason,
            COUNT(*) AS n
        FROM scan_candidates
        WHERE {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' '))
          AND decision IN ('data_unavailable','execution_failed')
        GROUP BY 1
        ORDER BY n DESC
        LIMIT 8
        """,
        (cutoff,),
    )

    return {
        "execution_failures": execution_failures,
        "top_policy_blocks": top_policy_blocks,
        "top_bug_flags": top_bug_flags,
    }
