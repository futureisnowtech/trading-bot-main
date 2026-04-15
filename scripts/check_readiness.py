#!/usr/bin/env python3
"""
check_readiness.py — Evaluates whether the system is ready to switch from
paper trading to live money. Reads directly from logs/trades.db.

Run manually:
    python3 scripts/check_readiness.py               # Standard (21 days / 50 trades)
    python3 scripts/check_readiness.py --fast-track  # After historical validation (3 days / 20 trades)

Or schedule via launchd (see scripts/com.algotrading.readiness.plist).
Sends an alert when ALL criteria are met for the first time in a day.

Standard criteria (all 8 must pass):
  1. At least 21 calendar days of paper trading activity
  2. At least 50 completed trades (closed positions per active market)
  3. Overall win rate >= 52% across all completed trades
  4. Profit factor >= 1.4 (gross wins / gross losses)
  5. No single day had a loss exceeding 3.5% of starting account
  6. No system crashes in the last 7 days
  7. Total paper P&L is positive
  8. Average P&L per trade >= $0.10 (the system earns something)

Fast-track criteria (--fast-track flag, after historical validation passes):
  1. At least 3 calendar days of paper trading activity
  2. At least 20 completed trades
  3. Win rate >= 48%
  4. Profit factor >= 1.2
  5. No single day worse than 5% of account
  6. No system crashes in last 2 days
  7. Positive total paper P&L
  8. Historical validation report exists and passed
"""
import os
import sys
import argparse
import sqlite3
from datetime import datetime, timedelta

# ── Resolve project root regardless of where script is called from ────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_ROOT  = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJ_ROOT)

DB_PATH = os.path.join(PROJ_ROOT, 'logs', 'trades.db')

# ── Criteria thresholds — Standard ───────────────────────────────────────────
MIN_DAYS            = 21      # calendar days with at least 1 signal
MIN_TRADES          = 50      # completed (SELL) trades
MIN_WIN_RATE        = 0.52    # 52% win rate
MIN_PROFIT_FACTOR   = 1.4     # gross wins / gross losses
MAX_DAILY_LOSS_PCT  = 0.035   # no day worse than -3.5% of account
try:
    from config import ACCOUNT_SIZE
except Exception:
    ACCOUNT_SIZE = 500.0   # fallback if config not loadable
MIN_AVG_PNL         = 0.10    # minimum average P&L per trade ($)
CRASH_LOOKBACK_DAYS = 7       # check for crashes/halts in last N days

# ── Criteria thresholds — Fast-track (after historical validation) ────────────
FT_MIN_DAYS           = 3
FT_MIN_TRADES         = 20
FT_MIN_WIN_RATE       = 0.48
FT_MIN_PROFIT_FACTOR  = 1.2
FT_MAX_DAILY_LOSS_PCT = 0.05
FT_MIN_AVG_PNL        = 0.05
FT_CRASH_LOOKBACK     = 2

VALIDATION_REPORT = os.path.join(PROJ_ROOT, 'logs', 'validation_report.txt')


def _conn():
    if not os.path.exists(DB_PATH):
        return None
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def check_historical_validation() -> dict:
    """Check if logs/validation_report.txt exists and contains a PASS."""
    if not os.path.exists(VALIDATION_REPORT):
        return {
            'value': False,
            'target': True,
            'pass': False,
            'label': 'Historical validation: NOT RUN (run: python3 scripts/rapid_validate.py --no-ai)',
        }
    with open(VALIDATION_REPORT) as f:
        content = f.read()
    passed = 'OVERALL: ✅ PASS' in content or 'OVERALL: PASS' in content
    return {
        'value': passed,
        'target': True,
        'pass': passed,
        'label': f'Historical validation: {"✅ PASSED" if passed else "❌ FAILED — re-run rapid_validate.py"}',
    }


