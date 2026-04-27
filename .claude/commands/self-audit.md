---
name: self-audit
description: Autonomous multi-agent audit for the v18+ trading system. Spawns four parallel specialist agents, performs 3-layer reality reconciliation (docs vs code vs observed behavior), quantifies opportunity cost in dollars from real trade data, auto-remediates zero-risk items, detects regressions vs previous audit, and outputs a prioritized work queue with specific fixes and verify steps. Supports --emergency (BLOCK items only, <15s), --deep (full ML internals), --revenue (funnel/gate focus only).
argument-hint: "[--emergency | --deep | --revenue]"
allowed-tools:
  - Bash
  - Read
  - Write
  - Glob
  - Grep
  - Agent
---

You are running the v18+ autonomous trading system self-audit. This is not a report generator — it is a reasoning loop that reads reality across four parallel evidence streams, closes gaps it can safely fix, and produces a ranked work queue with dollar estimates and exact verify steps.

Follow every phase in order. Do not produce any output until Phase 6. Gather all evidence first.

---

## PHASE 0 — MODE DETECTION

Check `$ARGUMENTS`:
- `--emergency`: Run Phase 1 only. Output BLOCK items and stop immediately.
- `--revenue`: Run Phase 1 + Agent B only. Output revenue findings only.
- `--deep`: Run all phases including extended ML internals in Agent D.
- (default): Run all phases at standard depth.

---

## PHASE 1 — INSTANT PULSE (always first, <15 seconds)

Run these immediately. If mode is `--emergency` and any BLOCK is found, output it and stop.

```bash
echo "=== RUNTIME MODE & BOT STATUS ==="
python3 -c "
import sys; sys.path.insert(0,'dashboard')
from db import _runtime_paper_flag
print('Mode:', 'PAPER' if _runtime_paper_flag() else 'LIVE')
" 2>/dev/null || echo "Mode: UNKNOWN"
pgrep -fl "main.py" || echo "⚠ NO BOT PROCESS FOUND"
pgrep -fl "streamlit" || echo "⚠ NO DASHBOARD PROCESS FOUND"

echo ""
echo "=== LANE HEARTBEAT AGES ==="
python3 -c "
import sqlite3
from datetime import datetime, timezone
conn = sqlite3.connect('logs/trades.db')
conn.row_factory = sqlite3.Row
rows = conn.execute('''SELECT lane_id, active, connected, mode, health,
    last_heartbeat_at, positions_open, buying_power_usd
    FROM lane_runtime_state ORDER BY id DESC''').fetchall()
now = datetime.now(timezone.utc)
for r in rows:
    hb = r['last_heartbeat_at'] or ''
    age = 'UNKNOWN'
    if hb:
        try:
            ts = datetime.fromisoformat(hb.replace('Z','+00:00'))
            age_s = int((now - ts).total_seconds())
            age = f'{age_s}s ago'
            if age_s > 300: age = f'STALE {age_s}s — BLOCK'
        except: pass
    print(f'  {r[\"lane_id\"]:15} active={r[\"active\"]} connected={r[\"connected\"]} mode={r[\"mode\"]:8} hb={age} pos={r[\"positions_open\"]} bp=\${r[\"buying_power_usd\"] or 0:.0f}')
conn.close()
" 2>/dev/null

echo ""
echo "=== KILL SWITCH PROXIMITY ==="
python3 -c "
import sqlite3
conn = sqlite3.connect('logs/trades.db')
conn.row_factory = sqlite3.Row
r = conn.execute('SELECT account_size_live FROM system_runtime_state ORDER BY id DESC LIMIT 1').fetchone()
bal = float(r['account_size_live'] or 0) if r else 0
floor = bal * 0.50
pct_above = (bal - floor) / bal * 100 if bal else 0
status = 'BLOCK' if pct_above < 15 else ('WARN' if pct_above < 25 else 'OK')
print(f'Balance: \${bal:.2f} | Floor (50%): \${floor:.2f} | Buffer: \${bal-floor:.2f} ({pct_above:.1f}% above floor) [{status}]')
fires = conn.execute('SELECT ts, reason FROM kill_switch_log ORDER BY ts DESC LIMIT 3').fetchall()
if fires:
    print('Recent kill switch fires:')
    for f in fires: print(f'  {f[\"ts\"][:19]}: {f[\"reason\"]}')
conn.close()
" 2>/dev/null || echo "Kill switch check: DB error"

echo ""
echo "=== OPEN INCIDENTS ==="
python3 -c "
import sqlite3
conn = sqlite3.connect('logs/trades.db')
conn.row_factory = sqlite3.Row
rows = conn.execute(\"SELECT lane_id, fingerprint, count, last_seen FROM incidents WHERE status='open' OR status IS NULL ORDER BY count DESC LIMIT 5\").fetchall()
if rows:
    for r in rows: print(f'  [{r[\"lane_id\"]}] {str(r[\"fingerprint\"])[:80]} (x{r[\"count\"]}, last: {str(r[\"last_seen\"])[:16]})')
else: print('  No open incidents.')
conn.close()
" 2>/dev/null

echo ""
echo "=== DISK + MEMORY ==="
df -h logs/ 2>/dev/null | tail -1 || df -h . | tail -1
ps aux | grep "main.py" | grep -v grep | awk '{printf "Bot RSS: %s KB (%.1f MB)\n", $6, $6/1024}' 2>/dev/null
```

