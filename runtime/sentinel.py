"""Proactive health sentinel.

Every notification today is reactive: the only thing that pings Telegram is a
trade actually opening or closing (notifications/notification_engine.py). If the
bot silently stops entering -- a veto storm or a release-gate block --
book -- nothing tells you until you happen to open the cockpit. This runs the same
checks an operator would ask JARVIS for, on the daemon's own cycle, and messages
Telegram only when a check transitions from OK to bad (edge-triggered) or the
problem has persisted past a cooldown, so it does not spam every cycle.

Called from execution_daemon.py's main loop. Must never raise: a bug here must not
take the trading loop down with it, so every check and the send itself are wrapped.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_COOLDOWN_SECONDS = 2 * 3600  # do not repeat an unresolved alert within this window


def _state_path() -> Path:
    from config import RUNTIME_ROOT

    return Path(RUNTIME_ROOT) / "sentinel_state.json"


def _load_state() -> dict:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _should_alert(state: dict, key: str, is_bad: bool) -> bool:
    if not is_bad:
        return False
    prev = state.get(key, {})
    if not prev.get("bad"):
        return True  # transition into bad state
    last_sent = float(prev.get("last_sent_ts") or 0)
    return (datetime.now(timezone.utc).timestamp() - last_sent) > _COOLDOWN_SECONDS


def _mark(state: dict, key: str, is_bad: bool, *, sent: bool) -> None:
    entry = state.setdefault(key, {})
    entry["bad"] = is_bad
    if sent:
        entry["last_sent_ts"] = datetime.now(timezone.utc).timestamp()


def _send(message: str) -> bool:
    try:
        from notifications.telegram_bot import send_message

        return send_message(f"\U0001F6F0️ <b>Sentinel</b>\n{message}")
    except Exception as exc:
        logger.warning("Sentinel could not send Telegram message: %s", exc)
        return False


def _check_entries_allowed() -> tuple[str, bool, str]:
    from runtime.operator_truth import get_release_status

    status = get_release_status() or {}
    allowed = bool(status.get("entries_allowed"))
    blockers = status.get("top_infrastructure_blockers") or []
    blocker = blockers[0] if blockers else "release audit not yet promoted"
    return "entries_allowed", not allowed, f"Fresh entries are BLOCKED. Blocker: {blocker}."


def _check_entry_stall(stall_hours: float = 3.0) -> tuple[str, bool, str]:
    """No successful BUY in stall_hours. Only meaningful while entries are allowed --
    a blocked gate already has its own alert, and a stall while blocked is expected."""
    from config import DB_PATH, TRADE_DATA_START_DATE
    from runtime.operator_truth import get_release_status

    status = get_release_status() or {}
    if not status.get("entries_allowed"):
        return "entry_stall", False, ""

    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT MAX(ts) FROM trades WHERE action='BUY' AND broker='kalshi' AND ts >= ?",
        (TRADE_DATA_START_DATE,),
    ).fetchone()
    conn.close()
    last_ts = row[0] if row else None
    if not last_ts:
        return "entry_stall", False, ""

    try:
        last_dt = datetime.fromisoformat(str(last_ts).replace("Z", "+00:00"))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except Exception:
        return "entry_stall", False, ""

    hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600.0
    is_bad = hours > stall_hours
    return (
        "entry_stall",
        is_bad,
        f"No new entry in {hours:.1f}h while entries are allowed (threshold {stall_hours:.0f}h). "
        f"Ask JARVIS 'why no trades' for the veto/execution breakdown.",
    )


def _check_pending_approvals() -> tuple[str, bool, str]:
    from runtime import approvals

    pending = approvals.list_pending()
    n = len(pending)
    if n == 0:
        return "pending_approvals", False, ""
    names = ", ".join(f"#{p['id']} {p['action']}" for p in pending[:3])
    return "pending_approvals", True, f"{n} change(s) awaiting cockpit approval: {names}."


def _check_rbi_evidence() -> tuple[str, bool, str]:
    """Alert if live scans stop producing complete current-epoch model traces."""
    from intelligence.health import get_rbi_evidence_health

    health = get_rbi_evidence_health()
    issues = list(health.get("issues") or [])
    prediction_age = health.get("latest_prediction_age_seconds")
    valid_age = health.get("latest_valid_prediction_age_seconds")
    run = health.get("latest_run") or {}
    run_age = run.get("age_seconds")
    if prediction_age is not None and float(prediction_age) > 30 * 60:
        issues.append("prediction_evidence_stale_over_30m")
    if valid_age is not None and float(valid_age) > 30 * 60:
        issues.append("valid_pricing_trace_stale_over_30m")
    if run_age is not None and float(run_age) > 20 * 3600:
        issues.append("intelligence_run_stale_over_20h")

    is_bad = str(health.get("status") or "unknown") != "healthy" or bool(issues)
    summary = ", ".join(dict.fromkeys(str(issue) for issue in issues)) or "unknown"
    return (
        "rbi_evidence",
        is_bad,
        "RBI evidence health is degraded: "
        f"{summary}. Trading remains governed by the 60/40 baseline; inspect "
        "JARVIS or /status before any promotion.",
    )


_CHECKS: list[Callable[[], tuple[str, bool, str]]] = [
    _check_entries_allowed,
    _check_entry_stall,
    _check_rbi_evidence,
    _check_pending_approvals,
]


def check_and_alert() -> list[str]:
    """Run all checks; send Telegram messages for new or renewed problems.

    Returns the messages actually sent, mainly so callers/tests can assert on it.
    """
    state = _load_state()
    sent: list[str] = []

    for check in _CHECKS:
        try:
            key, is_bad, message = check()
        except Exception as exc:
            logger.warning("Sentinel check %s failed: %s", getattr(check, "__name__", check), exc)
            continue

        if _should_alert(state, key, is_bad) and message:
            if _send(message):
                sent.append(message)
                _mark(state, key, is_bad, sent=True)
                continue
        _mark(state, key, is_bad, sent=False)

    _save_state(state)
    return sent
