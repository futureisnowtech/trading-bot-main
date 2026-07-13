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
    balance_path = Path(config.REPO_ROOT) / "logs" / "paper_balance.json"
    if balance_path.exists():
        try:
            balance_path.unlink()
        except OSError:
            pass

    # Now import forecast.db and initialize
    from forecast.db import init_forecast_db, get_open_forecast_positions_paper
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

    # Verify position is recorded in the paper positions table
    paper_positions = get_open_forecast_positions_paper(db_path=str(db_file))
    assert len(paper_positions) == 1
    assert paper_positions[0]["ticker"] == ticker
    assert paper_positions[0]["qty"] == 50
    assert paper_positions[0]["entry_price"] == 0.65
    assert paper_positions[0]["side"] == "YES"

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


def test_shadow_resolution_settlement(proof_runtime, monkeypatch):
    import config
    db_file = proof_runtime.db_path
    monkeypatch.setattr(config, "DB_PATH", str(db_file), raising=False)
    monkeypatch.setattr(config, "SHADOW_EXECUTION", True, raising=False)
    monkeypatch.setattr(config, "ACCOUNT_SIZE", 1000.0, raising=False)

    # Now import forecast.db
    from forecast.db import init_forecast_db, sync_open_forecast_position_paper, get_open_forecast_positions_paper
    init_forecast_db(db_path=str(db_file))

    balance_path = Path(config.REPO_ROOT) / "logs" / "paper_balance.json"
    if balance_path.exists():
        try:
            balance_path.unlink()
        except OSError:
            pass

    # Connect shadow broker to initialize balance file
    from execution.kalshi_broker import KalshiBroker
    broker = KalshiBroker()
    assert broker.connect(sync_positions=False, quiet=True) is True
    assert broker.get_account_balance() == 1000.0

    # 1. Manually insert contracts and positions to simulate state
    ticker_win = "TEST_WIN_T80"
    ticker_loss = "TEST_LOSS_T80"

    with sqlite3.connect(str(db_file)) as conn:
        # Insert markets
        conn.execute(
            """INSERT INTO forecast_markets 
               (id, market_symbol, market_name, first_seen_at, last_seen_at) 
               VALUES (1, 'TEST_M', 'Test Market', '2026-07-13T00:00:00Z', '2026-07-13T00:00:00Z')"""
        )
        
        # Insert contracts with required NOT NULL columns
        conn.execute(
            """INSERT INTO forecast_contracts 
               (local_symbol, market_id, active, first_seen_at, last_seen_at, right, strike) 
               VALUES (?, 1, 1, '2026-07-13T00:00:00Z', '2026-07-13T00:00:00Z', 'C', 80.0)""",
            (ticker_win,)
        )
        conn.execute(
            """INSERT INTO forecast_contracts 
               (local_symbol, market_id, active, first_seen_at, last_seen_at, right, strike) 
               VALUES (?, 1, 1, '2026-07-13T00:00:00Z', '2026-07-13T00:00:00Z', 'C', 80.0)""",
            (ticker_loss,)
        )
        
        # Get contract IDs
        win_id = conn.execute("SELECT id FROM forecast_contracts WHERE local_symbol=?", (ticker_win,)).fetchone()[0]
        loss_id = conn.execute("SELECT id FROM forecast_contracts WHERE local_symbol=?", (ticker_loss,)).fetchone()[0]

        # Insert mock resolutions
        conn.execute(
            "INSERT INTO forecast_resolutions (contract_id, resolved_side, resolved_value, resolved_at, source) VALUES (?, 'YES', 1.0, '2026-07-13T01:00:00Z', 'kalshi')",
            (win_id,)
        )
        conn.execute(
            "INSERT INTO forecast_resolutions (contract_id, resolved_side, resolved_value, resolved_at, source) VALUES (?, 'NO', 0.0, '2026-07-13T01:00:00Z', 'kalshi')",
            (loss_id,)
        )
        conn.commit()

    # Create paper positions
    # Position 1: 10 contracts of YES on ticker_win (Win: payout = 10 * $1.00 = $10.00)
    sync_open_forecast_position_paper(ticker_win, qty=10, entry_price=0.60, side="YES", db_path=str(db_file))
    # Position 2: 20 contracts of YES on ticker_loss (Loss: payout = $0.00)
    sync_open_forecast_position_paper(ticker_loss, qty=20, entry_price=0.55, side="YES", db_path=str(db_file))

    # Verify positions are open
    open_paper = get_open_forecast_positions_paper(db_path=str(db_file))
    assert len(open_paper) == 2

    # 2. Run Paper Position Settlement
    from forecast.resolution_sync import settle_paper_positions
    res = settle_paper_positions(db_path=str(db_file))
    assert res["settled_count"] == 2

    # Check updated balance: starting 1000.0 + $10.00 = 1010.0
    assert broker.get_account_balance() == 1010.0

    # Verify positions are closed in the DB
    open_paper_after = get_open_forecast_positions_paper(db_path=str(db_file))
    assert len(open_paper_after) == 0
