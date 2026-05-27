#!/bin/bash
# External watchdog: pings UptimeRobot (or any heartbeat URL) to signal the bot is alive.
# Set UPTIME_PING_URL in .env (e.g. https://heartbeat.uptimerobot.com/your-token-here)
# UptimeRobot will alert you if it stops receiving pings for >5 min.
#
# Setup:
#   1. Create a free account at https://uptimerobot.com
#   2. Add a new "Heartbeat" monitor — it gives you a unique ping URL
#   3. Set UPTIME_PING_URL=<that_url> in your .env file
#   4. Add to cron (every 5 min): */5 * * * * bash /path/to/scripts/ping_uptime.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$ROOT_DIR/.env"

if [ -f "$ENV_FILE" ]; then
    UPTIME_PING_URL=$(grep '^UPTIME_PING_URL=' "$ENV_FILE" | cut -d'=' -f2-)
fi

if [ -z "$UPTIME_PING_URL" ]; then
    echo "[watchdog] UPTIME_PING_URL not set in .env — skipping ping"
    exit 0
fi

# Only ping if the bot process is actually running
if pgrep -f "main.py" > /dev/null 2>&1; then
    curl -fsS --max-time 10 "$UPTIME_PING_URL" > /dev/null 2>&1
    echo "[watchdog] Heartbeat sent at $(date)"
else
    echo "[watchdog] Bot not running — NOT pinging uptime service (alert will fire)"
fi
