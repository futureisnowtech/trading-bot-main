#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# toggle_scalper_mode.sh — Switch between Defensive and Strategic Scalper
# BeforeAgent | Intercepts chat commands to toggle bot operational mode
# ─────────────────────────────────────────────────────────────────────────────

INPUT=$(cat -)
QUERY=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('query', '').strip())
except Exception:
    print('')
" 2>/dev/null)

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

if [ ! -f "$ENV_FILE" ]; then
    # Create .env if missing (rare, but defensive)
    touch "$ENV_FILE"
fi

if [[ "$QUERY" == "!scalper_on" ]]; then
    # Set STRATEGIC_SCALPER_MODE=true in .env
    if grep -q "STRATEGIC_SCALPER_MODE" "$ENV_FILE"; then
        sed "s/STRATEGIC_SCALPER_MODE=.*/STRATEGIC_SCALPER_MODE=true/" "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
    else
        echo "STRATEGIC_SCALPER_MODE=true" >> "$ENV_FILE"
    fi
    echo "{\"response\": \"🚀 **STRATEGIC SCALPER MODE ENABLED**. Legacy technical vetoes are now bypassed. Bot will execute on all +60% win-probability signals with dynamic sizing and tightened stops.\"}"
    exit 0
fi

if [[ "$QUERY" == "!scalper_off" ]]; then
    # Set STRATEGIC_SCALPER_MODE=false in .env
    if grep -q "STRATEGIC_SCALPER_MODE" "$ENV_FILE"; then
        sed "s/STRATEGIC_SCALPER_MODE=.*/STRATEGIC_SCALPER_MODE=false/" "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
    else
        echo "STRATEGIC_SCALPER_MODE=false" >> "$ENV_FILE"
    fi
    echo "{\"response\": \"🛡️ **DEFENSIVE MODE RESTORED**. Legacy technical vetoes (Confirms, Frames, Path Efficiency) are now active. Trade frequency will decrease.\"}"
    exit 0
fi

# Pass through for all other queries
echo "$INPUT"
exit 0
