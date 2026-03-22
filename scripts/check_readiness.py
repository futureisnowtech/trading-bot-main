#!/usr/bin/env python3
"""
check_readiness.py — Evaluates whether the system is ready to switch from
paper trading to live money. Reads directly from logs/trades.db.

Run manually:
    python3 scripts/check_readiness.py

Or schedule via launchd (see scripts/com.algotrading.readiness.plist).
Sends an email alert when ALL criteria are met for the first time in a day.

Criteria (all must pass):
  1. At least 14 calendar days of paper trading activity
  2. At least 30 completed trades (closed positions)
  3. Overall win rate >= 52% across all completed trades
  4. No system halts (5% daily loss triggers) in the last 7 days
  5. Total paper P&L is positive
  6. No single day had a loss exceeding 4% of starting account
  7. Average P&L per trade >= $0.10 (the system earns something)
"""
import os
import sys
import sqlite3
from datetime import datetime, timedelta

# ── Resolve project root regardless of where script is called from ────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_ROOT  = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJ_ROOT)

DB_PATH = os.path.join(PROJ_ROOT, 'logs', 'trades.db')

# ── Criteria thresholds ───────────────────────────────────────────────────────
MIN_DAYS            = 14      # calendar days with at least 1 signal
MIN_TRADES          = 30      # completed (SELL) trades
MIN_WIN_RATE        = 0.52    # 52% win rate
MAX_DAILY_LOSS_PCT  = 0.04    # no day worse than -4% of account
ACCOUNT_SIZE        = 500.0   # from config (fallback if .env not loaded)
MIN_AVG_PNL         = 0.10    # minimum average P&L per trade ($)
HALT_LOOKBACK_DAYS  = 7       # check for halts in last N days


def _conn():
    if not os.path.exists(DB_PATH):
        return None
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def check_criteria() -> dict:
    conn = _conn()
    if conn is None:
        return {'error': f'Database not found at {DB_PATH}. Run main.py first.'}

    results = {}
    now = datetime.now()

    # ── 1. Days of activity ───────────────────────────────────────────────────
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT substr(ts,1,10) FROM signals WHERE acted_on=1 ORDER BY ts")
    active_days = [r[0] for r in cur.fetchall()]
    if active_days:
        first_day = datetime.strptime(active_days[0], '%Y-%m-%d')
        days_elapsed = (now - first_day).days
    else:
        days_elapsed = 0
    results['days_trading'] = {
        'value': days_elapsed,
        'target': MIN_DAYS,
        'pass': days_elapsed >= MIN_DAYS,
        'label': f'{days_elapsed} calendar days (need {MIN_DAYS})',
    }

    # ── 2. Number of completed trades ─────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM trades WHERE paper=1 AND action='SELL'")
    trade_count = cur.fetchone()[0] or 0
    results['trade_count'] = {
        'value': trade_count,
        'target': MIN_TRADES,
        'pass': trade_count >= MIN_TRADES,
        'label': f'{trade_count} completed trades (need {MIN_TRADES})',
    }

    # ── 3. Win rate ───────────────────────────────────────────────────────────
    cur.execute("SELECT pnl_usd FROM trades WHERE paper=1 AND action='SELL'")
    pnls = [r[0] for r in cur.fetchall()]
    if pnls:
        wins = sum(1 for p in pnls if p > 0)
        win_rate = wins / len(pnls)
    else:
        win_rate = 0.0
    results['win_rate'] = {
        'value': win_rate,
        'target': MIN_WIN_RATE,
        'pass': win_rate >= MIN_WIN_RATE,
        'label': f'{win_rate:.1%} win rate (need {MIN_WIN_RATE:.0%})',
    }

    # ── 4. No system halts in last 7 days ─────────────────────────────────────
    cutoff = (now - timedelta(days=HALT_LOOKBACK_DAYS)).strftime('%Y-%m-%d')
    cur.execute(
        "SELECT COUNT(*) FROM system_events WHERE level='ERROR' AND message LIKE '%halt%' AND ts >= ?",
        (cutoff,))
    halt_count = cur.fetchone()[0] or 0
    results['no_recent_halts'] = {
        'value': halt_count,
        'target': 0,
        'pass': halt_count == 0,
        'label': f'{halt_count} halt events in last {HALT_LOOKBACK_DAYS} days (need 0)',
    }

    # ── 5. Positive total P&L ─────────────────────────────────────────────────
    total_pnl = sum(pnls) if pnls else 0.0
    results['positive_pnl'] = {
        'value': total_pnl,
        'target': 0,
        'pass': total_pnl > 0,
        'label': f'Total P&L: ${total_pnl:.2f} (must be positive)',
    }

    # ── 6. No single day worse than -4% of account ────────────────────────────
    cur.execute("""SELECT substr(ts,1,10) as day, SUM(pnl_usd) as daily_pnl
                   FROM trades WHERE paper=1 AND action='SELL'
                   GROUP BY day""")
    daily = {r[0]: r[1] for r in cur.fetchall()}
    max_loss_pct = 0.0
    worst_day = None
    for day, dpnl in daily.items():
        pct = abs(dpnl) / ACCOUNT_SIZE if dpnl < 0 else 0
        if pct > max_loss_pct:
            max_loss_pct = pct
            worst_day = day
    results['max_daily_loss'] = {
        'value': max_loss_pct,
        'target': MAX_DAILY_LOSS_PCT,
        'pass': max_loss_pct < MAX_DAILY_LOSS_PCT,
        'label': (
            f'Worst day: {max_loss_pct:.1%} loss'
            + (f' on {worst_day}' if worst_day else '')
            + f' (limit {MAX_DAILY_LOSS_PCT:.0%})'
        ),
    }

    # ── 7. Average P&L per trade ──────────────────────────────────────────────
    avg_pnl = (sum(pnls) / len(pnls)) if pnls else 0.0
    results['avg_pnl'] = {
        'value': avg_pnl,
        'target': MIN_AVG_PNL,
        'pass': avg_pnl >= MIN_AVG_PNL,
        'label': f'Avg P&L per trade: ${avg_pnl:.2f} (need ${MIN_AVG_PNL:.2f})',
    }

    conn.close()
    return results


