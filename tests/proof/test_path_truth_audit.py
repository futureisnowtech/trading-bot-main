"""
tests/proof/test_path_truth_audit.py — Proof tests for path_truth_audit.py (v16).

Coverage:
  1. r_multiple_reach returns required keys and correct hit percentages
  2. timing_to_threshold returns required keys; n_reached matches non-NULL rows
  3. path_by_group returns list with required keys per row
  4. exit_quality_context returns required keys; path_labels is a dict
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ── helpers ───────────────────────────────────────────────────────────────────


def _insert_entered(
    conn,
    scan_id,
    ts,
    symbol,
    regime,
    direction,
    stop_pct,
    hit_1r,
    hit_stop,
    hit_2r,
    mfe_4h_pct,
    mae_4h_pct,
    time_to_05r_min=None,
    time_to_1r_min=None,
    time_to_2r_min=None,
    peak_r_4h=None,
    path_timing_evaluated=1,
):
    conn.execute(
        """
        INSERT INTO scan_candidates
        (scan_id, ts, symbol, decision, source, exchange, regime, direction,
         stop_pct, labeled)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            scan_id,
            ts,
            symbol,
            "entered",
            "clean_paper_v10",
            "binance",
            regime,
            direction,
            stop_pct,
            1,
        ),
    )
    cand_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """
        INSERT INTO candidate_outcomes
        (candidate_id, label_status, hit_1r, hit_stop, hit_2r,
         mfe_4h_pct, mae_4h_pct, peak_r_4h, path_timing_evaluated,
         time_to_05r_min, time_to_1r_min, time_to_2r_min,
         entry_ref_price, price_1h, price_4h, ret_1h_pct, ret_4h_pct,
         best_exit_pct, worst_drawdown_pct)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            cand_id,
            "complete",
            hit_1r,
            hit_stop,
            hit_2r,
            mfe_4h_pct,
            mae_4h_pct,
            peak_r_4h,
            path_timing_evaluated,
            time_to_05r_min,
            time_to_1r_min,
            time_to_2r_min,
            100.0,
            101.0,
            102.0,
            1.0,
            2.0,
            mfe_4h_pct,
            mae_4h_pct,
        ),
    )
    return cand_id


# ── tests ─────────────────────────────────────────────────────────────────────


def test_r_multiple_reach_keys_and_percentages(proof_runtime, monkeypatch):
    """r_multiple_reach returns required keys; hit percentages are correct."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    with sqlite3.connect(proof_runtime.db_path) as conn:
        # 4 candidates: 3 hit 1R, 2 hit 2R, 1 hit stop
        _insert_entered(
            conn,
            "rm1",
            "2026-04-14T10:00:00",
            "BTCUSDT",
            "TRENDING_UP",
            "LONG",
            3.0,
            hit_1r=1,
            hit_stop=0,
            hit_2r=1,
            mfe_4h_pct=7.0,
            mae_4h_pct=-1.0,
            peak_r_4h=7.0 / 3.0,
        )
        _insert_entered(
            conn,
            "rm2",
            "2026-04-14T11:00:00",
            "ETHUSDT",
            "TRENDING_UP",
            "LONG",
            3.0,
            hit_1r=1,
            hit_stop=0,
            hit_2r=1,
            mfe_4h_pct=7.0,
            mae_4h_pct=-1.0,
            peak_r_4h=7.0 / 3.0,
        )
        _insert_entered(
            conn,
            "rm3",
            "2026-04-14T12:00:00",
            "SOLUSDT",
            "RANGING",
            "LONG",
            3.0,
            hit_1r=1,
            hit_stop=0,
            hit_2r=0,
            mfe_4h_pct=4.0,
            mae_4h_pct=-1.0,
            peak_r_4h=4.0 / 3.0,
        )
        _insert_entered(
            conn,
            "rm4",
            "2026-04-14T13:00:00",
            "XRPUSDT",
            "RANGING",
            "SHORT",
            2.0,
            hit_1r=0,
            hit_stop=1,
            hit_2r=0,
            mfe_4h_pct=0.5,
            mae_4h_pct=-2.5,
            peak_r_4h=0.5 / 2.0,
        )

    from scripts.path_truth_audit import r_multiple_reach

    with sqlite3.connect(proof_runtime.db_path) as conn:
        result = r_multiple_reach(conn, days=30)

    required = {
        "n",
        "hit_05r_pct",
        "hit_1r_pct",
        "hit_2r_pct",
        "hit_stop_pct",
        "avg_peak_r_4h",
        "avg_mfe_pct",
        "avg_mae_pct",
    }
    missing = required - set(result.keys())
    assert not missing, f"Missing keys: {missing}"

    assert result["n"] == 4
    assert abs(result["hit_1r_pct"] - 75.0) < 0.5, (
        f"expected 75%, got {result['hit_1r_pct']}"
    )
    assert abs(result["hit_2r_pct"] - 50.0) < 0.5, (
        f"expected 50%, got {result['hit_2r_pct']}"
    )
    assert abs(result["hit_stop_pct"] - 25.0) < 0.5, (
        f"expected 25%, got {result['hit_stop_pct']}"
    )


