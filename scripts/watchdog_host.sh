#!/usr/bin/env bash
# Host-side wrapper for the watchdog.
#
# The watchdog runs *inside* execution-engine. That means the single failure it
# most needs to report -- the container being dead -- is the exact failure that
# prevents it from running at all: `docker exec` fails, the error lands in a log
# file nobody reads, and no alert is ever sent. The safety net had a hole
# precisely where the bot falling over would land.
#
# This wrapper runs on the HOST. If the container is up it hands off to the real
# watchdog; if it is down it alerts by itself. Edge-triggered via a state file so
# a long outage pages once, not every fifteen minutes.
set -uo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/algo-runner/bot}"
CONTAINER="${WATCHDOG_CONTAINER:-execution-engine}"
STATE="${PROJECT_DIR}/logs/.watchdog_host_down"
ENV_FILE="${PROJECT_DIR}/.env"

_env_value() {
    # Last-wins, matching python-dotenv, because the droplet .env has duplicate keys.
    [ -r "${ENV_FILE}" ] || return 1
    grep -E "^${1}=" "${ENV_FILE}" 2>/dev/null | tail -1 | cut -d= -f2- \
        | sed -e 's/^["'\'']//' -e 's/["'\'']$//' -e 's/\r$//'
}

notify() {
    local token chat
    token="$(_env_value TELEGRAM_BOT_TOKEN)" || true
    chat="$(_env_value TELEGRAM_CHAT_ID)" || true
    if [ -z "${token:-}" ] || [ -z "${chat:-}" ]; then
        echo "[watchdog-host] no telegram credentials; cannot alert" >&2
        return 1
    fi
    curl -sS -m 20 -o /dev/null -X POST \
        "https://api.telegram.org/bot${token}/sendMessage" \
        -d "chat_id=${chat}" -d "parse_mode=HTML" \
        --data-urlencode "text=${1}"
}

is_running() {
    docker ps --filter "name=^/${CONTAINER}\$" --filter "status=running" \
        --format '{{.Names}}' 2>/dev/null | grep -q .
}

if is_running; then
    if [ -f "${STATE}" ]; then
        rm -f "${STATE}"
        notify "🟢 <b>Recovered</b>: <code>${CONTAINER}</code> is running again." || true
    fi
    exec docker exec "${CONTAINER}" python3 /app/scripts/watchdog.py "$@"
fi

# Not running. Nothing inside the container can report this.
if [ -f "${STATE}" ]; then
    echo "[watchdog-host] ${CONTAINER} still down; already alerted"
    exit 1
fi
: > "${STATE}" 2>/dev/null || true
notify "$(printf '🔴 <b>Weatherman watchdog</b>\n• Container <code>%s</code> is NOT running. The bot is not trading.\n• Nothing inside the container can report this, so this alert came from the host.' "${CONTAINER}")" || true
echo "[watchdog-host] ALERTED: ${CONTAINER} is not running"
exit 1
