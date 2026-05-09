"""
One-time cleanup: remove stale paper=1 open_positions that are orphaned
since the bot transitioned to live trading on 2026-04-15.
These positions were never closed and no live monitoring is happening for them.
Safe to delete: live mode queries filter so they never show in live UI anyway.
Run once: python3 scripts/purge_stale_paper_positions.py
"""

import sqlite3
import os

DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "trades.db")

with sqlite3.connect(DB) as conn:
    c = conn.cursor()
    c.execute("SELECT symbol, strategy, ts_entry FROM open_positions WHERE paper=1")
    rows = c.fetchall()
    if not rows:
        print("No stale paper positions to remove.")
    else:
        print(f"Removing {len(rows)} stale paper positions:")
        for r in rows:
            print(f"  {r}")
        c.execute("DELETE FROM open_positions WHERE paper=1")
        conn.commit()
        print(f"Done — deleted {c.rowcount} rows.")
    c.execute("SELECT COUNT(*) FROM open_positions")
    print(f"open_positions remaining: {c.fetchone()[0]}")
