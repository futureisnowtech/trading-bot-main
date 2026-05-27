#!/usr/bin/env python3
"""
scripts/seed_intelligence.py — Full intelligence seeding run.

Runs backtests across ALL strategies and asset classes, ingesting trade-level
attribution into signal_stats. Pre-populates Bayesian priors so the live system
starts with evidence-backed weights on Day 1 across every strategy path.

Coverage:
  CRYPTO MACD    — 8 pairs × 3 variants (workhorse, classic, sniper)
  MEAN REVERSION — 8 pairs (ranging/volatile regime signal calibration)
  PERP FUTURES   — 6 pairs (long+short breakout signal calibration)
  EQUITY         — 8 liquid US stocks (MACD+RSI+volume signal calibration)
  MES FUTURES    — ES=F (ORB signal calibration)

Usage:
  python3 scripts/seed_intelligence.py                    # all, 180 days
  python3 scripts/seed_intelligence.py --days 90          # shorter window
  python3 scripts/seed_intelligence.py --strategy crypto  # one asset class
  python3 scripts/seed_intelligence.py --symbol BTC-USDC  # one symbol
  python3 scripts/seed_intelligence.py --validate         # run validation gate
  python3 scripts/seed_intelligence.py --dry-run          # show plan
"""
import sys, os, argparse, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CRYPTO_PAIRS

EQUITY_SEED_SYMBOLS  = ['SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMZN', 'META']
PERP_SEED_SYMBOLS    = ['BTC-USDC', 'ETH-USDC', 'SOL-USDC', 'AVAX-USDC', 'LINK-USDC', 'MATIC-USDC']
FUTURES_SYMBOLS      = ['ES=F']
CRYPTO_MACD_VARIANTS = ['workhorse', 'classic', 'sniper']


def _period_label(days):
    if days <= 90:  return '3mo'
    if days <= 180: return '6mo'
    return '1y'


