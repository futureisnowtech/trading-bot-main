"""
dashboard/db.py — Database primitives shared across all dashboard modules.
"""

import os
import sqlite3
import sys
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_ROOT, "logs", "trades.db")
LOG_PATH = os.path.join(_ROOT, "logs", "bot.log")
LAUNCH_DATE = "2026-04-02"  # paper trading start
LIVE_START_DATE = "2026-04-15"  # live trading start
CURRENT_STRATEGY_EPOCH = os.getenv(
    "DASHBOARD_CURRENT_STRATEGY_EPOCH", "2026-04-24T00:00:00"
)

# Ensure `import db` and `import dashboard.db` resolve to the same module object.
# Without this, monkeypatching DB_PATH in tests or runtime shims can diverge across
# dashboard modules depending on how they imported the DB helper.
_THIS_MODULE = sys.modules[__name__]
sys.modules.setdefault("db", _THIS_MODULE)
sys.modules.setdefault("dashboard.db", _THIS_MODULE)


def get_effective_launch_date() -> str:
    """Return LIVE_START_DATE."""
    return LIVE_START_DATE


def _parse_dt(raw: str) -> datetime:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("blank timestamp")
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except Exception:
        if "T" in text:
            return datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
        if len(text) == 10:
            return datetime.strptime(text, "%Y-%m-%d")
        return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")


def get_current_strategy_start_date(*, normalized: bool = True) -> str:
    """
    Return the lower bound for current operational metrics.

    Live mode defaults to the most recent strategy rollout epoch so the dashboard
    highlights current-policy truth instead of mixing old strategy eras.
    """
    raw = CURRENT_STRATEGY_EPOCH
    dt = _parse_dt(raw)
    return dt.strftime("%Y-%m-%d %H:%M:%S") if normalized else dt.isoformat()


def clamp_metrics_cutoff(raw: str) -> str:
    """
    Clamp a rolling cutoff to the current strategy epoch floor.

    This keeps "current state" dashboard windows from drifting earlier than the
    latest live strategy rollout.
    """
    try:
        floor = _parse_dt(get_current_strategy_start_date(normalized=False))
        candidate = _parse_dt(raw)
        chosen = candidate if candidate > floor else floor
        return chosen.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return get_current_strategy_start_date(normalized=True)


def _runtime_paper_flag() -> int:
    """
    Return 1 if current process_mode is 'paper', else 0 (live).
    Authoritative source is the system_runtime_state table in the DB.
    """
    try:
        row = _q1("SELECT process_mode FROM system_runtime_state ORDER BY id DESC LIMIT 1")
        if row and row.get("process_mode") == "live":
            return 0
        if row and row.get("process_mode") == "paper":
            return 1
    except Exception:
        pass
    
    # Fallback for tests mocking config.False
    try:
        import sys
        _cfg = sys.modules.get("config")
        if _cfg and getattr(_cfg, "False", False):
            return 1
        
        # v10 legacy compatibility
        import config
        if getattr(config, "False", False):
            return 1
    except Exception:
        pass

    # v18.18 fallback: strictly live
    return 0


def _q(sql, params=()):
    try:
        with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute(sql, params).fetchall()]
    except Exception:
        return []


def _q1(sql, params=()):
    rows = _q(sql, params)
    return rows[0] if rows else {}


def _tail_log(n=800):
    try:
        with open(LOG_PATH, "r") as f:
            return f.readlines()[-n:]
    except Exception:
        return []
