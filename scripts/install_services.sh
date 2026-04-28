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

# Generate the live bot plist from template so it always has the correct PROJ path.
# This file is not stored in git (live mode requires deliberate activation).
_generate_live_plist() {
    cat > "$PROJ/scripts/com.algotrading.king.live.plist" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.algotrading.king.live</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Library/Frameworks/Python.framework/Versions/3.14/bin/python3</string>
        <string>-B</string>
        <string>$PROJ/scripts/boot.py</string>
        <string>--mode</string>
        <string>live</string>
        <string>--confirm-live</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$PROJ</string>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <false/>
    <key>ThrottleInterval</key>
    <integer>60</integer>
    <key>StandardOutPath</key>
    <string>$PROJ/logs/service/manual_live_bot.log</string>
    <key>StandardErrorPath</key>
    <string>$PROJ/logs/service/bot_error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/Library/Frameworks/Python.framework/Versions/3.14/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>$HOME</string>
        <key>PYTHONDONTWRITEBYTECODE</key>
        <string>1</string>
        <key>TQDM_DISABLE</key>
        <string>1</string>
        <key>TOKENIZERS_PARALLELISM</key>
        <string>false</string>
    </dict>
</dict>
</plist>
PLISTEOF
    echo "  Generated com.algotrading.king.live.plist (RunAtLoad=false — activated by go_live.py)"
}

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

# Generate live plist with correct paths baked in
_generate_live_plist
# Copy to LaunchAgents (NOT loaded — go_live.py activates it)
cp "$PROJ/scripts/com.algotrading.king.live.plist" "$AGENTS/com.algotrading.king.live.plist"
echo "  Installed com.algotrading.king.live.plist (inactive until go_live.py)"

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
echo "IMPORTANT: Live trading requires explicit activation:"
echo "  python3 scripts/go_live.py   # switch to live (survives reboots)"
echo "  python3 scripts/go_paper.py  # switch back to paper"
