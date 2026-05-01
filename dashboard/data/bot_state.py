"""
dashboard/data/bot_state.py — Real-time bot reasoning state for the new dashboard.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import db as _db

_q = _db._q
_q1 = _db._q1

SPOT_SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "LTC", "DOGE", "ADA", "LINK"]

SCORE_FLOORS: dict[str, dict[str, float]] = {
    "BTC": {"TREND": 57.0, "NEUTRAL": 57.0, "CHOP": 60.0},
    "ETH": {"TREND": 55.0, "NEUTRAL": 55.0, "CHOP": 60.0},
    "SOL": {"TREND": 56.0, "NEUTRAL": 56.0, "CHOP": 60.0},
    "XRP": {"TREND": 56.0, "NEUTRAL": 56.0, "CHOP": 60.0},
    "LTC": {"TREND": 55.0, "NEUTRAL": 55.0, "CHOP": 60.0},
    "DOGE": {"TREND": 57.0, "NEUTRAL": 57.0, "CHOP": 60.0},
    "ADA": {"TREND": 55.0, "NEUTRAL": 55.0, "CHOP": 60.0},
    "LINK": {"TREND": 55.0, "NEUTRAL": 55.0, "CHOP": 60.0},
}

DECISION_LABELS: dict[str, tuple[str, str]] = {
    "entered": ("ENTERED", "good"),
    "below_threshold": ("BELOW FLOOR", "watch"),
    "data_unavailable": ("NO DATA", "neutral"),
    "sizing_zero": ("SIZE = 0", "watch"),
    "research_only_block": ("RESEARCH ONLY", "neutral"),
    "dual_exposure_block": ("DUAL EXPOSURE", "neutral"),
    "chop_blocked": ("CHOP BLOCK", "neutral"),
    "pullback_reclaim_quarantined": ("QUARANTINED", "neutral"),
    "economics_fail": ("ECON FAIL", "watch"),
    "kill_switch": ("KILL SWITCH", "problem"),
    "regime_blocked": ("REGIME BLOCK", "neutral"),
    "score_gate": ("SCORE GATE", "watch"),
    "system_block": ("SYS BLOCK", "neutral"),
    "bug_integrity": ("INTEGRITY", "problem"),
}

REGIME_META: dict[str, tuple[str, str]] = {
    "TRENDING_UP": ("TREND ↑", "#3fb950"),
    "TRENDING_DOWN": ("TREND ↓", "#f85149"),
    "TRENDING": ("TREND", "#3fb950"),
    "NEUTRAL": ("NEUTRAL", "#d29922"),
    "RANGING": ("NEUTRAL", "#d29922"),
    "CHOP": ("CHOP", "#6e7681"),
    "": ("—", "#484f58"),
}


def _age_str(ts_str: str) -> str:
    if not ts_str:
        return "—"
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        secs = int((datetime.now(timezone.utc) - ts).total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        return f"{secs // 3600}h ago"
    except Exception:
        return "—"


def get_symbol_grid() -> list[dict[str, Any]]:
    """Latest scan result per symbol with regime, score, floor, decision."""
    rows = _q(
        """
        SELECT symbol, ts, decision, composite_score, primary_setup, regime, direction
        FROM scan_candidates
        WHERE source='live_v10'
          AND symbol IN ('BTC','ETH','SOL','XRP','LTC','DOGE','ADA','LINK')
        GROUP BY symbol
        HAVING ts = MAX(ts)
        """,
    )
    by_symbol = {r["symbol"]: r for r in rows}

    result = []
    for sym in SPOT_SYMBOLS:
        row = by_symbol.get(sym) or {}
        regime_raw = str(row.get("regime") or "")
        regime_label, regime_color = REGIME_META.get(regime_raw, ("—", "#484f58"))
        floors = SCORE_FLOORS.get(sym, {"TREND": 55.0, "NEUTRAL": 55.0, "CHOP": 60.0})
        regime_key = (
            "CHOP"
            if "CHOP" in regime_raw
            else ("TREND" if "TREND" in regime_raw else "NEUTRAL")
        )
        floor = floors.get(regime_key, 55.0)
        score = float(row.get("composite_score") or 0.0)
        decision_raw = str(row.get("decision") or "")
        d_label, d_status = DECISION_LABELS.get(
            decision_raw, (decision_raw.upper().replace("_", " ") or "—", "neutral")
        )
        setup = str(row.get("primary_setup") or "")
        direction = str(row.get("direction") or "")
        result.append(
            {
                "symbol": sym,
                "regime_label": regime_label,
                "regime_color": regime_color,
                "score": score,
                "floor": floor,
                "decision_label": d_label,
                "decision_status": d_status,
                "setup": setup,
                "direction": direction,
                "age": _age_str(str(row.get("ts") or "")),
                "has_data": bool(row),
            }
        )
    return result


def get_decision_log(limit: int = 12) -> list[dict[str, Any]]:
    """Recent scan decisions across all symbols, newest first."""
    rows = _q(
        """
        SELECT symbol, ts, decision, composite_score, regime, direction, primary_setup
        FROM scan_candidates
        WHERE source='live_v10'
          AND symbol IN ('BTC','ETH','SOL','XRP','LTC','DOGE','ADA','LINK')
        ORDER BY ts DESC
        LIMIT ?
        """,
        (limit,),
    )
    result = []
    for r in rows:
        decision_raw = str(r.get("decision") or "")
        d_label, d_status = DECISION_LABELS.get(
            decision_raw, (decision_raw.upper().replace("_", " ") or "—", "neutral")
        )
        regime_raw = str(r.get("regime") or "")
        regime_label, regime_color = REGIME_META.get(regime_raw, ("—", "#484f58"))
        result.append(
            {
                "symbol": r["symbol"],
                "age": _age_str(str(r.get("ts") or "")),
                "decision_label": d_label,
                "decision_status": d_status,
                "score": float(r.get("composite_score") or 0.0),
                "regime_label": regime_label,
                "regime_color": regime_color,
                "setup": str(r.get("primary_setup") or "—"),
                "direction": str(r.get("direction") or ""),
            }
        )
    return result


def get_bot_pulse() -> dict[str, Any]:
    """Bot heartbeat: last scan time, health status, kill switch."""
    evt = _q1(
        "SELECT ts, message FROM system_events WHERE source='heartbeat' ORDER BY ts DESC LIMIT 1"
    )
    health = _q1(
        "SELECT ts, message FROM system_events WHERE source='health_check' ORDER BY ts DESC LIMIT 1"
    )
    ks = _q1(
        "SELECT ts, reason, resumed_at FROM kill_switch_log ORDER BY id DESC LIMIT 1"
    )
    ks_active = bool(ks and not ks.get("resumed_at"))

    last_scan_ts = str(evt.get("ts") or "") if evt else ""
    last_scan_msg = str(evt.get("message") or "") if evt else ""
    health_msg = str(health.get("message") or "") if health else ""
    healthy = "HEALTHY" in health_msg

    state = _q1(
        "SELECT process_mode, launch_readiness_state FROM system_runtime_state ORDER BY id DESC LIMIT 1"
    )
    mode = str(state.get("process_mode") or "unknown") if state else "unknown"
    readiness = (
        str(state.get("launch_readiness_state") or "unknown") if state else "unknown"
    )

    return {
        "mode": mode,
        "readiness": readiness,
        "last_scan_age": _age_str(last_scan_ts),
        "last_scan_msg": last_scan_msg,
        "healthy": healthy,
        "health_msg": health_msg,
        "kill_switch_active": ks_active,
        "ks_reason": str(ks.get("reason") or "") if ks else "",
    }
