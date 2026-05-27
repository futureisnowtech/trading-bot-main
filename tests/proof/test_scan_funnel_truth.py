"""
tests/proof/test_scan_funnel_truth.py — Proof tests for exact scan funnel persistence (v16).

Coverage:
  1. scan_funnels table exists after init_db()
  2. log_scan_funnel() writes one row with correct counts
  3. Derived totals (scored_total, econ_passed_total, final_entryable_total) are exact
  4. research_only_block is correctly counted in derived totals
  5. execution_failed is correctly counted in derived totals
  6. Multiple scan_ids produce separate rows (no aggregation across scans)
  7. log_scan_funnel returns positive row id on success
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_scan_funnels_table_exists(proof_runtime):
    """scan_funnels table must exist after init_db."""
    with sqlite3.connect(proof_runtime.db_path) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "scan_funnels" in tables, "scan_funnels table missing after init_db()"


def test_log_scan_funnel_inserts_row(proof_runtime, monkeypatch):
    """log_scan_funnel must insert one row per call."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    row_id = tl.log_scan_funnel(
        scan_id="test_scan_001",
        scanner_candidates_total=50,
        dual_exposure_block=5,
        cooldown_block=3,
        risk_block=2,
        data_unavailable=1,
        below_threshold=20,
        econ_veto=10,
        research_only_block=4,
        sizing_zero=2,
        execution_failed=1,
        entered=2,
    )

    assert row_id > 0, f"expected positive row_id, got {row_id}"

    with sqlite3.connect(proof_runtime.db_path) as conn:
        row = conn.execute(
            "SELECT * FROM scan_funnels WHERE id=?", (row_id,)
        ).fetchone()
        cols = [
            d[1] for d in conn.execute("PRAGMA table_info(scan_funnels)").fetchall()
        ]

    assert row is not None, "row not found after insert"
    row_dict = dict(zip(cols, row))
    assert row_dict["scan_id"] == "test_scan_001"
    assert row_dict["scanner_candidates_total"] == 50
    assert row_dict["entered"] == 2


def test_derived_totals_are_exact(proof_runtime, monkeypatch):
    """scored_total, econ_passed_total, final_entryable_total must be exact."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    row_id = tl.log_scan_funnel(
        scan_id="derive_test",
        scanner_candidates_total=30,
        dual_exposure_block=2,
        cooldown_block=1,
        risk_block=1,
        data_unavailable=2,
        below_threshold=10,
        econ_veto=5,
        research_only_block=3,
        sizing_zero=1,
        execution_failed=2,
        entered=3,
    )

    with sqlite3.connect(proof_runtime.db_path) as conn:
        cols = [
            d[1] for d in conn.execute("PRAGMA table_info(scan_funnels)").fetchall()
        ]
        row = conn.execute(
            "SELECT * FROM scan_funnels WHERE id=?", (row_id,)
        ).fetchone()

    row_dict = dict(zip(cols, row))

    # scored_total = below_threshold + econ_veto + research_only_block + sizing_zero + execution_failed + entered
    expected_scored = 10 + 5 + 3 + 1 + 2 + 3
    assert row_dict["scored_total"] == expected_scored, (
        f"scored_total: expected {expected_scored}, got {row_dict['scored_total']}"
    )

    # econ_passed_total = research_only_block + sizing_zero + execution_failed + entered
    expected_econ_passed = 3 + 1 + 2 + 3
    assert row_dict["econ_passed_total"] == expected_econ_passed, (
        f"econ_passed_total: expected {expected_econ_passed}, got {row_dict['econ_passed_total']}"
    )

    # final_entryable_total = sizing_zero + execution_failed + entered
    expected_final = 1 + 2 + 3
    assert row_dict["final_entryable_total"] == expected_final, (
        f"final_entryable_total: expected {expected_final}, got {row_dict['final_entryable_total']}"
    )


def test_research_only_block_in_scored_but_not_final(proof_runtime, monkeypatch):
    """research_only_block must appear in scored_total and econ_passed_total but not final_entryable_total."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    row_id = tl.log_scan_funnel(
        scan_id="rob_test",
        research_only_block=5,
        entered=1,
    )

    with sqlite3.connect(proof_runtime.db_path) as conn:
        cols = [
            d[1] for d in conn.execute("PRAGMA table_info(scan_funnels)").fetchall()
        ]
        row = conn.execute(
            "SELECT * FROM scan_funnels WHERE id=?", (row_id,)
        ).fetchone()

    row_dict = dict(zip(cols, row))
    # research_only_block=5, entered=1 → scored=6, econ_passed=6, final=1
    assert row_dict["scored_total"] == 6
    assert row_dict["econ_passed_total"] == 6
    assert row_dict["final_entryable_total"] == 1


def test_execution_failed_in_final_entryable(proof_runtime, monkeypatch):
    """execution_failed must appear in final_entryable_total."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    row_id = tl.log_scan_funnel(
        scan_id="ef_test",
        execution_failed=3,
        entered=2,
    )

    with sqlite3.connect(proof_runtime.db_path) as conn:
        cols = [
            d[1] for d in conn.execute("PRAGMA table_info(scan_funnels)").fetchall()
        ]
        row = conn.execute(
            "SELECT * FROM scan_funnels WHERE id=?", (row_id,)
        ).fetchone()

    row_dict = dict(zip(cols, row))
    assert row_dict["final_entryable_total"] == 5  # 3+2


def test_multiple_scan_ids_separate_rows(proof_runtime, monkeypatch):
    """Each scan_id must produce an independent row (no cross-scan aggregation)."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    tl.log_scan_funnel(scan_id="scan_A", entered=1)
    tl.log_scan_funnel(scan_id="scan_B", entered=2)

    with sqlite3.connect(proof_runtime.db_path) as conn:
        rows = conn.execute(
            "SELECT scan_id, entered FROM scan_funnels ORDER BY id"
        ).fetchall()

    scan_ids = [r[0] for r in rows]
    assert "scan_A" in scan_ids
    assert "scan_B" in scan_ids
    assert len(rows) >= 2


def test_log_scan_funnel_returns_positive_id(proof_runtime, monkeypatch):
    """log_scan_funnel must return a positive integer row id."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    row_id = tl.log_scan_funnel(scan_id="id_test", entered=1)
    assert isinstance(row_id, int) and row_id > 0, (
        f"expected positive int, got {row_id!r}"
    )