If bot process not found → **BLOCK [SAFETY]**. Crypto heartbeat >300s → **BLOCK [SAFETY]**. Kill switch buffer <15% → **BLOCK [SAFETY]**.

If `--emergency`: output pulse findings and stop.

---

## PHASE 2 — SPAWN FOUR PARALLEL AGENTS

Send all four Agent tool calls in a single message so they run simultaneously. Wait for all four to complete before proceeding to Phase 3.

---

### AGENT A — System & Infrastructure Health

Prompt:
> You are gathering system health evidence for a trading bot audit. Run every command below, return all output verbatim. No analysis — just evidence and raw numbers.
>
> ```bash
> echo "=== LAUNCHD SERVICES ==="
> launchctl list 2>/dev/null | grep algotrading || echo "No algotrading services registered"
> ls -lah logs/service/ 2>/dev/null | head -12
>
> echo ""
> echo "=== BACKUP RECENCY ==="
> ls -lth logs/backups/ 2>/dev/null | head -5 || find . -name "trades_*.db" -o -name "*.db.bak" 2>/dev/null | head -5 || echo "No backups found"
> python3 -c "import os; s=os.stat('logs/trades.db'); from datetime import datetime; print(f'trades.db last modified: {datetime.fromtimestamp(s.st_mtime)}')" 2>/dev/null
>
> echo ""
> echo "=== LOG FILE SIZES ==="
> du -sh logs/ 2>/dev/null
> ls -lh logs/bot.log logs/service/streamlit.log logs/service/bot_error.log 2>/dev/null
>
> echo ""
> echo "=== STREAMLIT HEALTH ==="
> pgrep -fl streamlit
> tail -8 logs/service/streamlit.log 2>/dev/null || echo "No streamlit log"
>
> echo ""
> echo "=== RUNONSAVE CORRUPTION RISK ==="
> recent_py=$(git log --since="30 minutes ago" --name-only --pretty=format: 2>/dev/null | grep '\.py$' | wc -l | tr -d ' ')
> streamlit_pid=$(pgrep -f streamlit | head -1)
> echo "Python files changed in last 30min: $recent_py"
> echo "Streamlit PID: ${streamlit_pid:-NONE}"
> if [ "$recent_py" -gt "0" ] && [ -n "$streamlit_pid" ]; then echo "⚠ RUNONSAVE RISK ACTIVE"; fi
>
> echo ""
> echo "=== CDP KEY STATUS ==="
> python3 -c "
> from dotenv import load_dotenv; import os; load_dotenv()
> print('CDP_KEY_NAME:', 'SET' if os.getenv('COINBASE_CDP_KEY_NAME') else 'MISSING')
> print('CDP_PRIVATE_KEY:', 'SET' if os.getenv('COINBASE_CDP_PRIVATE_KEY') else 'MISSING')
> " 2>/dev/null
>
> echo ""
> echo "=== NIGHTLY MAINTENANCE LAST RUN ==="
> python3 -c "
> import sqlite3; conn = sqlite3.connect('logs/trades.db'); conn.row_factory = sqlite3.Row
> for src, lbl in [('nightly_audit','Nightly audit'),('candidate_labeler','Candidate labeler'),('rbi_research','RBI research')]:
>     r = conn.execute(f\"SELECT ts FROM system_events WHERE source='{src}' ORDER BY ts DESC LIMIT 1\").fetchone()
>     print(f'{lbl}: {r[\"ts\"][:16] if r else \"NEVER\"}')
> conn.close()
> " 2>/dev/null
>
> echo ""
> echo "=== RECENT SYSTEM EVENTS (last 15) ==="
> python3 -c "
> import sqlite3; conn = sqlite3.connect('logs/trades.db'); conn.row_factory = sqlite3.Row
> rows = conn.execute('SELECT ts, level, source, message FROM system_events ORDER BY ts DESC LIMIT 15').fetchall()
> for r in rows: print(f'{str(r[\"ts\"])[:19]} [{r[\"level\"]}] {r[\"source\"]}: {str(r[\"message\"])[:100]}')
> conn.close()
> " 2>/dev/null
> ```

---

### AGENT B — Trade Performance, Funnel & Revenue

