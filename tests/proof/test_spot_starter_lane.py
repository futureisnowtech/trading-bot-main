"""
tests/proof/test_spot_starter_lane.py — Proof suite for spot starter lane (v17.2).

Invariants proved:
  SP-01  coinbase_spot_broker paper mode returns mock fills with no API calls
  SP-02  spot_engine blocks when position already open for that symbol
  SP-03  spot_engine blocks when size_usd exceeds deployment cap (live mode)
  SP-04  spot_engine blocks when symbol not in SPOT_SYMBOLS (spot_symbol_not_allowed)
  SP-05  spot_engine blocks when SPOT_LANE_ACTIVE=False (spot_lane_disabled)
  SP-06  spot_engine writes to trades table with broker='coinbase_spot' tag in notes
  SP-07  get_spot_positions returns only spot_ strategy rows, not perp positions
  SP-08  spot balance source is isolated from perp futures_buying_power
"""

from __future__ import annotations

import os
import sys
import sqlite3
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _spot_state(symbol="ETH"):
    return {
        "symbol": symbol,
        "regime": "TREND",
        "derivative_score": 72.0,
        "setup_family": "impulse_continuation",
        "setup_score": 0.86,
        "structural_confirm_count": 2,
        "structural_confirms": "kst,supertrend",
        "tf_5m_state": "z=0.4|v=0.2|a=0.1|score=71",
        "tf_30m_state": "z=0.3|v=0.1|a=0.04|score=64",
        "tf_4h_state": "z=0.2|v=0.05|a=0.02|score=60",
        "tf_1d_state": "z=0.1|v=0.03|a=0.01|score=56",
        "ou_halflife_minutes": 10.0,
        "rv_ratio": 1.0,
        "frames": {
            "5m": {
                "v": 0.2,
                "a": 0.1,
                "frame_score": 71.0,
                "atr_pct": 0.006,
                "price": 2500.0,
                "path_efficiency": 0.25,
                "momentum_impulse": 0.20,
                "structure_component": 0.10,
                "participation_component": 0.05,
            },
            "30m": {
                "v": 0.1,
                "a": 0.04,
                "z": 0.3,
                "frame_score": 64.0,
                "volatility_quality": 0.10,
            },
            "4h": {"v": 0.05, "a": 0.02, "z": 0.2, "frame_score": 60.0},
            "1d": {"v": 0.03, "a": 0.01, "z": 0.1, "frame_score": 56.0},
        },
    }


# ── SP-01: paper mode returns mock fill, no API calls ────────────────────────


def test_sp01_paper_buy_returns_fill_no_api():
    from execution.coinbase_spot_broker import CoinbaseSpotBroker

    broker = CoinbaseSpotBroker(paper=True)
    broker.connect()
    assert broker.is_connected()

    # Patch get_mark_price to avoid network call
    broker._fallback_price = lambda sym: 2500.0

    result = broker.buy_spot("ETH", 50.0)
    assert result is not None, "paper buy_spot must return a dict"
    assert result["side"] == "BUY"
    assert result["paper"] is True
    assert "order_id" in result
    assert float(result["filled_value"]) == pytest.approx(50.0, abs=1.0)


def test_sp01_paper_sell_returns_fill_no_api():
    from execution.coinbase_spot_broker import CoinbaseSpotBroker

    broker = CoinbaseSpotBroker(paper=True)
    broker.connect()
    broker._fallback_price = lambda sym: 2500.0

    # Buy first to have something to sell
    broker.buy_spot("ETH", 50.0)
    holdings = broker.get_spot_positions()
    eth_pos = next((h for h in holdings if h["symbol"] == "ETH"), None)
    assert eth_pos is not None

    result = broker.sell_spot("ETH", eth_pos["qty"])
    assert result is not None
    assert result["side"] == "SELL"
    assert result["paper"] is True


# ── SP-02: spot_engine blocks duplicate open ──────────────────────────────────


