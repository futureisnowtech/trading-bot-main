"""
tests/proof/test_candidate_journal.py

Proof suite for the candidate journaling system (v13.6).

Verifies:
  1. scan_candidates and candidate_outcomes tables exist after init_db()
  2. log_scan_candidate() persists a row with correct fields
  3. get_unlabeled_candidates() returns rows past the age threshold
  4. log_candidate_outcome() writes outcome + marks candidate labeled=1
  5. get_candidate_journal_stats() returns correct counts
  6. candidate_labeler._compute_outcome() produces correct forward metrics
  7. candidate_labeler.run_labeling_pass() labels rows end-to-end with a stub
  8. nightly_audit.run_audit() returns a structured report (proof=skipped)
  9. _journal_scan_candidate() helper in v10_runner does not raise
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


# ── 1. Table existence ────────────────────────────────────────────────────────


def test_candidate_tables_exist(proof_runtime):
    """scan_candidates and candidate_outcomes must exist after init_db."""
    with sqlite3.connect(proof_runtime.db_path) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "scan_candidates" in tables, "scan_candidates table missing"
    assert "candidate_outcomes" in tables, "candidate_outcomes table missing"


# ── 2. log_scan_candidate ─────────────────────────────────────────────────────


def test_log_scan_candidate_persists_row(proof_runtime, monkeypatch):
    """log_scan_candidate() writes one row with all expected fields."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    row_id = tl.log_scan_candidate(
        scan_id="scan_test_001",
        symbol="ETHUSDT",
        exchange="binance",
        base_asset="ETH",
        direction="LONG",
        primary_setup="wae_explosion",
        scan_setups_json='["wae_explosion"]',
        price=3200.0,
        volume_24h_usd=4_500_000_000.0,
        spread_pct=0.02,
        bid_depth_usd=12000.0,
        ask_depth_usd=11000.0,
        atr_15m=32.0,
        stop_pct=3.0,
        target_pct=6.0,
        scanner_expected_profit=0.9,
        regime="TRENDING_UP",
        technical_score=74.0,
        ml_score=58.0,
        composite_score=68.0,
        entry_threshold=58.0,
        should_enter_signal=1,
        econ_approved=0,
        econ_tier="VETO",
        econ_reject_reason="ev_below_floor",
        edge_score=0.28,
        size_usd=0.0,
        leverage=3,
        entry_block_reason="economics: ev_below_floor",
        decision="econ_veto",
        paper=True,
        source="clean_paper_v10",
    )

    assert row_id > 0, f"expected positive row_id, got {row_id}"

    with sqlite3.connect(proof_runtime.db_path) as conn:
        row = conn.execute(
            "SELECT symbol, direction, decision, composite_score, econ_reject_reason, labeled "
            "FROM scan_candidates WHERE id=?",
            (row_id,),
        ).fetchone()

    assert row is not None, "row not found after insert"
    assert row[0] == "ETHUSDT"
    assert row[1] == "LONG"
    assert row[2] == "econ_veto"
    assert abs(row[3] - 68.0) < 0.01
    assert row[4] == "ev_below_floor"
    assert row[5] == 0, "labeled should start at 0"


# ── 3. get_unlabeled_candidates ───────────────────────────────────────────────


def test_get_unlabeled_candidates_respects_age(proof_runtime, monkeypatch):
    """Rows newer than min_age_hours must NOT appear; older ones must."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    # Insert a row with a timestamp 5 hours in the past
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    with sqlite3.connect(proof_runtime.db_path) as conn:
        conn.execute(
            "INSERT INTO scan_candidates "
            "(scan_id, ts, symbol, direction, decision, labeled) "
            "VALUES (?,?,?,?,?,?)",
            ("old_scan", old_ts, "BTCUSDT", "SHORT", "econ_veto", 0),
        )
        # Fresh row (now — should NOT be returned)
        conn.execute(
            "INSERT INTO scan_candidates "
            "(scan_id, ts, symbol, direction, decision, labeled) "
            "VALUES (?,?,?,?,?,?)",
            (
                "new_scan",
                datetime.now(timezone.utc).isoformat(),
                "SOLUSDT",
                "LONG",
                "entered",
                0,
            ),
        )

    rows = tl.get_unlabeled_candidates(min_age_hours=4.0, limit=50)
    symbols = [r["symbol"] for r in rows]

    assert "BTCUSDT" in symbols, "5h-old row should be returned"
    assert "SOLUSDT" not in symbols, "fresh row should not be returned (< 4h)"


# ── 4. log_candidate_outcome ──────────────────────────────────────────────────


def test_log_candidate_outcome_marks_labeled(proof_runtime, monkeypatch):
    """log_candidate_outcome() inserts outcome row and sets labeled=1."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    # Insert a bare candidate row
    with sqlite3.connect(proof_runtime.db_path) as conn:
        conn.execute(
            "INSERT INTO scan_candidates (scan_id, ts, symbol, direction, decision, labeled) "
            "VALUES (?,?,?,?,?,?)",
            (
                "sc_001",
                datetime.now(timezone.utc).isoformat(),
                "BTCUSDT",
                "LONG",
                "below_threshold",
                0,
            ),
        )
        cand_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    tl.log_candidate_outcome(
        candidate_id=cand_id,
        label_status="complete",
        entry_ref_price=65000.0,
        price_1h=65500.0,
        price_4h=66000.0,
        ret_1h_pct=0.77,
        ret_4h_pct=1.54,
        mfe_4h_pct=2.0,
        mae_4h_pct=-0.5,
        hit_1r=0,
        hit_2r=0,
        hit_stop=0,
        best_exit_pct=2.0,
        worst_drawdown_pct=-0.5,
    )

    with sqlite3.connect(proof_runtime.db_path) as conn:
        labeled = conn.execute(
            "SELECT labeled FROM scan_candidates WHERE id=?", (cand_id,)
        ).fetchone()[0]
        outcome = conn.execute(
            "SELECT label_status, ret_4h_pct FROM candidate_outcomes WHERE candidate_id=?",
            (cand_id,),
        ).fetchone()

    assert labeled == 1, "labeled flag should be 1 after outcome written"
    assert outcome is not None, "candidate_outcomes row should exist"
    assert outcome[0] == "complete"
    assert abs(outcome[1] - 1.54) < 0.01


