"""
runtime/incident_tracker.py — Groups repeated errors into incidents.

Table: incidents (in logs/trades.db)
  id INTEGER PRIMARY KEY AUTOINCREMENT
  lane_id TEXT           ('crypto', 'forecast', 'mes_archived', 'system')
  source TEXT            (from system_events.source)
  fingerprint TEXT       (normalized error signature — first 120 chars of message, lowercased)
  first_seen_at TEXT
  last_seen_at TEXT
  count INTEGER
  severity TEXT          ('INFO', 'WARNING', 'ERROR', 'CRITICAL')
  state TEXT             ('open', 'resolved', 'suppressed')
  updated_at TEXT
  UNIQUE(lane_id, fingerprint)
"""

import os
import sys
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DB_PATH, FUTURES_LANE_ACTIVE

_DDL_INCIDENTS = """
CREATE TABLE IF NOT EXISTS incidents (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    lane_id        TEXT NOT NULL,
    source         TEXT NOT NULL DEFAULT '',
    fingerprint    TEXT NOT NULL,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    count          INTEGER NOT NULL DEFAULT 1,
    severity       TEXT NOT NULL DEFAULT 'INFO',
    state          TEXT NOT NULL DEFAULT 'open',
    updated_at     TEXT NOT NULL,
    UNIQUE(lane_id, fingerprint)
)
"""


def _conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    c = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lane_from_source(source: str) -> str:
    """Map a system_events source name to a lane_id."""
    src = (source or "").lower()
    if any(x in src for x in ("ibkrbroker", "mes_", "mes", "ibkr")):
        return "mes_archived"
    if any(x in src for x in ("forecastrunner", "forecast", "forecastex")):
        return "forecast"
    if any(x in src for x in ("coinbase", "crypto", "perp", "scanner", "v10_runner")):
        return "crypto"
    return "system"


def _severity_from_level(level: str) -> str:
    """Map system_events.level to incident severity."""
    lvl = (level or "INFO").upper()
    if lvl in ("CRITICAL", "FATAL"):
        return "CRITICAL"
    if lvl == "ERROR":
        return "ERROR"
    if lvl == "WARNING":
        return "WARNING"
    return "INFO"


# ── Table init ────────────────────────────────────────────────────────────────

def init_incident_table(db_path: str = DB_PATH) -> None:
    """CREATE TABLE IF NOT EXISTS incidents."""
    with _conn(db_path) as c:
        c.execute(_DDL_INCIDENTS)


# ── Ingestion ─────────────────────────────────────────────────────────────────

def ingest_system_events(lookback_minutes: int = 60, db_path: str = DB_PATH) -> int:
    """
    Read system_events for the past lookback_minutes, group by fingerprint,
    and upsert into incidents. Returns count of incidents upserted.

    fingerprint = source + '::' + message[:80].lower().strip()
    """
    cutoff_iso = (
        datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    ).isoformat()

    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT source, level, message, ts FROM system_events "
                "WHERE ts >= ? ORDER BY ts",
                (cutoff_iso,),
            ).fetchall()
    except Exception:
        return 0

    # Group by fingerprint
    groups: dict[tuple, dict] = {}  # (lane_id, fingerprint) → aggregated info
    for row in rows:
        source = row["source"] or ""
        level = row["level"] or "INFO"
        message = (row["message"] or "")
        ts = row["ts"] or _now_iso()

        lane_id = _lane_from_source(source)

        # Skip archived lane sources when FUTURES_LANE_ACTIVE=False
        if lane_id == "mes_archived" and not FUTURES_LANE_ACTIVE:
            continue

        fp = f"{source}::{message[:80].lower().strip()}"

        key = (lane_id, fp)
        if key not in groups:
            groups[key] = {
                "lane_id": lane_id,
                "source": source,
                "fingerprint": fp,
                "first_seen_at": ts,
                "last_seen_at": ts,
                "count": 1,
                "severity": _severity_from_level(level),
            }
        else:
            groups[key]["count"] += 1
            groups[key]["last_seen_at"] = max(groups[key]["last_seen_at"], ts)
            # Escalate severity if higher
            sev_order = {"INFO": 0, "WARNING": 1, "ERROR": 2, "CRITICAL": 3}
            new_sev = _severity_from_level(level)
            if sev_order.get(new_sev, 0) > sev_order.get(groups[key]["severity"], 0):
                groups[key]["severity"] = new_sev

    if not groups:
        return 0

    now = _now_iso()
    upserted = 0
    try:
        with _conn(db_path) as c:
            for key, g in groups.items():
                c.execute(
                    """
                    INSERT INTO incidents
                        (lane_id, source, fingerprint, first_seen_at, last_seen_at,
                         count, severity, state, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)
                    ON CONFLICT(lane_id, fingerprint) DO UPDATE SET
                        last_seen_at = MAX(last_seen_at, excluded.last_seen_at),
                        count        = count + excluded.count,
                        severity     = CASE
                            WHEN excluded.severity='CRITICAL' THEN 'CRITICAL'
                            WHEN severity='CRITICAL' THEN 'CRITICAL'
                            WHEN excluded.severity='ERROR' THEN 'ERROR'
                            WHEN severity='ERROR' THEN 'ERROR'
                            WHEN excluded.severity='WARNING' THEN 'WARNING'
                            WHEN severity='WARNING' THEN 'WARNING'
                            ELSE 'INFO' END,
                        state        = CASE WHEN state='resolved' THEN 'open' ELSE state END,
                        updated_at   = excluded.updated_at
                    """,
                    (
                        g["lane_id"],
                        g["source"],
                        g["fingerprint"],
                        g["first_seen_at"],
                        g["last_seen_at"],
                        g["count"],
                        g["severity"],
                        now,
                    ),
                )
                upserted += 1
    except Exception:
        pass

    return upserted