Prompt:
> You are gathering trade performance and revenue evidence for a trading bot audit. Run every command below and return all output verbatim. No analysis — just raw numbers.
>
> ```bash
> echo "=== SCAN FUNNEL (last 24h from DB) ==="
> python3 -c "
> import sqlite3; conn = sqlite3.connect('logs/trades.db'); conn.row_factory = sqlite3.Row
> r = conn.execute('''SELECT SUM(scanner_candidates_total) as scanned,
>     SUM(scored_total) as threshold, SUM(econ_passed_total) as econ, SUM(entered) as entered
>     FROM scan_funnels WHERE datetime(replace(substr(ts,1,19),'T',' ')) >= datetime('now','-24 hours')''').fetchone()
> scanned = r['scanned'] or 0; entered = r['entered'] or 0
> conv = entered/scanned*100 if scanned else 0
> print(f'Scanned: {scanned} | Threshold pass: {r[\"threshold\"] or 0} | Econ pass: {r[\"econ\"] or 0} | Entered: {entered} | Conversion: {conv:.1f}%')
> conn.close()
> " 2>/dev/null
>
> echo ""
> echo "=== TOP BLOCKERS (last 24h, with avg composite score) ==="
> python3 -c "
> import sqlite3; conn = sqlite3.connect('logs/trades.db'); conn.row_factory = sqlite3.Row
> rows = conn.execute('''SELECT trade_blocked_reason, COUNT(*) as n,
>     AVG(CAST(composite_score as REAL)) as avg_score
>     FROM scan_candidates WHERE status='blocked'
>     AND datetime(replace(substr(ts,1,19),'T',' ')) >= datetime('now','-24 hours')
>     GROUP BY trade_blocked_reason ORDER BY n DESC LIMIT 12''').fetchall()
> total = sum(r['n'] for r in rows)
> for r in rows:
>     pct = r['n']/total*100 if total else 0
>     print(f'  {r[\"n\"]:4}x ({pct:4.1f}%) {str(r[\"trade_blocked_reason\"] or \"unknown\"):<45} avg_composite={r[\"avg_score\"] or 0:.1f}')
> conn.close()
> " 2>/dev/null
>
> echo ""
> echo "=== WIN RATE: THIS WEEK vs LAST WEEK ==="
> python3 -c "
> import sqlite3; conn = sqlite3.connect('logs/trades.db'); conn.row_factory = sqlite3.Row
> def wk(start, end):
>     r = conn.execute('''SELECT COUNT(*) as n, SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END) as w,
>         SUM(pnl_usd) as pnl, SUM(fee_usd) as fees,
>         AVG(CAST(composite_score as REAL)) as avg_score
>         FROM trades WHERE action IN ('SELL','CLOSE') AND pnl_usd IS NOT NULL AND paper=0
>         AND datetime(replace(substr(ts,1,19),'T',' ')) >= datetime('now',?) 
>         AND datetime(replace(substr(ts,1,19),'T',' ')) < datetime('now',?)''', (f'-{start} days', f'-{end} days')).fetchone()
>     wr = r['w']/r['n']*100 if r['n'] else 0
>     return r['n'], wr, r['pnl'] or 0, r['fees'] or 0, r['avg_score'] or 0
> n1,wr1,pnl1,fee1,sc1 = wk(7,0); n2,wr2,pnl2,fee2,sc2 = wk(14,7)
> print(f'This week:  closes={n1} WR={wr1:.0f}% PnL=\${pnl1:.2f} fees=\${fee1:.2f} avg_composite={sc1:.1f}')
> print(f'Last week:  closes={n2} WR={wr2:.0f}% PnL=\${pnl2:.2f} fees=\${fee2:.2f} avg_composite={sc2:.1f}')
> if n1 and n2:
>     dwr = wr1-wr2; dpnl = pnl1-pnl2
>     print(f'Delta WR:   {dwr:+.0f}pp  |  Delta PnL: \${dpnl:+.2f}')
>     if dwr < -10: print('⚠ WIN RATE REGRESSION >10pp week-over-week')
> conn.close()
> " 2>/dev/null
>
> echo ""
> echo "=== MAKER vs TAKER SPLIT (spot fills, last 7d) ==="
> python3 -c "
> import sqlite3; conn = sqlite3.connect('logs/trades.db'); conn.row_factory = sqlite3.Row
> r = conn.execute('''SELECT execution_route, COUNT(*) as n, SUM(fee_usd) as fees
>     FROM open_positions WHERE paper=0 AND strategy LIKE 'spot_%'
>     GROUP BY execution_route''').fetchall()
> for row in r: print(f'  open_positions: {row[\"execution_route\"] or \"unknown\"}: {row[\"n\"]} fills, fees=\${row[\"fees\"] or 0:.3f}')
> r2 = conn.execute('''SELECT SUM(CASE WHEN notes LIKE \"%maker%\" THEN 1 ELSE 0 END) as maker,
>     SUM(CASE WHEN notes LIKE \"%taker%\" THEN 1 ELSE 0 END) as taker,
>     COUNT(*) as total, SUM(fee_usd) as total_fees
>     FROM trades WHERE broker LIKE \"%spot%\" AND paper=0
>     AND datetime(replace(substr(ts,1,19),'T',' ')) >= datetime('now','-7 days')''').fetchone()
> maker = r2['maker'] or 0; taker = r2['taker'] or 0; tot = r2['total'] or 0
> maker_pct = maker/tot*100 if tot else 0
> print(f'  7d trades: maker={maker} ({maker_pct:.0f}%) taker={taker} ({100-maker_pct:.0f}%) total={tot} fees=\${r2[\"total_fees\"] or 0:.3f}')
> if maker_pct < 40: print(f'  ⚠ MAKER RATE LOW: {maker_pct:.0f}% (target >55%)')
> conn.close()
> " 2>/dev/null
>
> echo ""
> echo "=== SPOT vs PERP P&L ATTRIBUTION (all-time live) ==="
> python3 -c "
> import sqlite3; conn = sqlite3.connect('logs/trades.db'); conn.row_factory = sqlite3.Row
> for label, where in [('SPOT','broker LIKE \"%spot%\"'), ('PERP','broker LIKE \"%coinbase%\" AND broker NOT LIKE \"%spot%\"')]:
>     r = conn.execute(f'''SELECT COUNT(*) as n, SUM(pnl_usd) as pnl, SUM(fee_usd) as fees,
>         SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END) as wins
>         FROM trades WHERE {where} AND action IN ('SELL','CLOSE') AND pnl_usd IS NOT NULL AND paper=0''').fetchone()
>     wr = r['wins']/r['n']*100 if r['n'] else 0
>     print(f'  {label}: closes={r[\"n\"]} WR={wr:.0f}% PnL=\${r[\"pnl\"] or 0:.2f} fees=\${r[\"fees\"] or 0:.2f}')
> conn.close()
> " 2>/dev/null
>
> echo ""
> echo "=== MFE vs TARGET-R (spot closed trades) ==="
> python3 -c "
> import sqlite3; conn = sqlite3.connect('logs/trades.db'); conn.row_factory = sqlite3.Row
> rows = conn.execute('''SELECT pnl_usd, entry, target, stop FROM trades
>     WHERE broker LIKE \"%spot%\" AND action='SELL' AND pnl_usd IS NOT NULL AND paper=0
>     AND entry > 0 AND stop > 0 ORDER BY ts DESC LIMIT 50''').fetchall()
> if rows:
>     risk_list = [abs(r['entry']-r['stop'])/r['entry']*100 if r['stop'] else 0 for r in rows]
>     pnl_r_list = [r['pnl_usd'] for r in rows]
>     avg_risk = sum(risk_list)/len(risk_list) if risk_list else 0
>     wins = [p for p in pnl_r_list if p > 0]; losses = [p for p in pnl_r_list if p <= 0]
>     print(f'  Sample: {len(rows)} trades | avg stop dist: {avg_risk:.2f}%')
>     print(f'  Avg win: \${sum(wins)/len(wins):.2f}' if wins else '  No wins in sample')
>     print(f'  Avg loss: \${sum(losses)/len(losses):.2f}' if losses else '  No losses in sample')
> else: print('  No closed spot trades with full entry/stop data')
> conn.close()
> " 2>/dev/null
>
> echo ""
> echo "=== OPEN POSITIONS INTEGRITY ==="
> python3 -c "
> import sqlite3; conn = sqlite3.connect('logs/trades.db'); conn.row_factory = sqlite3.Row
> rows = conn.execute('''SELECT symbol, strategy, paper, qty, entry, stop, target,
>     ts_entry, trailing_active, scale_33_done, scale_66_done, execution_route
>     FROM open_positions ORDER BY ts_entry DESC''').fetchall()
> for r in rows:
>     issues = []
>     if not r['stop'] or float(r['stop'] or 0) == 0: issues.append('NO STOP')
>     if not r['target'] or float(r['target'] or 0) == 0: issues.append('NO TARGET')
>     if not r['entry'] or float(r['entry'] or 0) == 0: issues.append('NO ENTRY')
>     flag = '  ⚠ ' + ', '.join(issues) if issues else ''
>     print(f'  {r[\"symbol\"]:6} {str(r[\"strategy\"]):<18} paper={r[\"paper\"]} qty={r[\"qty\"] or 0:.4f} entry={r[\"entry\"] or 0:.4f} stop={r[\"stop\"] or 0:.4f}{flag}')
> conn.close()
> " 2>/dev/null
>
> echo ""
> echo "=== AMYGDALA RULE CHECKS ==="
> python3 -c "
> import sqlite3; conn = sqlite3.connect('logs/trades.db'); conn.row_factory = sqlite3.Row
> # Rule: no duplicate symbols in live open_positions
> dupes = conn.execute('SELECT symbol, COUNT(*) as n FROM open_positions WHERE paper=0 GROUP BY symbol HAVING n > 1').fetchall()
> print('Average-down check:', 'VIOLATION:' + str([r['symbol'] for r in dupes]) if dupes else 'PASS')
> # Rule: stop never widened after entry (check if trailing moved stop lower on a LONG)
> rows = conn.execute('''SELECT symbol, strategy, stop, trailing_stop_price, trailing_active, entry
>     FROM open_positions WHERE paper=0 AND trailing_active=1''').fetchall()
> for r in rows:
>     if r['entry'] and r['stop'] and float(r['stop']) < float(r['entry']) * 0.70:
>         print(f'POSSIBLE WIDE STOP: {r[\"symbol\"]} stop={r[\"stop\"]:.4f} entry={r[\"entry\"]:.4f} (>{30:.0f}% away)')
> print('Stop-widening check: PASS (no clear violations detected)')
> # Check for duplicate symbol trades within 30 min (averaging down pattern)
> recent = conn.execute('''SELECT symbol, ts, action FROM trades WHERE paper=0
>     AND action='BUY' AND datetime(replace(substr(ts,1,19),'T',' ')) >= datetime('now','-7 days')
>     ORDER BY symbol, ts''').fetchall()
> from datetime import datetime
> prev = {}
> for r in recent:
>     ts_str = str(r['ts'])[:19].replace('T',' ')
>     try:
>         ts = datetime.fromisoformat(ts_str)
>         if r['symbol'] in prev:
>             gap = abs((ts - prev[r['symbol']]).total_seconds())
>             if gap < 1800: print(f'POSSIBLE AVG DOWN: {r[\"symbol\"]} bought twice within {int(gap/60)}m')
>         prev[r['symbol']] = ts
>     except: pass
> conn.close()
> " 2>/dev/null
>
> echo ""
> echo "=== INTEGRITY SUBSTRATE (last 7d) ==="
> python3 -c "
> import sqlite3; conn = sqlite3.connect('logs/trades.db'); conn.row_factory = sqlite3.Row
> try:
>     rows = conn.execute('''SELECT tier, COUNT(*) as n FROM trade_integrity
>         WHERE created_at > datetime('now','-7 days') GROUP BY tier ORDER BY n DESC''').fetchall()
>     for r in rows:
>         flag = '  ⚠ CHECK BAYESIAN EXCLUSION' if r['tier'] in ('suspect','quarantined','excluded') else ''
>         print(f'  {r[\"tier\"]}: {r[\"n\"]}{flag}')
>     eq = conn.execute('''SELECT AVG(opportunity_loss_pct) as avg_ol,
>         COUNT(CASE WHEN opportunity_loss_pct > 40 THEN 1 END) as bad
>         FROM exit_evaluations WHERE created_at > datetime('now','-7 days')''').fetchone()
>     if eq: print(f'  Exit quality: avg_opportunity_loss={eq[\"avg_ol\"] or 0:.1f}% bad_exits(>40%)={eq[\"bad\"] or 0}')
> except Exception as e: print(f'  Integrity tables: {e}')
> conn.close()
> " 2>/dev/null

---

### AGENT C — Code, Git & Test Coverage

Prompt:
> You are gathering code and test evidence for a trading bot audit. Run every command below and return all output verbatim. No analysis — raw evidence only.
>
> ```bash
> echo "=== GIT STATE ==="
> git status --short
> git log --oneline -8
> git diff origin/feature/v10-rebuild..HEAD --oneline 2>/dev/null | head -5 || echo "Up to date with origin"
>
> echo ""
> echo "=== FILES CHANGED IN LAST 7 DAYS ==="
> git log --since="7 days ago" --name-only --pretty=format: 2>/dev/null | sort -u | grep '\.py$' | grep -v '^$'
>
> echo ""
> echo "=== HIGH-RISK FILE TOUCH CHECK ==="
> CHANGED=$(git log --since="7 days ago" --name-only --pretty=format: 2>/dev/null | sort -u)
> HIGH_RISK="scanner.py signal_engine.py position_manager.py perps_engine.py risk/economics_gate.py scheduler/v10_runner.py logging_db/trade_logger.py spot_engine.py execution/coinbase_broker.py execution/coinbase_spot_broker.py runtime/spot_momentum.py runtime/crypto_tradeability.py runtime/spot_strategy.py kill_switch.py"
> for f in $HIGH_RISK; do
>   if echo "$CHANGED" | grep -q "$f"; then echo "  TOUCHED: $f"; fi
> done
> echo "(scan complete)"
>
> echo ""
> echo "=== TEST COVERAGE GAPS (changed files with no proof test importing them) ==="
> CHANGED_PY=$(git log --since="7 days ago" --name-only --pretty=format: 2>/dev/null | sort -u | grep '\.py$' | grep -v '^$' | grep -v test_)
> for f in $CHANGED_PY; do
>   base=$(basename "$f" .py)
>   if ! grep -rl "$base" tests/proof/ 2>/dev/null | grep -q .; then
>     echo "  NO COVERAGE: $f"
>   fi
> done
>
> echo ""
> echo "=== PROOF SUITE ==="
> python3 -m pytest tests/proof/ -q --tb=line --no-header -p no:warnings 2>&1 | tail -6
>
> echo ""
> echo "=== VERSION STRING CONSISTENCY ==="
> grep "^## Current Version" CLAUDE.md | head -1
> grep -n "version\|v1[6789]\|v18" scripts/validate.py 2>/dev/null | grep -i "version\|v1[6789]\|v18" | head -3
> grep "^VERSION\|^__version__\|SYSTEM_VERSION" config.py 2>/dev/null | head -2
>
> echo ""
> echo "=== BRITTLE TEST STRING ASSERTIONS (recently changed code) ==="
> grep -rn "assert.*'.*open_spot\|assert.*'.*render_spot\|assert.*'.*_manual_trade\|assert.*'.*render_position" tests/proof/ 2>/dev/null | head -8
>
> echo ""
> echo "=== LIVE CALIBRATION VALUES (read directly from code) ==="
> echo "-- Frame score anchor (spot_momentum.py) --"
> grep "frame_score\s*=\s*float" runtime/spot_momentum.py | head -2
>
> echo "-- Composite/derivative weights --"
> grep "SPOT_SCALP_SCORE_WEIGHT" config.py | head -2
>
> echo "-- Spot maker wait seconds --"
> grep "SPOT_MAKER_WAIT_SECONDS" config.py | head -1
>
> echo "-- Economics gate EV thresholds --"
> grep "_TIER_APLUS_EV\|_TIER_A_EV\|_TIER_B_EV\|_MIN_VOLUME" risk/economics_gate.py | grep "=" | grep -v "#" | head -6
>
> echo "-- Spot deployment + order config --"
> grep "SPOT_MAX_DEPLOYED_PCT\|SPOT_MIN_ORDER_USD\|SPOT_TARGET_R\|SPOT_THESIS_MIN_SCORE\|SPOT_THESIS_MIN_HOLD\|SPOT_MAKER_WAIT" config.py | head -8
>
> echo "-- Kill switch thresholds --"
> grep "_LIVE_PCT\|_PAPER_PCT\|0\.50\|0\.75" kill_switch.py | grep -v "#" | head -4
>
> echo "-- Position sizing caps --"
> grep "_MAX_SINGLE_POSITION_PCT\|STOCKS_MAX_POSITION_PCT\|STOCKS_RISK_PCT\|SPOT_MAX_DEPLOYED" config.py | head -5
>
> echo "-- Scanner volume floor --"
> grep "_MIN_VOLUME_24H_USD" scanner.py | grep "=" | grep -v "#\|log\|debug" | head -2
>
> echo "-- Composite score threshold (v10_runner) --"
> grep "composite.*>=\|_TIER.*FLOOR\|tier.*threshold\|COMPOSITE_THRESHOLD" scheduler/v10_runner.py | grep -v "#" | head -5
> ```

---

### AGENT D — ML, Learning Loop & Spot Regime

Prompt:
> You are gathering ML and learning loop evidence for a trading bot audit. Run every command below and return all output verbatim.
>
> ```bash
> echo "=== ML MODEL FRESHNESS ==="
> python3 -c "
> import os, glob
> from datetime import datetime
> pickles = glob.glob('ml/models/*.pkl') + glob.glob('ml/*.pkl') + glob.glob('ml/models/**/*.pkl', recursive=True)
> if pickles:
>     for p in sorted(pickles, key=os.path.getmtime, reverse=True)[:5]:
>         age = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(p))).days
>         print(f'  {p} ({age}d old)')
> else:
>     print('  NO PICKLE FILES FOUND — all ML scores returning 50.0')
> "
>
> echo ""
> echo "=== TRAINING SAMPLE ADEQUACY ==="
> python3 -c "
> import sqlite3; conn = sqlite3.connect('logs/trades.db'); conn.row_factory = sqlite3.Row
> r = conn.execute('''SELECT COUNT(*) as total,
>     SUM(CASE WHEN source='live_v10' THEN 1 ELSE 0 END) as live,
>     SUM(CASE WHEN source='clean_paper_v10' THEN 1 ELSE 0 END) as clean_paper,
>     MAX(ts) as latest
>     FROM ml_feature_snapshots''').fetchone()
> print(f'Snapshots: total={r[\"total\"]} live={r[\"live\"]} clean_paper={r[\"clean_paper\"]}')
> print(f'Latest snapshot: {r[\"latest\"]}')
> if r['live'] and r['live'] < 30: print('⚠ LIVE TRAINING SAMPLE TOO SMALL (<30 trades): ML may be unreliable')
> conn.close()
> " 2>/dev/null || echo "ml_feature_snapshots table not accessible"
>
> echo ""
> echo "=== ML SCORE DISTRIBUTION (last 7d candidates) ==="
> python3 -c "
> import sqlite3; conn = sqlite3.connect('logs/trades.db'); conn.row_factory = sqlite3.Row
> r = conn.execute('''SELECT COUNT(*) as total,
>     SUM(CASE WHEN CAST(ml_score as REAL) BETWEEN 45 AND 55 THEN 1 ELSE 0 END) as flat,
>     AVG(CAST(ml_score as REAL)) as avg, MIN(CAST(ml_score as REAL)) as mn, MAX(CAST(ml_score as REAL)) as mx
>     FROM scan_candidates WHERE ml_score IS NOT NULL AND ml_score != ''
>     AND datetime(replace(substr(ts,1,19),'T',' ')) >= datetime('now','-7 days')''').fetchone()
> if r and r['total']:
>     flat_pct = (r['flat'] or 0)/r['total']*100
>     print(f'n={r[\"total\"]} avg={r[\"avg\"] or 0:.1f} range=[{r[\"mn\"] or 0:.1f},{r[\"mx\"] or 0:.1f}] flat(45-55)={flat_pct:.0f}%')
>     if flat_pct > 60: print('⚠ ML NOT DISCRIMINATING: >60% of scores in 45-55 band — model may need retraining')
> else: print('No ML score data')
> conn.close()
> " 2>/dev/null
>
> echo ""
> echo "=== ML SCORE vs ACTUAL P&L CORRELATION ==="
> python3 -c "
> import sqlite3, statistics
> conn = sqlite3.connect('logs/trades.db'); conn.row_factory = sqlite3.Row
> rows = conn.execute('''SELECT sc.ml_score, t.pnl_usd FROM scan_candidates sc
>     JOIN trades t ON sc.symbol=t.symbol
>     WHERE t.action IN ('SELL','CLOSE') AND t.pnl_usd IS NOT NULL AND t.paper=0
>     AND sc.ml_score IS NOT NULL AND sc.ml_score != ''
>     AND datetime(replace(substr(t.ts,1,19),'T',' ')) >= datetime('now','-30 days')
>     LIMIT 300''').fetchall()
> if len(rows) >= 15:
>     s = [float(r['ml_score']) for r in rows]; p = [float(r['pnl_usd']) for r in rows]
>     n = len(s); ms=sum(s)/n; mp=sum(p)/n
>     cov = sum((a-ms)*(b-mp) for a,b in zip(s,p))/n
>     ss=statistics.stdev(s); sp=statistics.stdev(p)
>     corr = cov/(ss*sp) if ss and sp else 0
>     interp = 'CONTRIBUTING SIGNAL' if corr > 0.15 else ('ANTICORRELATED — REVIEW' if corr < -0.10 else 'NOISE (near zero)')
>     print(f'Pearson r={corr:.3f} n={n} — {interp}')
> else: print(f'Insufficient data (n={len(rows)}, need >= 15)')
> conn.close()
> " 2>/dev/null
>
> echo ""
> echo "=== CANDIDATE LABELING LAG ==="
> python3 -c "
> import sqlite3; conn = sqlite3.connect('logs/trades.db'); conn.row_factory = sqlite3.Row
> try:
>     r = conn.execute('''SELECT COUNT(*) as total,
>         SUM(CASE WHEN co.hit_1r IS NOT NULL THEN 1 ELSE 0 END) as labeled
>         FROM scan_candidates sc LEFT JOIN candidate_outcomes co ON sc.id=co.candidate_id
>         WHERE datetime(replace(substr(sc.ts,1,19),'T',' ')) < datetime('now','-4 hours')
>         AND datetime(replace(substr(sc.ts,1,19),'T',' ')) >= datetime('now','-7 days')''').fetchone()
>     if r and r['total']:
>         lag = (r['total']-r['labeled'])/r['total']*100
>         print(f'Labeled: {r[\"labeled\"]}/{r[\"total\"]} ({100-lag:.0f}%)')
>         if lag > 20: print(f'⚠ LABELING LAG: {lag:.0f}% unlabeled — Bayesian/ML updates delayed')
>     else: print('No labeling data')
> except Exception as e: print(f'Labeling check: {e}')
> conn.close()
> " 2>/dev/null
>
> echo ""
> echo "=== BAYESIAN SIGNAL WEIGHT DRIFT ==="
> python3 -c "
> import sys; sys.path.insert(0,'.')
> try:
>     from learning.dynamic_weights import get_all_weights
>     weights = get_all_weights() or {}
>     if weights:
>         for sig, w in sorted(weights.items(), key=lambda x: x[1]):
>             flag = '  ← SUPPRESSED (<0.20)' if w < 0.20 else ('  ← DOMINANT (>1.80)' if w > 1.80 else '')
>             print(f'  {sig:<40} {w:.3f}{flag}')
>     else: print('No weights found (using priors)')
> except Exception as e: print(f'Dynamic weights: {e}')
> " 2>/dev/null
>
> echo ""
> echo "=== RBI CHALLENGER STATE ==="
> python3 -c "
> import sqlite3; conn = sqlite3.connect('logs/trades.db'); conn.row_factory = sqlite3.Row
> try:
>     rows = conn.execute('SELECT strategy_id, state, paper_trades, paper_pnl, promoted_at FROM challenger_state ORDER BY created_at DESC LIMIT 10').fetchall()
>     if rows:
>         for r in rows:
>             flag = '  ← AWAITING YOUR DECISION' if r['state'] == 'PROMOTED_PENDING_HUMAN' else ''
>             print(f'  {str(r[\"strategy_id\"]):<35} {r[\"state\"]} trades={r[\"paper_trades\"]} pnl=\${r[\"paper_pnl\"] or 0:.2f}{flag}')
>     else: print('No challengers in RBI system')
> except Exception as e: print(f'RBI: {e}')
> conn.close()
> " 2>/dev/null
>
> echo ""
> echo "=== SPOT REGIME PER SYMBOL ==="
> python3 -c "
> import sys; sys.path.insert(0,'.')
> try:
>     from config import SPOT_SYMBOLS
>     from runtime.spot_regime import get_spot_regime
>     for sym in SPOT_SYMBOLS:
>         try:
>             r = get_spot_regime(sym)
>             print(f'  {sym:<6}: {r}')
>         except Exception as e:
>             print(f'  {sym:<6}: ERROR — {e}')
> except Exception as e: print(f'Spot regime: {e}')
> " 2>/dev/null
>
> echo ""
> echo "=== SPOT SYMBOL COOLDOWNS ==="
> python3 -c "
> import sqlite3; conn = sqlite3.connect('logs/trades.db'); conn.row_factory = sqlite3.Row
> from datetime import datetime, timezone
> now = datetime.now(timezone.utc)
> rows = conn.execute(\"SELECT symbol, cooldown_until FROM open_positions WHERE cooldown_until IS NOT NULL AND cooldown_until != ''\").fetchall()
> for r in rows:
>     try:
>         cd = datetime.fromisoformat(str(r['cooldown_until']).replace('Z','+00:00'))
>         remaining = (cd - now).total_seconds()
>         if remaining > 0: print(f'  {r[\"symbol\"]}: cooldown {remaining/60:.0f}m remaining')
>     except: pass
> conn.close()
> " 2>/dev/null
>
> echo ""
> echo "=== WALK-FORWARD LAST RUN ==="
> python3 -c "
> import sqlite3; conn = sqlite3.connect('logs/trades.db'); conn.row_factory = sqlite3.Row
> r = conn.execute(\"SELECT ts, message FROM system_events WHERE source='walk_forward_trainer' OR message LIKE '%retrain%' ORDER BY ts DESC LIMIT 3\").fetchone()
> print(f'Last retrain event: {r[\"ts\"][:16] if r else \"NONE FOUND\"}')
> conn.close()
> " 2>/dev/null
> ```

---

## PHASE 3 — PREVIOUS AUDIT DELTA

```bash
PREV=$(ls -t audit_results/*.md 2>/dev/null | head -1)
if [ -n "$PREV" ]; then
  echo "Previous audit: $PREV"
  grep "^  [🔴🟠🟡🟢]" "$PREV" 2>/dev/null | head -20
else
  echo "First audit run — no delta available"
fi
```

For any finding that appeared in the previous audit's work queue AND is still present in the current evidence:
- Escalate severity one level: LOW→MEDIUM, MEDIUM→HIGH, HIGH→BLOCK
- Append: `[RECURRING — fix overdue]`

---

## PHASE 4 — AUTO-REMEDIATION

Before producing output, automatically fix these zero-risk items if detected. Show a one-line description of each fix. After any file edit, run `python3 -m pytest tests/proof/ -q --tb=line -p no:warnings 2>&1 | tail -3` to confirm nothing broke.

**Auto-fix without asking:**
1. Version string drift in `scripts/validate.py` — if CLAUDE.md version != what validate.py reports
2. Duplicate stale auto-alert blocks in `brain/01_current_system/Open Questions.md`
3. Missing CHANGELOG entry — if commits exist since last CHANGELOG line (append only)

**Never auto-fix:**
- Any value in `config.py`, `scheduler/`, `risk/`, or the live execution path
- Any DB state (flag ghosts — never delete)
- Anything that could affect a live order

---

## PHASE 5 — SYNTHESIS AND REASONING

Now reason across ALL evidence from Phases 1–4. This is where Claude's intelligence provides the value — not a summary of raw output, but cross-layer analysis.

**For every finding, compute:**

**Dollar impact** — use actual trade data:
- For each high-volume blocker: `N_blocked × historical_WR_at_that_score_band × avg_PnL_per_winner`
- For maker/taker gap: `(taker_pct - target_pct) × avg_order_size × 0.003_extra_fee × daily_fills`
- For labeling lag: `unlabeled_pct × Bayesian_update_frequency_per_day × signal_lift_estimate`

**Severity** — BLOCK / HIGH / MEDIUM / LOW

**Impact type** — SAFETY / REVENUE / COST / ENGINEERING (can be multiple)

**Cross-correlations to find and escalate:**
- Flat ML distribution + low ML-PnL correlation → ML tower contributing nothing → every score-threshold calibration finding doubles in importance
- Multiple high-risk files changed + zero new proof tests on those files → compound risk, escalate each gap
- Same top blocker as previous audit → RECURRING escalation
- Win rate regression >10pp week-over-week + composite score of entries dropping → signal quality degrading, not just variance
- Maker rate <40% + fee drag above budget → combined COST finding with combined dollar estimate
- No model pickle files found → all ML candidates getting exactly 50.0 → ML weight in composite is wasted → REVENUE finding

**Behavioral invariant violations** (from Agent B amygdala checks) → always BLOCK regardless of dollar size. The rules exist because breaking them has historically destroyed accounts.

**For the calibration values read by Agent C:** present them factually. Flag any value that is inconsistent with a documented decision in CLAUDE.md, or where observed behavior (from Agent B) contradicts what the value implies.

---

## PHASE 6 — OUTPUT

Produce the final output in this exact format. Omit any finding that is clean PASS. If the work queue is empty, say so explicitly.

```
╔══════════════════════════════════════════════════════════════╗
║  SELF-AUDIT v[X] · [DATE TIME] · [PAPER|LIVE] MODE          ║
║  Bot: [●ALIVE|○DOWN] · HB: [age] · Positions: N · P&L: $X  ║
╚══════════════════════════════════════════════════════════════╝

■ AUTO-REMEDIATED ([N] items — no action needed)
  [each fix on one line, or "None this run"]

■ WORK QUEUE — [N] items
  [sort order: BLOCK first, then by SAFETY→REVENUE→COST→ENGINEERING, then by dollar impact]

  🔴/🟠/🟡/🟢 [SEVERITY] [IMPACT TYPE(S)]
  [One-line title]
  Evidence: [specific number or query result that proves it]
  Impact:   [dollar estimate or specific risk, derived from actual data]
  Fix:      [exact file:line_range or command to run]
  Verify:   [exact query or test that proves it's fixed on next audit run]

■ LIVE CALIBRATION SNAPSHOT (from code — not from docs)
  [list each key value read from the actual file, no "expected" column — just what's there]

■ METRICS SNAPSHOT
  Funnel 24h:     [N scanned → N threshold → N econ → N entered] ([%] conversion)
  Win rate:       [this week]% vs [last week]% ([delta]pp)
  Maker rate:     [%] ([target >55%])
  Fee drag:       [$X / [N] trades = avg [%] round-trip] ([budget <0.25%])
  ML signal:      r=[correlation] ([CONTRIBUTING|NOISE|ANTICORRELATED])
  Top blocker:    [reason] ([N] kills, [%] of blocks, [recurring or new])
  Spot regimes:   [symbol: regime, ...]
  [⚠ REGRESSION if any metric dropped >20% week-over-week]

■ STORED → audit_results/[YYYY-MM-DD-HH].md · Memory updated with key metrics
```

---

## PHASE 7 — STORE AND PERSIST

```bash
mkdir -p audit_results
AUDIT_FILE="audit_results/$(date +%Y-%m-%d-%H).md"
```

Write the complete output to `$AUDIT_FILE`.

Write a memory entry (type: project) capturing:
- Top blocker and count
- Win rate this week and delta from last week
- Maker fill rate
- ML score correlation
- Any BLOCK or HIGH items (brief description)
- Date of this audit

If any BLOCK-level finding was found, also log it to system_events:
```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from logging_db.trade_logger import log_system_event
log_system_event('self_audit', 'ERROR', 'BLOCK: [describe the finding]')
" 2>/dev/null
```
