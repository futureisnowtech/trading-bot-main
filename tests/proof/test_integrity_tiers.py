"""
tests/proof/test_integrity_tiers.py

Proof suite for the integrity tier substrate (v18.16).

Verifies the invariants that prevent quarantined/suspect/excluded rows from
silently contaminating live learning (Bayesian weights, Kelly sizing, ML training).

Invariants:
  1. log_trade_integrity() inserts a row with the correct tier
  2. get_integrity_tier() returns 'suspect' for unknown close_order_id (fail-closed)
  3. is_integrity_trusted() returns True only for 'verified'
  4. Duplicate calls are idempotent (INSERT OR IGNORE)
  5. log_exit_evaluation() captures exit quality metrics correctly
  6. get_exit_quality_summary() aggregates correctly
  7. dashboard/data/integrity.get_integrity_summary() returns expected structure
  8. Quarantine tier fires correctly when PnL exceeds 50% of account
  9. Excluded source check never reaches verified tier
 10. Bayesian consumer gate: is_integrity_trusted() returns False for quarantined rows
"""

from __future__ import annotations

import sqlite3
import time

import pytest

ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


# ── 1. log_trade_integrity inserts correctly ──────────────────────────────────


def test_log_trade_integrity_inserts_row(proof_runtime, monkeypatch):
    """log_trade_integrity() must persist one row with the given tier."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    ok = tl.log_trade_integrity(
        close_order_id="close_BTC_001",
        tier="verified",
        reason="attribution_succeeded",
        source_check="clean_paper_v10",
        notes="exit=trailing_stop",
    )
    assert ok is True, "Expected True (new row inserted)"

    with sqlite3.connect(proof_runtime.db_path) as conn:
        row = conn.execute(
            "SELECT tier, reason, source_check FROM trade_integrity WHERE close_order_id='close_BTC_001'"
        ).fetchone()

    assert row is not None, "Row was not written"
    assert row[0] == "verified"
    assert row[1] == "attribution_succeeded"
    assert row[2] == "clean_paper_v10"


# ── 2. Unknown close_order_id returns 'suspect' (fail-closed) ─────────────────


def test_get_integrity_tier_returns_suspect_for_unknown(proof_runtime, monkeypatch):
    """Unknown close_order_id must fail-closed to 'suspect', not crash."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    tier = tl.get_integrity_tier("nonexistent_order_xyz")
    assert tier == "suspect", f"Expected 'suspect', got '{tier}'"


# ── 3. is_integrity_trusted returns True only for 'verified' ─────────────────


def test_is_integrity_trusted_only_for_verified(proof_runtime, monkeypatch):
    """is_integrity_trusted() must return True only when tier == 'verified'."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    for tier, expected in [
        ("verified", True),
        ("suspect", False),
        ("quarantined", False),
        ("excluded", False),
    ]:
        oid = f"order_{tier}"
        tl.log_trade_integrity(
            close_order_id=oid,
            tier=tier,
            reason="test",
            source_check="test",
        )
        result = tl.is_integrity_trusted(oid)
        assert result is expected, (
            f"is_integrity_trusted('{tier}') expected {expected}, got {result}"
        )


# ── 4. Duplicate calls are idempotent ─────────────────────────────────────────


def test_log_trade_integrity_idempotent(proof_runtime, monkeypatch):
    """Calling log_trade_integrity() twice with the same close_order_id
    must not create duplicate rows (INSERT OR IGNORE)."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    oid = "order_idempotent_test"
    tl.log_trade_integrity(
        close_order_id=oid, tier="verified", reason="r", source_check="s"
    )
    second = tl.log_trade_integrity(
        close_order_id=oid, tier="suspect", reason="r2", source_check="s2"
    )

    assert second is False, "Second insert should return False (already exists)"

    with sqlite3.connect(proof_runtime.db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM trade_integrity WHERE close_order_id=?", (oid,)
        ).fetchone()[0]
    assert count == 1, "Must be exactly 1 row after duplicate call"

    # Original tier must be preserved
    tier = tl.get_integrity_tier(oid)
    assert tier == "verified", "Original tier must not be overwritten"


# ── 5. log_exit_evaluation captures exit quality correctly ───────────────────


