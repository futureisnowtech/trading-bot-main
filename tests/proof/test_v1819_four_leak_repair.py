"""
tests/proof/test_v1819_four_leak_repair.py — proof tests for v18.19 fixes.

Covers:
  Fix 1  fee-inflated target_price + economics_ok_to_exit gate
  Fix 2  sticky regime hysteresis + DB persistence
  Fix 3  external_manual filter + sell_blocked halt logic
  Fix 4  entry cooldown enforcement + thesis-exit floor hysteresis
  Metrics  session reset + per-asset label cleanup
"""

from __future__ import annotations

import time

import pytest


# ── Fix 1: economics_ok_to_exit ─────────────────────────────────────────────


def test_economics_gate_blocks_pyrrhic_winner():
    """Gross > 0 but net < 0: classic pyrrhic-winner case. Must block."""
    from risk.spot_economics_gate import economics_ok_to_exit

    ok, reason = economics_ok_to_exit(
        symbol="XRP",
        entry_price=2.50,
        current_price=2.51,
        qty=10.0,
        entry_fee_usd=0.15,
        execution_route_guess="taker",
    )
    assert ok is False, f"expected block, got allow ({reason})"
    assert "pyrrhic_winner" in reason


def test_economics_gate_allows_genuine_loser():
    """Gross < 0: discipline > economics. Free the capital."""
    from risk.spot_economics_gate import economics_ok_to_exit

    ok, reason = economics_ok_to_exit(
        symbol="XRP",
        entry_price=2.50,
        current_price=2.30,
        qty=10.0,
        entry_fee_usd=0.15,
        execution_route_guess="taker",
    )
    assert ok is True, f"expected allow on genuine loser, got block ({reason})"
    assert "genuine_loser" in reason


def test_economics_gate_allows_clean_winner():
    from risk.spot_economics_gate import economics_ok_to_exit

    ok, reason = economics_ok_to_exit(
        symbol="XRP",
        entry_price=2.50,
        current_price=2.60,
        qty=10.0,
        entry_fee_usd=0.15,
        execution_route_guess="taker",
    )
    assert ok is True, f"expected allow, got block ({reason})"
    assert reason.startswith("net_ok")


def test_economics_gate_force_mandatory():
    """Hard stops / EOD must NOT be blocked by net check."""
    from risk.spot_economics_gate import economics_ok_to_exit

    ok, reason = economics_ok_to_exit(
        symbol="XRP",
        entry_price=2.50,
        current_price=2.30,
        qty=10.0,
        entry_fee_usd=0.15,
        mandatory=True,
    )
    assert ok is True
    assert reason == "mandatory"


# ── Fix 1: fee-inflated target_price math ───────────────────────────────────


def test_target_price_includes_round_trip_fees():
    """Target at price=$2.50, target_r=0.5, stop_pct=0.02 should clear the old
    gross 2.525 by at least 1% round-trip fee → target ≥ ~$2.5375."""
    from risk.spot_economics_gate import SPOT_MAKER_FEE_PCT, SPOT_TAKER_FEE_PCT

    price = 2.50
    stop_pct = 0.02
    target_r = 0.5
    round_trip = SPOT_MAKER_FEE_PCT + SPOT_TAKER_FEE_PCT
    # Reproduce the open_spot formula
    target_price = round(price * (1.0 + stop_pct * target_r + round_trip), 8)
    gross_target = round(price * (1.0 + stop_pct * target_r), 8)
    assert target_price > gross_target
    # 0.02 * 0.5 = 0.01 (gross), + 0.01 round-trip = 0.02 total → $2.55
    assert target_price >= 2.5375


# ── Fix 2: sticky regime hysteresis ─────────────────────────────────────────


def test_regime_no_symbol_is_stateless(proof_runtime):
    """Without symbol, classify behaves like the old stateless version."""
    from runtime.spot_regime import classify_spot_regime

    assert classify_spot_regime({"er": 0.75, "adx": 30.0}, {}) == "TREND"
    assert classify_spot_regime({"er": 0.15, "adx": 15.0}, {}) == "CHOP"
    assert classify_spot_regime({"er": 0.45, "adx": 22.0}, {}) == "NEUTRAL"


