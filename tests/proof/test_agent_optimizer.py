"""
tests/proof/test_agent_optimizer.py — Proof Test Suite for Closed-Loop Agent Optimizer.

Verifies:
1. Operational bounds enforcement (rejection of out-of-bounds parameters).
2. Walk-forward chronological data splitting.
3. Mutation ledger recording and status updates.
4. Atomic parameter optimization and pre-flight check validation.
5. Post-deploy 72h soak monitoring and automated circuit-breaker rollback.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from learning.agent_optimizer import (
    OutOfSampleValidationFailedError,
    ParameterOutOfBoundsError,
    evaluate_walk_forward_split,
    propose_and_apply_hub_parameter,
    validate_parameter_bounds,
)
from runtime.agent_mutation_ledger import (
    STATUS_APPLIED,
    STATUS_PROPOSED,
    STATUS_REVERTED,
    STATUS_SOAKING,
    get_active_soak_mutations,
    get_mutation_history,
    init_mutation_ledger_db,
    record_mutation_proposal,
    update_mutation_status,
)
from runtime.post_deploy_soak import (
    compute_post_mutation_performance,
    execute_parameter_rollback,
    run_post_deploy_soak_monitor,
)


def test_parameter_bounds_enforcement():
    """Verify that out-of-bounds parameters are strictly rejected."""
    assert validate_parameter_bounds("hard_rbi_threshold", 0.50) is True
    assert validate_parameter_bounds("hard_rbi_threshold", 0.35) is False
    assert validate_parameter_bounds("hard_rbi_threshold", 0.85) is False

    assert validate_parameter_bounds("max_position_size", 50.0) is True
    assert validate_parameter_bounds("max_position_size", 150.0) is False

    assert validate_parameter_bounds("gfs_weight", 0.60) is True
    assert validate_parameter_bounds("gfs_weight", 0.10) is False


def test_walk_forward_split():
    """Verify chronological 70/30 data split."""
    records = [{"ts": f"2026-07-{i:02d}T00:00:00Z", "val": i} for i in range(1, 11)]
    in_s, out_s = evaluate_walk_forward_split(records, in_sample_pct=0.70)
    assert len(in_s) == 7
    assert len(out_s) == 3
    assert in_s[-1]["val"] == 7
    assert out_s[0]["val"] == 8


def test_mutation_ledger_lifecycle():
    """Verify SQLite mutation logging lifecycle."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test_trades.db")
        init_mutation_ledger_db(db_path)

        mutation_id = record_mutation_proposal(
            sha_before="sha123",
            target_file="config/hub_params.json",
            parameter_key="MIDWEST.hard_rbi_threshold",
            old_value=0.50,
            new_value=0.55,
            reasoning_trace="Test optimization proposal",
            db_path=db_path,
        )
        assert mutation_id > 0

        history = get_mutation_history(limit=5, db_path=db_path)
        assert len(history) == 1
        assert history[0]["parameter_key"] == "MIDWEST.hard_rbi_threshold"
        assert history[0]["status"] == STATUS_PROPOSED

        update_mutation_status(mutation_id, STATUS_SOAKING, db_path=db_path)
        active_soak = get_active_soak_mutations(db_path=db_path)
        assert len(active_soak) == 1
        assert active_soak[0]["status"] == STATUS_SOAKING


def test_out_of_bounds_proposal_rejection():
    """Verify propose_and_apply_hub_parameter rejects invalid proposals."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test_trades.db")
        res = propose_and_apply_hub_parameter(
            hub_name="MIDWEST",
            parameter_key="hard_rbi_threshold",
            new_value=0.95,  # Out of bounds (max 0.80)
            reasoning="Invalid test",
            db_path=db_path,
        )
        assert res["success"] is False
        assert "violates safety bounds" in res["error"]


def test_automated_rollback_trigger():
    """Verify circuit-breaker rollback on degraded performance."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test_trades.db")
        init_mutation_ledger_db(db_path)

        # Create a mutation in SOAKING state
        mutation_id = record_mutation_proposal(
            sha_before="sha123",
            target_file="config/hub_params.json",
            parameter_key="MIDWEST.hard_rbi_threshold",
            old_value=0.50,
            new_value=0.60,
            reasoning_trace="Test soak mutation",
            db_path=db_path,
        )
        update_mutation_status(mutation_id, STATUS_SOAKING, db_path=db_path)

        # Create trades with losing results post-mutation
        import sqlite3

        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY,
                ts TEXT,
                paper INTEGER,
                pnl_usd REAL,
                won INTEGER
            )
            """
        )
        now_str = "2026-08-01T00:00:00Z"
        for i in range(6):
            conn.execute(
                "INSERT INTO trades (ts, paper, pnl_usd, won) VALUES (?, 0, -1.0, 0)",
                (now_str,),
            )
        conn.commit()
        conn.close()


        # Run soak monitor check
        soak_results = run_post_deploy_soak_monitor(repo_root=tmp_dir, db_path=db_path)
        assert len(soak_results) == 1
        assert soak_results[0]["status"] == STATUS_REVERTED

