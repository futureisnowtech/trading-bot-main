from __future__ import annotations

import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _trend_state(symbol="ETH", derivative_score=72.0):
    return {
        "symbol": symbol,
        "regime": "TREND",
        "derivative_score": derivative_score,
        "setup_family": "impulse_continuation",
        "setup_score": 0.84,
        "structural_confirm_count": 2,
        "structural_confirms": "supertrend,kst",
        "tf_5m_state": "score=71.0",
        "tf_30m_state": "score=65.0",
        "tf_4h_state": "score=60.0",
        "tf_1d_state": "score=56.0",
        "frames": {
            "5m": {
                "frame_score": 71.0,
                "price": 2000.0,
                "atr_pct": 0.006,
                "path_efficiency": 0.22,
                "momentum_impulse": 0.20,
                "structure_component": 0.10,
                "participation_component": 0.05,
            },
            "30m": {
                "frame_score": 65.0,
                "volatility_quality": 0.10,
            },
        },
    }


def _paper_broker(mark_price=2000.0):
    from unittest.mock import MagicMock

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
        "average_filled_price": mark_price * 1.01,
        "fee_usd": 0.25,
        "execution_route": "paper_market",
    }
    return broker


def test_sli01_spot_close_persists_learning_and_tv_lineage(proof_runtime, monkeypatch):
    import spot_engine

    monkeypatch.setattr(spot_engine, "SPOT_LANE_ACTIVE", True)
    monkeypatch.setattr(
        spot_engine,
        "SPOT_SYMBOLS",
        ["BTC", "ETH", "SOL", "XRP", "LTC", "DOGE", "ADA", "LINK"],
    )
    monkeypatch.setattr(spot_engine, "SPOT_TOTAL_ALLOC_CAP_PCT", 0.95)
    monkeypatch.setattr(spot_engine, "_get_broker", lambda paper: _paper_broker(2000.0))
    monkeypatch.setattr(spot_engine, "build_spot_state", lambda symbol: _trend_state(symbol))

    result = spot_engine.open_spot(
        "ETH",
        100.0,
        paper=True,
        composite_score=70.0,
        final_spot_score=72.0,
        tv_context={
            "symbol": "ETH-USDC",
            "profile_name": "algobot_htf_v2",
            "htf_bias": "LONG",
            "tf_min": "240",
            "age_seconds": 30,
            "indicator_name": "AlgoBot HTF Confluence Engine v2",
            "strength": "strong",
            "ts": "2026-04-28T00:00:00+00:00",
        },
    )
    assert result is not None

    with sqlite3.connect(str(proof_runtime.db_path)) as conn:
        row = conn.execute(
            """
            SELECT entry_trade_id, entry_feature_snapshot_id, tv_profile_name, tv_signal_bias
            FROM open_positions
            WHERE strategy='spot_eth'
            LIMIT 1
            """
        ).fetchone()
    assert row is not None
    assert row[0] > 0
    assert row[1] > 0
    assert row[2] == "algobot_htf_v2"
    assert row[3] == "LONG"

    closed = spot_engine.close_spot("ETH", paper=True, exit_reason="target_hit")
    assert closed is not None

    with sqlite3.connect(str(proof_runtime.db_path)) as conn:
        mlfs = conn.execute("SELECT COUNT(*) FROM ml_feature_snapshots").fetchone()[0]
        attr = conn.execute("SELECT COUNT(*) FROM trade_attribution").fetchone()[0]
    assert mlfs == 1
    assert attr == 1
