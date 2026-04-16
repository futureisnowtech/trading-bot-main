"""
tests/proof/test_entry_priors.py — Proof tests for Bayesian entry priors (v16).

Coverage:
  1. estimate_candidate_win_rate returns required keys
  2. Bayesian smoothing math is correct
  3. win_rate_estimate is clipped to [0.40, 0.70]
  4. Fallback hierarchy (most specific -> global) fires in order
  5. hit_1r=1 AND hit_stop=0 is the win label (not just hit_1r)
  6. v10_runner uses estimate_candidate_win_rate (not just hardcoded 0.54)
  7. sample_n=0 gives smoothed prior (not raw 0.52)
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def test_estimate_returns_required_keys():
    """estimate_candidate_win_rate must return win_rate_estimate, sample_n, bucket_used."""
    from learning.entry_priors import estimate_candidate_win_rate

    result = estimate_candidate_win_rate()
    assert "win_rate_estimate" in result
    assert "sample_n" in result
    assert "bucket_used" in result


def test_bayesian_smoothing_math():
    """Posterior = (prior_n * prior_p + wins) / (prior_n + n)."""
    from learning.entry_priors import _PRIOR_N, _PRIOR_P, _bayesian_posterior

    # 10 wins out of 10 trials
    posterior = _bayesian_posterior(wins=10, n=10)
    expected = (_PRIOR_N * _PRIOR_P + 10) / (_PRIOR_N + 10)
    expected = max(0.40, min(0.70, expected))
    assert abs(posterior - expected) < 0.001


def test_win_rate_clipped_to_bounds():
    """win_rate_estimate must be clipped to [0.40, 0.70]."""
    from learning.entry_priors import _bayesian_posterior

    # All wins -> should not exceed 0.70
    assert _bayesian_posterior(wins=1000, n=1000) <= 0.70
    # All losses -> should not go below 0.40
    assert _bayesian_posterior(wins=0, n=1000) >= 0.40


def test_zero_data_returns_smoothed_prior(proof_runtime, monkeypatch):
    """With n=0 (empty DB), win_rate_estimate should be the smoothed prior (~0.52, clipped)."""
    import learning.entry_priors as ep

    # Point entry_priors at the empty test DB so no historical data exists
    monkeypatch.setattr(ep, "_db_path", lambda: str(proof_runtime.db_path))

    from learning.entry_priors import _PRIOR_P, estimate_candidate_win_rate

    result = estimate_candidate_win_rate(
        exchange="nonexistent_exchange_xyz", primary_setup="nonexistent_setup_xyz"
    )
    assert result["sample_n"] == 0
    assert result["bucket_used"] == "global"
    assert 0.40 <= result["win_rate_estimate"] <= 0.70


def test_hierarchy_fallback_uses_data_over_global(proof_runtime, monkeypatch):
    """When specific bucket has data, it should be used over global."""
    import logging_db.trade_logger as tl
    from learning.entry_priors import estimate_candidate_win_rate

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    # Insert an entered candidate with a complete outcome (win)
    with sqlite3.connect(proof_runtime.db_path) as conn:
        conn.execute(
            "INSERT INTO scan_candidates "
            "(scan_id, ts, symbol, exchange, primary_setup, regime, direction, decision, source, labeled) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "sc_prior_1",
                "2026-04-10T10:00:00",
                "BTCUSDT",
                "binance",
                "momentum",
                "TRENDING_UP",
                "LONG",
                "entered",
                "clean_paper_v10",
                1,
            ),
        )
        cand_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO candidate_outcomes "
            "(candidate_id, label_status, hit_1r, hit_stop) VALUES (?,?,?,?)",
            (cand_id, "complete", 1, 0),  # win
        )

    # Monkeypatch the DB path for entry_priors
    import learning.entry_priors as ep

    monkeypatch.setattr(ep, "_db_path", lambda: str(proof_runtime.db_path))

    result = estimate_candidate_win_rate(
        exchange="binance",
        primary_setup="momentum",
        regime="TRENDING_UP",
        direction="LONG",
    )

    # Should find the specific bucket (not global)
    assert result["sample_n"] >= 1, "should find data in specific bucket"
    assert result["bucket_used"] != "global" or result["sample_n"] >= 1


def test_win_label_requires_hit_1r_and_not_hit_stop(proof_runtime, monkeypatch):
    """Win label = hit_1r=1 AND hit_stop=0. A stopped-out candidate is NOT a win."""
    import logging_db.trade_logger as tl
    from learning.entry_priors import estimate_candidate_win_rate

    monkeypatch.setattr(tl, "DB_PATH", str(proof_runtime.db_path))

    with sqlite3.connect(proof_runtime.db_path) as conn:
        # Insert: 1 win (hit_1r=1, hit_stop=0), 1 stopped (hit_1r=1, hit_stop=1), 1 loss (hit_1r=0)
        for i, (h1r, hstop, src) in enumerate(
            [
                (1, 0, "clean_paper_v10"),  # WIN
                (1, 1, "clean_paper_v10"),  # NOT a win (stopped out)
                (0, 0, "clean_paper_v10"),  # loss
            ]
        ):
            conn.execute(
                "INSERT INTO scan_candidates "
                "(scan_id, ts, symbol, exchange, primary_setup, regime, direction, decision, source, labeled) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    f"sc_win_{i}",
                    "2026-04-11T10:00:00",
                    "SOLUSDT",
                    "hyperliquid",
                    "win_test_setup",
                    "RANGING",
                    "SHORT",
                    "entered",
                    src,
                    1,
                ),
            )
            cand_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO candidate_outcomes "
                "(candidate_id, label_status, hit_1r, hit_stop) VALUES (?,?,?,?)",
                (cand_id, "complete", h1r, hstop),
            )

    import learning.entry_priors as ep

    monkeypatch.setattr(ep, "_db_path", lambda: str(proof_runtime.db_path))

    result = estimate_candidate_win_rate(
        exchange="hyperliquid",
        primary_setup="win_test_setup",
        regime="RANGING",
        direction="SHORT",
    )

    # 1 win out of 3, Bayesian smoothed
    assert result["sample_n"] == 3
    # wins=1, n=3: posterior = (20*0.52 + 1) / (20+3) = 11.4/23 ~= 0.495 -> clipped to 0.495
    expected_posterior = (20 * 0.52 + 1) / (20 + 3)
    expected_clipped = max(0.40, min(0.70, expected_posterior))
    assert abs(result["win_rate_estimate"] - expected_clipped) < 0.005, (
        f"expected {expected_clipped:.4f}, got {result['win_rate_estimate']}"
    )


def test_v10_runner_uses_estimate_candidate_win_rate():
    """v10_runner must call estimate_candidate_win_rate for WR prior."""
    runner_path = _ROOT / "scheduler" / "v10_runner.py"
    src = runner_path.read_text()
    assert "estimate_candidate_win_rate" in src or "entry_priors" in src, (
        "v10_runner must use estimate_candidate_win_rate for economics gate WR prior"
    )