# ── Read ──────────────────────────────────────────────────────────────────────

def get_open_incidents(exclude_archived: bool = True, db_path: str = DB_PATH) -> list:
    """
    Returns open incidents as list of dicts.
    When exclude_archived=True, filters out lane_id='mes_archived'.
    """
    try:
        with _conn(db_path) as c:
            if exclude_archived:
                rows = c.execute(
                    "SELECT * FROM incidents WHERE state='open' AND lane_id != 'mes_archived' "
                    "ORDER BY last_seen_at DESC"
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM incidents WHERE state='open' ORDER BY last_seen_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def get_incident_summary(db_path: str = DB_PATH) -> dict:
    """Returns {total_open, by_lane, by_severity}."""
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT lane_id, severity, COUNT(*) as cnt FROM incidents "
                "WHERE state='open' GROUP BY lane_id, severity"
            ).fetchall()

        by_lane: dict = {}
        by_severity: dict = {}
        total = 0
        for r in rows:
            lane = r["lane_id"]
            sev = r["severity"]
            cnt = r["cnt"]
            total += cnt
            by_lane[lane] = by_lane.get(lane, 0) + cnt
            by_severity[sev] = by_severity.get(sev, 0) + cnt

        return {"total_open": total, "by_lane": by_lane, "by_severity": by_severity}
    except Exception:
        return {"total_open": 0, "by_lane": {}, "by_severity": {}}


# ── Stale resolution ──────────────────────────────────────────────────────────

def resolve_stale_incidents(stale_minutes: int = 30, db_path: str = DB_PATH) -> int:
    """
    Mark incidents as 'resolved' if last_seen_at is older than stale_minutes
    AND the source has not appeared in system_events within stale_minutes.
    Returns count of incidents resolved.
    """
    cutoff_iso = (
        datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
    ).isoformat()

    try:
        with _conn(db_path) as c:
            # Get open incidents older than stale_minutes
            old = c.execute(
                "SELECT id, source, fingerprint FROM incidents "
                "WHERE state='open' AND last_seen_at < ?",
                (cutoff_iso,),
            ).fetchall()

            if not old:
                return 0

            resolved = 0
            for row in old:
                # Check if source appeared recently in system_events
                recent = c.execute(
                    "SELECT COUNT(*) FROM system_events WHERE source=? AND ts >= ?",
                    (row["source"], cutoff_iso),
                ).fetchone()[0]
                if recent == 0:
                    c.execute(
                        "UPDATE incidents SET state='resolved', updated_at=? WHERE id=?",
                        (_now_iso(), row["id"]),
                    )
                    resolved += 1
            return resolved
    except Exception:
        return 0
