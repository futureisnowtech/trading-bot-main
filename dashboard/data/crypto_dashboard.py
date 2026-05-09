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

import db as _db

_q = _db._q
_q1 = _db._q1
clamp_metrics_cutoff = getattr(_db, "clamp_metrics_cutoff", lambda s: s)
get_current_strategy_start_date = getattr(
    _db,
    "get_current_strategy_start_date",
    lambda normalized=True: (
        "2026-04-24 00:00:00" if normalized else "2026-04-24T00:00:00"
    ),
)

_TS_NORM = "datetime(replace(substr(ts,1,19),'T',' '))"


def _cutoff(hours: int) -> str:
    raw = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    return clamp_metrics_cutoff(raw)


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
        "spot_cash_available": 0.0,
        "spot_equity": 0.0,
        "spot_symbols": [],
        "spot_deployed_pct": 0.0,
        "perp_deployed_pct": 0.0,
        "open_count": 0,
        "metrics_since": get_current_strategy_start_date(normalized=True),
    }

    # Lane runtime state
    lane = _q1(
        """
        SELECT health, active, autonomous_enabled, mode
        FROM lane_runtime_state
        WHERE lane_id='crypto'
        ORDER BY id DESC
        LIMIT 1
        """
    )
    result["lane_health"] = lane.get("health") or "UNKNOWN"
    result["perp_active"] = bool(lane.get("active"))

    # Spot lane active flag from config
    try:
        from config import SPOT_LANE_ACTIVE
        from config import SPOT_SYMBOLS

        result["spot_active"] = bool(SPOT_LANE_ACTIVE) and bool(lane.get("active", 1))
        result["spot_symbols"] = list(SPOT_SYMBOLS)
    except Exception:
        result["spot_active"] = False
        result["spot_symbols"] = ["BTC", "ETH", "SOL", "XRP"]

    # Runtime mode
    try:
        result["mode_label"] = "PAPER" if False else "LIVE"
    except Exception:
        pass

    # Balance / buying power
    try:
        from data.balance import get_coinbase_balance

        bal = get_coinbase_balance()
        result["buying_power"] = float(bal.get("balance") or 0.0)
    except Exception:
        pass

    # ── Deployment percentages (spot and perp, computed separately) ───────────
    # spot_deployed_pct = spot notional / spot_usd_available (from spot balance truth)
    # perp_deployed_pct = perp notional / total account equity
    try:
        from data.positions import get_spot_positions_dashboard, get_perp_positions

        spot_positions = get_spot_positions_dashboard()
        perp_positions = get_perp_positions()
        result["open_count"] = len(spot_positions) + len(perp_positions)
        spot_notional = sum(
            float(p.get("current_value") or 0.0)
            or abs(float(p.get("qty") or 0))
            * float(p.get("current_price") or p.get("entry") or 0)
            for p in spot_positions
        )
        try:
            from data.balance import get_spot_balance_summary

            spot_bal = get_spot_balance_summary()
            result["spot_cash_available"] = float(spot_bal.get("usd_available") or 0.0)
            result["spot_equity"] = float(spot_bal.get("spot_equity") or 0.0)
            # usd_available is how much USD remains for spot; add back notional to get total spot USD
            spot_total = float(spot_bal.get("spot_equity") or 0) or (
                float(spot_bal.get("usd_available") or 0) + spot_notional
            )
            result["spot_deployed_pct"] = (
                round(spot_notional / spot_total * 100, 1) if spot_total > 0 else 0.0
            )
        except Exception:
            result["spot_deployed_pct"] = 0.0

        perp_notional = sum(
            abs(float(p.get("qty") or 0))
            * float(p.get("current_price") or p.get("entry") or 0)
            for p in perp_positions
        )
        # Use buying_power already fetched as the perp account base
        perp_base = result["buying_power"]
        result["perp_deployed_pct"] = (
            round(perp_notional / perp_base * 100, 1) if perp_base > 0 else 0.0
        )
    except Exception:
        pass  # defaults already 0.0

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
            COALESCE(base_asset, symbol, '') AS underlying,
            COALESCE(exchange, source, '') AS exchange,
            COALESCE(primary_setup, '') AS primary_setup,
            COALESCE(direction, '') AS direction,
            COALESCE(recommended_lane, '') AS recommended_lane,
            COALESCE(tradeability_status, 'not_evaluated') AS status,
            COALESCE(auto_executable, 0) AS auto_executable,
            COALESCE(manual_executable, 0) AS manual_executable,
            COALESCE(NULLIF(final_spot_score, 0.0), composite_score, 0.0) AS score,
            COALESCE(econ_approved, 0) AS econ_approved,
            COALESCE(scanner_expected_profit, 0.0) AS expected_profit,
            COALESCE(stop_pct, 0.0) AS stop_pct,
            COALESCE(regime_floor, 0.0) AS regime_floor,
            COALESCE(trade_blocked_reason, '') AS trade_blocked_reason,
            COALESCE(trade_size_block_reason, '') AS trade_size_block_reason,
            COALESCE(trade_source_reason, '') AS trade_source_reason,
            COALESCE(spot_regime, '') AS spot_regime,
            COALESCE(setup_family, '') AS setup_family,
            COALESCE(setup_score, 0.0) AS setup_score,
            COALESCE(setup_preference, '') AS setup_preference,
            COALESCE(tf_5m_state, '') AS tf_5m_state,
            COALESCE(tf_30m_state, '') AS tf_30m_state,
            COALESCE(tf_4h_state, '') AS tf_4h_state,
            COALESCE(tf_1d_state, '') AS tf_1d_state,
            COALESCE(structural_confirms, '') AS structural_confirms,
            COALESCE(execution_route, '') AS execution_route,
            COALESCE(cooldown_until, '') AS cooldown_until,
            COALESCE(microstructure_veto, '') AS microstructure_veto,
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
      top_quality_blocks   — list of {reason, count} for score / setup gating
      top_econ_blocks      — list of {reason, count} for economics / microstructure gating
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

    top_quality_blocks = _q(
        f"""
        SELECT
            COALESCE(NULLIF(trade_blocked_reason,''), NULLIF(entry_block_reason,''), decision) AS reason,
            COUNT(*) AS n
        FROM scan_candidates
        WHERE {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' '))
          AND (
            decision='below_threshold'
            OR COALESCE(trade_blocked_reason,'')='below_regime_floor'
          )
        GROUP BY 1
        ORDER BY n DESC
        LIMIT 8
        """,
        (cutoff,),
    )

    top_econ_blocks = _q(
        f"""
        SELECT
            COALESCE(NULLIF(trade_blocked_reason,''), NULLIF(entry_block_reason,''), decision) AS reason,
            COUNT(*) AS n
        FROM scan_candidates
        WHERE {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' '))
          AND decision='econ_veto'
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
        "top_quality_blocks": top_quality_blocks,
        "top_econ_blocks": top_econ_blocks,
        "top_bug_flags": top_bug_flags,
    }