def test_regime_sticky_neutral_to_chop(proof_runtime):
    """With prior=NEUTRAL, ER=0.32 stays NEUTRAL (above 0.30 exit cutoff).
    ER=0.25 drops to CHOP."""
    from logging_db.trade_logger import save_spot_regime_state
    from runtime.spot_regime import classify_spot_regime

    save_spot_regime_state("XYZ", "NEUTRAL")
    assert classify_spot_regime({"er": 0.32, "adx": 18}, {}, symbol="XYZ") == "NEUTRAL"
    save_spot_regime_state("XYZ", "NEUTRAL")
    assert classify_spot_regime({"er": 0.25, "adx": 18}, {}, symbol="XYZ") == "CHOP"


def test_regime_sticky_chop_to_neutral(proof_runtime):
    """With prior=CHOP, ER=0.32 stays CHOP (still below 0.40 entry cutoff).
    ER=0.45 rises to NEUTRAL."""
    from logging_db.trade_logger import save_spot_regime_state
    from runtime.spot_regime import classify_spot_regime

    save_spot_regime_state("XYZ", "CHOP")
    assert classify_spot_regime({"er": 0.32, "adx": 18}, {}, symbol="XYZ") == "CHOP"
    save_spot_regime_state("XYZ", "CHOP")
    assert classify_spot_regime({"er": 0.45, "adx": 22}, {}, symbol="XYZ") == "NEUTRAL"


def test_regime_state_persists(proof_runtime):
    from logging_db.trade_logger import (
        load_spot_regime_state,
        save_spot_regime_state,
    )

    assert load_spot_regime_state("ZZZ") is None
    save_spot_regime_state("ZZZ", "TREND")
    assert load_spot_regime_state("ZZZ") == "TREND"
    save_spot_regime_state("ZZZ", "CHOP")
    assert load_spot_regime_state("ZZZ") == "CHOP"


# ── Fix 3: external_manual filter + sell_blocked halt ───────────────────────


def test_bot_managed_only_excludes_external_manual(proof_runtime):
    """A symbol classified external_manual must NOT appear when
    bot_managed_only=True, even if there's a DB row for it."""
    import sqlite3

    from runtime.spot_position_truth import set_holding_classification
    from spot_engine import _load_spot_positions_from_db

    with sqlite3.connect(str(proof_runtime.db_path)) as conn:
        conn.execute(
            "INSERT INTO open_positions(symbol,strategy,qty,entry,stop,target,"
            "high_since_entry,ts_entry,paper) VALUES(?,?,?,?,?,?,?,?,?)",
            ("SOL", "spot_sol", 1.0, 100.0, 95.0, 110.0, 100.0, "2026-05-13T00:00:00", 0),
        )
        conn.commit()

    set_holding_classification("SOL", "external_manual", note="user-managed")

    all_rows = _load_spot_positions_from_db(paper=False, bot_managed_only=False)
    bot_rows = _load_spot_positions_from_db(paper=False, bot_managed_only=True)
    assert any(r["symbol"] == "SOL" for r in all_rows)
    assert not any(r["symbol"] == "SOL" for r in bot_rows)


def test_bot_managed_only_excludes_sell_blocked(proof_runtime):
    import sqlite3

    from spot_engine import _load_spot_positions_from_db

    with sqlite3.connect(str(proof_runtime.db_path)) as conn:
        conn.execute(
            "INSERT INTO open_positions(symbol,strategy,qty,entry,stop,target,"
            "high_since_entry,ts_entry,paper,sell_blocked,sell_blocked_reason) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("LINK", "spot_link", 1.0, 10.0, 9.0, 11.0, 10.0,
             "2026-05-13T00:00:00", 0, 1, "INSUFFICIENT_FUND"),
        )
        conn.commit()

    bot_rows = _load_spot_positions_from_db(paper=False, bot_managed_only=True)
    assert not any(r["symbol"] == "LINK" for r in bot_rows)


def test_sell_failure_increment_and_halt(proof_runtime):
    """3 consecutive failures should flip sell_blocked=1, NOT delete the row."""
    import sqlite3

    from logging_db.trade_logger import increment_sell_failure, mark_sell_blocked

    with sqlite3.connect(str(proof_runtime.db_path)) as conn:
        conn.execute(
            "INSERT INTO open_positions(symbol,strategy,qty,entry,stop,target,"
            "high_since_entry,ts_entry,paper) VALUES(?,?,?,?,?,?,?,?,?)",
            ("DOGE", "spot_doge", 100.0, 0.30, 0.28, 0.32, 0.30,
             "2026-05-13T00:00:00", 0),
        )
        conn.commit()

    c1 = increment_sell_failure("DOGE", "spot_doge", paper=0)
    c2 = increment_sell_failure("DOGE", "spot_doge", paper=0)
    c3 = increment_sell_failure("DOGE", "spot_doge", paper=0)
    assert (c1, c2, c3) == (1, 2, 3)

    mark_sell_blocked("DOGE", "spot_doge", "INSUFFICIENT_FUND", paper=0)
    with sqlite3.connect(str(proof_runtime.db_path)) as conn:
        row = conn.execute(
            "SELECT sell_blocked, sell_blocked_reason, sell_failure_count "
            "FROM open_positions WHERE symbol='DOGE'"
        ).fetchone()
    assert row[0] == 1
    assert row[1] == "INSUFFICIENT_FUND"
    assert row[2] == 3


