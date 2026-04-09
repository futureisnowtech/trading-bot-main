"""
dashboard/data/health.py — System health, heartbeat, error rate, ML gate status.
"""

import re
from datetime import datetime, timedelta

from db import _q, _q1
from formatters import _ts_age_s


def get_health_status() -> dict:
    """Parse the last health_check event from system_events."""
    row = _q1("""
        SELECT ts, level, message FROM system_events
        WHERE source = 'health_check'
        ORDER BY rowid DESC LIMIT 1
    """)
    if not row:
        return {
            "status": "UNKNOWN",
            "score": 0,
            "total": 6,
            "ts": None,
            "message": "No health check data yet",
        }
    msg = row.get("message", "")
    ts = row.get("ts", "")
    m = re.search(r"(\d+)/(\d+)", msg)
    score = int(m.group(1)) if m else 0
    total = int(m.group(2)) if m else 6
    if "HEALTHY" in msg.upper():
        status = "HEALTHY"
    elif "DEGRADED" in msg.upper():
        status = "DEGRADED"
    else:
        status = "UNHEALTHY"
    return {"status": status, "score": score, "total": total, "ts": ts, "message": msg}


def get_heartbeat_age() -> int:
    """Seconds since last heartbeat write."""
    row = _q1("""
        SELECT ts FROM system_events
        WHERE source = 'heartbeat'
        ORDER BY rowid DESC LIMIT 1
    """)
    if not row or not row.get("ts"):
        return 9999
    return _ts_age_s(row["ts"])


def get_error_rate_1h() -> int:
    """Count of ERROR events in last 60 minutes."""
    cutoff = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    r = _q1(
        "SELECT COUNT(*) AS n FROM system_events WHERE level='ERROR' AND ts >= ?",
        (cutoff,),
    )
    return r.get("n") or 0


def get_restart_count_24h() -> int:
    """Number of bot start events in last 24h."""
    cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    r = _q1(
        "SELECT COUNT(*) AS n FROM system_events WHERE ts >= ? AND message LIKE '%Bot started%'",
        (cutoff,),
    )
    return r.get("n") or 0


def get_ml_status():
    r = _q1("SELECT COUNT(*) AS n FROM trade_features")
    return {"snapshots": r.get("n") or 0, "min_needed": 30}
