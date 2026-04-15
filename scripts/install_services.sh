#!/bin/bash
# install_services.sh — Registers all launchd services in one step.
#
# What this does:
#   1. Copies the three .plist files to ~/Library/LaunchAgents/
#   2. Loads each one so it starts immediately (and on every reboot)
#
# Run once after cloning/moving the project:
#   bash scripts/install_services.sh
#
# To uninstall everything:
#   bash scripts/install_services.sh --uninstall

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
PLISTS=(
    "com.algotrading.king"
    "com.algotrading.backup"
    "com.algotrading.readiness"
    "com.algotrading.brain"
    "com.algotrading.tradingview"
)

# Make scripts executable
chmod +x "$PROJ/scripts/start_bot.sh"
chmod +x "$PROJ/scripts/backup_db.sh"
chmod +x "$PROJ/scripts/backup_credentials.sh"
chmod +x "$PROJ/scripts/check_readiness.py"

if [ "$1" == "--uninstall" ]; then
    echo "Uninstalling launchd services..."
    for label in "${PLISTS[@]}"; do
        launchctl unload "$AGENTS/$label.plist" 2>/dev/null
        rm -f "$AGENTS/$label.plist"
        echo "  Removed $label"
    done
    echo "Done."
    exit 0
fi

echo "Installing launchd services..."

# Create log dir so launchd can write to it before the bot creates it
mkdir -p "$PROJ/logs/service"

for label in "${PLISTS[@]}"; do
    src="$PROJ/scripts/$label.plist"
    dst="$AGENTS/$label.plist"

    if [ ! -f "$src" ]; then
        echo "  ERROR: $src not found — skipping"
        continue
    fi

    cp "$src" "$dst"
    # Replace the baked-in checkout path with the actual PROJ location (portability)
    sed -i '' "s|/Users/joshmacbookair2020/Projects/algo_trading_final|$PROJ|g" "$dst"

    # Unload first in case it was already registered
    launchctl unload "$dst" 2>/dev/null

    if launchctl load "$dst"; then
        echo "  ✅ $label loaded"
    else
        echo "  ❌ Failed to load $label"
    fi
done

echo ""
echo "Services installed. The bot will now:"
echo "  • Start automatically when you log in"
echo "  • Restart if it crashes (paper mode only)"
echo "  • Back up the database and credentials at 2:00 AM daily"
echo "  • Check readiness for live trading at 7:00 AM daily
  • Generate daily brain summary at 9:47 PM daily"
echo ""
echo "To check service status:"
echo "  launchctl list | grep algotrading"
echo ""
echo "To view bot logs:"
echo "  tail -f $PROJ/logs/service/bot.log"
echo ""
echo "IMPORTANT: Live trading is NOT auto-started. Use the controlled launcher:"
echo "  python3 scripts/go_live.py"
echo "Return to paper:"
echo "  python3 scripts/go_paper.py"
