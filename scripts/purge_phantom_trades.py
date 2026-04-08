"""
scripts/purge_phantom_trades.py — One-time DB cleanup (forensic audit 2026-04-08).

Removes:
  1. REZ phantom trade ($2.5M PnL) from ml_feature_snapshots and trade_attribution.
     This contaminates every ML retrain until removed.
  2. Any other implausibly large PnL rows (> $100K) for safety.

Usage:
    python3 scripts/purge_phantom_trades.py
"""

import sqlite3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH

print(f"Connecting to {DB_PATH} ...")
conn = sqlite3.connect(DB_PATH)

# Audit before
fs_before = conn.execute(
    "SELECT id, pnl_usd FROM ml_feature_snapshots WHERE ABS(pnl_usd) > 100000"
).fetchall()
ta_before = conn.execute(
    "SELECT id, pnl_usd FROM trade_attribution WHERE ABS(pnl_usd) > 100000"
).fetchall()

print(f"\nRows to delete:")
print(f"  ml_feature_snapshots: {fs_before}")
print(f"  trade_attribution:    {ta_before}")

if not fs_before and not ta_before:
    print("\nNothing to purge — DB is already clean.")
    conn.close()
    sys.exit(0)

resp = input("\nProceed with deletion? [yes/no]: ").strip().lower()
if resp != "yes":
    print("Aborted.")
    conn.close()
    sys.exit(0)

conn.execute("DELETE FROM ml_feature_snapshots WHERE ABS(pnl_usd) > 100000")
conn.execute("DELETE FROM trade_attribution WHERE ABS(pnl_usd) > 100000")
conn.commit()

# Confirm
fs_after = conn.execute(
    "SELECT COUNT(*) FROM ml_feature_snapshots WHERE ABS(pnl_usd) > 100000"
).fetchone()[0]
ta_after = conn.execute(
    "SELECT COUNT(*) FROM trade_attribution WHERE ABS(pnl_usd) > 100000"
).fetchone()[0]
conn.close()

print(
    f"\nDone. Phantom rows remaining: ml_feature_snapshots={fs_after}, trade_attribution={ta_after}"
)
print("ML retrains will no longer see the $2.5M REZ phantom.")
