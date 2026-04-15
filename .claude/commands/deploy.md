---
name: deploy
description: Pre-flight check and deployment sequence for starting or restarting the trading bot
argument-hint: "[--mode=paper|live] [--crypto-only|--equity-only]"
allowed-tools:
  - Read
  - Bash
  - Glob
---

Run the full pre-flight checklist and deploy the trading bot safely.

## Process

### 1. Parse Mode

Extract `--mode` from `$ARGUMENTS`. Default: paper.
If `--mode=live`: display a WARNING and require explicit confirmation before proceeding.

### 2. Environment Check

```bash
python3 scripts/validate.py
```

Must pass: env vars set, config loads, DB accessible, broker imports work.
Stop if any check fails — do not deploy a broken system.

### 3. Readiness Score (Live Mode Only)

If deploying live, call `get_readiness_score` via MCP and display all 7 criteria.
Refuse to proceed if score < 7/7.

### 4. Process Cleanup

Check for existing bot processes:
```bash
ps aux | grep "main.py" | grep -v grep
```

If running: show the PID and ask the user whether to kill it before starting fresh.
Never kill automatically — ask first.

### 5. Database Backup

```bash
bash scripts/backup_db.sh
```

Confirm backup succeeded before starting the bot.

### 6. Launch

Paper mode:
```bash
python3 main.py --mode paper {extra_flags}
```

Live mode (only if readiness = 7/7 and user confirmed):
```bash
python3 scripts/go_live.py
```

### 7. Health Check

Wait 30 seconds then verify the bot is running:
```bash
ps aux | grep "main.py" | grep -v grep
```

Query recent notifications:
```bash
python3 -c "from logging_db.trade_logger import get_recent_notifications; import json; print(json.dumps(get_recent_notifications(limit=5), indent=2))"
```

### 8. Output

Deployment summary:
- Mode: paper / live
- Process PID
- First 5 system events from the new session
- Dashboard URL: http://localhost:8501

### 9. Lane 3 Status (if LANE3_ENABLED=true)

If Lane 3 is configured, show prediction market status:
```bash
python3 -c "
from config import LANE3_ENABLED, POLYMARKET_ENABLED, KALSHI_ENABLED, POLYMARKET_PAPER, KALSHI_PAPER
print(f'Lane 3: {LANE3_ENABLED} | Polymarket: {POLYMARKET_ENABLED} (paper={POLYMARKET_PAPER}) | Kalshi: {KALSHI_ENABLED} (paper={KALSHI_PAPER})')
"
```

Note: Lane 3 starts in paper mode by default (POLYMARKET_PAPER=true, KALSHI_PAPER=true).
Polymarket live requires a Polygon crypto wallet. Kalshi live requires a verified Kalshi account.

## Safety Rules

- NEVER deploy live without 7/7 readiness score
- ALWAYS backup DB before starting
- NEVER kill a running live-mode process without user confirmation
- If deployment fails (process not found after 30s): show the last 20 lines of logs/bot.log
- NEVER enable LANE3 live trading without separate confirmation from user (prediction markets are real money)
