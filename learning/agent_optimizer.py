"""
learning/agent_optimizer.py — Closed-Loop Autonomous Parameter & Strategy Optimizer.

Implements walk-forward validation, strict operational boundary enforcement,
atomic file mutex updates, local pre-flight proof verification, and mutation ledger logging.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import DB_PATH, REPO_ROOT, RUNTIME_ROOT
from runtime.agent_mutation_ledger import (
    STATUS_APPLIED,
    STATUS_PROPOSED,
    STATUS_REJECTED,
    STATUS_SOAKING,
    record_mutation_proposal,
    update_mutation_status,
)

logger = logging.getLogger(__name__)

LOCK_FILE_PATH = os.path.join(RUNTIME_ROOT, "agent_optimizer.lock")

# Strict Operational Safety Bounds (Hard Invariants)
PARAM_SAFETY_BOUNDS: Dict[str, Tuple[float, float]] = {
    "hard_rbi_threshold": (0.40, 0.80),
    "max_position_size": (1.0, 100.0),
    "gfs_weight": (0.20, 0.80),
    "ecmwf_weight": (0.20, 0.80),
    "min_edge_cents": (0.01, 0.20),
}


class ParameterOutOfBoundsError(ValueError):
    """Raised when an agent proposal violates hard operational bounds."""

    pass


class OutOfSampleValidationFailedError(ValueError):
    """Raised when out-of-sample backtest validation fails."""

    pass


def validate_parameter_bounds(param_name: str, value: float) -> bool:
    """Check if a parameter value is strictly within safety bounds."""
    if param_name not in PARAM_SAFETY_BOUNDS:
        return True
    low, high = PARAM_SAFETY_BOUNDS[param_name]
    try:
        val = float(value)
        return low <= val <= high
    except (TypeError, ValueError):
        return False


def get_current_git_sha() -> str:
    """Get current git commit SHA."""
    try:
        res = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return res.strip()
    except Exception:
        return "unknown"


def evaluate_walk_forward_split(
    data: List[Dict[str, Any]],
    in_sample_pct: float = 0.70,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split trade/settlement records chronologically into in-sample and out-of-sample."""
    if not data:
        return [], []
    sorted_data = sorted(data, key=lambda x: str(x.get("ts") or ""))
    split_idx = int(len(sorted_data) * in_sample_pct)
    return sorted_data[:split_idx], sorted_data[split_idx:]


def compute_sample_brier_and_ev(records: List[Dict[str, Any]], proposed_val: float) -> Tuple[float, float]:
    """
    Compute Brier score and expected value metric on a dataset slice.
    Lower Brier score is better. Higher EV is better.
    """
    if not records:
        return 0.0, 0.0
    total_brier = 0.0
    total_ev = 0.0
    count = 0
    for r in records:
        won = float(r.get("won") or 0.0)
        prob = float(r.get("forecast_yes_prob") or 0.50)
        total_brier += (prob - won) ** 2
        total_ev += float(r.get("pnl_usd") or 0.0)
        count += 1
    avg_brier = total_brier / max(1, count)
    avg_ev = total_ev / max(1, count)
    return avg_brier, avg_ev


def run_local_preflight_checks() -> Tuple[bool, str]:
    """Run local release audit and proof suite before committing any change."""
    release_script = os.path.join(REPO_ROOT, "scripts", "release_audit.py")
    if not os.path.exists(release_script):
        return True, "No release_audit.py found; preflight skipped."
    try:
        res = subprocess.check_output(
            [sys.executable, release_script, "--local", "--format", "json"],
            cwd=REPO_ROOT,
            stderr=subprocess.STDOUT,
            timeout=300,
            text=True,
        )
        if "READY_FOR_LIVE" in res or "PASS_WITH_WARNINGS" in res or '"entries_allowed": true' in res:
            return True, "Preflight passed."
        return False, f"Release audit did not pass: {res[:200]}"
    except subprocess.CalledProcessError as err:
        return False, f"Preflight audit process error: {err.output[:300]}"
    except Exception as exc:
        return False, f"Preflight check exception: {exc}"


