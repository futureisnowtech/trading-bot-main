import pytest
import os
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

def test_shadow_broker_initialization_and_order_flow(proof_runtime, monkeypatch):
    # 1. Monkeypatch config variables BEFORE importing modules that bind DB_PATH
    import config
    db_file = proof_runtime.db_path
    monkeypatch.setattr(config, "DB_PATH", str(db_file), raising=False)
    monkeypatch.setattr(config, "SHADOW_EXECUTION", True, raising=False)
    monkeypatch.setattr(config, "ACCOUNT_SIZE", 1000.0, raising=False)

    # Clean any pre-existing balance file in test env
    balance_path = Path(config.REPO_ROOT) / "logs" / "shadow_balance.json"
    if balance_path.exists():
        try:
            balance_path.unlink()
        except OSError:
            pass

    # Now import forecast.db and initialize
    from forecast.db import init_forecast_db
    init_forecast_db(db_path=str(db_file))

    # 2. Get Broker and Connect
    from execution.kalshi_broker import KalshiBroker
    broker = KalshiBroker()
    
    # Verify that connect() works in shadow mode without valid API key / key path
    assert broker.connect(sync_positions=True, quiet=True) is True
    assert broker.is_connected() is True

    # Verify paper balance file was created with default budget ($1000)
    assert balance_path.exists()
    assert broker.get_account_balance() == 1000.0

    # 3. Mock live quotes for a test contract
    ticker = "TEST_T80_YES"
    contract_dict = {"local_symbol": ticker, "right": "C"}
    
    # Mock quote with Yes Ask = $0.65, size = 100, and Yes Bid = $0.62, size = 100
    mock_quote = {
        "local_symbol": ticker,
        "yes_ask": 0.65,
        "yes_ask_vol": 100.0,
        "yes_bid": 0.62,
        "yes_bid_vol": 100.0,
        "no_ask": 0.35,
        "no_ask_vol": 100.0,
        "no_bid": 0.38,
        "no_bid_vol": 100.0,
    }
    monkeypatch.setattr(broker, "get_quote", lambda t: mock_quote)

    # 4. Place simulated BUY order
    # Buy 50 contracts at limit $0.65 (taker order)
    res = broker.place_buy_order(contract_dict, qty=50, limit_price=0.65)
    assert res["status"] == "executed"
    assert res["qty"] == 50
    assert res["price"] == 0.65

    # Check fee calculation: fee(0.65, 50, 0.07) = 0.80
    # Total cost = 50 * 0.65 + 0.80 = 33.30
    expected_balance = 1000.0 - 33.30
    assert abs(broker.get_account_balance() - expected_balance) < 1e-4

    # Shadow positions are held in memory only. They used to be mirrored into
    # forecast_positions_paper; that table retired with the paper lanes, and a dry
    # run must not write to the store the live lane reads.
    held = broker._open_positions[f"{ticker}_C"]
    assert held["local_symbol"] == ticker
    assert held["qty"] == 50
    assert held["entry_price"] == 0.65
    assert held["side"] == "YES"

    # 5. Place simulated BUY order exceeding resting size (Liquidity constraint)
    # Attempting to buy 200, but depth is only 100. It should clamp to 100.
    res2 = broker.place_buy_order(contract_dict, qty=200, limit_price=0.65)
    assert res2["status"] == "executed"
    assert res2["qty"] == 100

    # 6. Exit/Flatten paper position
    # Close 50 contracts. Exit should execute at Yes Bid = 0.62.
    # Bid size is 100, so we get filled for the full 50.
    # Fee: fee(0.62, 50, 0.07) = ceil(0.07 * 50 * 0.62 * 0.38 * 100) / 100 = 0.83
    # Payout = 50 * 0.62 - 0.83 = 30.17
    # Available balance should rise.
    pre_balance = broker.get_account_balance()
    exit_res = broker.flatten_position(ticker, "C", qty=50)
    assert exit_res["status"] == "executed"
    assert exit_res["exit_price"] == 0.62
    assert exit_res["filled_qty"] == 50
    
    post_balance = broker.get_account_balance()
    assert abs(post_balance - (pre_balance + 30.17)) < 1e-4

    # 7. Check DDL firewall: direct mutating REST calls must be blocked
    with pytest.raises(RuntimeError, match="Mutating request blocked by shadow mode firewall"):
        broker._request("POST", "/trade-api/v2/portfolio/events/orders", body={})


# NOTE: test_shadow_resolution_settlement was removed with the paper lanes.
# It exercised forecast.resolution_sync.settle_paper_positions, which settled
# forecast_positions_paper against forecast_resolutions. Paper lanes A and B are
# retired, so there is no successor behavior to assert.
