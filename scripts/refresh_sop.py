"""Regenerate SOP.html with live DB state. Run anytime: python3 scripts/refresh_sop.py"""
import json, subprocess, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logging_db.trade_logger import _conn

conn = _conn()
t  = conn.execute("SELECT COUNT(*), SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END), SUM(pnl_usd) FROM trades WHERE paper=1").fetchone()
pos = conn.execute("SELECT symbol, direction, entry, stop, target, ts_entry FROM open_positions WHERE paper=1").fetchall()
rbi = conn.execute("SELECT status, COUNT(*) FROM rbi_incubation GROUP BY status").fetchall()
nf  = conn.execute("SELECT category, severity, title, ts FROM notifications ORDER BY CAST(ts AS REAL) DESC LIMIT 8").fetchall()
conn.close()

bot  = subprocess.run(["pgrep","-f","main.py"],  capture_output=True).returncode == 0
dash = subprocess.run(["pgrep","-f","streamlit"], capture_output=True).returncode == 0

live = {
    "total_trades": t[0] or 0,
    "wins": t[1] or 0,
    "total_pnl": round(float(t[2] or 0), 2),
    "open_positions": [{"symbol":r[0],"direction":r[1],"entry":r[2],"stop":r[3],"target":r[4],"ts":r[5]} for r in pos],
    "rbi": {r[0]: r[1] for r in rbi},
    "notifications": [{"cat":r[0],"sev":r[1],"title":r[2],"ts":r[3]} for r in nf],
    "bot_running": bot,
    "dash_running": dash,
}

sop = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "SOP.html")
with open(sop) as f: html = f.read()

import re
new_state = f'const state = {json.dumps(live)};'
html = re.sub(r'const state = .*?;', lambda _: new_state, html, count=1)
with open(sop, "w") as f: f.write(html)

print(f"SOP.html refreshed — bot={bot} dash={dash} trades={live['total_trades']} positions={len(live['open_positions'])}")
