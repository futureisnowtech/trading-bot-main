#!/bin/bash
# backup_credentials.sh — Backs up .env (API keys & credentials) to a
# local folder outside the project directory. NOT git-tracked. Never put
# this backup in a cloud sync folder without encrypting it first.
#
# Storage location: ~/.algo_backup/credentials/
# Keeps the last 10 versions.

PROJ="/Users/joshmacbookair2020/Desktop/algo_trading_final"
ENV_SRC="$PROJ/.env"
BACKUP_DIR="$HOME/.algo_backup/credentials"
DATE=$(date +%Y-%m-%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

if [ ! -f "$ENV_SRC" ]; then
    echo "$(date): ERROR — .env not found at $ENV_SRC"
    exit 1
fi

cp "$ENV_SRC" "$BACKUP_DIR/.env.$DATE"
echo "$(date): Credentials backed up → $BACKUP_DIR/.env.$DATE"

# Prune — keep only the 10 most recent
ls -t "$BACKUP_DIR"/.env.* 2>/dev/null | tail -n +11 | xargs rm -f
echo "$(date): Credential backups: $(ls "$BACKUP_DIR"/.env.* 2>/dev/null | wc -l | tr -d ' ') kept"