def print_report(results: dict) -> bool:
    """Print the readiness report. Returns True if ALL criteria pass."""
    if 'error' in results:
        print(f'\n  ERROR: {results["error"]}\n')
        return False

    print('\n' + '='*60)
    print('  PAPER → LIVE READINESS REPORT')
    print(f'  Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('='*60)

    all_pass = True
    for key, r in results.items():
        icon = '✅' if r['pass'] else '❌'
        print(f'  {icon}  {r["label"]}')
        if not r['pass']:
            all_pass = False

    print('='*60)
    if all_pass:
        print('  🏆 ALL CRITERIA MET — System is ready for live trading!')
        print('  Next step: python3 main.py --mode live')
    else:
        failed = sum(1 for r in results.values() if not r['pass'])
        print(f'  ⏳ {failed} criteria not yet met. Keep paper trading.')
    print('='*60 + '\n')
    return all_pass


def send_ready_alert():
    """Fire an email alert when all criteria are met."""
    try:
        sys.path.insert(0, PROJ_ROOT)
        from alerts.telegram_alert import alert_system
        alert_system('READY',
            '🏆 PAPER TRADING CRITERIA MET!\n\n'
            'The system has passed all readiness checks.\n'
            'Run: python3 main.py --mode live\n\n'
            'Review the full report:\n'
            'python3 scripts/check_readiness.py')
        print('  📧 Alert sent.')
    except Exception as e:
        print(f'  ⚠️  Could not send alert: {e}')


def main():
    results = check_criteria()
    ready   = print_report(results)

    # Only send alert if we crossed the threshold today
    if ready:
        flag_path = os.path.join(PROJ_ROOT, 'logs', '.readiness_alert_sent')
        today_str = datetime.now().strftime('%Y-%m-%d')
        already_sent = False
        if os.path.exists(flag_path):
            with open(flag_path) as f:
                already_sent = f.read().strip() == today_str
        if not already_sent:
            send_ready_alert()
            os.makedirs(os.path.join(PROJ_ROOT, 'logs'), exist_ok=True)
            with open(flag_path, 'w') as f:
                f.write(today_str)


if __name__ == '__main__':
    main()