# ── 5. get_candidate_journal_stats ───────────────────────────────────────────


def test_get_candidate_journal_stats(proof_runtime, monkeypatch):
    """get_candidate_journal_stats() returns correct totals."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    now = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()

    with sqlite3.connect(proof_runtime.db_path) as conn:
        # 2 unlabeled old rows, 1 labeled row, 1 fresh row
        conn.execute(
            "INSERT INTO scan_candidates (scan_id, ts, symbol, direction, decision, labeled) "
            "VALUES (?,?,?,?,?,?)",
            ("s1", old, "BTCUSDT", "LONG", "econ_veto", 0),
        )
        conn.execute(
            "INSERT INTO scan_candidates (scan_id, ts, symbol, direction, decision, labeled) "
            "VALUES (?,?,?,?,?,?)",
            ("s2", old, "ETHUSDT", "SHORT", "below_threshold", 0),
        )
        conn.execute(
            "INSERT INTO scan_candidates (scan_id, ts, symbol, direction, decision, labeled) "
            "VALUES (?,?,?,?,?,?)",
            ("s3", now, "SOLUSDT", "LONG", "entered", 1),
        )
        conn.execute(
            "INSERT INTO scan_candidates (scan_id, ts, symbol, direction, decision, labeled) "
            "VALUES (?,?,?,?,?,?)",
            ("s4", now, "BNBUSDT", "LONG", "cooldown_block", 0),
        )

    stats = tl.get_candidate_journal_stats(days=7)

    assert stats["total_candidates"] == 4, (
        f"expected 4 total, got {stats['total_candidates']}"
    )
    assert stats["labeled"] == 1, f"expected 1 labeled, got {stats['labeled']}"
    assert stats["unlabeled_backlog"] == 2, (
        f"expected backlog=2, got {stats['unlabeled_backlog']}"
    )
    assert "econ_veto" in stats["decision_counts"]
    assert "entered" in stats["decision_counts"]


# ── 6. _compute_outcome ───────────────────────────────────────────────────────


def test_compute_outcome_long_hit_1r():
    """_compute_outcome: LONG that moves 4% should hit 1R (stop_pct=3%)."""
    import pandas as pd
    from learning.candidate_labeler import _compute_outcome

    # Simulate: 200 bars, last 5 bars show entry then 4% up move
    n = 200
    base = 65000.0
    closes = [base] * (n - 5) + [
        base,
        base * 1.01,
        base * 1.02,
        base * 1.03,
        base * 1.04,
    ]
    highs = [c * 1.001 for c in closes]
    lows = [c * 0.999 for c in closes]
    df = pd.DataFrame({"close": closes, "high": highs, "low": lows})

    result = _compute_outcome(
        df, ref_price=base, direction="LONG", stop_pct=3.0, atr_15m=200.0
    )

    assert result["label_status"] == "complete"
    assert result["ret_4h_pct"] > 0, "LONG with up move should have positive return"
    assert result["hit_1r"] == 1, f"should hit 1R, mfe={result['mfe_4h_pct']:.2f}%"
    assert result["hit_stop"] == 0, "should not hit stop"


def test_compute_outcome_short_hit_stop():
    """_compute_outcome: SHORT that reverses 4% up should hit the stop."""
    import pandas as pd
    from learning.candidate_labeler import _compute_outcome

    n = 200
    base = 65000.0
    closes = [base] * (n - 5) + [
        base,
        base * 1.01,
        base * 1.02,
        base * 1.03,
        base * 1.04,
    ]
    highs = [c * 1.001 for c in closes]
    lows = [c * 0.999 for c in closes]
    df = pd.DataFrame({"close": closes, "high": highs, "low": lows})

    result = _compute_outcome(
        df, ref_price=base, direction="SHORT", stop_pct=3.0, atr_15m=200.0
    )

    assert result["label_status"] == "complete"
    assert result["ret_4h_pct"] < 0, "SHORT with up move should have negative return"
    assert result["hit_stop"] == 1, f"should hit stop, mae={result['mae_4h_pct']:.2f}%"


# ── 7. run_labeling_pass end-to-end ───────────────────────────────────────────


def test_run_labeling_pass_labels_old_candidates(proof_runtime, monkeypatch):
    """run_labeling_pass() labels unlabeled rows using a stub get_candles."""
    import pandas as pd
    import logging_db.trade_logger as tl
    from learning.candidate_labeler import run_labeling_pass

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    # Insert one old unlabeled row
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    with sqlite3.connect(proof_runtime.db_path) as conn:
        conn.execute(
            "INSERT INTO scan_candidates "
            "(scan_id, ts, symbol, direction, price, stop_pct, atr_15m, decision, labeled) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("lp_001", old_ts, "BTCUSDT", "LONG", 65000.0, 3.0, 200.0, "econ_veto", 0),
        )
        cand_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Stub get_candles: returns a 200-bar DataFrame with a 3% up move in the last 5 bars
    base = 65000.0
    n = 200
    closes = [base] * (n - 5) + [
        base,
        base * 1.01,
        base * 1.02,
        base * 1.03,
        base * 1.04,
    ]
    highs = [c * 1.001 for c in closes]
    lows = [c * 0.999 for c in closes]
    stub_df = pd.DataFrame({"close": closes, "high": highs, "low": lows})

    def stub_get_candles(symbol, interval, limit):
        return stub_df

    result = run_labeling_pass(get_candles=stub_get_candles)

    assert result["labeled"] >= 1, f"expected >=1 labeled, got {result}"

    with sqlite3.connect(proof_runtime.db_path) as conn:
        labeled = conn.execute(
            "SELECT labeled FROM scan_candidates WHERE id=?", (cand_id,)
        ).fetchone()[0]
        outcome_cnt = conn.execute(
            "SELECT COUNT(*) FROM candidate_outcomes WHERE candidate_id=?", (cand_id,)
        ).fetchone()[0]

    assert labeled == 1, "candidate should be marked labeled=1 after labeling pass"
    assert outcome_cnt == 1, "candidate_outcomes should have one row"


# ── 8. nightly_audit.run_audit ────────────────────────────────────────────────


def test_nightly_audit_returns_structured_report(proof_runtime, monkeypatch):
    """run_audit(run_proof=False) returns a dict with all expected check keys."""
    import logging_db.trade_logger as tl
    import config

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))
    monkeypatch.setattr(config, "DB_PATH", str(proof_runtime.db_path), raising=False)

    from monitoring.nightly_audit import run_audit

    report = run_audit(run_proof=False)

    assert isinstance(report, dict), "run_audit must return a dict"
    assert "overall" in report
    assert "checks" in report
    checks = report["checks"]
    assert "proof_suite" in checks
    assert "candidate_journaling" in checks
    assert "repo_drift" in checks
    assert "learning_health" in checks

    # All checks must have a 'status' field
    for name, check in checks.items():
        assert "status" in check, f"check '{name}' missing 'status' field"

    assert report["overall"] in ("pass", "warn", "fail", "skipped")


# ── 9. _journal_scan_candidate does not raise ─────────────────────────────────


def test_journal_scan_candidate_is_resilient(proof_runtime, monkeypatch):
    """_journal_scan_candidate must silently handle DB errors — never raise."""
    import logging_db.trade_logger as tl
    import scheduler.v10_runner as runner

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))
    monkeypatch.setattr(runner, "_paper", True, raising=False)

    from tests.proof.support import build_candidate

    cand = build_candidate(symbol="BTCUSDT", direction="LONG")

    # Should not raise regardless of what we pass
    runner._journal_scan_candidate(
        "testscan001",
        cand,
        "econ_veto",
        regime="TRENDING_UP",
        technical_score=72.0,
        ml_score=55.0,
        composite_score=66.0,
        econ_tier="VETO",
        econ_reject_reason="ev_below_floor",
    )

    # Also test with a deliberately bad scan_id (empty string)
    runner._journal_scan_candidate("", cand, "below_threshold")

    with sqlite3.connect(proof_runtime.db_path) as conn:
        cnt = conn.execute("SELECT COUNT(*) FROM scan_candidates").fetchone()[0]

    assert cnt == 2, f"expected 2 rows from resilience test, got {cnt}"