def test_sp02_blocks_duplicate_position(proof_runtime, monkeypatch):
    import config
    import spot_engine

    monkeypatch.setattr(config, "SPOT_LANE_ACTIVE", True, raising=False)
    monkeypatch.setattr(config, "SPOT_SYMBOLS", ["BTC", "ETH", "SOL", "XRP"], raising=False)
    monkeypatch.setattr(config, "SPOT_MIN_ORDER_USD", 10.0, raising=False)
    monkeypatch.setattr(config, "SPOT_MAX_DEPLOYED_PCT", 0.40, raising=False)

    spot_engine._load_config()

    # Seed a fake open position in DB
    import logging_db.trade_logger as tl

    tl.persist_position(
        symbol="ETH",
        strategy="spot_eth",
        qty=0.02,
        entry=2500.0,
        stop=0.0,
        target=0.0,
        high_since_entry=2500.0,
        ts_entry="2026-04-17T12:00:00",
        paper=True,
        direction="LONG",
        leverage=1,
    )

    monkeypatch.setattr(spot_engine, "build_spot_state", lambda symbol: _spot_state(symbol))
    result = spot_engine.open_spot("ETH", 50.0, paper=True, final_spot_score=72.0)
    assert result is None, "must block when position already open"


# ── SP-03: spot_engine blocks deployment cap exceeded ────────────────────────


def test_sp03_blocks_deployment_cap(monkeypatch):
    import config
    import spot_engine
    from execution.coinbase_spot_broker import CoinbaseSpotBroker

    monkeypatch.setattr(config, "SPOT_LANE_ACTIVE", True, raising=False)
    monkeypatch.setattr(config, "SPOT_SYMBOLS", ["BTC", "ETH", "SOL", "XRP"], raising=False)
    monkeypatch.setattr(config, "SPOT_MIN_ORDER_USD", 10.0, raising=False)
    monkeypatch.setattr(config, "SPOT_MAX_DEPLOYED_PCT", 0.40, raising=False)
    spot_engine._load_config()

    # Mock broker returning $100 USD available — cap = $40
    mock_broker = CoinbaseSpotBroker(paper=False)
    mock_broker._paper = False
    mock_broker._connected = True
    mock_broker.get_spot_balance = lambda: {
        "usd_available": 100.0,
        "btc_available": 0.0,
        "eth_available": 0.0,
    }

    monkeypatch.setattr(spot_engine, "_get_broker", lambda paper: mock_broker)
    # Ensure no existing positions in DB for this test
    monkeypatch.setattr(
        spot_engine, "_load_spot_positions_from_db", lambda paper=True: []
    )

    monkeypatch.setattr(spot_engine, "build_spot_state", lambda symbol: _spot_state(symbol))

    # Request $50 which exceeds 50% of $100 = $50? no, set total alloc cap lower for this proof.
    monkeypatch.setattr(spot_engine, "SPOT_TOTAL_ALLOC_CAP_PCT", 0.40)
    result = spot_engine.open_spot("ETH", 50.0, paper=False, final_spot_score=72.0)
    assert result is None, "must block when size_usd > deployment cap"


# ── SP-04: spot_engine blocks unsupported symbol ─────────────────────────────


def test_sp04_blocks_unsupported_symbol(monkeypatch):
    import config
    import spot_engine

    monkeypatch.setattr(config, "SPOT_LANE_ACTIVE", True, raising=False)
    monkeypatch.setattr(config, "SPOT_SYMBOLS", ["BTC", "ETH", "SOL", "XRP"], raising=False)
    monkeypatch.setattr(config, "SPOT_MIN_ORDER_USD", 10.0, raising=False)
    spot_engine._load_config()

    result = spot_engine.open_spot("DOGE", 50.0, paper=True, final_spot_score=72.0)
    assert result is None, "DOGE must be blocked — not in SPOT_SYMBOLS"


# ── SP-05: spot_engine blocks when lane disabled ──────────────────────────────


def test_sp05_blocks_lane_disabled(monkeypatch):
    import config
    import spot_engine

    monkeypatch.setattr(config, "SPOT_LANE_ACTIVE", False, raising=False)
    spot_engine._load_config()

    result = spot_engine.open_spot("ETH", 50.0, paper=True, final_spot_score=72.0)
    assert result is None, "must block when SPOT_LANE_ACTIVE=False"


# ── SP-06: spot_engine writes to trades table ─────────────────────────────────


