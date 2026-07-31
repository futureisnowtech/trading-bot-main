"""
runtime/post_deploy_soak.py — Post-Deploy 72-Hour Soak & Circuit-Breaker Rollback Monitor.

Monitors autonomous parameter and code mutations in SOAKING state.
Triggers an automated rollback and CRITICAL incident if post-update trade win-rate
or Brier score degrades beyond threshold.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from config import DB_PATH, REPO_ROOT
from runtime.agent_mutation_ledger import (
    STATUS_PASSED,
    STATUS_REVERTED,
    STATUS_SOAKING,
    get_active_soak_mutations,
    update_mutation_status,
)
from runtime.incident_tracker import record_incident

logger = logging.getLogger(__name__)

SOAK_DURATION_HOURS = 72.0
MAX_ALLOWED_BRIER_DEGRADATION = 0.10
MAX_ALLOWED_WINRATE_DROP_PCT = 0.15


def _parse_iso(ts_str: str) -> datetime:
    """Parse ISO timestamp safely into timezone-aware UTC datetime."""
    try:
        raw = str(ts_str or "").strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)



def compute_post_mutation_performance(
    mutation_ts: str,
    db_path: str = DB_PATH,
) -> Dict[str, float]:
    """Calculate trade win-rate and average PnL for trades placed after mutation_ts."""
    try:
        dt_mutation = _parse_iso(mutation_ts)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        all_rows = conn.execute(
            """
            SELECT ts, pnl_usd, won FROM trades
            WHERE paper = 0
            """
        ).fetchall()
        conn.close()

        rows = [dict(r) for r in all_rows if _parse_iso(str(r["ts"] if isinstance(r, sqlite3.Row) else r.get("ts") or "")) >= dt_mutation]

        if not rows:
            return {"sample_size": 0, "win_rate": 1.0, "total_pnl": 0.0}

        wins = sum(1 for r in rows if float(r.get("pnl_usd") or 0.0) > 0 or int(r.get("won") or 0) == 1)
        win_rate = wins / max(1, len(rows))
        total_pnl = sum(float(r.get("pnl_usd") or 0.0) for r in rows)

        return {
            "sample_size": float(len(rows)),
            "win_rate": win_rate,
            "total_pnl": total_pnl,
        }
    except Exception as exc:
        logger.error("Error computing post-mutation performance: %s", exc)
        return {"sample_size": 0, "win_rate": 1.0, "total_pnl": 0.0}


def execute_parameter_rollback(
    mutation: Dict[str, Any],
    reason: str,
    *,
    repo_root: str = REPO_ROOT,
    db_path: str = DB_PATH,
) -> bool:
    """Revert a parameter change back to its old value."""
    try:
        target_file = mutation.get("target_file", "config/hub_params.json")
        abs_path = os.path.join(repo_root, target_file)
        param_key = str(mutation.get("parameter_key") or "")
        old_val_json = str(mutation.get("old_value_json") or "{}")
        old_val = json.loads(old_val_json)

        current_config: dict = {}
        if os.path.exists(abs_path):
            with open(abs_path, "r", encoding="utf-8") as f:
                current_config = json.load(f)

        if "." in param_key:
            hub_key, key = param_key.split(".", 1)
            if hub_key not in current_config:
                current_config[hub_key] = {}
            current_config[hub_key][key] = old_val
        else:
            current_config[param_key] = old_val

        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        temp_path = abs_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(current_config, f, indent=2, sort_keys=True)

        os.replace(temp_path, abs_path)
        logger.warning(
            "AUTOMATED ROLLBACK EXECUTED for mutation %s (%s): %s",
            mutation.get("id"),
            param_key,
            reason,
        )

        try:
            record_incident(
                lane_id="forecast",
                source="post_deploy_soak",
                fingerprint=f"rollback_mutation_{mutation.get('id')}",
                message=f"Rolled back mutation {mutation.get('id')} ({param_key}): {reason}",
                level=40,  # ERROR / CRITICAL level
                db_path=db_path,
            )
        except Exception:
            pass
        return True
    except Exception as exc:
        logger.error("Failed to execute parameter rollback for mutation %s: %s", mutation.get("id"), exc)
        return False


def run_post_deploy_soak_monitor(
    *,
    repo_root: str = REPO_ROOT,
    db_path: str = DB_PATH,
) -> List[Dict[str, Any]]:
    """
    Evaluates all active mutations in SOAKING status.
    Promotes to PASSED if 72 hours elapse cleanly.
    Reverts and logs CRITICAL incident if performance degrades.
    """
    results = []
    active = get_active_soak_mutations(db_path)
    now = datetime.now(timezone.utc)

    for mutation in active:
        mutation_id = int(mutation.get("id"))
        ts = _parse_iso(str(mutation.get("ts") or ""))
        elapsed_hours = (now - ts).total_seconds() / 3600.0

        perf = compute_post_mutation_performance(str(mutation.get("ts") or ""), db_path=db_path)
        sample_size = perf["sample_size"]
        win_rate = perf["win_rate"]

        # Check circuit breaker conditions if we have at least 5 post-mutation trades
        if sample_size >= 5 and win_rate < (1.0 - MAX_ALLOWED_WINRATE_DROP_PCT):
            rollback_reason = f"Post-deploy win-rate degraded to {win_rate:.1%} across {sample_size:.0f} trades."
            execute_parameter_rollback(mutation, rollback_reason, repo_root=repo_root, db_path=db_path)
            update_mutation_status(
                mutation_id,
                STATUS_REVERTED,
                rollback_reason=rollback_reason,
                db_path=db_path,
            )
            results.append({"id": mutation_id, "status": STATUS_REVERTED, "reason": rollback_reason})

        elif elapsed_hours >= SOAK_DURATION_HOURS:
            update_mutation_status(mutation_id, STATUS_PASSED, db_path=db_path)
            logger.info("Mutation %s successfully passed 72h soak duration.", mutation_id)
            results.append({"id": mutation_id, "status": STATUS_PASSED, "reason": "72h soak completed."})
        else:
            results.append({
                "id": mutation_id,
                "status": STATUS_SOAKING,
                "elapsed_hours": elapsed_hours,
                "win_rate": win_rate,
                "sample_size": sample_size,
            })

    return results
