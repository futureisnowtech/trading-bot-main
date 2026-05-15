#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# toggle_scalper_mode.sh — Switch between Defensive and Strategic Scalper
# BeforeAgent | Intercepts chat commands to toggle bot operational mode
# ─────────────────────────────────────────────────────────────────────────────

# LOG_FILE="/tmp/gemini_hook_debug.log"
# printf "--- Hook Start ---\n" >> "$LOG_FILE"

INPUT=$(cat -)
# printf "INPUT: %s\n" "$INPUT" >> "$LOG_FILE"

if [ -z "$INPUT" ]; then
    # printf "Empty input, exiting\n" >> "$LOG_FILE"
    exit 0
fi

QUERY=$(printf "%s" "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    # Check common keys for user message
    q = d.get('query') or d.get('prompt') or d.get('user_message') or ''
    print(q.strip())
except Exception as e:
    # sys.stderr.write(f'JSON error: {e}\n')
    print('')
" 2>/dev/null)

# printf "QUERY: %s\n" "$QUERY" >> "$LOG_FILE"

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

if [[ "$QUERY" == "!scalper_on" ]]; then
    if [ ! -f "$ENV_FILE" ]; then touch "$ENV_FILE"; fi
    if grep -q "STRATEGIC_SCALPER_MODE" "$ENV_FILE"; then
        sed "s/STRATEGIC_SCALPER_MODE=.*/STRATEGIC_SCALPER_MODE=true/" "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
    else
        printf "STRATEGIC_SCALPER_MODE=true\n" >> "$ENV_FILE"
    fi
    printf "{\"response\": \"🚀 **STRATEGIC SCALPER MODE ENABLED**. Legacy technical vetoes are now bypassed. Bot will execute on all +60%% win-probability signals with dynamic sizing and tightened stops.\"}\n"
    exit 0
fi

if [[ "$QUERY" == "!scalper_off" ]]; then
    if [ ! -f "$ENV_FILE" ]; then touch "$ENV_FILE"; fi
    if grep -q "STRATEGIC_SCALPER_MODE" "$ENV_FILE"; then
        sed "s/STRATEGIC_SCALPER_MODE=.*/STRATEGIC_SCALPER_MODE=false/" "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
    else
        printf "STRATEGIC_SCALPER_MODE=false\n" >> "$ENV_FILE"
    fi
    printf "{\"response\": \"🛡️ **DEFENSIVE MODE RESTORED**. Legacy technical vetoes (Confirms, Frames, Path Efficiency) are now active. Trade frequency will decrease.\"}\n"
    exit 0
fi

# IMPORTANT: Always return the original payload if no intercept occurred.
# Gemini CLI hooks MUST return valid JSON if they consume stdin.
printf "%s" "$INPUT"
exit 0