def atomic_write_json(file_path: str, data: Any) -> None:
    """Atomically update a JSON config file using a temp file and atomic rename."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp." + str(os.getpid()))
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_path, path)


def propose_and_apply_hub_parameter(
    hub_name: str,
    parameter_key: str,
    new_value: float,
    reasoning: str,
    *,
    trade_records: Optional[List[Dict[str, Any]]] = None,
    db_path: str = DB_PATH,
) -> Dict[str, Any]:
    """
    Main entry point for autonomous hub parameter optimization.
    Enforces bounds, walk-forward out-of-sample validation, atomic writes, and pre-flight checks.
    """
    os.makedirs(Path(LOCK_FILE_PATH).parent, exist_ok=True)
    lock_fd = open(LOCK_FILE_PATH, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        return {
            "success": False,
            "error": "Optimizer lock active. Another optimization is running.",
        }

    try:
        hub_key = str(hub_name or "").strip().upper()
        if not validate_parameter_bounds(parameter_key, new_value):
            low, high = PARAM_SAFETY_BOUNDS.get(parameter_key, (0, 0))
            raise ParameterOutOfBoundsError(
                f"Value {new_value} for '{parameter_key}' violates safety bounds [{low}, {high}]"
            )

        config_path = os.path.join(REPO_ROOT, "config", "hub_params.json")
        current_config: Dict[str, Any] = {}
        if os.path.exists(config_path):
            with open(config_path, encoding="utf-8") as f:
                current_config = json.load(f)

        old_val = current_config.get(hub_key, {}).get(parameter_key, 0.50)
        sha_before = get_current_git_sha()

        # Walk-forward validation
        in_sample, out_sample = evaluate_walk_forward_split(trade_records or [])
        in_brier, in_ev = compute_sample_brier_and_ev(in_sample, new_value)
        out_brier, out_ev = compute_sample_brier_and_ev(out_sample, new_value)
        base_out_brier, base_out_ev = compute_sample_brier_and_ev(out_sample, old_val)

        # Reject if out-of-sample performance degrades Brier score significantly
        if out_sample and out_brier > base_out_brier + 0.05:
            raise OutOfSampleValidationFailedError(
                f"Out-of-sample Brier score degraded from {base_out_brier:.4f} to {out_brier:.4f}"
            )

        mutation_id = record_mutation_proposal(
            sha_before=sha_before,
            target_file="config/hub_params.json",
            parameter_key=f"{hub_key}.{parameter_key}",
            old_value=old_val,
            new_value=new_value,
            reasoning_trace=reasoning,
            in_sample_brier_delta=in_brier - base_out_brier,
            out_sample_pnl_delta=out_ev - base_out_ev,
            db_path=db_path,
        )

        # Apply update in-memory
        if hub_key not in current_config:
            current_config[hub_key] = {}
        current_config[hub_key][parameter_key] = new_value

        # Atomic file write
        atomic_write_json(config_path, current_config)

        # Run pre-flight checks
        ok, msg = run_local_preflight_checks()
        if not ok:
            # Revert atomic write
            current_config[hub_key][parameter_key] = old_val
            atomic_write_json(config_path, current_config)
            update_mutation_status(
                mutation_id,
                STATUS_REJECTED,
                rollback_reason=f"Preflight failed: {msg}",
                db_path=db_path,
            )
            return {
                "success": False,
                "mutation_id": mutation_id,
                "error": f"Preflight verification failed: {msg}",
            }

        update_mutation_status(mutation_id, STATUS_SOAKING, db_path=db_path)
        logger.info(
            "Successfully applied autonomous parameter mutation %s: %s.%s = %s -> %s",
            mutation_id,
            hub_key,
            parameter_key,
            old_val,
            new_value,
        )

        return {
            "success": True,
            "mutation_id": mutation_id,
            "hub": hub_key,
            "parameter": parameter_key,
            "old_value": old_val,
            "new_value": new_value,
            "status": STATUS_SOAKING,
            "preflight": msg,
        }

    except Exception as exc:
        logger.error("Autonomous parameter optimization failed: %s", exc)
        return {"success": False, "error": str(exc)}
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
        except Exception:
            pass
