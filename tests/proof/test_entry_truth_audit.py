"""
tests/proof/test_entry_truth_audit.py — Proof tests for entry_truth_audit.py (v16).

Coverage:
  1. funnel_summary returns required keys and correct conversion rate math
  2. scanner_ev_calibration returns required keys and cap rate is non-negative
  3. source_quality returns list of dicts with required keys
  4. symbol_class_quality differentiates entered (core) vs research_only_block
  5. integrity_snapshot reads actual trade_integrity schema and duplicate-close events
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _recent_ts(offset_hours: float = 0) -> str:
    """Fixture timestamp 5 days in the past, plus N hours.
    Keeps fixtures inside the 30-day rolling cutoff in scripts/entry_truth_audit._cutoff."""
    return (
        datetime.now(timezone.utc) - timedelta(days=5) + timedelta(hours=offset_hours)
    ).strftime("%Y-%m-%dT%H:%M:%S")


# ── helpers ───────────────────────────────────────────────────────────────────


def _insert_scan_funnel(
    conn, ts, scanned, above, econ, ro_block, sizing, exec_fail, entered
):
    """Insert a scan_funnels row using the actual schema column names."""
    econ_veto = above - econ  # inferred from threshold-passed rows
    below_threshold = scanned - above
    econ_passed_total = ro_block + sizing + exec_fail + entered
    scored_total = below_threshold + above
    conn.execute(
        """
        INSERT INTO scan_funnels
        (scan_id, ts, scanner_candidates_total, below_threshold,
         econ_veto, research_only_block, sizing_zero, execution_failed, entered,
         scored_total, econ_passed_total, final_entryable_total)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            f"sf_{ts[:10]}",
            ts,
            scanned,
            below_threshold,
            econ_veto,
            ro_block,
            sizing,
            exec_fail,
            entered,
            scored_total,
            econ_passed_total,
            sizing + exec_fail + entered,
        ),
    )


def _insert_candidate_with_outcome(
    conn,
    scan_id,
    ts,
    symbol,
    decision,
    source,
    exchange,
    setup,
    regime,
    direction,
    stop_pct,
    hit_1r,
    hit_stop,
    mfe_4h_pct,
    mae_4h_pct,
    theor_pos=None,
    eff_pos=None,
):
    conn.execute(
        """
        INSERT INTO scan_candidates
        (scan_id, ts, symbol, decision, source, exchange, primary_setup,
         regime, direction, stop_pct, labeled,
         scanner_theoretical_position_usd, scanner_effective_position_usd)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            scan_id,
            ts,
            symbol,
            decision,
            source,
            exchange,
            setup,
            regime,
            direction,
            stop_pct,
            1,
            theor_pos,
            eff_pos,
        ),
    )
    cand_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """
        INSERT INTO candidate_outcomes
        (candidate_id, label_status, hit_1r, hit_stop, mfe_4h_pct, mae_4h_pct,
         entry_ref_price, price_1h, price_4h, ret_1h_pct, ret_4h_pct,
         hit_2r, best_exit_pct, worst_drawdown_pct)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            cand_id,
            "complete",
            hit_1r,
            hit_stop,
            mfe_4h_pct,
            mae_4h_pct,
            100.0,
            101.0,
            102.0,
            1.0,
            2.0,
            0,
            mfe_4h_pct,
            mae_4h_pct,
        ),
    )
    return cand_id


# ── tests ─────────────────────────────────────────────────────────────────────


