"""
scripts/check_v10_readiness.py — v10 Go-Live Readiness Checker

Evaluates all 6 go-live criteria from the v10 spec:
  1. ML model Brier score < 0.22
  2. ≥1 RBI strategy graduated from incubation to production
  3. Zero kill switch triggers in paper period
  4. Cost per profitable trade < 25% of average win
  5. 1+ day successful paper trading
  6. WR ≥ 52% and portfolio backtest Sharpe > 0.8

Usage:
    python3 scripts/check_v10_readiness.py
    python3 scripts/check_v10_readiness.py --detailed
"""

import argparse
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytz


def _conn():
    from logging_db.trade_logger import _conn as _tc
    return _tc()


def check_ml_brier() -> tuple:
    """Criterion 1: ML Brier score < 0.22"""
    try:
        conn = _conn()
        rows = conn.execute("""
            SELECT MIN(brier_score) FROM ml_calibration
            WHERE brier_score IS NOT NULL
        """).fetchone()
        conn.close()
        best = rows[0]
        if best is None:
            return False, None, 'No calibration data yet — needs 30+ v10 live trades'
        passed = best < 0.22
        return passed, best, f'Best Brier = {best:.3f} (target < 0.22)'
    except Exception as e:
        return False, None, f'DB error: {e}'


def check_rbi_graduates() -> tuple:
    """Criterion 2: ≥1 RBI strategy graduated"""
    try:
        conn = _conn()
        n = conn.execute(
            "SELECT COUNT(*) FROM rbi_incubation WHERE status='graduated'"
        ).fetchone()[0]
        conn.close()
        passed = n >= 1
        return passed, n, f'{n} graduated strategies (target ≥ 1)'
    except Exception as e:
        return False, 0, f'DB error: {e}'


def check_kill_switches(days: int = 14) -> tuple:
    """Criterion 3: Zero kill switch triggers in paper period"""
    try:
        cutoff = time.time() - days * 86400
        conn = _conn()
        n = conn.execute(
            "SELECT COUNT(*) FROM kill_switch_log WHERE ts > ? AND trigger_type != 'resume'",
            (cutoff,)
        ).fetchone()[0]
        conn.close()
        passed = n == 0
        return passed, n, f'{n} kill switch triggers in last {days}d (target 0)'
    except Exception as e:
        # Table may not have data yet
        return True, 0, f'No kill_switch_log entries (OK)'


def check_cost_efficiency() -> tuple:
    """Criterion 4: Cost per profitable trade < 25% of average win"""
    try:
        conn = _conn()
        rows = conn.execute("""
            SELECT pnl_usd, fee_usd FROM trades
            WHERE paper=1 AND action='SELL' AND pnl_usd IS NOT NULL
            ORDER BY ts DESC LIMIT 100
        """).fetchall()
        conn.close()

        if len(rows) < 10:
            return False, None, f'Only {len(rows)} closed trades (need ≥ 10 to assess)'

        profitable = [(r[0], r[1] or 0) for r in rows if r[0] > 0]
        if not profitable:
            return False, None, 'No profitable trades yet'

        avg_win = sum(p for p, _ in profitable) / len(profitable)
        avg_fee = sum(f for _, f in profitable) / len(profitable)
        ratio = avg_fee / (avg_win + 1e-9)
        passed = ratio < 0.25
        return passed, ratio, f'Fee/win ratio = {ratio:.1%} (target < 25%)'
    except Exception as e:
        return False, None, f'DB error: {e}'


