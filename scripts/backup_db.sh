#!/bin/bash
# backup_db.sh — Daily backup of the SQLite trade database and CSV logs.
# Run manually or via launchd (see com.algotrading.backup.plist).
# Keeps the last 30 daily backups, then prunes older ones.

PROJ="/Users/joshmacbookair2020/Desktop/algo_trading_final"
BACKUP_DIR="$HOME/.algo_backup/db"
DB_SRC="$PROJ/logs/trades.db"
CSV_SRC="$PROJ/logs/csv"
DATE=$(date +%Y-%m-%d)

mkdir -p "$BACKUP_DIR"

# ── SQLite backup using the SQLite .backup command (safe while DB is live) ──
if [ -f "$DB_SRC" ]; then
    sqlite3 "$DB_SRC" ".backup '$BACKUP_DIR/trades_$DATE.db'"
    echo "$(date): DB backed up → $BACKUP_DIR/trades_$DATE.db"
else
    echo "$(date): WARNING — trades.db not found at $DB_SRC"
fi

# ── Copy CSV exports ─────────────────────────────────────────────────────────
if [ -d "$CSV_SRC" ]; then
    CSV_DEST="$BACKUP_DIR/csv_$DATE"
    mkdir -p "$CSV_DEST"
    cp "$CSV_SRC"/*.csv "$CSV_DEST/" 2>/dev/null
    echo "$(date): CSVs backed up → $CSV_DEST"
fi

# ── Prune backups older than 30 days ─────────────────────────────────────────
find "$BACKUP_DIR" -name "trades_*.db" -mtime +30 -delete
find "$BACKUP_DIR" -name "csv_*" -type d -mtime +30 -exec rm -rf {} + 2>/dev/null
echo "$(date): Pruned backups older than 30 days"
