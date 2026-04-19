"""
dashboard/data/engineering_console.py — Data reader for the ENGINEERING CONSOLE page.

Composes from journal_health, integrity, health readers.
All failures return safe defaults — never crashes the dashboard.
"""

from __future__ import annotations

import os
import sys

_DASH_DIR = os.path.dirname(os.path.abspath(__file__))
_DASHBOARD_DIR = os.path.dirname(_DASH_DIR)
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

from db import _q, _q1


def get_engineering_truth_summary() -> dict:
    """
    Returns:
      version, proof_count, journal_health, integrity_summary,
      runtime_truth_age, validator_summary
    """
    result: dict = {
        "version": "unknown",
        "proof_count": None,
        "journal_health": {},
        "integrity_summary": {},
        "runtime_truth_age": None,
        "validator_summary": {},
    }

    # Version from config
    try:
        from config import VERSION

        result["version"] = str(VERSION)
    except Exception:
        pass

    # Proof count from system_events
    try:
        row = _q1(
            "SELECT message FROM system_events WHERE source='proof_suite' ORDER BY rowid DESC LIMIT 1"
        )
        if row and row.get("message"):
            result["proof_count"] = row["message"]
    except Exception:
        pass

    # Journal health
    try:
        from data.journal_health import get_journal_health

        result["journal_health"] = get_journal_health()
    except Exception:
        pass

    # Integrity summary
    try:
        from data.integrity import get_integrity_summary

        result["integrity_summary"] = get_integrity_summary()
    except Exception:
        pass

    # Runtime truth age — how old is the most recent system_runtime_state row
    try:
        row = _q1(
            "SELECT startup_ts FROM system_runtime_state ORDER BY id DESC LIMIT 1"
        )
        if row and row.get("startup_ts"):
            result["runtime_truth_age"] = row["startup_ts"]
    except Exception:
        pass

    return result
