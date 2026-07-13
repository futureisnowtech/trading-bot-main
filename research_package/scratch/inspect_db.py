import sqlite3
import json
import os

db_path = "/Users/joshmacbookair2020/projects/algo_trading_final/logs/trades.db"

def inspect():
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get all tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row['name'] for row in cursor.fetchall()]
    print(f"Total tables: {len(tables)}")
    print(json.dumps(tables, indent=2))

    summary = {}
    for table in sorted(tables):
        cursor.execute(f"PRAGMA table_info({table});")
        columns = [{"name": c['name'], "type": c['type']} for c in cursor.fetchall()]
        
        cursor.execute(f"SELECT COUNT(*) as cnt FROM {table};")
        count = cursor.fetchone()['cnt']
        
        summary[table] = {
            "count": count,
            "columns": columns
        }
        print(f"Table: {table} | Row Count: {count}")
        
        if count > 0:
            cursor.execute(f"SELECT * FROM {table} LIMIT 2;")
            samples = [dict(row) for row in cursor.fetchall()]
            print(f"  Samples: {json.dumps(samples, default=str)[:300]}...")

    # Save details
    os.makedirs("/Users/joshmacbookair2020/projects/algo_trading_final/research_package/scratch", exist_ok=True)
    with open("/Users/joshmacbookair2020/projects/algo_trading_final/research_package/scratch/db_meta.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print("Metadata written to db_meta.json")

    # Inspect Trades
    cursor.execute("SELECT * FROM trades")
    trades = [dict(row) for row in cursor.fetchall()]
    print(f"Trades in DB: {len(trades)}")
    for t in trades:
        print(t)

if __name__ == "__main__":
    inspect()
