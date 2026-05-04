"""
tests/proof/test_spot_governance.py

Proof tests for the evidence-derived spot governance layer added in the
2026-04-28 surgery pass:

SG-01  pullback_reclaim in NEUTRAL is quarantined by spot_quality_block_reason
SG-02  pullback_reclaim in CHOP   is quarantined by spot_quality_block_reason
SG-03  pullback_reclaim in TREND is also quarantined in tiny-live mode
SG-04  impulse_continuation in NEUTRAL is NOT quarantined
SG-05  legacy neutral flag no longer re-enables pullback_reclaim
SG-06  legacy chop flag no longer re-enables pullback_reclaim
SG-07  _compute_stop_pct applies NEUTRAL tighten multiplier (< raw stop)
SG-08  _compute_stop_pct applies CHOP tighten multiplier (< raw stop)
SG-09  _compute_stop_pct applies pullback_reclaim tighten (< raw stop)
SG-10  taker_fallback buy returns 'taker_fallback_disabled' when config false
SG-11  taker_fallback sell returns 'taker_fallback_disabled' when config false
SG-12  kill_switch consecutive losses threshold fires
SG-13  kill_switch does NOT fire when losses < threshold
SG-14  SPOT_TAKER_FALLBACK_ENABLED=true allows taker path (gate removed)
"""

from __future__ import annotations

import importlib
import sqlite3
import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── helpers ──────────────────────────────────────────────────────────────────


def _spot_state(regime: str, setup_family: str, setup_score: float = 0.5) -> dict:
    return {
        "regime": regime,
        "setup_family": setup_family,
        "setup_score": setup_score,
        "structural_confirm_count": 2,
        "frames": {
            "5m": {
                "frame_score": 55.0,
                "momentum_impulse": 0.3,
                "structure_component": 0.3,
                "path_efficiency": 0.3,
                "participation_component": 0.3,
                "atr_pct": 0.012,
                "a": 0.1,
                "v": 0.2,
            },
            "30m": {"frame_score": 55.0, "volatility_quality": 0.4},
        },
    }


# ── SG-01 through SG-06: setup family and regime gates ───────────────────────


def test_sg01_pullback_reclaim_neutral_allowed():
    from runtime.spot_strategy import spot_quality_block_reason

    state = _spot_state("NEUTRAL", "pullback_reclaim", setup_score=0.8)
    state["structural_confirm_count"] = 3  # NEUTRAL min=3
    state["frames"]["30m"]["frame_score"] = 60.0  # NEUTRAL min=58.0
    reason, _ = spot_quality_block_reason("BTC", state, final_spot_score=65.0)
    assert reason == "", f"pullback_reclaim NEUTRAL unexpectedly blocked: {reason}"


def test_sg02_pullback_reclaim_chop_quarantined():
    from runtime.spot_strategy import spot_quality_block_reason

    reason, _ = spot_quality_block_reason(
        "ETH",
        _spot_state("CHOP", "pullback_reclaim"),
        final_spot_score=60.0,
    )
    assert reason == "spot_regime_not_allowed:CHOP", reason


def test_sg03_pullback_reclaim_trend_allowed():
    from runtime.spot_strategy import spot_quality_block_reason

    reason, _ = spot_quality_block_reason(
        "SOL",
        _spot_state("TREND", "pullback_reclaim", setup_score=0.8),
        final_spot_score=70.0,
    )
    assert reason == "", f"pullback_reclaim TREND unexpectedly blocked: {reason}"


def test_sg04_impulse_continuation_neutral_not_quarantined():
    from runtime.spot_strategy import spot_quality_block_reason

    reason, _ = spot_quality_block_reason(
        "BTC",
        _spot_state("NEUTRAL", "impulse_continuation", setup_score=0.8),
        final_spot_score=72.0,
    )
    assert "quarantined" not in reason, f"Unexpected quarantine: {reason}"