def check_criteria(fast_track: bool = False) -> dict:
    min_days         = FT_MIN_DAYS           if fast_track else MIN_DAYS
    min_trades       = FT_MIN_TRADES         if fast_track else MIN_TRADES
    min_win_rate     = FT_MIN_WIN_RATE       if fast_track else MIN_WIN_RATE
    min_pf           = FT_MIN_PROFIT_FACTOR  if fast_track else MIN_PROFIT_FACTOR
    max_daily_loss   = FT_MAX_DAILY_LOSS_PCT if fast_track else MAX_DAILY_LOSS_PCT
    min_avg_pnl      = FT_MIN_AVG_PNL        if fast_track else MIN_AVG_PNL
    crash_lookback   = FT_CRASH_LOOKBACK     if fast_track else CRASH_LOOKBACK_DAYS

    conn = _conn()
    if conn is None:
        return {'error': f'Database not found at {DB_PATH}. Run main.py first.'}

    results = {}
    now = datetime.now()
    cur = conn.cursor()

    # ── Fast-track only: historical validation ────────────────────────────────
    if fast_track:
        results['historical_validation'] = check_historical_validation()

    # ── 1. Days of activity ───────────────────────────────────────────────────
    cur.execute("SELECT DISTINCT substr(ts,1,10) FROM signals WHERE acted_on=1 ORDER BY ts")
    active_days = [r[0] for r in cur.fetchall()]
    if active_days:
        first_day    = datetime.strptime(active_days[0], '%Y-%m-%d')
        days_elapsed = (now - first_day).days
    else:
        days_elapsed = 0
    results['days_trading'] = {
        'value':  days_elapsed,
        'target': min_days,
        'pass':   days_elapsed >= min_days,
        'label':  f'{days_elapsed} calendar days (need {min_days})',
    }

    # ── 2. Number of completed trades ─────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM trades WHERE paper=1 AND pnl_usd != 0")
    trade_count = cur.fetchone()[0] or 0
    results['trade_count'] = {
        'value':  trade_count,
        'target': min_trades,
        'pass':   trade_count >= min_trades,
        'label':  f'{trade_count} completed trades (need {min_trades})',
    }

    # ── 3. Win rate ───────────────────────────────────────────────────────────
    cur.execute("SELECT pnl_usd FROM trades WHERE paper=1 AND pnl_usd != 0")
    pnls = [r[0] for r in cur.fetchall()]
    if pnls:
        wins     = sum(1 for p in pnls if p > 0)
        win_rate = wins / len(pnls)
    else:
        win_rate = 0.0
    results['win_rate'] = {
        'value':  win_rate,
        'target': min_win_rate,
        'pass':   win_rate >= min_win_rate,
        'label':  f'{win_rate:.1%} win rate (need {min_win_rate:.0%})',
    }

    # ── 4. Profit factor ──────────────────────────────────────────────────────
    gross_wins   = sum(p for p in pnls if p > 0)
    gross_losses = abs(sum(p for p in pnls if p < 0))
    pf = (gross_wins / gross_losses) if gross_losses > 0 else (1.0 if not pnls else 99.0)
    results['profit_factor'] = {
        'value':  round(pf, 2),
        'target': min_pf,
        'pass':   pf >= min_pf,
        'label':  f'Profit factor: {pf:.2f} (need {min_pf:.1f})',
    }

    # ── 5. No single day worse than max_daily_loss of account ─────────────────
    cur.execute("""SELECT substr(ts,1,10) as day, SUM(pnl_usd) as daily_pnl
                   FROM trades WHERE paper=1 AND pnl_usd != 0
                   GROUP BY day""")
    daily = {r[0]: r[1] for r in cur.fetchall()}
    max_loss_pct = 0.0
    worst_day    = None
    for day, dpnl in daily.items():
        pct = abs(dpnl) / ACCOUNT_SIZE if dpnl < 0 else 0
        if pct > max_loss_pct:
            max_loss_pct = pct
            worst_day    = day
    results['max_daily_loss'] = {
        'value':  max_loss_pct,
        'target': max_daily_loss,
        'pass':   max_loss_pct < max_daily_loss,
        'label':  (
            f'Worst day: {max_loss_pct:.1%} loss'
            + (f' on {worst_day}' if worst_day else '')
            + f' (limit {max_daily_loss:.1%})'
        ),
    }

    # ── 6. No crashes or halts in last N days ─────────────────────────────────
    cutoff = (now - timedelta(days=crash_lookback)).strftime('%Y-%m-%d')
    cur.execute(
        "SELECT COUNT(*) FROM system_events "
        "WHERE level='ERROR' AND (message LIKE '%halt%' OR message LIKE '%crash%' OR message LIKE '%HALT%') "
        "AND ts >= ?",
        (cutoff,),
    )
    crash_count = cur.fetchone()[0] or 0
    results['no_crashes'] = {
        'value':  crash_count,
        'target': 0,
        'pass':   crash_count == 0,
        'label':  f'{crash_count} halt/crash events in last {crash_lookback} days (need 0)',
    }

    # ── 7. Positive total P&L ─────────────────────────────────────────────────
    total_pnl = sum(pnls) if pnls else 0.0
    results['positive_pnl'] = {
        'value':  total_pnl,
        'target': 0,
        'pass':   total_pnl > 0,
        'label':  f'Total P&L: ${total_pnl:.2f} (must be positive)',
    }

    # ── 8. Average P&L per trade ──────────────────────────────────────────────
    avg_pnl = (sum(pnls) / len(pnls)) if pnls else 0.0
    results['avg_pnl'] = {
        'value':  avg_pnl,
        'target': min_avg_pnl,
        'pass':   avg_pnl >= min_avg_pnl,
        'label':  f'Avg P&L per trade: ${avg_pnl:.2f} (need ${min_avg_pnl:.2f})',
    }

    conn.close()
    return results


def print_report(results: dict, fast_track: bool = False) -> bool:
    """Print the readiness report. Returns True if ALL criteria pass."""
    if 'error' in results:
        print(f'\n  ERROR: {results["error"]}\n')
        return False

    mode_label = '⚡ FAST-TRACK' if fast_track else 'STANDARD'
    print('\n' + '='*60)
    print(f'  PAPER → LIVE READINESS REPORT  [{mode_label}]')
    print(f'  Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    if fast_track:
        print('  Mode: Historical validation passed → relaxed thresholds')
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
        print('  Next step: python3 scripts/go_live.py')
    else:
        failed = sum(1 for r in results.values() if not r['pass'])
        print(f'  ⏳ {failed} criteria not yet met. Keep paper trading.')
    print('='*60 + '\n')
    return all_pass


def send_ready_alert():
    """Write a readiness notification to the dashboard via notification_engine."""
    try:
        from notifications.notification_engine import NotificationEngine
        ne = NotificationEngine()
        ne.notify_system(
            title='PAPER TRADING CRITERIA MET',
            detail='All readiness checks passed. Run: python3 scripts/go_live.py',
        )
        print('  ✅ Readiness notification written to dashboard.')
    except Exception as e:
        print(f'  ⚠️  Could not write notification: {e}')


def main():
    parser = argparse.ArgumentParser(description='Paper → Live readiness check')
    parser.add_argument('--fast-track', action='store_true',
                        help='Use relaxed thresholds after historical validation passes')
    args = parser.parse_args()

    results = check_criteria(fast_track=args.fast_track)
    ready   = print_report(results, fast_track=args.fast_track)

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
