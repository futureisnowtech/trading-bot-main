
import sqlite3
import os

DB_PATH = "logs/trades.db"

def query():
    if not os.path.exists(DB_PATH):
        print(f"DB not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM trades WHERE broker='kalshi' ORDER BY ts DESC LIMIT 20").fetchall()
    
    if not rows:
        print("No Kalshi trades found.")
        return

    for row in rows:
        print(dict(row))

if __name__ == "__main__":
    query()