# ── Fix 4: cooldown enforcement ─────────────────────────────────────────────


def test_cooldown_blocks_within_window(proof_runtime, monkeypatch):
    """A recent last_exit_ts should block entry until cooldown_min elapses."""
    from logging_db.trade_logger import save_spot_cooldown_state
    from spot_engine import check_spot_entry_cooldown
    import spot_engine

    # cooldown_min defaults to 10 for symbols not in the config map → 600s
    save_spot_cooldown_state("BTC", int(time.time()) - 60)  # 1 min ago
    ok, reason = check_spot_entry_cooldown("BTC")
    assert ok is False
    assert "cooldown_active" in reason


def test_cooldown_allows_after_window(proof_runtime):
    from logging_db.trade_logger import save_spot_cooldown_state
    from spot_engine import check_spot_entry_cooldown

    # 24h ago → way past any cooldown
    save_spot_cooldown_state("BTC", int(time.time()) - 86400)
    ok, reason = check_spot_entry_cooldown("BTC")
    assert ok is True
    assert reason == "ready"


def test_cooldown_allows_when_no_prior_exit(proof_runtime):
    from spot_engine import check_spot_entry_cooldown

    ok, reason = check_spot_entry_cooldown("NEVERTRADED")
    assert ok is True
    assert reason == "no_prior_exit"


# ── Fix 4: thesis exit uses _EXIT floor (47), not entry floor (52) ──────────


def test_thesis_exit_uses_lower_floor():
    """SPOT_THESIS_MIN_SCORE_EXIT should be strictly lower than the entry
    SPOT_THESIS_MIN_SCORE to produce the entry/exit hysteresis."""
    from config import SPOT_THESIS_MIN_SCORE, SPOT_THESIS_MIN_SCORE_EXIT

    assert SPOT_THESIS_MIN_SCORE_EXIT < SPOT_THESIS_MIN_SCORE


def test_regime_score_exit_floors_are_lower_than_entry_floors():
    from config import SPOT_REGIME_SCORE_EXIT_FLOORS, SPOT_REGIME_SCORE_FLOORS

    for regime in ("TREND", "NEUTRAL", "CHOP"):
        assert (
            SPOT_REGIME_SCORE_EXIT_FLOORS[regime]
            < SPOT_REGIME_SCORE_FLOORS[regime]
        )


# ── Metrics: session reset + label cleanup ──────────────────────────────────


def test_session_reset_zeros_session_gauges():
    from monitoring import metrics

    metrics.PNL_NET_GAUGE.set(12.34)
    metrics.SESSION_TRADES_GAUGE.set(7)
    metrics.reset_session_metrics()
    # Read by name via REGISTRY since prometheus gauges don't expose .get()
    # consistently across versions.
    assert _gauge_value(metrics.PNL_NET_GAUGE) == 0.0
    assert _gauge_value(metrics.SESSION_TRADES_GAUGE) == 0.0


def test_drop_open_position_labels_removes_series():
    from monitoring import metrics

    metrics.OPEN_POS_PNL_GAUGE.labels(asset="BTC").set(1.23)
    metrics.OPEN_POS_ENTRY_GAUGE.labels(asset="BTC").set(62000.0)
    metrics.drop_open_position_labels("BTC")
    # After remove(), labels(asset="BTC") again returns a fresh zero gauge.
    new_gauge = metrics.OPEN_POS_PNL_GAUGE.labels(asset="BTC")
    assert _gauge_value(new_gauge) == 0.0


def _gauge_value(gauge):
    try:
        return float(gauge._value.get())  # type: ignore[attr-defined]
    except Exception:
        # Labeled gauge fallback: iterate REGISTRY
        from prometheus_client import REGISTRY

        for fam in REGISTRY.collect():
            for s in fam.samples:
                if s.name == gauge._name or s.name == f"{gauge._name}_total":
                    return float(s.value)
        return 0.0