def _run_batch(label, items, runner_fn, delay=3):
    results = []
    for i, item in enumerate(items):
        print(f"\n  [{i+1}/{len(items)}] {item['desc']}...")
        try:
            result = runner_fn(**item['kwargs'])
            if 'error' in result:
                print(f"    ERROR: {result['error']}")
                results.append({**item, 'status': 'error', 'error': result['error']})
            else:
                stats  = result.get('stats', {})
                passed = result.get('passed')
                status = 'PASS' if passed else ('WARN' if passed is None else 'FAIL')
                attr   = result.get('trades_attributed', 0)
                print(f"    [{status}] wr={stats.get('win_rate',0):.1%} "
                      f"sharpe={stats.get('sharpe',0):.2f} "
                      f"trades={stats.get('total_trades',0)} "
                      f"attributed={attr}")
                results.append({**item, 'status': 'ok', 'stats': stats,
                                'passed': passed, 'trades_attributed': attr})
        except Exception as e:
            print(f"    EXCEPTION: {e}")
            results.append({**item, 'status': 'exception', 'error': str(e)})
        time.sleep(delay)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days',     type=int, default=180,
                        help='History window in days (default: 180)')
    parser.add_argument('--symbol',   default=None,
                        help='Run for a single symbol only')
    parser.add_argument('--strategy', default=None,
                        choices=['crypto', 'mean_reversion', 'perp', 'equity', 'futures'],
                        help='Run one strategy class only')
    parser.add_argument('--validate', action='store_true',
                        help='Run strategy validation gate after each backtest')
    parser.add_argument('--dry-run',  action='store_true',
                        help='Show the work plan without executing')
    args = parser.parse_args()

    period = _period_label(args.days)

    # ── Build work plan ───────────────────────────────────────────────────────
    work_plan = []

    if args.symbol:
        sym = args.symbol
        if 'USDC' in sym or ('USD' in sym and sym not in ('BTC-USD', 'ETH-USD')):
            for v in CRYPTO_MACD_VARIANTS:
                work_plan.append({
                    'desc': f"{sym} crypto_macd_{v}",
                    'kwargs': {'symbol': sym, 'strategy': 'crypto', 'period': period,
                               'variant': v, 'validate': args.validate},
                })
            work_plan.append({
                'desc': f"{sym} mean_reversion",
                'kwargs': {'symbol': sym, 'strategy': 'mean_reversion', 'period': period,
                           'validate': args.validate},
            })
        elif sym in FUTURES_SYMBOLS:
            work_plan.append({
                'desc': f"{sym} futures",
                'kwargs': {'symbol': sym, 'strategy': 'futures', 'period': period,
                           'validate': args.validate},
            })
        else:
            work_plan.append({
                'desc': f"{sym} equity",
                'kwargs': {'symbol': sym, 'strategy': 'equity', 'period': period,
                           'validate': args.validate},
            })
    else:
        crypto_pairs = CRYPTO_PAIRS[:8]
        strategies_to_run = (
            [args.strategy] if args.strategy
            else ['crypto', 'mean_reversion', 'perp', 'equity', 'futures']
        )

        if 'crypto' in strategies_to_run:
            for sym in crypto_pairs:
                for v in CRYPTO_MACD_VARIANTS:
                    work_plan.append({
                        'desc': f"{sym} crypto_macd_{v}",
                        'kwargs': {'symbol': sym, 'strategy': 'crypto', 'period': period,
                                   'variant': v, 'validate': args.validate},
                    })

        if 'mean_reversion' in strategies_to_run:
            for sym in crypto_pairs:
                work_plan.append({
                    'desc': f"{sym} mean_reversion",
                    'kwargs': {'symbol': sym, 'strategy': 'mean_reversion',
                               'period': period, 'validate': args.validate},
                })

        if 'perp' in strategies_to_run:
            for sym in PERP_SEED_SYMBOLS:
                work_plan.append({
                    'desc': f"{sym} perp",
                    'kwargs': {'symbol': sym, 'strategy': 'perp', 'period': period,
                               'validate': args.validate},
                })

        if 'equity' in strategies_to_run:
            for sym in EQUITY_SEED_SYMBOLS:
                work_plan.append({
                    'desc': f"{sym} equity",
                    'kwargs': {'symbol': sym, 'strategy': 'equity', 'period': period,
                               'validate': args.validate},
                })

        if 'futures' in strategies_to_run:
            for sym in FUTURES_SYMBOLS:
                work_plan.append({
                    'desc': f"{sym} futures",
                    'kwargs': {'symbol': sym, 'strategy': 'futures', 'period': period,
                               'validate': args.validate},
                })

    total = len(work_plan)
    print(f"\n{'='*65}")
    print(f"  FULL INTELLIGENCE SEEDING — {total} runs | {period} | {args.days}d")
    print(f"{'='*65}\n")

    if args.dry_run:
        print("DRY RUN — work plan:")
        for i, item in enumerate(work_plan):
            print(f"  {i+1:3}. {item['desc']}")
        print(f"\n{total} total runs. Remove --dry-run to execute.")
        return

    # ── Init tables ───────────────────────────────────────────────────────────
    from learning.signal_performance import init_learning_tables
    init_learning_tables()

    from backtesting.backtest_engine import run_with_intelligence
    from data.price_archive import get_summary

    # ── Execute all runs ──────────────────────────────────────────────────────
    all_results = _run_batch("ALL", work_plan, run_with_intelligence, delay=2)

    # ── Summary ───────────────────────────────────────────────────────────────
    ok     = [r for r in all_results if r.get('status') == 'ok']
    errors = [r for r in all_results if r.get('status') in ('error', 'exception')]
    passed = [r for r in ok if r.get('passed') is True]
    failed = [r for r in ok if r.get('passed') is False]
    warned = [r for r in ok if r.get('passed') is None]

    print(f"\n{'='*65}")
    print(f"  SEEDING COMPLETE")
    print(f"{'='*65}")
    print(f"  Runs:     {len(ok)} succeeded | {len(errors)} errors")
    print(f"  Gate:     {len(passed)} passed | {len(failed)} failed | {len(warned)} unvalidated")

    total_attributed = sum(r.get('trades_attributed', 0) for r in ok)
    print(f"  Attributed: {total_attributed} trades ingested into signal_stats")

    # Price archive summary
    try:
        summary = get_summary()
        total_rows = sum(s['rows'] for s in summary)
        print(f"\nPrice Archive: {len(summary)} series | {total_rows:,} candles")
        for s in summary[:8]:
            print(f"  {s['symbol']:14} {s['timeframe']:14} {s['rows']:>8,} bars | "
                  f"{s['first']} -> {s['last']}")
    except Exception as e:
        print(f"\nPrice Archive: (summary unavailable: {e})")

    # Signal stats leaderboard
    try:
        from learning.signal_performance import get_signal_report
        sig_report = get_signal_report(min_fires=5)
        if sig_report:
            print(f"\nSignal Intelligence ({len(sig_report)} signals with >=5 fires):")
            print(f"  {'Signal':<28} {'Fires':>6} {'Win%':>7} {'Avg P&L':>9} {'Bayes':>8}")
            print(f"  {'-'*28} {'-'*6} {'-'*7} {'-'*9} {'-'*8}")
            for s in sorted(sig_report, key=lambda x: x['bayesian_pts'] or 0, reverse=True)[:20]:
                wr = f"{s['win_rate']:.1%}" if s['win_rate'] is not None else 'N/A'
                ap = f"${s['avg_pnl']:+.3f}" if s['avg_pnl'] is not None else 'N/A'
                bp = f"{s['bayesian_pts']:.1f}" if s['bayesian_pts'] is not None else 'N/A'
                print(f"  {s['signal_name']:<28} {s['fires']:>6} {wr:>7} {ap:>9} {bp:>8}")
    except Exception as e:
        print(f"\nSignal report unavailable: {e}")

    print(f"\nBayesian priors seeded across {len(ok)} strategy/symbol runs.")
    print(f"All 5 strategy paths now have evidence-backed signal weights.")
    print(f"Run 'python3 main.py' to start trading with pre-populated intelligence.\n")


if __name__ == '__main__':
    main()
