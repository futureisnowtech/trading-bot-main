---
name: health
description: Quick 30-second system health check — is the bot running, is it trading, are there errors?
argument-hint: ""
allowed-tools:
  - Bash
  - Read
---

Fast system health check. Designed to run in under 30 seconds.

## Process

### 1. Process Status

```bash
ps aux | grep "main.py" | grep -v grep
```

Output: RUNNING (PID: XXXX) or NOT RUNNING.

### 2. Recent Activity

```bash
python3 -c "
from logging_db.trade_logger import get_recent_notifications, get_today_stats
from config import PAPER_TRADING
import json
notifs = get_recent_notifications(limit=10)
stats = get_today_stats(paper=PAPER_TRADING)
print('STATS:', json.dumps(stats))
print('RECENT:')
for n in notifs[:5]:
    print(f'  [{n.get(\"level\",\"?\")}] {n.get(\"message\",\"?\")}')
"
```

### 3. Error Check

```bash
grep -c "ERROR\|HALT\|Exception\|Traceback" logs/bot.log 2>/dev/null | head -1
```

If > 0 errors: show last 3 error lines.

### 4. Database Check

```bash
python3 -c "
import sqlite3, os
db = 'logs/trades.db'
if os.path.exists(db):
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM trades WHERE paper=1')
    n = cur.fetchone()[0]
    conn.close()
    print(f'DB OK — {n} paper trades recorded')
else:
    print('DB MISSING')
"
```

### 5. Output

Single dashboard block:

```
╔══════════════════════════════════════╗
║  SYSTEM HEALTH CHECK                 ║
╠══════════════════════════════════════╣
║  Bot:      RUNNING (PID: 12345)      ║
║  Mode:     PAPER                     ║
║  Trades:   X today (X wins / X loss) ║
║  P&L:      +$X.XX net               ║
║  Halted:   NO                        ║
║  Errors:   0 in log                  ║
║  DB:       OK — XXXX trades          ║
╠══════════════════════════════════════╣
║  STATUS: 🟢 HEALTHY                  ║
╚══════════════════════════════════════╝
```

Status: 🟢 HEALTHY / 🟡 DEGRADED / 🔴 DOWN

DEGRADED = running but has errors or is halted.
DOWN = process not found.

If DOWN or DEGRADED: suggest `bash scripts/log_change.sh` and `python3 main.py --mode paper` to restart.
