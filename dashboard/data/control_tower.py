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
try:
    from db import get_current_strategy_start_date
except Exception:  # pragma: no cover - fail-soft for runtime import drift
    get_current_strategy_start_date = lambda normalized=True: "2026-04-24 00:00:00"

_TS_NORM = "datetime(replace(substr(ts,1,19),'T',' '))"


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _lane_defaults() -> dict[str, dict]:
    try:
        from config import (
            STOCKS_DASHBOARD_VISIBLE,
            STOCKS_AUTONOMOUS_ENABLED,
            STOCKS_MANUAL_ENABLED,
            FORECAST_DASHBOARD_VISIBLE,
            FORECAST_AUTONOMOUS_ENABLED,
            FORECAST_MANUAL_ENABLED,
            FUTURES_DASHBOARD_VISIBLE,
        )
    except Exception:
        STOCKS_DASHBOARD_VISIBLE = True
        STOCKS_AUTONOMOUS_ENABLED = False
        STOCKS_MANUAL_ENABLED = False
        FORECAST_DASHBOARD_VISIBLE = True
        FORECAST_AUTONOMOUS_ENABLED = False
        FORECAST_MANUAL_ENABLED = False
        FUTURES_DASHBOARD_VISIBLE = True

    return {
        "crypto": {
            "display_name": "Crypto",
            "lane_role": "primary",
            "dashboard_visible": 1,
            "autonomous_enabled": 1,
            "manual_allowed": 1,
            "promotion_condition": "Primary live lane",
        },
        "stocks": {
            "display_name": "Stocks",
            "lane_role": "dormant_ready",
            "dashboard_visible": int(STOCKS_DASHBOARD_VISIBLE),
            "autonomous_enabled": int(STOCKS_AUTONOMOUS_ENABLED),
            "manual_allowed": int(STOCKS_MANUAL_ENABLED),
            "promotion_condition": "Promote after equity edge and PDT-aware rules are proven",
        },
        "forecast": {
            "display_name": "Forecast",
            "lane_role": "blocked_ready",
            "dashboard_visible": int(FORECAST_DASHBOARD_VISIBLE),
            "autonomous_enabled": int(FORECAST_AUTONOMOUS_ENABLED),
            "manual_allowed": int(FORECAST_MANUAL_ENABLED),
            "promotion_condition": "Promote after enrollment, tradable contracts, and stable heartbeat truth",
        },
        "mes_archived": {
            "display_name": "Futures",
            "lane_role": "archived",
            "dashboard_visible": int(FUTURES_DASHBOARD_VISIBLE),
            "autonomous_enabled": 0,
            "manual_allowed": 0,
            "promotion_condition": "Reactivate after futures approval and MES lane validation",
        },
    }


def _lane_overview() -> list[dict]:
    defaults = _lane_defaults()
    lane_ids = tuple(defaults.keys())
    try:
        rows = _q(
            """
            SELECT lane_id, lane_role, dashboard_visible, autonomous_enabled,
                   manual_allowed, active, connected, tradable, readiness_state,
                   blocked_reason, mode, promotion_condition, buying_power_usd,
                   capital_deployed_usd, positions_open
            FROM lane_runtime_state
            WHERE lane_id IN (?,?,?,?)
            ORDER BY CASE lane_id
                WHEN 'crypto' THEN 1
                WHEN 'stocks' THEN 2
                WHEN 'forecast' THEN 3
                WHEN 'mes_archived' THEN 4
                ELSE 99 END
            """,
            lane_ids,
        )
    except Exception:
        rows = _q(
            """
            SELECT lane_id, active, connected, tradable, readiness_state,
                   blocked_reason, mode, buying_power_usd, capital_deployed_usd,
                   positions_open
            FROM lane_runtime_state
            WHERE lane_id IN (?,?,?,?)
            ORDER BY CASE lane_id
                WHEN 'crypto' THEN 1
                WHEN 'stocks' THEN 2
                WHEN 'forecast' THEN 3
                WHEN 'mes_archived' THEN 4
                ELSE 99 END
            """,
            lane_ids,
        )

    by_id = {r.get("lane_id"): r for r in (rows or [])}
    result: list[dict] = []
    for lane_id in ("crypto", "stocks", "forecast", "mes_archived"):
        base = defaults[lane_id].copy()
        row = by_id.get(lane_id, {})
        base.update(row or {})
        base["lane_id"] = lane_id
        base["active"] = int(base.get("active") or 0)
        base["connected"] = int(base.get("connected") or 0)
        base["tradable"] = int(base.get("tradable") or 0)
        base["dashboard_visible"] = int(base.get("dashboard_visible") or 0)
        base["autonomous_enabled"] = int(base.get("autonomous_enabled") or 0)
        base["manual_allowed"] = int(base.get("manual_allowed") or 0)
        result.append(base)
    return [r for r in result if r.get("dashboard_visible")]