def test_timing_to_threshold_keys_and_n_reached(proof_runtime, monkeypatch):
    """timing_to_threshold n_reached matches actual non-NULL timing rows."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    with sqlite3.connect(proof_runtime.db_path) as conn:
        # 3 candidates: 2 have time_to_1r_min set, 1 does not
        _insert_entered(
            conn,
            "tt1",
            "2026-04-14T10:00:00",
            "BTCUSDT",
            "TRENDING_UP",
            "LONG",
            3.0,
            hit_1r=1,
            hit_stop=0,
            hit_2r=0,
            mfe_4h_pct=4.0,
            mae_4h_pct=-0.5,
            time_to_05r_min=15,
            time_to_1r_min=45,
            time_to_2r_min=None,
        )
        _insert_entered(
            conn,
            "tt2",
            "2026-04-14T11:00:00",
            "ETHUSDT",
            "TRENDING_UP",
            "LONG",
            3.0,
            hit_1r=1,
            hit_stop=0,
            hit_2r=0,
            mfe_4h_pct=4.0,
            mae_4h_pct=-0.5,
            time_to_05r_min=30,
            time_to_1r_min=60,
            time_to_2r_min=None,
        )
        _insert_entered(
            conn,
            "tt3",
            "2026-04-14T12:00:00",
            "SOLUSDT",
            "RANGING",
            "SHORT",
            2.0,
            hit_1r=0,
            hit_stop=1,
            hit_2r=0,
            mfe_4h_pct=0.5,
            mae_4h_pct=-2.5,
            time_to_05r_min=None,
            time_to_1r_min=None,
            time_to_2r_min=None,
        )

    from scripts.path_truth_audit import timing_to_threshold

    with sqlite3.connect(proof_runtime.db_path) as conn:
        result = timing_to_threshold(conn, days=30)

    required = {
        "total_entered_labeled",
        "timing_evaluated_n",
        "time_to_05r",
        "time_to_1r",
        "time_to_2r",
        "note",
    }
    missing = required - set(result.keys())
    assert not missing, f"Missing keys: {missing}"

    assert result["total_entered_labeled"] == 3
    assert result["timing_evaluated_n"] == 3

    t1r = result["time_to_1r"]
    assert t1r["n_reached"] == 2, f"expected 2 with time_to_1r, got {t1r['n_reached']}"
    assert t1r["median_min"] == 52.5, f"expected median 52.5m, got {t1r['median_min']}"
    assert abs(t1r["reach_pct"] - 66.7) < 0.2, (
        f"expected ~66.7%, got {t1r['reach_pct']}"
    )

    t2r = result["time_to_2r"]
    assert t2r["n_reached"] == 0, "no candidates reached 2R in test data"


def test_timing_to_threshold_uses_timing_evaluated_denominator(proof_runtime, monkeypatch):
    """reach_pct must use timing-evaluated rows, not all labeled entered rows."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    with sqlite3.connect(proof_runtime.db_path) as conn:
        _insert_entered(
            conn,
            "te1",
            "2026-04-14T10:00:00",
            "BTCUSDT",
            "TRENDING_UP",
            "LONG",
            3.0,
            hit_1r=1,
            hit_stop=0,
            hit_2r=0,
            mfe_4h_pct=4.0,
            mae_4h_pct=-0.5,
            time_to_1r_min=30,
            path_timing_evaluated=1,
        )
        _insert_entered(
            conn,
            "te2",
            "2026-04-14T11:00:00",
            "ETHUSDT",
            "TRENDING_UP",
            "LONG",
            3.0,
            hit_1r=1,
            hit_stop=0,
            hit_2r=0,
            mfe_4h_pct=4.0,
            mae_4h_pct=-0.5,
            time_to_1r_min=None,
            path_timing_evaluated=1,
        )
        _insert_entered(
            conn,
            "te3",
            "2026-04-14T12:00:00",
            "SOLUSDT",
            "RANGING",
            "LONG",
            3.0,
            hit_1r=1,
            hit_stop=0,
            hit_2r=0,
            mfe_4h_pct=4.0,
            mae_4h_pct=-0.5,
            time_to_1r_min=None,
            path_timing_evaluated=0,
        )

    from scripts.path_truth_audit import timing_to_threshold

    with sqlite3.connect(proof_runtime.db_path) as conn:
        result = timing_to_threshold(conn, days=30)

    assert result["total_entered_labeled"] == 3
    assert result["timing_evaluated_n"] == 2
    assert abs(result["time_to_1r"]["reach_pct"] - 50.0) < 0.2


