
import os
import sys
import json
from datetime import datetime, timezone

# Ensure project root is on sys.path
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import execution.kalshi_broker
execution.kalshi_broker.KALSHI_PRIVATE_KEY_PATH = os.path.join(_ROOT, "kalshi_private_key_no_eq.pem")

from execution.kalshi_broker import get_kalshi_broker
import sqlite3
from config import DB_PATH

def reconcile():
    broker = get_kalshi_broker()
    if not broker.connect():
        print("Failed to connect to Kalshi.")
        return

    # 1. Get Live Positions
    live_positions = broker._request("GET", "/trade-api/v2/portfolio/positions")
    print("\n--- Live Kalshi Positions ---")
    print(json.dumps(live_positions, indent=2))

    # 2. Get Open Trades from DB
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Strategy for forecast trades usually starts with 'forecast_'
    db_open = conn.execute(
        "SELECT id, ts, symbol, qty, price, strategy FROM trades WHERE action='BUY' AND strategy LIKE 'forecast_%' AND id NOT IN (SELECT id FROM trades WHERE action='SELL') ORDER BY ts DESC LIMIT 10"
    ).fetchall()
    
    # Actually, the 'trades' table doesn't have a simple 'is_closed' flag, we usually look for a matching SELL.
    # Let's just find recent BUYs that don't have a corresponding SELL in the last X days.
    
    print("\n--- Recent DB Forecast BUYs (Potential Open) ---")
    for row in db_open:
        print(f"ID: {row['id']} | {row['ts']} | {row['symbol']} | Qty: {row['qty']} | Price: {row['price']} | Strat: {row['strategy']}")

    # 3. Get Recent Fills (to find the manual exit)
    # Kalshi V2: GET /trade-api/v2/portfolio/fills
    fills = broker._request("GET", "/trade-api/v2/portfolio/fills")
    print("\n--- Recent Kalshi Fills ---")
    print(json.dumps(fills, indent=2))

if __name__ == "__main__":
    reconcile()
