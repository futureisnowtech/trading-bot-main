import sqlite3
import sys
import os

# Mock configuration
DB_PATH = "/Users/joshmacbookair2020/projects/algo_trading_final/logs/trades.db"

def load_latest_entry_context(ticker: str, side: str) -> dict:
    payload = {
        "entry_price": None,
        "forecast_yes_prob": None,
        "model_prob_gfs": None,
        "model_prob_ecmwf": None,
        "weather_mode": None,
        "forecast_hours_to_resolution": None,
        "entered_at": None,
    }
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT price,
                   forecast_yes_prob,
                   model_prob_gfs,
                   model_prob_ecmwf,
                   weather_mode,
                   forecast_hours_to_resolution,
                   ts
            FROM trades
            WHERE broker='kalshi'
              AND action='BUY'
              AND symbol=?
              AND contract_side=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (ticker, side.upper()),
        ).fetchone()
    except Exception as e:
        print(f"Exception during query: {e}")
        return payload

    if not row:
        print("No row found!")
        return payload

    row_dict = dict(row)
    print(f"Found row in DB: {row_dict}")
    return row_dict

print("=== Testing KXLOWTATL-26JUN09-T64 ===")
res = load_latest_entry_context("KXLOWTATL-26JUN09-T64", "YES")
print(res)

print("=== Testing KXHIGHAUS-26JUN09-T90 ===")
res2 = load_latest_entry_context("KXHIGHAUS-26JUN09-T90", "YES")
print(res2)
