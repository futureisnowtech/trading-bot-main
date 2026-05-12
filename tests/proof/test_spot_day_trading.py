"""
tests/proof/test_spot_day_trading.py — proof coverage for the v17 spot scalp lane.

Invariants proved:
  SDT-01  check_spot_targets closes when mark >= persisted target
  SDT-02  check_spot_targets does not close below target
  SDT-03  EOD flatten is disabled by default for crypto spot
  SDT-04  EOD flatten closes only when explicitly enabled and past the configured time
  SDT-05  open_spot persists scalp target metadata
  SDT-06  stagnation exits close dead trades after the expected half-life
  SDT-07  thesis decay respects the minimum-hold gate
  SDT-08  thesis decay closes after the hold gate when the live derivative stack breaks
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _seed_spot_position(
    db_path,
    *,
    symbol="ETH",
    entry=2000.0,
    stop=1980.0,
    target=2036.0,
    ts_entry: str | None = None,
    spot_regime="TREND",
    target_r=0.85,
    trail_arm_r=0.55,
    entry_fee_usd=0.0,
):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO open_positions
                (symbol, strategy, qty, entry, stop, target, high_since_entry,
                 low_since_entry, ts_entry, paper, direction, leverage,
                 spot_regime, target_r, trail_arm_r, entry_fee_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                f"spot_{symbol.lower()}",
                0.05,
                entry,
                stop,
                target,
                entry,
                entry,
                ts_entry or datetime.now().isoformat(),
                1,
                "LONG",
                1,
                spot_regime,
                target_r,
                trail_arm_r,
                entry_fee_usd,
            ),
        )


def _trend_state(symbol="ETH", derivative_score=72.0, five_v=0.25, five_a=0.10):
    return {
        "symbol": symbol,
        "regime": "TREND",
        "derivative_score": derivative_score,
        "setup_family": "impulse_continuation",
        "setup_score": 0.86,
        "structural_confirm_count": 2,
        "structural_confirms": "kst,supertrend",
        "tf_5m_state": "z=0.40|v=0.25|a=0.10|score=71.0",
        "tf_30m_state": "z=0.30|v=0.12|a=0.05|score=65.0",
        "tf_4h_state": "z=0.20|v=0.05|a=0.02|score=60.0",
        "tf_1d_state": "z=0.10|v=0.03|a=0.01|score=56.0",
        "ou_halflife_minutes": 10.0,
        "rv_ratio": 1.0,
        "frames": {
            "5m": {
                "v": five_v,
                "a": five_a,
                "frame_score": 71.0,
                "atr_pct": 0.006,
                "price": 2000.0,
                "path_efficiency": 0.25,
                "momentum_impulse": 0.20,
                "structure_component": 0.10,
                "participation_component": 0.05,
            },
            "30m": {
                "v": 0.12,
                "a": 0.05,
                "z": 0.30,
                "frame_score": 65.0,
                "volatility_quality": 0.10,
            },
            "4h": {"v": 0.05, "a": 0.02, "z": 0.20, "frame_score": 60.0},
            "1d": {"v": 0.03, "a": 0.01, "z": 0.10, "frame_score": 56.0},
        },
    }


def _paper_broker(mark_price=2000.0):
    broker = MagicMock()
    broker.get_mark_price.return_value = mark_price
    broker.get_spot_balance.return_value = {"usd_available": 1000.0}
    broker.buy_spot.return_value = {
        "order_id": "buy_1",
        "filled_size": 0.05,
        "average_filled_price": mark_price,
        "fee_usd": 0.25,
        "execution_route": "paper_market",
    }
    broker.sell_spot.return_value = {
        "order_id": "sell_1",
        "filled_size": 0.05,
        "average_filled_price": mark_price,
        "fee_usd": 0.25,
        "execution_route": "paper_market",
    }
    return broker


def test_sdt01_target_hit_closes_position(proof_runtime, monkeypatch):
    import spot_engine

    _seed_spot_position(str(proof_runtime.db_path), target=2036.0)
    monkeypatch.setattr(spot_engine, "_get_broker", lambda paper=False: _paper_broker(2040.0))

    closed = spot_engine.check_spot_targets()
    assert len(closed) == 1
    assert closed[0]["trigger"] == "target_hit"
    assert closed[0]["exit_reason"] == "target_hit"


def test_sdt02_below_target_no_close(proof_runtime, monkeypatch):
    import spot_engine

    _seed_spot_position(str(proof_runtime.db_path), target=2036.0)
    monkeypatch.setattr(spot_engine, "_get_broker", lambda paper=False: _paper_broker(2020.0))

    assert spot_engine.check_spot_targets() == []


def test_sdt03_eod_flatten_disabled_by_default(proof_runtime, monkeypatch):
    import spot_engine

    _seed_spot_position(str(proof_runtime.db_path))
    monkeypatch.setattr(spot_engine, "SPOT_EOD_FLATTEN_ENABLED", False)
    assert spot_engine.check_spot_eod_close() == []


def test_sdt04_eod_close_at_time_when_enabled(proof_runtime, monkeypatch):
    import pytz
    import spot_engine

    _seed_spot_position(str(proof_runtime.db_path))
    monkeypatch.setattr(spot_engine, "SPOT_EOD_FLATTEN_ENABLED", True)
    monkeypatch.setattr(spot_engine, "SPOT_EOD_CLOSE_TIME", "15:45")
    monkeypatch.setattr(spot_engine, "_get_broker", lambda paper=False: _paper_broker(2050.0))

    et = pytz.timezone("America/New_York")
    fake_now = et.localize(datetime(2026, 4, 20, 15, 46, 0))
    with patch("spot_engine.datetime") as mock_dt:
        mock_dt.datetime.now.return_value = fake_now
        mock_dt.datetime.fromisoformat = datetime.fromisoformat
        closed = spot_engine.check_spot_eod_close()

    assert len(closed) == 1
    assert closed[0]["trigger"] == "eod_close"


def test_sdt05_open_spot_persists_scalp_target_metadata(proof_runtime, monkeypatch):
    import spot_engine

    monkeypatch.setattr(spot_engine, "SPOT_LANE_ACTIVE", True)
    monkeypatch.setattr(spot_engine, "SPOT_SYMBOLS", ["BTC", "ETH", "SOL", "XRP", "LTC", "DOGE", "ADA", "LINK"])
    monkeypatch.setattr(spot_engine, "SPOT_TOTAL_ALLOC_CAP_PCT", 0.95)
    monkeypatch.setattr(spot_engine, "_get_broker", lambda paper=False: _paper_broker(2000.0))
    monkeypatch.setattr(spot_engine, "_load_spot_positions_from_db", lambda paper=True: [])
    monkeypatch.setattr(spot_engine, "build_spot_state", lambda symbol: _trend_state(symbol))

    result = spot_engine.open_spot(
        "ETH",
        100.0,
        composite_score=70.0,
        final_spot_score=72.0,
    )
    assert result is not None
    assert result["target_r"] == pytest.approx(3.0)
    assert result["trail_arm_r"] == pytest.approx(1.2)

    with sqlite3.connect(str(proof_runtime.db_path)) as conn:
        row = conn.execute(
            """
            SELECT target, target_r, trail_arm_r, stop_model_version, target_model_version
            FROM open_positions
            WHERE strategy='spot_eth'
            LIMIT 1
            """
        ).fetchone()

    assert row is not None
    assert row[0] > 0
    assert row[1] == pytest.approx(3.0)
    assert row[2] == pytest.approx(1.2)
    assert row[3] == "spot_scalp_v1"
    assert row[4] == "spot_scalp_precision_v1"


def test_sdt06_stagnation_exit_closes_dead_trade(proof_runtime, monkeypatch):
    import spot_engine

    old_ts = (datetime.now() - timedelta(minutes=40)).isoformat()
    _seed_spot_position(
        str(proof_runtime.db_path),
        entry=2000.0,
        stop=1980.0,
        target=2036.0,
        ts_entry=old_ts,
    )
    monkeypatch.setattr(spot_engine, "_get_broker", lambda paper=False: _paper_broker(2003.0))
    monkeypatch.setattr(
        spot_engine,
        "build_spot_state",
        lambda symbol: _trend_state(symbol, derivative_score=55.0, five_v=-0.10, five_a=-0.06),
    )

    closed = spot_engine.check_spot_stagnation_exits()
    assert len(closed) == 1
    assert closed[0]["trigger"] == "stagnation_exit"


def test_sdt07_thesis_decay_respects_min_hold_gate(proof_runtime, monkeypatch):
    import spot_engine

    recent_ts = (datetime.now() - timedelta(minutes=2)).isoformat()
    _seed_spot_position(str(proof_runtime.db_path), ts_entry=recent_ts)
    monkeypatch.setattr(spot_engine, "SPOT_THESIS_MIN_HOLD_MINS", 8.0)
    monkeypatch.setattr(
        spot_engine,
        "build_spot_state",
        lambda symbol: _trend_state(symbol, derivative_score=20.0, five_v=-0.10, five_a=-0.05),
    )

    assert spot_engine.check_spot_thesis_exits() == []


def test_sdt08_thesis_decay_closes_after_hold_gate(proof_runtime, monkeypatch):
    import spot_engine

    old_ts = (datetime.now() - timedelta(minutes=20)).isoformat()
    _seed_spot_position(str(proof_runtime.db_path), ts_entry=old_ts)
    monkeypatch.setattr(spot_engine, "SPOT_THESIS_MIN_HOLD_MINS", 8.0)
    monkeypatch.setattr(spot_engine, "_get_broker", lambda paper=False: _paper_broker(1995.0))
    monkeypatch.setattr(
        spot_engine,
        "build_spot_state",
        lambda symbol: _trend_state(symbol, derivative_score=20.0, five_v=-0.10, five_a=-0.05),
    )

    closed = spot_engine.check_spot_thesis_exits()
    assert len(closed) == 1
    assert closed[0]["trigger"] == "thesis_decay"


def test_sdt09_taker_fallback_requires_higher_score(proof_runtime, monkeypatch):
    import spot_engine

    class _LiveBroker:
        _paper = False

        def is_connected(self):
            return True

        def get_spot_balance(self):
            return {"usd_available": 1000.0}

        def get_spot_top_of_book(self, symbol):
            return {
                "best_bid": 2000.0,
                "best_ask": 2001.0,
                "spread_pct": 0.0005,
                "top_depth_usd": 20000.0,
            }

        def place_limit_buy_spot(self, symbol, size_usd, limit_price, post_only=True):
            return {"order_id": "maker_try"}

        def get_spot_order_status(self, order_id, fallback_symbol=None):
            return {"status": "OPEN", "completion_pct": 0.0}

        def cancel_spot_order(self, order_id):
            return True

        def buy_spot(self, symbol, size_usd):
            raise AssertionError("taker fallback should have been blocked by score")

        def get_mark_price(self, symbol):
            return 2000.0

    monkeypatch.setattr(spot_engine, "SPOT_LANE_ACTIVE", True)
    monkeypatch.setattr(
        spot_engine,
        "SPOT_SYMBOLS",
        ["BTC", "ETH", "SOL", "XRP", "LTC", "DOGE", "ADA", "LINK"],
    )
    monkeypatch.setattr(spot_engine, "SPOT_TOTAL_ALLOC_CAP_PCT", 0.95)
    monkeypatch.setattr(spot_engine, "_get_broker", lambda paper=False: _LiveBroker())
    monkeypatch.setattr(
        spot_engine, "_load_spot_positions_from_db", lambda paper=True: []
    )
    monkeypatch.setattr(spot_engine, "time", MagicMock(sleep=lambda *_: None))

    result = spot_engine.open_spot(
        "BTC",
        100.0,
        composite_score=50.0,
        final_spot_score=51.0,
        spot_state=_trend_state("BTC"),
    )
    assert result is None