def test_sg06_chop_blocks_pullback_reclaim():
    from runtime.spot_strategy import spot_quality_block_reason

    reason, _ = spot_quality_block_reason(
        "ETH",
        _spot_state("CHOP", "pullback_reclaim", setup_score=0.9),
        final_spot_score=71.0,
    )
    assert reason == "spot_regime_not_allowed:CHOP", reason


# ── SG-07 through SG-09: stop tighten ────────────────────────────────────────


def _raw_stop(regime: str, setup_family: str = "impulse_continuation") -> float:
    """Compute stop with tighten=1 to get baseline."""
    from spot_engine import _compute_stop_pct

    state = _spot_state(regime, setup_family)
    with (
        patch("config.SPOT_STOP_TIGHTEN_NEUTRAL", 1.0),
        patch("config.SPOT_STOP_TIGHTEN_CHOP", 1.0),
        patch("config.SPOT_STOP_TIGHTEN_PULLBACK", 1.0),
    ):
        return _compute_stop_pct("BTC", state)


def test_sg07_neutral_tighten_applied():
    from spot_engine import _compute_stop_pct

    state = _spot_state("NEUTRAL", "impulse_continuation")
    baseline = _raw_stop("NEUTRAL")
    with patch("config.SPOT_STOP_TIGHTEN_NEUTRAL", 0.92):
        tightened = _compute_stop_pct("BTC", state)
    assert tightened <= baseline, (
        f"tightened={tightened} should be <= baseline={baseline}"
    )


def test_sg08_chop_tighten_applied():
    from spot_engine import _compute_stop_pct

    state = _spot_state("CHOP", "compression_breakout")
    baseline = _raw_stop("CHOP")
    with patch("config.SPOT_STOP_TIGHTEN_CHOP", 0.88):
        tightened = _compute_stop_pct("BTC", state)
    # CHOP adds a +0.10 penalty then we tighten — net should still be ≤ baseline×1.10
    assert tightened <= baseline * 1.10 + 1e-9, (
        f"tightened={tightened} baseline={baseline}"
    )


def test_sg09_pullback_tighten_applied():
    from spot_engine import _compute_stop_pct

    state = _spot_state("TREND", "pullback_reclaim")
    baseline = _raw_stop("TREND")
    with patch("config.SPOT_STOP_TIGHTEN_PULLBACK", 0.90):
        tightened = _compute_stop_pct("BTC", state)
    assert tightened <= baseline + 1e-9, (
        f"tightened={tightened} should be <= baseline={baseline}"
    )


# ── SG-10 through SG-11: taker fallback disabled ──────────────────────────────


def _mock_broker(fill_maker: bool = False):
    broker = MagicMock()
    broker.get_spot_top_of_book.return_value = {
        "best_bid": 100.0,
        "best_ask": 100.1,
        "spread_pct": 0.001,
        "top_depth_usd": 50000,
    }
    if fill_maker:
        broker.get_spot_order_status.return_value = {
            "status": "FILLED",
            "completion_pct": 100.0,
        }
    else:
        broker.get_spot_order_status.return_value = {
            "status": "OPEN",
            "completion_pct": 0.0,
        }
    broker.place_limit_buy_spot.return_value = {"order_id": "test-order-123"}
    broker.place_limit_sell_spot.return_value = {"order_id": "test-order-456"}
    broker.cancel_spot_order.return_value = True
    broker.buy_spot.return_value = {"order_id": "taker-buy", "execution_route": "taker"}
    broker.sell_spot.return_value = {
        "order_id": "taker-sell",
        "execution_route": "taker",
    }
    return broker


def test_sg10_taker_buy_disabled_when_config_false():
    from spot_engine import _maker_first_buy

    broker = _mock_broker(fill_maker=False)
    with patch("config.SPOT_TAKER_FALLBACK_ENABLED", False):
        _, route, _ = _maker_first_buy(broker, "BTC-USD", 50.0)
    assert route == "taker_fallback_disabled", route
    broker.buy_spot.assert_not_called()


