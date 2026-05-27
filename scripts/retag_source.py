#!/usr/bin/env python3
"""One-time migration: rename source='paper_v10' → 'clean_paper_v10' in trades table."""

import sqlite3, sys, os

DB = os.path.join(os.path.dirname(__file__), "..", "logs", "trades.db")
conn = sqlite3.connect(DB, timeout=10)
conn.execute("PRAGMA journal_mode=WAL")
cur = conn.execute(
    "UPDATE trades SET source='clean_paper_v10' WHERE source='paper_v10'"
)
conn.commit()
print(f"Retagged {cur.rowcount} rows  paper_v10 → clean_paper_v10")
for row in conn.execute(
    "SELECT source, COUNT(*) FROM trades GROUP BY source ORDER BY COUNT(*) DESC"
):
    print(f"  {row[1]:>6}  {row[0]}")
conn.close()