def get_control_tower_snapshot(hours: int = 24) -> dict:
    """
    Returns a flat dict with everything the CONTROL TOWER page needs.

    hours — the operator-selected window (1, 24, or 168). All windowed
    sub-readers (crypto_funnel, lifecycle_stages, action_items) use this
    value. Non-windowed panels (open positions, lane health, heartbeat)
    are always current and labeled as such in the UI.

    Returns:
      health, heartbeat_age, error_count, runtime_mode,
      open_positions, perp_positions, spot_positions, forecast_positions,
      account_equity, daily_pnl, deployed_usd, deployed_pct,
      crypto_funnel, lifecycle_stages, forecast_snapshot,
      incident_count, action_items, lane_overview, window_hours
    """
    result: dict = {"window_hours": hours}
    result["metrics_since"] = get_current_strategy_start_date(normalized=True)

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
    pos_snapshot = _safe(
        lambda: __import__(
            "data.positions", fromlist=["get_crypto_deployed_snapshot"]
        ).get_crypto_deployed_snapshot(),
        {},
    ) or {}
    result["perp_positions"] = pos_snapshot.get("perp_positions") or []
    result["spot_positions"] = pos_snapshot.get("spot_positions") or []
    result["open_positions"] = (result["perp_positions"] or []) + (result["spot_positions"] or [])

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
    deployed_usd = float(pos_snapshot.get("deployed_usd") or 0.0)
    result["deployed_usd"] = deployed_usd
    try:
        result["deployed_pct"] = (
            (deployed_usd / result["account_equity"] * 100)
            if result["account_equity"] and result["account_equity"] > 0
            else 0.0
        )
    except Exception:
        result["deployed_pct"] = 0.0

    # ── Crypto funnel (windowed) ───────────────────────────────────────────────
    result["crypto_funnel"] = _safe(
        lambda: __import__(
            "data.trading_control", fromlist=["get_crypto_control_snapshot"]
        ).get_crypto_control_snapshot(hours),
        {},
    )
    result["current_trade_stats"] = _safe(
        lambda: __import__(
            "data.performance", fromlist=["get_performance_stats"]
        ).get_performance_stats(current_only=True),
        {"closes": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "profit_factor": 0.0, "total_pnl": 0.0, "total_fees": 0.0},
    )

    # ── Lifecycle stages (windowed) ────────────────────────────────────────────
    result["lifecycle_stages"] = _safe(
        lambda: __import__(
            "data.trading_control", fromlist=["get_lifecycle_stages"]
        ).get_lifecycle_stages(hours),
        [],
    )

    # ── Forecast snapshot ──────────────────────────────────────────────────────
    result["forecast_snapshot"] = _safe(
        lambda: __import__(
            "data.trading_control", fromlist=["get_forecast_control_snapshot"]
        ).get_forecast_control_snapshot(),
        {},
    )
    result["lane_overview"] = _safe(_lane_overview, [])

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
            f"{exec_failed} execution failures in {hours}h window — check broker connection"
        )

    blank_count = int(
        (result["crypto_funnel"] or {}).get("blank_tradeability_count", 0)
    )
    if blank_count > 0:
        actions.append(
            f"{blank_count} candidates with blank tradeability fields in {hours}h window"
        )

    if result["incident_count"] and result["incident_count"] > 0:
        actions.append(f"{result['incident_count']} open incidents")

    result["action_items"] = actions

    return result
