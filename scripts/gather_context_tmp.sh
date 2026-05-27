#!/bin/bash

# Run from your repo root: bash scripts/gather_context_tmp.sh
# Gathers context for a new session

TS=$(date +%Y%m%d_%H%M%S)
OUTPUT_DIR="$HOME/Downloads/bot_context_$TS"
mkdir -p "$OUTPUT_DIR"

echo "Gathering context into $OUTPUT_DIR..."

# --- Git state ---
git log --oneline -30 > "$OUTPUT_DIR/git_log.txt"
git status > "$OUTPUT_DIR/git_status.txt"
git diff HEAD > "$OUTPUT_DIR/git_diff.txt"

# --- Directory structure ---
find . -type f -name "*.py" | sort > "$OUTPUT_DIR/file_tree.txt"
ls -la >> "$OUTPUT_DIR/file_tree.txt"

# --- Core files ---
CORE_FILES=(
  "signal_engine.py"
  "learning_loop.py"
  "ml/online_learner.py"
  "data/edge_monitor.py"
  "runtime/spot_strategy.py"
  "spot_engine.py"
  "scheduler/v10_runner.py"
  "logging_db/trade_logger.py"
  "notifications/notification_engine.py"
  "notifications/telegram_bot.py"
  "notifications/ai_agent.py"
  "ml/feature_builder.py"
  "config.py"
  "main.py"
  "MIGRATION_MANIFEST_V14_TO_V18.md"
  "AGENTS.md"
  "CHANGELOG.md"
  "CLAUDE.md"
)

for f in "${CORE_FILES[@]}"; do
  if [ -f "$f" ]; then
    DEST_NAME=$(echo "$f" | tr '/' '__')
    cp "$f" "$OUTPUT_DIR/$DEST_NAME"
    echo "  ✓ $f"
  else
    echo "  ✗ MISSING: $f"
  fi
done

# --- Manifests ---
find . -maxdepth 2 \( -name "*MANIFEST*" -o -name "*MIGRATION*" -o -name "*manifest*" \) | while read f; do
  DEST_NAME=$(echo "$f" | tr '/' '__')
  cp "$f" "$OUTPUT_DIR/$DEST_NAME"
  echo "  ✓ (manifest) $f"
done

# --- DB schema ---
DB_FILE=$(find logs -name "*.db" | head -1)
if [ -z "$DB_FILE" ]; then
    DB_FILE=$(find . -name "*.db" | head -1)
fi

if [ -n "$DB_FILE" ]; then
  sqlite3 "$DB_FILE" ".schema" > "$OUTPUT_DIR/live_db_schema.sql" 2>/dev/null
  echo "  ✓ Live DB schema from $DB_FILE"
fi

echo ""
echo "Done. Files in $OUTPUT_DIR:"
ls -lh "$OUTPUT_DIR"
