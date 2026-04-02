"""
migrate_v10.py — One-time migration script for v10 system rebuild.

Run this ONCE before starting v10. It:
1. Backs up trades.db
2. Adds new tables needed by v10 (notifications, pair_intelligence,
   rbi_research, rbi_backtest, rbi_incubation, ml_calibration)
3. Adds new columns to existing tables where needed
4. Does NOT delete any existing data

Usage:
    python3 scripts/migrate_v10.py
    python3 scripts/migrate_v10.py --dry-run   (show SQL only, don't execute)
"""
import sqlite3
import shutil
import os
import sys
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'logs', 'trades.db')
BACKUP_DIR = os.path.expanduser('~/.algo_backup/db/')


def backup_db(db_path: str) -> str:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y-%m-%d_%H%M%S')
    backup_path = os.path.join(BACKUP_DIR, f'trades_pre_v10_{ts}.db')
    shutil.copy2(db_path, backup_path)
    print(f'[backup] DB backed up to {backup_path}')
    return backup_path


MIGRATIONS = [
    # --- notifications table (replaces telegram) ---
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id          TEXT PRIMARY KEY,
        ts          TEXT NOT NULL,
        category    TEXT NOT NULL,
        severity    TEXT NOT NULL DEFAULT 'INFO',
        title       TEXT NOT NULL,
        message     TEXT NOT NULL,
        data        TEXT,
        read        INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_notifications_ts ON notifications(ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_notifications_category ON notifications(category)",

    # --- pair_intelligence table ---
    """
    CREATE TABLE IF NOT EXISTS pair_intelligence (
        symbol              TEXT NOT NULL,
        hour_et             INTEGER NOT NULL,
        win_rate            REAL,
        trade_count         INTEGER DEFAULT 0,
        avg_pnl             REAL,
        avg_hold_minutes    REAL,
        updated_at          TEXT,
        PRIMARY KEY (symbol, hour_et)
    )
    """,

    # --- pair volatility profile ---
    """
    CREATE TABLE IF NOT EXISTS pair_volatility (
        symbol              TEXT PRIMARY KEY,
        avg_atr_pct         REAL,
        avg_spread_pct      REAL,
        typical_vol_spike   REAL,
        funding_persistence REAL,
        correlation_cluster TEXT,
        updated_at          TEXT
    )
    """,

    # --- RBI research results ---
    """
    CREATE TABLE IF NOT EXISTS rbi_research (
        id              TEXT PRIMARY KEY,
        ts              TEXT NOT NULL,
        combination     TEXT NOT NULL,
        win_rate        REAL,
        profit_factor   REAL,
        sharpe          REAL,
        max_drawdown    REAL,
        trade_count     INTEGER,
        p_value         REAL,
        promoted        INTEGER DEFAULT 0,
        failure_reason  TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rbi_research_ts ON rbi_research(ts DESC)",

    # --- RBI backtest results ---
    """
    CREATE TABLE IF NOT EXISTS rbi_backtest (
        id                  TEXT PRIMARY KEY,
        ts                  TEXT NOT NULL,
        combination         TEXT NOT NULL,
        mean_win_rate       REAL,
        worst_win_rate      REAL,
        mean_profit_factor  REAL,
        worst_drawdown      REAL,
        kelly_fraction      REAL,
        trade_count         INTEGER,
        passed              INTEGER DEFAULT 0,
        failure_reason      TEXT
    )
    """,

    # --- RBI incubation tracking ---
    """
    CREATE TABLE IF NOT EXISTS rbi_incubation (
        id                          TEXT PRIMARY KEY,
        combination                 TEXT NOT NULL,
        started_at                  TEXT NOT NULL,
        status                      TEXT DEFAULT 'incubating',
        trades_count                INTEGER DEFAULT 0,
        live_win_rate               REAL,
        live_profit_factor          REAL,
        live_max_drawdown           REAL,
        backtest_max_drawdown       REAL,
        graduated_at                TEXT,
        killed_at                   TEXT,
        failure_reason              TEXT
    )
    """,

    # --- ML calibration tracking ---
    """
    CREATE TABLE IF NOT EXISTS ml_calibration (
        id              TEXT PRIMARY KEY,
        ts              TEXT NOT NULL,
        model_type      TEXT NOT NULL,
        brier_score     REAL,
        calibration_ok  INTEGER,
        trade_count     INTEGER,
        notes           TEXT
    )
    """,

    # --- ML feature importance log ---
    """
    CREATE TABLE IF NOT EXISTS ml_feature_importance (
        id          TEXT PRIMARY KEY,
        ts          TEXT NOT NULL,
        model_type  TEXT NOT NULL,
        feature     TEXT NOT NULL,
        importance  REAL,
        rank        INTEGER
    )
    """,

    # --- Kill switch log ---
    """
    CREATE TABLE IF NOT EXISTS kill_switch_log (
        id          TEXT PRIMARY KEY,
        ts          TEXT NOT NULL,
        reason      TEXT NOT NULL,
        balance     REAL,
        peak_balance REAL,
        positions_closed INTEGER,
        resumed_at  TEXT
    )
    """,

    # --- Add new columns to trade_attribution if not present ---
    # (ALTER TABLE IF NOT EXISTS column is not standard SQL;
    # we wrap these in try blocks in the runner)
    "ALTER TABLE trade_attribution ADD COLUMN technical_score REAL",
    "ALTER TABLE trade_attribution ADD COLUMN ml_score REAL",
    "ALTER TABLE trade_attribution ADD COLUMN composite_score REAL",
    "ALTER TABLE trade_attribution ADD COLUMN regime TEXT",
    "ALTER TABLE trade_attribution ADD COLUMN entry_thesis_score REAL",
    "ALTER TABLE trade_attribution ADD COLUMN exit_thesis_score REAL",
    "ALTER TABLE trade_attribution ADD COLUMN trailing_stop_activated INTEGER DEFAULT 0",
    "ALTER TABLE trade_attribution ADD COLUMN scale_out_triggered INTEGER DEFAULT 0",
    "ALTER TABLE trade_attribution ADD COLUMN funding_collected REAL DEFAULT 0",
    "ALTER TABLE trade_attribution ADD COLUMN maker_rebate REAL DEFAULT 0",
]


def run_migration(dry_run: bool = False):
    db_path = os.path.abspath(DB_PATH)
    if not os.path.exists(db_path):
        print(f'[error] DB not found at {db_path}')
        sys.exit(1)

    if not dry_run:
        backup_db(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('PRAGMA journal_mode=WAL')

    ok = 0
    skipped = 0
    errors = 0

    for sql in MIGRATIONS:
        sql = sql.strip()
        if not sql:
            continue
        if dry_run:
            print(f'[dry-run] {sql[:80]}...' if len(sql) > 80 else f'[dry-run] {sql}')
            continue
        try:
            cursor.execute(sql)
            ok += 1
        except sqlite3.OperationalError as e:
            err_msg = str(e)
            if 'duplicate column name' in err_msg or 'already exists' in err_msg:
                skipped += 1
            else:
                print(f'[error] {err_msg} | SQL: {sql[:60]}')
                errors += 1

    if not dry_run:
        conn.commit()
        conn.close()
        print(f'\n[migration] Complete. OK: {ok} | Skipped (already exists): {skipped} | Errors: {errors}')
        if errors == 0:
            print('[migration] v10 DB migration successful.')
        else:
            print('[migration] WARNING: some migrations failed. Review errors above.')
    else:
        conn.close()
        print(f'\n[dry-run] {len(MIGRATIONS)} statements would run.')


if __name__ == '__main__':
    dry_run = '--dry-run' in sys.argv
    if dry_run:
        print('[dry-run mode] No changes will be made.\n')
    run_migration(dry_run=dry_run)