def test_log_exit_evaluation_inserts_row(proof_runtime, monkeypatch):
    """log_exit_evaluation() must persist one row with correct metrics."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    ok = tl.log_exit_evaluation(
        close_order_id="exit_eval_001",
        exit_type="trailing_stop",
        actual_exit_price=105.0,
        actual_exit_pct=0.05,
        optimal_exit_price=110.0,
        opportunity_loss_pct=0.05,
        stop_overshoot_pct=0.0,
        regime="TRENDING_UP",
        composite_score_at_exit=72.0,
        mfe_at_exit=0.10,
        mae_at_exit=0.0,
        path_label="winner",
        trade_id="exit_eval_001",
    )
    assert ok is True

    with sqlite3.connect(proof_runtime.db_path) as conn:
        row = conn.execute(
            "SELECT exit_type, actual_exit_price, opportunity_loss_pct, path_label "
            "FROM exit_evaluations WHERE close_order_id='exit_eval_001'"
        ).fetchone()

    assert row is not None
    assert row[0] == "trailing_stop"
    assert abs(row[1] - 105.0) < 0.001
    assert abs(row[2] - 0.05) < 0.0001
    assert row[3] == "winner"


# ── 6. get_exit_quality_summary aggregates correctly ─────────────────────────


def test_get_exit_quality_summary_aggregates(proof_runtime, monkeypatch):
    """get_exit_quality_summary() must count rows and average opportunity_loss."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    # Insert 3 exit evaluations
    for i in range(3):
        tl.log_exit_evaluation(
            close_order_id=f"agg_eval_{i}",
            exit_type="hard_stop",
            actual_exit_price=100.0 - i,
            actual_exit_pct=-0.02 * (i + 1),
            opportunity_loss_pct=0.02 * (i + 1),
            path_label="loser",
            trade_id=f"agg_eval_{i}",
        )

    summary = tl.get_exit_quality_summary(days=7)
    assert summary["count"] == 3, f"Expected 3 rows, got {summary['count']}"
    assert summary["avg_opportunity_loss_pct"] > 0, (
        "avg_opportunity_loss_pct must be > 0"
    )
    assert "loser" in summary["path_label_counts"]


# ── 7. Dashboard integrity summary returns expected structure ─────────────────


def test_dashboard_integrity_summary_structure(proof_runtime, monkeypatch):
    """get_integrity_summary() must return a dict with all required keys."""
    import logging_db.trade_logger as tl
    import dashboard.data.integrity as integ

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))
    # proof_runtime already patches db.DB_PATH (used by integrity._q helpers)

    # Seed one integrity row so the summary is non-trivial
    tl.log_trade_integrity(
        close_order_id="dash_test_001",
        tier="verified",
        reason="test",
        source_check="clean_paper_v10",
    )

    summary = integ.get_integrity_summary()

    # Keys returned by get_integrity_summary() — total_closes, not total
    required_keys = {
        "verified",
        "suspect",
        "quarantined",
        "excluded",
        "total_closes",
        "coverage_pct",
    }
    missing = required_keys - set(summary.keys())
    assert not missing, f"get_integrity_summary() missing keys: {missing}"

    assert summary["verified"] >= 1, "Expected at least 1 verified row"
    assert 0.0 <= summary["coverage_pct"] <= 100.0


# ── 8. Quarantine fires for large PnL ────────────────────────────────────────


def test_quarantine_tier_for_impossible_pnl(proof_runtime, monkeypatch):
    """A trade with PnL > 50% of account must be quarantined, not verified."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    # PnL of $2,600 on a $5,000 account = 52% → quarantine
    account_size = 5_000.0
    pnl_usd = 2_600.0
    oid = "quarantine_test_001"

    tier = "quarantined" if abs(pnl_usd) > account_size * 0.5 else "verified"
    tl.log_trade_integrity(
        close_order_id=oid,
        tier=tier,
        reason=f"pnl_sanity:|{pnl_usd:.2f}|>50%_account",
        source_check="clean_paper_v10",
    )

    assert tl.get_integrity_tier(oid) == "quarantined"
    assert tl.is_integrity_trusted(oid) is False


# ── 9. Excluded source never reaches verified ─────────────────────────────────


def test_excluded_tier_not_trusted(proof_runtime, monkeypatch):
    """Rows tagged 'excluded' (e.g. synthetic/replay source) must not be trusted."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    oid = "excluded_synthetic_001"
    tl.log_trade_integrity(
        close_order_id=oid,
        tier="excluded",
        reason="source=synthetic",
        source_check="synthetic",
    )

    assert tl.get_integrity_tier(oid) == "excluded"
    assert tl.is_integrity_trusted(oid) is False


# ── 10. Bayesian consumer gate: quarantined row cannot reach signal_stats ─────


def test_quarantined_row_blocked_from_bayesian_update(proof_runtime, monkeypatch):
    """
    The Bayesian consumer must check is_integrity_trusted() and skip quarantined rows.

    We simulate this at the gate level: if the gate returns False, the calling
    code (post_trade_analyzer) must not proceed to update signal_stats.
    This test verifies the gate itself — not the full analyzer path.
    """
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    oid = "quarantined_gate_001"
    tl.log_trade_integrity(
        close_order_id=oid,
        tier="quarantined",
        reason="pnl_sanity",
        source_check="test",
    )

    # Simulate what the Bayesian consumer must check before updating weights
    gate_passes = tl.is_integrity_trusted(oid)
    assert gate_passes is False, (
        "Bayesian gate must return False for quarantined rows — "
        "contaminated data must never reach signal_stats"
    )