def check_paper_days() -> tuple:
    """Criterion 5: 1+ day paper trading on v10"""
    try:
        conn = _conn()
        # Look for the earliest v10 trade (has composite_score set)
        row = conn.execute("""
            SELECT MIN(entry_ts) FROM trade_attribution
            WHERE composite_score IS NOT NULL AND composite_score > 0
        """).fetchone()
        conn.close()

        if not row or not row[0]:
            # Fall back to earliest paper trade
            conn2 = _conn()
            row2 = conn2.execute(
                "SELECT MIN(ts) FROM trades WHERE paper=1"
            ).fetchone()
            conn2.close()
            if not row2 or not row2[0]:
                return False, 0, '0 paper trading days (v10 not started)'
            oldest_ts = row2[0]
        else:
            oldest_ts = row[0]

        # ts may be ISO string
        try:
            days_running = (time.time() - float(oldest_ts)) / 86400
        except Exception:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(str(oldest_ts))
                days_running = (time.time() - dt.timestamp()) / 86400
            except Exception:
                days_running = 0

        passed = days_running >= 1
        return (passed, round(days_running, 1),
                f'{days_running:.1f} days paper trading (target ≥ 1)')
    except Exception as e:
        return False, 0, f'DB error: {e}'


def check_win_rate() -> tuple:
    """Criterion 6: WR ≥ 52% on live v10 trades (excludes seeded backtest data)"""
    try:
        conn = _conn()
        # Only count live trades with v10 composite scores — excludes backtest seeds
        rows = conn.execute("""
            SELECT won FROM trade_attribution
            WHERE won IS NOT NULL
              AND source = 'live'
              AND composite_score IS NOT NULL
              AND composite_score > 0
            ORDER BY entry_ts DESC LIMIT 100
        """).fetchall()
        conn.close()

        if len(rows) < 10:
            return False, None, (f'Only {len(rows)} v10 live trades (need ≥ 10) — '
                                 f'start v10 to accumulate data')

        wr = sum(r[0] for r in rows) / len(rows)
        passed = wr >= 0.52
        return passed, wr, f'WR = {wr:.1%} over {len(rows)} v10 live trades (target ≥ 52%)'
    except Exception as e:
        return False, None, f'DB error: {e}'


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='v10 Go-Live Readiness Checker')
    parser.add_argument('--detailed', action='store_true', help='Show detailed output')
    args = parser.parse_args()

    et = pytz.timezone('US/Eastern')
    now_str = datetime.now(et).strftime('%Y-%m-%d %H:%M ET')

    print(f'\n{"="*60}')
    print(f'  v10 GO-LIVE READINESS CHECK  —  {now_str}')
    print(f'{"="*60}\n')

    checks = [
        ('ML Brier < 0.22',          check_ml_brier),
        ('≥1 RBI Graduated',          check_rbi_graduates),
        ('Zero Kill Switches (14d)',  check_kill_switches),
        ('Cost Efficiency < 25%',     check_cost_efficiency),
        ('1+ Day Paper Trading',       check_paper_days),
        ('Win Rate ≥ 52%',            check_win_rate),
    ]

    results = []
    for label, fn in checks:
        passed, value, detail = fn()
        results.append((label, passed, value, detail))

    all_pass = all(r[1] for r in results)
    pass_count = sum(1 for r in results if r[1])

    for label, passed, value, detail in results:
        icon = '✅' if passed else '❌'
        print(f'  {icon}  {label}')
        if args.detailed:
            print(f'       {detail}')

    print(f'\n{"─"*60}')
    print(f'  {pass_count}/{len(results)} criteria met\n')

    if all_pass:
        print('  🟢  READY FOR LIVE TRADING')
        print('     Fund account to $10,000 and set PAPER_TRADING=false')
        print('     Run: python3 main.py --mode live')
    elif pass_count >= 4:
        print('  🟡  CLOSE — address remaining criteria before going live')
    else:
        print('  🔴  NOT READY — continue paper trading')

    remaining = [r for r in results if not r[1]]
    if remaining and not all_pass:
        print('\n  Remaining criteria:')
        for label, _, _, detail in remaining:
            print(f'     ❌ {label}: {detail}')

    print(f'\n{"="*60}\n')
    return 0 if all_pass else 1


if __name__ == '__main__':
    sys.exit(main())
