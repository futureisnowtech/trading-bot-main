"""Human-in-the-loop approval queue for changes proposed from a read-only surface.

Telegram cannot call write-tier tools directly (see runtime/brain.py's permission
tiers) -- a mistyped phone message must not be able to touch live trading code.
But that leaves a real gap: noticing a problem on your phone and having to walk to
a laptop to act on it. This closes it without weakening the split: Telegram can
*propose* one of a small whitelisted set of changes, the proposal lands here, and
it only takes effect once approved from the cockpit, which retains write access.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

# Every allowed action maps to an existing, already-safe write tool. This module
# adds no new mutation paths -- it only adds a queue in front of ones that exist.
WHITELIST: dict[str, dict[str, Any]] = {
    "set_maker_entry_enabled": {
        "description": "Enable or disable maker-first entry.",
        "params": ["enabled"],
    },
    "update_system_parameter": {
        "description": "Update a dynamic strategy parameter (passes the Safety Shield audit).",
        "params": ["key", "value", "rationale"],
    },
    "promote_release": {
        "description": "Promote the current release audit so fresh entries are allowed.",
        "params": [],
    },
    "promote_rbi_artifact": {
        "description": "Promote a validated RBI 2.0 challenger to production champion.",
        "params": ["artifact_id", "rationale"],
    },
    "create_cerebro_experiment": {
        "description": "Create and approve a shadow-only Cerebro experiment from an archived insight.",
        "params": ["insight_id", "rationale"],
    },
}


def _conn() -> sqlite3.Connection:
    from config import DB_PATH

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS pending_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            surface TEXT NOT NULL,
            action TEXT NOT NULL,
            params_json TEXT NOT NULL,
            rationale TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            resolved_at TEXT,
            result TEXT
        )"""
    )
    conn.commit()
    return conn


def whitelist_help() -> str:
    return "\n".join(
        f"- {name}: {meta['description']} (params: {', '.join(meta['params']) or 'none'})"
        for name, meta in WHITELIST.items()
    )


def request_change(
    action: str,
    params: dict[str, Any] | None = None,
    rationale: str = "",
    *,
    surface: str = "telegram",
    dedupe_pending: bool = False,
) -> str:
    """Queue a proposed change for cockpit approval. Never executes anything itself."""
    if action not in WHITELIST:
        return f"Unknown action '{action}'. Allowed actions:\n{whitelist_help()}"
    encoded_params = json.dumps(params or {}, sort_keys=True)
    conn = _conn()
    if dedupe_pending:
        existing = conn.execute(
            "SELECT id FROM pending_approvals WHERE status='pending' AND action=? AND params_json=? ORDER BY id DESC LIMIT 1",
            (action, encoded_params),
        ).fetchone()
        if existing:
            row_id = int(existing["id"])
            conn.close()
            return (
                f"Proposal #{row_id} is already pending for cockpit approval: {action}({params or {}}). "
                f"No duplicate was queued."
            )
    conn.execute(
        "INSERT INTO pending_approvals (created_at, surface, action, params_json, rationale, status) "
        "VALUES (?, ?, ?, ?, ?, 'pending')",
        (datetime.now(timezone.utc).isoformat(), surface, action, encoded_params, rationale),
    )
    conn.commit()
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return (
        f"Proposal #{row_id} queued for cockpit approval: {action}({params or {}}). "
        f"Rationale: {rationale or '(none given)'}. It has no effect until approved."
    )


def list_pending(limit: int = 20) -> list[dict[str, Any]]:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM pending_approvals WHERE status='pending' ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _execute(action: str, params: dict[str, Any]) -> str:
    if action == "set_maker_entry_enabled":
        from dashboard.jarvis_brain import update_system_parameter

        return update_system_parameter(
            "MAKER_ENTRY_ENABLED", str(bool(params.get("enabled"))), "approved via cockpit queue"
        )
    if action == "update_system_parameter":
        from dashboard.jarvis_brain import update_system_parameter

        return update_system_parameter(
            str(params.get("key", "")), str(params.get("value", "")), str(params.get("rationale", ""))
        )
    if action == "promote_release":
        from notifications.agent_tools import run_release_audit

        return run_release_audit("promote")
    if action == "promote_rbi_artifact":
        from intelligence.rbi2 import promote_challenger
        result = promote_challenger(
            str(params.get("artifact_id", "")),
            promoted_by="cockpit_approval",
            reason=str(params.get("rationale", "approved Cerebro proposal")),
        )
        return json.dumps(result, sort_keys=True)
    if action == "create_cerebro_experiment":
        from intelligence.cerebro import create_experiment_from_insight

        result = create_experiment_from_insight(
            str(params.get("insight_id", "")),
            approved_by="cockpit_approval",
        )
        return json.dumps(result, sort_keys=True)
    return f"No executor registered for action '{action}'."


def resolve(approval_id: int, *, approve: bool) -> str:
    """Approve or reject a pending item. Only the cockpit calls this."""
    conn = _conn()
    row = conn.execute(
        "SELECT * FROM pending_approvals WHERE id=? AND status='pending'", (approval_id,)
    ).fetchone()
    if not row:
        conn.close()
        return f"No pending approval #{approval_id}."

    status = "approved" if approve else "rejected"
    result = ""
    if approve:
        try:
            result = _execute(row["action"], json.loads(row["params_json"]))
        except Exception as exc:
            result = f"Execution error: {exc}"

    conn.execute(
        "UPDATE pending_approvals SET status=?, resolved_at=?, result=? WHERE id=?",
        (status, datetime.now(timezone.utc).isoformat(), result, approval_id),
    )
    conn.commit()
    conn.close()
    return result or status
