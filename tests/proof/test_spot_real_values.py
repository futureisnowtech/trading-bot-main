"""
tests/proof/test_spot_real_values.py — Invariant: spot gate rows must carry real computed values.

Coverage:
  RV-01  log_scan_candidate accepts actual_stop_pct / actual_target_pct / net_rr / net_win_usd / econ_gate_class
  RV-02  econ_veto rows written with actual_stop_pct have non-None, non-stub stop value
  RV-03  entered rows written with actual_stop_pct have net_rr > 0
  RV-04  data_unavailable rows are exempt (no real computation ran)
  RV-05  stub-value detector correctly identifies placeholder stop values (0, 50, 100)
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    db = tmp_path / "trades.db"
    import config
    import logging_db.trade_logger as trade_logger

    monkeypatch.setattr(config, "DB_PATH", str(db))
    monkeypatch.setattr(trade_logger, "_conn", lambda: sqlite3.connect(str(db)))
    trade_logger.init_db()
    return db


def _write_candidate(
    db,
    decision,
    actual_stop_pct,
    actual_target_pct,
    net_rr,
    net_win_usd,
    econ_gate_class,
):
    from logging_db.trade_logger import log_scan_candidate

    return log_scan_candidate(
        scan_id="test-scan-001",
        symbol="SOL",
        exchange="coinbase_spot",
        base_asset="SOL",
        direction="LONG",
        primary_setup="impulse_continuation",
        scan_setups_json="[]",
        price=85.0,
        volume_24h_usd=5_000_000,
        spread_pct=0.001,
        bid_depth_usd=10_000,
        ask_depth_usd=10_000,
        atr_15m=0.30,
        stop_pct=0.004,
        target_pct=0.012,
        scanner_expected_profit=0.5,
        regime="NEUTRAL",
        technical_score=60.0,
        ml_score=50.0,
        composite_score=55.0,
        entry_threshold=48.0,
        should_enter_signal=1,
        econ_approved=0 if decision != "entered" else 1,
        econ_tier="B",
        econ_reject_reason="projected_net_win_too_small"
        if decision == "econ_veto"
        else "",
        edge_score=0.5,
        size_usd=200.0,
        leverage=1,
        entry_block_reason="",
        decision=decision,
        paper=True,
        source="test",
        actual_stop_pct=actual_stop_pct,
        actual_target_pct=actual_target_pct,
        net_rr=net_rr,
        net_win_usd=net_win_usd,
        econ_gate_class=econ_gate_class,
    )


def test_rv01_new_columns_accepted(tmp_db):
    """log_scan_candidate must accept all 5 new real-value columns."""
    row_id = _write_candidate(
        tmp_db,
        "econ_veto",
        actual_stop_pct=0.0035,
        actual_target_pct=0.0042,
        net_rr=1.05,
        net_win_usd=0.64,
        econ_gate_class="economics",
    )
    assert row_id > 0


def test_rv02_econ_veto_stop_not_stub(tmp_db):
    """econ_veto rows with real values must not have placeholder stop (0, 50, 100)."""
    _write_candidate(
        tmp_db,
        "econ_veto",
        actual_stop_pct=0.0035,
        actual_target_pct=0.0042,
        net_rr=1.05,
        net_win_usd=0.64,
        econ_gate_class="economics",
    )
    conn = sqlite3.connect(str(tmp_db))
    row = conn.execute(
        "SELECT actual_stop_pct FROM scan_candidates WHERE decision='econ_veto' ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] not in (None, 0.0, 50.0, 100.0)
    assert 0.001 < row[0] < 0.20


def test_rv03_entered_net_rr_positive(tmp_db):
    """entered rows with real values must have net_rr > 0."""
    _write_candidate(
        tmp_db,
        "entered",
        actual_stop_pct=0.0035,
        actual_target_pct=0.0042,
        net_rr=1.15,
        net_win_usd=0.64,
        econ_gate_class="approved",
    )
    conn = sqlite3.connect(str(tmp_db))
    row = conn.execute(
        "SELECT net_rr FROM scan_candidates WHERE decision='entered' ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] > 0


def test_rv04_data_unavailable_exempt(tmp_db):
    """data_unavailable rows are allowed to have NULL actual_stop_pct."""
    row_id = _write_candidate(
        tmp_db,
        "data_unavailable",
        actual_stop_pct=None,
        actual_target_pct=None,
        net_rr=None,
        net_win_usd=None,
        econ_gate_class="",
    )
    assert row_id > 0
    conn = sqlite3.connect(str(tmp_db))
    row = conn.execute(
        "SELECT actual_stop_pct FROM scan_candidates WHERE decision='data_unavailable' ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row[0] is None


def test_rv05_stub_detector_identifies_placeholder(tmp_db):
    """Stub detector query must flag rows with stop_pct in (0, 50, 100)."""
    conn = sqlite3.connect(str(tmp_db))
    # Write a row with scanner-level placeholder stop_pct=50 (the bug we're fixing)
    conn.execute(
        """INSERT INTO scan_candidates
           (scan_id, ts, symbol, exchange, base_asset, direction, primary_setup,
            scan_setups_json, price, volume_24h_usd, spread_pct, bid_depth_usd,
            ask_depth_usd, atr_15m, stop_pct, target_pct, scanner_expected_profit,
            regime, technical_score, ml_score, composite_score, entry_threshold,
            should_enter_signal, econ_approved, econ_tier, econ_reject_reason,
            edge_score, size_usd, leverage, entry_block_reason, decision, paper, source, labeled)
           VALUES ('stub-test','2099-01-01T00:00:00','SOL','coinbase_spot','SOL','LONG',
            '','[]',85,5000000,0.001,10000,10000,0.3,50.0,100.0,0,'NEUTRAL',
            60,50,55,48,1,0,'B','',0.5,200,1,'','econ_veto',1,'test',0)"""
    )
    conn.commit()
    n = conn.execute(
        """SELECT COUNT(*) FROM scan_candidates
           WHERE decision != 'data_unavailable'
           AND (stop_pct IN (0,50,100) OR target_pct IN (0,50,100))"""
    ).fetchone()[0]
    conn.close()
    assert n == 1
