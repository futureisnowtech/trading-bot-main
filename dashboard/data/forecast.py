from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from config import DB_PATH as _CFG_DB_PATH
import dashboard.db as dashboard_db

_DEFAULT_DB_PATH = _CFG_DB_PATH
DB_PATH = _DEFAULT_DB_PATH

OPERATIONAL = "OPERATIONAL"
LANE_NOT_STARTED = "LANE_NOT_STARTED"
NO_TRADABLE_CONTRACTS_RIGHT_NOW = "NO_TRADABLE_CONTRACTS_RIGHT_NOW"


def _resolve_db_path() -> str:
    if DB_PATH != _DEFAULT_DB_PATH:
        return DB_PATH
    return getattr(dashboard_db, "DB_PATH", _DEFAULT_DB_PATH)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_resolve_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def get_forecast_health() -> dict:
    lane_started = False
    lane_heartbeat_at = None
    underliers_visible = 0

    with _connect() as conn:
        if _table_exists(conn, "forecast_markets"):
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM forecast_markets WHERE COALESCE(active, 1)=1"
            ).fetchone()
            underliers_visible = int(row["n"] or 0) if row else 0

        if _table_exists(conn, "lane_runtime_state"):
            row = conn.execute(
                """
                SELECT active, last_heartbeat_at
                FROM lane_runtime_state
                WHERE lane_id='forecast'
                LIMIT 1
                """
            ).fetchone()
            if row:
                lane_started = bool(row["active"])
                lane_heartbeat_at = row["last_heartbeat_at"]
                if lane_started and lane_heartbeat_at:
                    try:
                        heartbeat = datetime.fromisoformat(
                            str(lane_heartbeat_at).replace("Z", "+00:00")
                        )
                        if heartbeat.tzinfo is None:
                            heartbeat = heartbeat.replace(tzinfo=timezone.utc)
                        lane_started = heartbeat >= (
                            datetime.now(timezone.utc) - timedelta(hours=2)
                        )
                    except Exception:
                        lane_started = False
        else:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM system_events
                WHERE source='ForecastRunner'
                  AND datetime(replace(substr(ts,1,19),'T',' '))
                      >= datetime('now', '-2 hours')
                """
            ).fetchone()
            lane_started = bool(row and row["n"])

    return {
        "lane_started": lane_started,
        "lane_heartbeat_at": lane_heartbeat_at,
        "underliers_visible": underliers_visible,
    }


def get_forecast_readiness() -> dict:
    checks = []
    health = get_forecast_health()

    with _connect() as conn:
        markets = 0
        contracts = 0
        quotes = 0
        bars = 0
        if _table_exists(conn, "forecast_markets"):
            markets = int(
                conn.execute("SELECT COUNT(*) AS n FROM forecast_markets").fetchone()["n"]
            )
        if _table_exists(conn, "forecast_contracts"):
            contracts = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM forecast_contracts WHERE active=1"
                ).fetchone()["n"]
            )
        if _table_exists(conn, "forecast_quotes"):
            quotes = int(
                conn.execute("SELECT COUNT(*) AS n FROM forecast_quotes").fetchone()["n"]
            )
        if _table_exists(conn, "forecast_bars"):
            bars = int(
                conn.execute("SELECT COUNT(*) AS n FROM forecast_bars").fetchone()["n"]
            )

    if not health["lane_started"]:
        checks.append({"status": "WARN", "detail": "Forecast lane not started."})
        return {
            "lane_state": LANE_NOT_STARTED,
            "status": "ACTION_NEEDED",
            "underliers_visible": health["underliers_visible"],
            "contracts_unavailable_count": max(0, markets - contracts),
            "checks": checks,
        }

    if contracts == 0:
        checks.append(
            {
                "status": "WARN",
                "detail": "Lane active but no tradable forecast contracts right now.",
            }
        )
        checks.append(
            {
                "status": "INFO",
                "detail": f"Underliers={markets} Contracts={contracts} Quotes={quotes} Bars={bars}",
            }
        )
        return {
            "lane_state": NO_TRADABLE_CONTRACTS_RIGHT_NOW,
            "status": "BLOCKED",
            "underliers_visible": health["underliers_visible"],
            "contracts_unavailable_count": markets,
            "checks": checks,
        }

    if quotes == 0 or bars == 0:
        checks.append({"status": "WARN", "detail": "Forecast data incomplete."})
        return {
            "lane_state": NO_TRADABLE_CONTRACTS_RIGHT_NOW,
            "status": "BLOCKED",
            "underliers_visible": health["underliers_visible"],
            "contracts_unavailable_count": max(0, contracts),
            "checks": checks,
        }

    checks.append({"status": "PASS", "detail": f"Contracts available: {contracts}"})
    return {
        "lane_state": OPERATIONAL,
        "status": "READY",
        "underliers_visible": health["underliers_visible"],
        "contracts_unavailable_count": 0,
        "checks": checks,
    }