def test_path_by_group_returns_list_with_required_keys(proof_runtime, monkeypatch):
    """path_by_group returns list of dicts with required keys."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    with sqlite3.connect(proof_runtime.db_path) as conn:
        for i in range(3):
            _insert_entered(
                conn,
                f"pg_{i}",
                "2026-04-14T10:00:00",
                f"BTCUSDT_{i}",
                "TRENDING_UP",
                "LONG",
                3.0,
                hit_1r=1,
                hit_stop=0,
                hit_2r=0,
                mfe_4h_pct=4.0,
                mae_4h_pct=-0.5,
                time_to_1r_min=30 + i * 15,
            )

    from scripts.path_truth_audit import path_by_group

    with sqlite3.connect(proof_runtime.db_path) as conn:
        result = path_by_group(conn, days=30)

    assert isinstance(result, list)
    assert len(result) >= 1, "should find at least one regime/direction group"

    required = {
        "regime",
        "direction",
        "n",
        "hit_1r_pct",
        "hit_2r_pct",
        "avg_peak_r",
        "avg_mfe_pct",
    }
    for row in result:
        missing = required - set(row.keys())
        assert not missing, f"Missing keys {missing} in path_by_group row"


def test_exit_quality_context_keys_and_path_labels(proof_runtime, monkeypatch):
    """exit_quality_context returns required keys; path_labels is a dict."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    with sqlite3.connect(proof_runtime.db_path) as conn:
        for i, (opp_loss, overshoot, mfe_exit, label) in enumerate(
            [
                (0.5, 0.1, 3.0, "trail_hit"),
                (2.0, 0.0, 5.0, "take_profit"),
                (0.0, 1.5, 1.0, "hard_stop"),
            ]
        ):
            conn.execute(
                """
                INSERT INTO exit_evaluations
                (close_order_id, created_at, opportunity_loss_pct,
                 stop_overshoot_pct, mfe_at_exit, path_label)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    f"order_{i}",
                    "2026-04-15T10:00:00",
                    opp_loss,
                    overshoot,
                    mfe_exit,
                    label,
                ),
            )

    from scripts.path_truth_audit import exit_quality_context

    with sqlite3.connect(proof_runtime.db_path) as conn:
        result = exit_quality_context(conn, days=30)

    required = {
        "n",
        "avg_opportunity_loss_pct",
        "avg_stop_overshoot_pct",
        "avg_mfe_at_exit_pct",
        "high_opportunity_loss_count",
        "high_overshoot_count",
        "path_labels",
    }
    missing = required - set(result.keys())
    assert not missing, f"Missing keys: {missing}"

    assert result["n"] == 3
    assert isinstance(result["path_labels"], dict)
    assert "trail_hit" in result["path_labels"]
    assert "take_profit" in result["path_labels"]
    assert "hard_stop" in result["path_labels"]
    # high_opportunity_loss_count: opp_loss > 1.0 → only 2.0 qualifies
    assert result["high_opportunity_loss_count"] == 1
    # high_overshoot_count: overshoot > 0.5 → only 1.5 qualifies
    assert result["high_overshoot_count"] == 1
