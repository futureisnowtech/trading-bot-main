
import sqlite3

from config import DB_PATH

def query_open_kalshi():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # Find all BUYs that don't have a corresponding SELL for the same symbol and qty (simple heuristic)
    # or just find all Kalshi BUYs where won is None.
    rows = conn.execute("""
        SELECT * FROM trades 
        WHERE broker='kalshi' 
          AND action='BUY' 
          AND won IS NULL
        ORDER BY ts DESC
    """).fetchall()
    
    for row in rows:
        print(f"ID: {row['id']} | {row['ts']} | {row['symbol']} | Qty: {row['qty']} | Price: {row['price']} | Strat: {row['strategy']}")

if __name__ == "__main__":
    query_open_kalshi()
