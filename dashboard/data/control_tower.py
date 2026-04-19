"""
dashboard/data/control_tower.py — Thin composition layer for the CONTROL TOWER page.

Aggregates across existing data readers into one flat dict so the page widget
has a single call to make. Every sub-reader is wrapped in try/except so one
failure does not crash the whole tower.
"""

from __future__ import annotations

import os
import sys

_DASH_DIR = os.path.dirname(os.path.abspath(__file__))
_DASHBOARD_DIR = os.path.dirname(_DASH_DIR)
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

from db import _q, _q1

_TS_NORM = "datetime(replace(substr(ts,1,19),'T',' '))"


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def get_control_tower_snapshot() -> dict:
    """
    Returns a flat dict with everything the CONTROL TOWER page needs:
      health, heartbeat_age, error_count, runtime_mode,
      open_positions, perp_positions, spot_positions, forecast_positions,
      account_equity, daily_pnl, deployed_usd, deployed_pct,
      crypto_funnel, forecast_snapshot, incident_count, action_items
    """
    result: dict = {}

    # ── Health / heartbeat ─────────────────────────────────────────────────────
    result["health"] = _safe(
        lambda: __import__(
            "data.health", fromlist=["get_health_status"]
        ).get_health_status(),
        {"status": "UNKNOWN", "score": 0, "total": 7, "ts": None, "message": ""},
    )
    result["heartbeat_age"] = _safe(
        lambda: __import__(
            "data.health", fromlist=["get_heartbeat_age"]
        ).get_heartbeat_age(),
        9999,
    )
    result["error_count"] = _safe(
        lambda: __import__(
            "data.health", fromlist=["get_error_rate_1h"]
        ).get_error_rate_1h(),
        0,
    )

    # ── Runtime mode ───────────────────────────────────────────────────────────
    try:
        from db import _runtime_paper_flag

        result["runtime_mode"] = "PAPER" if _runtime_paper_flag() else "LIVE"
    except Exception:
        result["runtime_mode"] = "UNKNOWN"

    # ── Positions ──────────────────────────────────────────────────────────────
    all_pos = _safe(
        lambda: __import__(
            "data.positions", fromlist=["get_open_positions"]
        ).get_open_positions(),
        [],
    )
    result["open_positions"] = all_pos or []
    result["perp_positions"] = [
        p for p in (all_pos or []) if not str(p.get("strategy", "")).startswith("spot_")
    ]
    result["spot_positions"] = [
        p for p in (all_pos or []) if str(p.get("strategy", "")).startswith("spot_")
    ]

    # Forecast open positions count from DB
    result["forecast_positions"] = len(
        _q("SELECT id FROM open_positions WHERE strategy LIKE 'forecast_%'")
    )

    # ── Account / equity ───────────────────────────────────────────────────────
    try:
        from data.account import get_account, get_today_pnl

        equity, _, _ = get_account()
        result["account_equity"] = equity
        result["daily_pnl"] = get_today_pnl()
    except Exception:
        result["account_equity"] = 0.0
        result["daily_pnl"] = 0.0

    # ── Deployed capital ───────────────────────────────────────────────────────
    deployed_usd = 0.0
    for p in result["perp_positions"]:
        try:
            deployed_usd += abs(float(p.get("qty") or 0)) * float(p.get("entry") or 0)
        except Exception:
            pass
    result["deployed_usd"] = deployed_usd
    try:
        result["deployed_pct"] = (
            (deployed_usd / result["account_equity"] * 100)
            if result["account_equity"] and result["account_equity"] > 0
            else 0.0
        )
    except Exception:
        result["deployed_pct"] = 0.0

    # ── Crypto funnel ──────────────────────────────────────────────────────────
    result["crypto_funnel"] = _safe(
        lambda: __import__(
            "data.trading_control", fromlist=["get_crypto_control_snapshot"]
        ).get_crypto_control_snapshot(24),
        {},
    )

    # ── Forecast snapshot ──────────────────────────────────────────────────────
    result["forecast_snapshot"] = _safe(
        lambda: __import__(
            "data.trading_control", fromlist=["get_forecast_control_snapshot"]
        ).get_forecast_control_snapshot(),
        {},
    )

    # ── Open incident count ────────────────────────────────────────────────────
    result["incident_count"] = _safe(
        lambda: (
            _q1(
                "SELECT COUNT(*) AS n FROM incidents WHERE status='open' OR status IS NULL"
            ).get("n")
            or 0
        ),
        0,
    )

    # ── Action items ───────────────────────────────────────────────────────────
    actions: list[str] = []

    if result["heartbeat_age"] > 300:
        actions.append(
            f"Heartbeat stale ({result['heartbeat_age']}s) — bot may be down"
        )

    if result["error_count"] > 5:
        actions.append(f"{result['error_count']} errors in last hour — check logs")

    exec_failed = int(
        (result["crypto_funnel"] or {})
        .get("decision_counts", {})
        .get("execution_failed", 0)
    )
    if exec_failed > 0:
        actions.append(
            f"{exec_failed} execution failures in 24h — check broker connection"
        )

    blank_count = int(
        (result["crypto_funnel"] or {}).get("blank_tradeability_count", 0)
    )
    if blank_count > 0:
        actions.append(f"{blank_count} candidates with blank tradeability fields")

    if result["incident_count"] and result["incident_count"] > 0:
        actions.append(f"{result['incident_count']} open incidents")

    result["action_items"] = actions

    return result