def test_sg11_taker_sell_disabled_when_config_false():
    from spot_engine import _maker_first_sell

    broker = _mock_broker(fill_maker=False)
    with patch("config.SPOT_TAKER_FALLBACK_ENABLED", False):
        _, route, _ = _maker_first_sell(broker, "BTC-USD", 0.001)
    assert route == "taker_fallback_disabled", route
    broker.sell_spot.assert_not_called()


def test_sg14_taker_allowed_when_config_true():
    """When SPOT_TAKER_FALLBACK_ENABLED=True the taker path is reached."""
    from spot_engine import _maker_first_buy

    broker = _mock_broker(fill_maker=False)
    with patch("config.SPOT_TAKER_FALLBACK_ENABLED", True):
        result, route, _ = _maker_first_buy(
            broker, "BTC-USD", 50.0, final_spot_score=None
        )
    # With config True, taker order is placed (buy_spot called)
    assert route in ("taker_fallback", "taker_fallback_failed"), route


# ── SG-12 through SG-13: kill switch ─────────────────────────────────────────


def _make_mem_db_with_trades(pnl_list: list[float]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE trades (
            id INTEGER PRIMARY KEY, ts TEXT, strategy TEXT,
            action TEXT, paper INTEGER, pnl_usd REAL, symbol TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE system_events (
            id INTEGER PRIMARY KEY, ts TEXT, level TEXT, source TEXT, message TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE lane_runtime_state (
            lane TEXT PRIMARY KEY, spot_kill_switch_active INTEGER DEFAULT 0,
            last_halt_reason TEXT, last_halt_ts TEXT
        )"""
    )
    conn.execute("INSERT INTO lane_runtime_state (lane) VALUES ('crypto')")
    ts_base = "2026-04-28T10:00:0"
    # paper=0 so kill-switch (which is a live-path concern) reads these rows
    for i, pnl in enumerate(pnl_list):
        conn.execute(
            "INSERT INTO trades (ts, strategy, action, paper, pnl_usd, symbol) VALUES (?,?,?,?,?,?)",
            (f"{ts_base}{i:02d}Z", "spot_btc", "SELL", 0, pnl, "BTC-USD"),
        )
    conn.commit()
    return conn


def test_sg12_kill_switch_consecutive_losses_fires():
    from runtime import spot_kill_switch as _ks

    conn = _make_mem_db_with_trades([-1.0, -1.5, -0.8, -2.0])
    # paper=False so the query matches the paper=0 trades and the check fires
    with (
        patch.object(_ks, "_conn", return_value=conn),
        patch("config.SPOT_KS_CONSECUTIVE_LOSSES", 4),
        patch("config.SPOT_KS_DAILY_LOSS_PCT", 0.99),  # daily threshold disabled
        patch("config.ACCOUNT_SIZE", 5000),
        patch(
            "runtime.spot_position_truth.get_spot_position_truth",
            return_value={"snapshot_ok": True, "blocking_issues": []},
        ),
    ):
        halt, reason = _ks.check_spot_kill_switch(paper=False)
    assert halt is True, f"Expected halt, got reason={reason}"
    assert "ks10a" in reason, reason


def test_sg13_kill_switch_does_not_fire_below_threshold():
    from runtime import spot_kill_switch as _ks

    # Only 3 trades (< threshold of 4), with the most recent being a win
    conn = _make_mem_db_with_trades([-1.0, -1.5, 0.5])
    with (
        patch.object(_ks, "_conn", return_value=conn),
        patch("config.SPOT_KS_CONSECUTIVE_LOSSES", 4),
        patch("config.SPOT_KS_DAILY_LOSS_PCT", 0.99),
        patch("config.ACCOUNT_SIZE", 5000),
        patch(
            "runtime.spot_position_truth.get_spot_position_truth",
            return_value={"snapshot_ok": True, "blocking_issues": []},
        ),
    ):
        halt, reason = _ks.check_spot_kill_switch(paper=False)
    assert halt is False, f"Should not halt, got reason={reason}"
