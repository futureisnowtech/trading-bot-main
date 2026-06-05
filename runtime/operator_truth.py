"""Canonical operator-truth helpers for live Kalshi status and drift detection."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from config import DB_PATH


def _connect_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def _json_or_empty(value: Any) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _normalize_broker_position(position: dict) -> dict:
    return {
        "ticker": str(position.get("local_symbol") or ""),
        "side": str(position.get("side") or "").upper(),
        "right": str(position.get("right") or ""),
        "qty": float(position.get("qty") or 0.0),
        "entry_price": float(
            position.get("entry_price")
            or position.get("entry")
            or position.get("avg_entry")
            or 0.0
        ),
        "forecast_yes_prob": position.get("forecast_yes_prob"),
        "entered_at": position.get("entered_at"),
        "source": "broker",
    }


def _normalize_db_position(position: sqlite3.Row | dict) -> dict:
    row = dict(position)
    return {
        "ticker": str(row.get("ticker") or ""),
        "side": str(row.get("side") or "").upper(),
        "qty": float(row.get("qty") or 0.0),
        "entry_price": float(row.get("entry_price") or 0.0),
        "opened_at": row.get("opened_at"),
        "source": "db",
    }


def _position_key(position: dict) -> tuple[str, str]:
    return (
        str(position.get("ticker") or ""),
        str(position.get("side") or "").upper(),
    )


def _position_drift(broker_positions: list[dict], db_positions: list[dict]) -> dict:
    broker_map = {_position_key(pos): pos for pos in broker_positions}
    db_map = {_position_key(pos): pos for pos in db_positions}

    broker_only = sorted(
        [
            broker_map[key]
            for key in broker_map.keys() - db_map.keys()
            if broker_map[key]["ticker"]
        ],
        key=lambda pos: (pos["ticker"], pos["side"]),
    )
    db_only = sorted(
        [
            db_map[key]
            for key in db_map.keys() - broker_map.keys()
            if db_map[key]["ticker"]
        ],
        key=lambda pos: (pos["ticker"], pos["side"]),
    )

    qty_mismatches = []
    entry_mismatches = []
    for key in broker_map.keys() & db_map.keys():
        b_pos = broker_map[key]
        d_pos = db_map[key]
        if abs(float(b_pos["qty"]) - float(d_pos["qty"])) > 1e-9:
            qty_mismatches.append(
                {
                    "ticker": b_pos["ticker"],
                    "side": b_pos["side"],
                    "broker_qty": b_pos["qty"],
                    "db_qty": d_pos["qty"],
                }
            )
        if abs(float(b_pos["entry_price"]) - float(d_pos["entry_price"])) > 1e-9:
            entry_mismatches.append(
                {
                    "ticker": b_pos["ticker"],
                    "side": b_pos["side"],
                    "broker_entry_price": b_pos["entry_price"],
                    "db_entry_price": d_pos["entry_price"],
                }
            )

    return {
        "has_drift": bool(broker_only or db_only or qty_mismatches or entry_mismatches),
        "broker_only": broker_only,
        "db_only": db_only,
        "qty_mismatches": sorted(qty_mismatches, key=lambda item: (item["ticker"], item["side"])),
        "entry_mismatches": sorted(entry_mismatches, key=lambda item: (item["ticker"], item["side"])),
    }


def get_recent_veto_summary(
    *, db_path: str = DB_PATH, lookback_hours: int = 6, limit: int = 200
) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, int(lookback_hours)))).isoformat()
    records: list[dict] = []

    try:
        with _connect_db(db_path) as conn:
            rows = conn.execute(
                """
                SELECT ts, source, message
                FROM system_events
                WHERE source='ForecastRunner'
                  AND level IN ('WARNING', 'ERROR')
                  AND ts >= ?
                  AND message LIKE '% vetoed: %'
                ORDER BY ts DESC
                LIMIT ?
                """,
                (cutoff, max(1, int(limit))),
            ).fetchall()
    except Exception as exc:
        return {
            "lookback_hours": lookback_hours,
            "count": 0,
            "top_reasons": [],
            "recent_records": [],
            "error": str(exc),
        }

    reasons = Counter()
    for row in rows:
        message = str(row["message"] or "")
        _prefix, _sep, reason = message.partition(" vetoed: ")
        reason = reason.strip() or "unknown"
        reasons[reason] += 1
        records.append(
            {
                "ts": row["ts"],
                "reason": reason,
                "message": message,
            }
        )

    return {
        "lookback_hours": lookback_hours,
        "count": len(records),
        "top_reasons": [
            {"reason": reason, "count": count}
            for reason, count in reasons.most_common(8)
        ],
        "recent_records": records[:12],
    }


def get_recent_execution_summary(
    *, db_path: str = DB_PATH, lookback_hours: int = 6, limit: int = 200
) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, int(lookback_hours)))).isoformat()
    records: list[dict] = []

    try:
        with _connect_db(db_path) as conn:
            rows = conn.execute(
                """
                SELECT ts, source, message
                FROM system_events
                WHERE source='ForecastRunner'
                  AND level IN ('WARNING', 'ERROR')
                  AND ts >= ?
                  AND (
                        message LIKE '% execution_result: %'
                     OR message LIKE '% execution_blocked: %'
                  )
                ORDER BY ts DESC
                LIMIT ?
                """,
                (cutoff, max(1, int(limit))),
            ).fetchall()
    except Exception as exc:
        return {
            "lookback_hours": lookback_hours,
            "count": 0,
            "top_outcomes": [],
            "recent_records": [],
            "error": str(exc),
        }

    outcomes = Counter()
    for row in rows:
        message = str(row["message"] or "")
        if " execution_result: " in message:
            _prefix, _sep, outcome = message.partition(" execution_result: ")
        else:
            _prefix, _sep, outcome = message.partition(" execution_blocked: ")
        outcome = outcome.strip() or "unknown"
        outcomes[outcome] += 1
        records.append(
            {
                "ts": row["ts"],
                "outcome": outcome,
                "message": message,
            }
        )

    return {
        "lookback_hours": lookback_hours,
        "count": len(records),
        "top_outcomes": [
            {"outcome": outcome, "count": count}
            for outcome, count in outcomes.most_common(8)
        ],
        "recent_records": records[:12],
    }


def get_live_kalshi_status(
    *,
    db_path: str = DB_PATH,
    connect: bool = True,
    sync_broker: bool = True,
    include_recent_vetoes: bool = True,
    include_recent_execution: bool = True,
) -> dict:
    """Return broker-first live truth for Telegram, HUD, and operator analysis."""
    from execution.kalshi_broker import get_kalshi_broker

    broker = get_kalshi_broker()
    broker_connected = broker.is_connected()
    broker_error = ""

    if connect and not broker_connected:
        try:
            broker_connected = bool(broker.connect())
        except Exception as exc:
            broker_error = str(exc)
            broker_connected = False

    if broker_connected and sync_broker:
        try:
            broker.sync_positions()
        except Exception as exc:
            if not broker_error:
                broker_error = f"sync_positions_failed: {exc}"

    balance_usd = 0.0
    broker_positions: list[dict] = []
    if broker_connected:
        try:
            balance_usd = float(broker.get_account_balance() or 0.0)
        except Exception as exc:
            if not broker_error:
                broker_error = f"get_account_balance_failed: {exc}"
        try:
            broker_positions = [
                _normalize_broker_position(pos)
                for pos in broker.get_positions()
                if float(pos.get("qty") or 0.0) > 0
            ]
        except Exception as exc:
            if not broker_error:
                broker_error = f"get_positions_failed: {exc}"

    db_positions: list[dict] = []
    active_markets = 0
    lane_state = {}
    snapshot = {}
    db_error = ""
    try:
        with _connect_db(db_path) as conn:
            db_positions = [
                _normalize_db_position(row)
                for row in conn.execute(
                    """
                    SELECT ticker, qty, entry_price, side, opened_at
                    FROM forecast_positions
                    WHERE active = 1 AND qty > 0
                    ORDER BY opened_at ASC
                    """
                ).fetchall()
            ]

            row = conn.execute(
                "SELECT COUNT(*) AS n FROM forecast_markets WHERE active=1"
            ).fetchone()
            active_markets = int((row["n"] if row else 0) or 0)

            lane_row = conn.execute(
                "SELECT * FROM lane_runtime_state WHERE lane_id='forecast'"
            ).fetchone()
            if lane_row:
                lane_state = dict(lane_row)
                snapshot = _json_or_empty(lane_row["snapshot_json"])
                lane_state.pop("snapshot_json", None)
    except Exception as exc:
        db_error = str(exc)

    drift = _position_drift(broker_positions, db_positions)

    payload = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "broker_connected": broker_connected,
        "broker_error": broker_error,
        "db_error": db_error,
        "balance_usd": round(balance_usd, 2),
        "active_markets": active_markets,
        "broker_positions_count": len(broker_positions),
        "db_positions_count": len(db_positions),
        "broker_positions": broker_positions,
        "db_positions": db_positions,
        "position_drift": drift,
        "forecast_lane": lane_state,
        "forecast_snapshot": snapshot,
    }
    if include_recent_vetoes:
        payload["recent_vetoes"] = get_recent_veto_summary(db_path=db_path)
    if include_recent_execution:
        payload["recent_execution"] = get_recent_execution_summary(db_path=db_path)
    return payload
