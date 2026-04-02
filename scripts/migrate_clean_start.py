"""
scripts/migrate_clean_start.py — Tag pre-v10 and paper-threshold-relaxed trades
as contaminated so they are excluded from ML training.

Run ONCE after deploying the threshold fix (2026-04-XX).

What it does:
  1. Tags all existing trade_attribution rows as 'pre_v10_contaminated'
     (these were generated under -20pt paper threshold, CoinGecko synthetic
     universe, or old v9 architecture)
  2. Prints a summary of what was tagged and what the new training data
     baseline looks like
  3. Does NOT delete any data — only updates the source column

Safe to run multiple times (idempotent).
"""
import os
import sys
import sqlite3
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from config import DB_PATH
except Exception:
    DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'logs', 'trades.db')

# All training data before this date is considered contaminated.
# Set to today — the day we deployed the threshold fix and Bybit migration.
CLEAN_START_DATE = datetime.now(timezone.utc).strftime('%Y-%m-%d')


def run():
    if not os.path.exists(DB_PATH):
        print(f'[migrate] DB not found at {DB_PATH} — nothing to do')
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL')

    # ── Count what exists before migration ────────────────────────────────────
    before = conn.execute("""
        SELECT source, COUNT(*) as n
        FROM trade_attribution
        GROUP BY source
        ORDER BY n DESC
    """).fetchall()

    print('\n[migrate_clean_start] Current trade_attribution sources:')
    for source, n in before:
        print(f'  {source or "NULL":30s} {n:>6} rows')

    total_before = sum(n for _, n in before)
    print(f'  TOTAL: {total_before} rows\n')

    # ── Tag everything that is not already clean or backtest-excluded ─────────
    conn.execute("""
        UPDATE trade_attribution
        SET source = 'pre_v10_contaminated'
        WHERE source NOT IN ('pre_v10_contaminated', 'live_v10', 'clean_paper_v10')
    """)
    conn.commit()

    # ── Confirm ────────────────────────────────────────────────────────────────
    after = conn.execute("""
        SELECT source, COUNT(*) as n
        FROM trade_attribution
        GROUP BY source
        ORDER BY n DESC
    """).fetchall()

    print('[migrate_clean_start] After migration:')
    for source, n in after:
        print(f'  {source or "NULL":30s} {n:>6} rows')

    print(f'\n[migrate_clean_start] Clean start date: {CLEAN_START_DATE}')
    print('[migrate_clean_start] ML trainer will now exclude all pre_v10_contaminated rows.')
    print('[migrate_clean_start] New paper/live trades will be tagged clean_paper_v10 / live_v10.')
    print('[migrate_clean_start] Done.\n')

    conn.close()


if __name__ == '__main__':
    run()
