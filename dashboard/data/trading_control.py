"""
dashboard/data/trading_control.py — Canonical control-plane summary for all lanes.

This module powers the master trading-control view in SYSTEM SETTINGS and is
designed to answer one operator question clearly:

    "Where is the system losing trades right now, and is that a strategy,
     system-policy, or bug/integrity problem?"

Primary sources of truth:
  - scan_funnels
  - scan_candidates
  - lane_runtime_state
  - forecast_* tables via dashboard.data.forecast
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

_STRATEGY_DECISIONS = {"below_threshold", "econ_veto"}
_SYSTEM_DECISIONS = {
    "dual_exposure_block",
    "cooldown_block",
    "risk_block",
    "research_only_block",
    "not_autonomous_live_eligible",
    "sizing_zero",
}
_BUG_DECISIONS = {"data_unavailable", "execution_failed"}


def _cutoff_hours(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )


def _classify_decision(decision: str) -> str:
    if decision in _STRATEGY_DECISIONS:
        return "strategy"
    if decision in _SYSTEM_DECISIONS:
        return "system"
    if decision in _BUG_DECISIONS:
        return "bug"
    if decision == "entered":
        return "success"
    return "other"


def get_crypto_control_snapshot(hours: int = 24) -> dict:
    cutoff = _cutoff_hours(hours)
    funnel = _q1(
        f"""
        SELECT
            COALESCE(SUM(scanner_candidates_total), 0) AS scanner_candidates_total,
            COALESCE(SUM(dual_exposure_block), 0) AS dual_exposure_block,
            COALESCE(SUM(cooldown_block), 0) AS cooldown_block,
            COALESCE(SUM(risk_block), 0) AS risk_block,
            COALESCE(SUM(data_unavailable), 0) AS data_unavailable,
            COALESCE(SUM(below_threshold), 0) AS below_threshold,
            COALESCE(SUM(econ_veto), 0) AS econ_veto,
            COALESCE(SUM(research_only_block), 0) AS research_only_block,
            COALESCE(SUM(sizing_zero), 0) AS sizing_zero,
            COALESCE(SUM(execution_failed), 0) AS execution_failed,
            COALESCE(SUM(entered), 0) AS entered,
            COALESCE(SUM(scored_total), 0) AS scored_total,
            COALESCE(SUM(econ_passed_total), 0) AS econ_passed_total,
            COALESCE(SUM(final_entryable_total), 0) AS final_entryable_total
        FROM scan_funnels
        WHERE {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' '))
        """,
        (cutoff,),
    )
    decisions = _q(
        f"""
        SELECT decision, COUNT(*) AS n
        FROM scan_candidates
        WHERE {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' '))
        GROUP BY decision
        ORDER BY n DESC
        """,
        (cutoff,),
    )
    decision_counts = {
        r["decision"]: int(r["n"]) for r in decisions if r.get("decision")
    }

    blank_tradeability = (
        _q1(
            f"""
        SELECT COUNT(*) AS n
        FROM scan_candidates
        WHERE {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' '))
          AND decision IN (
            'below_threshold','econ_veto','research_only_block','not_autonomous_live_eligible',
            'sizing_zero','execution_failed','entered','dual_exposure_block','risk_block'
          )
          AND COALESCE(recommended_lane, '') = ''
        """,
            (cutoff,),
        ).get("n", 0)
        or 0
    )

    top_blockers = _q(
        f"""
        SELECT
            COALESCE(NULLIF(trade_blocked_reason,''), NULLIF(entry_block_reason,''), NULLIF(econ_reject_reason,''), decision) AS reason,
            COUNT(*) AS n
        FROM scan_candidates
        WHERE {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' '))
        GROUP BY 1
        ORDER BY n DESC
        LIMIT 8
        """,
        (cutoff,),
    )

    issue_breakdown = {"strategy": 0, "system": 0, "bug": 0, "success": 0, "other": 0}
    for decision, n in decision_counts.items():
        issue_breakdown[_classify_decision(decision)] += int(n)

    entered = int(funnel.get("entered") or 0)
    scored = int(funnel.get("scored_total") or 0)
    conversion_pct = round((entered / scored) * 100, 1) if scored else 0.0

    stage_rows = [
        {
            "stage": "Scanned",
            "count": int(funnel.get("scanner_candidates_total") or 0),
            "class": "flow",
        },
        {
            "stage": "Signal rejected",
            "count": int(funnel.get("below_threshold") or 0),
            "class": "strategy",
        },
        {
            "stage": "Economics veto",
            "count": int(funnel.get("econ_veto") or 0),
            "class": "strategy",
        },
        {
            "stage": "Policy/system block",
            "count": int(
                (funnel.get("dual_exposure_block") or 0)
                + (funnel.get("cooldown_block") or 0)
                + (funnel.get("risk_block") or 0)
                + (funnel.get("research_only_block") or 0)
                + decision_counts.get("not_autonomous_live_eligible", 0)
                + (funnel.get("sizing_zero") or 0)
            ),
            "class": "system",
        },
        {
            "stage": "Execution failed",
            "count": int(funnel.get("execution_failed") or 0),
            "class": "bug",
        },
        {"stage": "Entered", "count": entered, "class": "success"},
    ]

    return {
        "window_hours": hours,
        "funnel": funnel,
        "decision_counts": decision_counts,
        "issue_breakdown": issue_breakdown,
        "conversion_pct": conversion_pct,
        "blank_tradeability_count": int(blank_tradeability),
        "top_blockers": top_blockers,
        "stage_rows": stage_rows,
    }


def get_lifecycle_stages(hours: int = 24) -> list[dict]:
    """
    Return the standardized 8-stage candidate lifecycle for the Control Tower
    central funnel.  Each stage is:
      {stage, count, source, derived}

    Stage sources:
      discovered        — scan_funnels.scanner_candidates_total (persisted)
      signal_pass       — scan_funnels.scored_total (persisted)
      econ_pass         — scan_funnels.econ_passed_total (persisted)
      route_decided     — econ_pass minus research_only_block and
                          not_autonomous_live_eligible (DERIVED from funnels +
                          decision counts)
      size_pass         — route_decided minus sizing_zero (DERIVED)
      execution_attempted — scan_funnels.final_entryable_total (persisted)
      position_open     — scan_funnels.entered (persisted)
      exit_complete     — trades table closes in window (DERIVED from trades)
    """
    cutoff = _cutoff_hours(hours)

    funnel = _q1(
        f"""
        SELECT
            COALESCE(SUM(scanner_candidates_total), 0) AS discovered,
            COALESCE(SUM(scored_total), 0)             AS signal_pass,
            COALESCE(SUM(econ_passed_total), 0)        AS econ_pass,
            COALESCE(SUM(research_only_block), 0)      AS research_only_block,
            COALESCE(SUM(sizing_zero), 0)              AS sizing_zero,
            COALESCE(SUM(final_entryable_total), 0)    AS execution_attempted,
            COALESCE(SUM(entered), 0)                  AS position_open
        FROM scan_funnels
        WHERE {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' '))
        """,
        (cutoff,),
    )

    # not_autonomous_live_eligible is in scan_candidates, not scan_funnels
    not_auto = (
        _q1(
            f"""
        SELECT COUNT(*) AS n FROM scan_candidates
        WHERE {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' '))
          AND decision = 'not_autonomous_live_eligible'
        """,
            (cutoff,),
        ).get("n")
        or 0
    )

    # exit_complete — trades with a closing action in window (DERIVED)
    exit_complete_row = _q1(
        f"""
        SELECT COUNT(*) AS n FROM trades
        WHERE {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' '))
          AND action IN ('SELL', 'CLOSE', 'SHORT_CLOSE', 'LONG_CLOSE')
          AND pnl_usd != 0
        """,
        (cutoff,),
    )
    exit_complete = int(exit_complete_row.get("n") or 0)

    discovered = int(funnel.get("discovered") or 0)
    signal_pass = int(funnel.get("signal_pass") or 0)
    econ_pass = int(funnel.get("econ_pass") or 0)
    # DERIVED: route_decided = econ_pass minus routing-stage blocks
    route_decided = max(
        0, econ_pass - int(funnel.get("research_only_block") or 0) - int(not_auto)
    )
    # DERIVED: size_pass = route_decided minus sizing_zero
    size_pass = max(0, route_decided - int(funnel.get("sizing_zero") or 0))
    execution_attempted = int(funnel.get("execution_attempted") or 0)
    position_open = int(funnel.get("position_open") or 0)

    return [
        {"stage": "discovered", "count": discovered, "derived": False},
        {"stage": "signal_pass", "count": signal_pass, "derived": False},
        {"stage": "econ_pass", "count": econ_pass, "derived": False},
        {"stage": "route_decided", "count": route_decided, "derived": True},
        {"stage": "size_pass", "count": size_pass, "derived": True},
        {
            "stage": "execution_attempted",
            "count": execution_attempted,
            "derived": False,
        },
        {"stage": "position_open", "count": position_open, "derived": False},
        {"stage": "exit_complete", "count": exit_complete, "derived": True},
    ]


def get_forecast_control_snapshot() -> dict:
    from data.forecast import get_forecast_health, get_forecast_readiness

    health = get_forecast_health()
    readiness = get_forecast_readiness()
    lane = _q1(
        """
        SELECT lane_id, enabled, active, connected, tradable, health,
               blocked_reason, readiness_state, last_heartbeat_at
        FROM lane_runtime_state
        WHERE lane_id='forecast'
        ORDER BY id DESC LIMIT 1
        """
    )

    contradictions: list[str] = []
    if lane.get("readiness_state") and lane.get("readiness_state") != readiness.get(
        "lane_state"
    ):
        contradictions.append(
            "forecast runtime readiness_state does not match dashboard-derived lane_state"
        )
    if (
        health.get("underliers_visible", 0) > 0
        and readiness.get("lane_state") == "NO_UNDERLIERS"
    ):
        contradictions.append(
            "forecast has discovered underliers in DB but readiness still says NO_UNDERLIERS"
        )
    if (
        health.get("active_contracts", 0) == 0
        and health.get("underliers_visible", 0) > 0
    ):
        contradictions.append(
            "forecast underliers exist but no active contracts are tradable right now"
        )
    if lane.get("active") and not lane.get("last_heartbeat_at"):
        contradictions.append(
            "forecast lane marked active without a heartbeat timestamp"
        )
    if lane.get("active") and not health.get("lane_started", False):
        contradictions.append("forecast lane marked active but heartbeat is stale")

    return {
        "lane": lane,
        "health": health,
        "readiness": readiness,
        "contradictions": contradictions,
    }


def get_truth_control_checks(hours: int = 24) -> list[dict]:
    crypto = get_crypto_control_snapshot(hours)
    forecast = get_forecast_control_snapshot()
    checks = []

    blank_count = crypto["blank_tradeability_count"]
    checks.append(
        {
            "name": "Crypto tradeability persistence",
            "status": "PASS" if blank_count == 0 else "WARN",
            "detail": (
                "All recent decision-grade crypto candidates have tradeability fields"
                if blank_count == 0
                else f"{blank_count} recent crypto candidates still have blank tradeability fields"
            ),
        }
    )

    exec_failed = int(crypto["decision_counts"].get("execution_failed", 0))
    checks.append(
        {
            "name": "Crypto execution reliability",
            "status": "PASS" if exec_failed == 0 else "WARN",
            "detail": (
                "No recent execution_failed rows"
                if exec_failed == 0
                else f"{exec_failed} recent crypto entries failed after screening"
            ),
        }
    )

    if forecast["contradictions"]:
        checks.append(
            {
                "name": "Forecast truth alignment",
                "status": "WARN",
                "detail": forecast["contradictions"][0],
            }
        )
    else:
        checks.append(
            {
                "name": "Forecast truth alignment",
                "status": "PASS",
                "detail": "No forecast runtime/data contradictions detected",
            }
        )

    return checks


def get_trading_control_snapshot(hours: int = 24) -> dict:
    return {
        "crypto": get_crypto_control_snapshot(hours),
        "forecast": get_forecast_control_snapshot(),
        "checks": get_truth_control_checks(hours),
    }