def test_funnel_summary_keys_and_conversion(proof_runtime, monkeypatch):
    """funnel_summary returns required keys; conversion_rate_pct math is correct."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    with sqlite3.connect(proof_runtime.db_path) as conn:
        # scanned=100, above=40, econ=30, ro_block=5, sizing=2, exec_fail=1, entered=22
        _insert_scan_funnel(
            conn,
            _recent_ts(24),
            100,
            40,
            30,
            5,
            2,
            1,
            22,
        )

    from scripts.entry_truth_audit import funnel_summary

    with sqlite3.connect(proof_runtime.db_path) as conn:
        result = funnel_summary(conn, days=30)

    required = {
        "cycles",
        "scanned",
        "scored_total",
        "above_threshold",
        "below_threshold",
        "econ_passed",
        "econ_veto",
        "research_only_block",
        "sizing_zero",
        "execution_failed",
        "entered",
        "conversion_rate_pct",
        "econ_veto_rate_pct",
    }
    missing = required - set(result.keys())
    assert not missing, f"Missing keys: {missing}"

    assert result["scanned"] == 100
    assert result["scored_total"] == 100
    assert result["above_threshold"] == 40
    assert result["below_threshold"] == 60
    assert result["econ_passed"] == 30
    assert result["entered"] == 22
    assert result["research_only_block"] == 5
    # conversion = entered / above_threshold = 22/40 = 55%
    assert abs(result["conversion_rate_pct"] - 55.0) < 0.5


def test_scanner_ev_calibration_keys_and_cap_rate(proof_runtime, monkeypatch):
    """scanner_ev_calibration returns required keys; cap rate is non-negative."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    with sqlite3.connect(proof_runtime.db_path) as conn:
        # Insert candidates with theoretical > 100 (capped) and < 100 (not capped)
        for i, (theor, eff) in enumerate(
            [
                (500.0, 100.0),  # capped
                (50.0, 50.0),  # not capped
                (1200.0, 100.0),  # capped
            ]
        ):
            conn.execute(
                """
                INSERT INTO scan_candidates
                (scan_id, ts, symbol, direction, decision, labeled,
                 scanner_theoretical_position_usd, scanner_effective_position_usd)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    f"ev_{i}",
                    _recent_ts(24),
                    f"BTCUSDT_{i}",
                    "LONG",
                    "entered",
                    0,
                    theor,
                    eff,
                ),
            )

    from scripts.entry_truth_audit import scanner_ev_calibration

    with sqlite3.connect(proof_runtime.db_path) as conn:
        result = scanner_ev_calibration(conn, days=30)

    required = {
        "n",
        "avg_theoretical_usd",
        "avg_effective_usd",
        "effective_cap_rate_pct",
        "note",
    }
    missing = required - set(result.keys())
    assert not missing, f"Missing keys: {missing}"

    assert result["n"] == 3
    # Avg theoretical = (500 + 50 + 1200) / 3 = 583.33
    assert result["avg_theoretical_usd"] > 100, "average theoretical should be > $100"
    # Avg effective = (100 + 50 + 100) / 3 = 83.33
    assert result["avg_effective_usd"] <= 100, (
        "average effective should not exceed $100"
    )
    assert result["effective_cap_rate_pct"] >= 0, "cap rate must be non-negative"


def test_source_quality_returns_list_with_required_keys(proof_runtime, monkeypatch):
    """source_quality returns a list of dicts with required keys."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    with sqlite3.connect(proof_runtime.db_path) as conn:
        # 2 wins on binance, 1 loss on hyperliquid
        _insert_candidate_with_outcome(
            conn,
            "sq1",
            _recent_ts(0),
            "BTCUSDT",
            "entered",
            "clean_paper_v10",
            "binance",
            "momentum",
            "TRENDING_UP",
            "LONG",
            3.0,
            hit_1r=1,
            hit_stop=0,
            mfe_4h_pct=4.0,
            mae_4h_pct=-1.0,
        )
        _insert_candidate_with_outcome(
            conn,
            "sq2",
            _recent_ts(1),
            "ETHUSDT",
            "entered",
            "clean_paper_v10",
            "binance",
            "momentum",
            "TRENDING_UP",
            "LONG",
            3.0,
            hit_1r=1,
            hit_stop=0,
            mfe_4h_pct=4.0,
            mae_4h_pct=-1.0,
        )
        _insert_candidate_with_outcome(
            conn,
            "sq3",
            _recent_ts(2),
            "SOLUSDT",
            "entered",
            "clean_paper_v10",
            "hyperliquid",
            "momentum",
            "RANGING",
            "SHORT",
            2.0,
            hit_1r=0,
            hit_stop=1,
            mfe_4h_pct=0.5,
            mae_4h_pct=-2.5,
        )

    from scripts.entry_truth_audit import source_quality

    with sqlite3.connect(proof_runtime.db_path) as conn:
        result = source_quality(conn, days=30)

    assert isinstance(result, list), "source_quality must return a list"
    assert len(result) >= 1, "should find at least one source group"

    for row in result:
        for key in ("exchange", "source", "n", "wins", "win_rate_pct", "avg_mfe_pct"):
            assert key in row, f"missing key '{key}' in source_quality row"

    binance_rows = [r for r in result if r["exchange"] == "binance"]
    assert binance_rows, "binance group should appear"
    assert binance_rows[0]["win_rate_pct"] == 100.0, "both binance trades won"


def test_symbol_class_quality_separates_tiers(proof_runtime, monkeypatch):
    """symbol_class_quality shows different metrics for core vs research_only tiers."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    with sqlite3.connect(proof_runtime.db_path) as conn:
        # 2 core entered — both win
        for i in range(2):
            _insert_candidate_with_outcome(
                conn,
                f"core_{i}",
                _recent_ts(0),
                "BTCUSDT",
                "entered",
                "clean_paper_v10",
                "binance",
                "momentum",
                "TRENDING_UP",
                "LONG",
                3.0,
                hit_1r=1,
                hit_stop=0,
                mfe_4h_pct=4.0,
                mae_4h_pct=-0.5,
            )
        # 3 research_only_block on a long-tail symbol — all lose
        for i in range(3):
            _insert_candidate_with_outcome(
                conn,
                f"ro_{i}",
                _recent_ts(1),
                "PEPEUSDT",
                "research_only_block",
                "clean_paper_v10",
                "binance",
                "momentum",
                "RANGING",
                "LONG",
                3.0,
                hit_1r=0,
                hit_stop=1,
                mfe_4h_pct=0.5,
                mae_4h_pct=-3.5,
            )

    from scripts.entry_truth_audit import symbol_class_quality

    with sqlite3.connect(proof_runtime.db_path) as conn:
        result = symbol_class_quality(conn, days=30)

    assert isinstance(result, list)
    tiers = {r["tier"]: r for r in result}

    assert "core" in tiers, f"'core' tier missing from result: {list(tiers)}"
    assert "research_only" in tiers, f"'research_only' tier missing from result: {list(tiers)}"

    core = tiers["core"]
    ro = tiers["research_only"]

    assert core["win_rate_pct"] == 100.0, "core wins should be 100%"
    assert ro["win_rate_pct"] == 0.0, "research_only wins should be 0%"
    assert core["n"] == 2
    assert ro["n"] == 3


def test_integrity_snapshot_reads_actual_schema_and_duplicate_events(
    proof_runtime, monkeypatch
):
    """integrity_snapshot must query trade_integrity.tier and system_events duplicate-close rows."""
    import logging_db.trade_logger as tl

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    with sqlite3.connect(proof_runtime.db_path) as conn:
        conn.execute(
            """
            INSERT INTO trade_integrity
            (trade_id, close_order_id, tier, reason, source_check, created_at, notes)
            VALUES (?,?,?,?,?,?,?)
            """,
            (1, "close_1", "verified", "ok", "test", _recent_ts(24), ""),
        )
        conn.execute(
            """
            INSERT INTO trade_integrity
            (trade_id, close_order_id, tier, reason, source_check, created_at, notes)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                2,
                "close_2",
                "suspect",
                "missing_lineage",
                "test",
                _recent_ts(25),
                "",
            ),
        )
        conn.execute(
            """
            INSERT INTO system_events (ts, level, source, message)
            VALUES (?,?,?,?)
            """,
            (
                _recent_ts(25.5),
                "WARN",
                "perps_engine",
                "duplicate close suppressed for BTCUSDT",
            ),
        )

    from scripts.entry_truth_audit import integrity_snapshot

    with sqlite3.connect(proof_runtime.db_path) as conn:
        result = integrity_snapshot(conn, days=30)

    assert result["total_closes"] == 2
    assert result["tiers"]["verified"] == 1
    assert result["tiers"]["suspect"] == 1
    assert result["duplicate_close_event_count"] == 1
    assert result["top_non_verified_reasons"][0]["reason"] == "missing_lineage"