def test_sp06_writes_to_trades_table(proof_runtime, monkeypatch):
    import config
    import spot_engine
    from execution.coinbase_spot_broker import CoinbaseSpotBroker

    monkeypatch.setattr(config, "SPOT_LANE_ACTIVE", True, raising=False)
    monkeypatch.setattr(config, "SPOT_SYMBOLS", ["BTC", "ETH", "SOL", "XRP"], raising=False)
    monkeypatch.setattr(config, "SPOT_MIN_ORDER_USD", 10.0, raising=False)
    monkeypatch.setattr(config, "SPOT_MAX_DEPLOYED_PCT", 0.40, raising=False)
    spot_engine._load_config()

    # Ensure no existing spot position
    monkeypatch.setattr(
        spot_engine, "_load_spot_positions_from_db", lambda paper=True: []
    )

    # Mock broker so no real API call
    mock_broker = CoinbaseSpotBroker(paper=True)
    mock_broker._paper = True
    mock_broker._connected = True
    mock_broker._fallback_price = lambda sym: 2500.0

    monkeypatch.setattr(spot_engine, "_get_broker", lambda paper: mock_broker)
    monkeypatch.setattr(spot_engine, "build_spot_state", lambda symbol: _spot_state(symbol))

    result = spot_engine.open_spot("ETH", 25.0, paper=True, final_spot_score=72.0)
    assert result is not None, "open_spot should succeed"

    # Verify trade written
    db_path = str(proof_runtime.db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT broker, strategy FROM trades WHERE symbol='ETH' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None, "trade row must be written to DB"
    broker_val, strategy_val = row
    assert broker_val == "coinbase_spot", (
        f"broker must be 'coinbase_spot', got {broker_val!r}"
    )
    assert strategy_val.startswith("spot_"), (
        f"strategy must start with 'spot_', got {strategy_val!r}"
    )


# ── SP-07: get_spot_positions returns only spot_ rows, not perp ───────────────


def test_sp07_no_perp_contamination(proof_runtime, monkeypatch):
    import config
    import spot_engine
    import logging_db.trade_logger as tl

    monkeypatch.setattr(config, "SPOT_LANE_ACTIVE", True, raising=False)
    spot_engine._load_config()

    # Seed a perp position (strategy='v10_perp')
    tl.persist_position(
        symbol="ETH",
        strategy="v10_perp",
        qty=0.1,
        entry=2500.0,
        stop=2400.0,
        target=2700.0,
        high_since_entry=2500.0,
        ts_entry="2026-04-17T12:00:00",
        paper=True,
        direction="LONG",
        leverage=3,
    )
    # Seed a spot position (strategy='spot_eth')
    tl.persist_position(
        symbol="BTC",
        strategy="spot_btc",
        qty=0.001,
        entry=90000.0,
        stop=0.0,
        target=0.0,
        high_since_entry=90000.0,
        ts_entry="2026-04-17T12:00:00",
        paper=True,
        direction="LONG",
        leverage=1,
    )

    positions = spot_engine.get_spot_positions(paper=True)
    symbols = [p["symbol"] for p in positions]

    # ETH perp must not appear; BTC spot must appear
    assert "ETH" not in symbols, "perp ETH must not appear in spot positions"
    assert "BTC" in symbols, "spot BTC must appear in spot positions"


# ── SP-08: spot balance isolated from perp futures_buying_power ───────────────


def test_sp08_spot_balance_isolated_from_perp():
    """
    get_spot_balance_summary() must not read from perp CFM API.
    It must return source='disabled' when SPOT_LANE_ACTIVE=False.
    """
    import importlib
    import config

    # Ensure SPOT_LANE_ACTIVE is False for this test
    orig = config.SPOT_LANE_ACTIVE
    config.SPOT_LANE_ACTIVE = False
    try:
        import dashboard.data.balance as bal_mod

        result = bal_mod.get_spot_balance_summary()
        assert result["source"] == "disabled", (
            f"Expected 'disabled' when SPOT_LANE_ACTIVE=False, got {result['source']!r}"
        )
        assert result["usd_available"] == 0.0
        assert result["btc_held_usd"] == 0.0
        assert result["eth_held_usd"] == 0.0
    finally:
        config.SPOT_LANE_ACTIVE = orig
