"""
backtesting/run.py — CLI entry point for running backtests.

Replaces run_backtest.py as the canonical operator surface.
Results are written to backtest_results table (RESEARCH-GRADE).

Usage:
    python3 backtesting/run.py --mode candidate_replay --strategy v10_default
    python3 backtesting/run.py --mode candidate_replay --symbol BTCUSDT --days 14
    python3 backtesting/run.py --promote  # evaluate all runs for promotion
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main():
    parser = argparse.ArgumentParser(description="Algo Trading Backtester (v18.16)")
    parser.add_argument(
        "--mode",
        default="candidate_replay",
        choices=["candidate_replay", "historical", "stress"],
        help="Backtest mode (default: candidate_replay)",
    )
    parser.add_argument(
        "--strategy",
        default="v10_default",
        help="Strategy name tag for results (default: v10_default)",
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="Specific symbol to backtest (default: all)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Days of candidate history to replay (default: 30)",
    )
    parser.add_argument(
        "--notes",
        default=None,
        help="Optional notes to attach to this backtest run",
    )
    parser.add_argument(
        "--promote",
        action="store_true",
        help="Run promotion engine after backtest",
    )
    parser.add_argument(
        "--promote-only",
        action="store_true",
        help="Skip backtest, only run promotion engine evaluation",
    )
    args = parser.parse_args()

    if not args.promote_only:
        from backtesting.event_backtester import EventBacktester

        print(
            f"[backtester] mode={args.mode} strategy={args.strategy} "
            f"symbol={args.symbol or 'ALL'} days={args.days}"
        )
        print("[backtester] TRUST LEVEL: RESEARCH-GRADE (not live-equivalent)")

        bt = EventBacktester(mode=args.mode)
        result = bt.run(
            strategy=args.strategy,
            symbol=args.symbol,
            days_back=args.days,
            notes=args.notes,
        )
        print(f"\n[backtester] run_id={result.get('run_id')}")
        print(f"  n_trades     = {result.get('n_trades')}")
        print(f"  win_rate     = {result.get('win_rate', 0):.1%}")
        print(f"  profit_factor= {result.get('profit_factor', 0):.2f}")
        print(f"  net_pnl      = ${result.get('net_pnl', 0):+.2f}")
        print(f"  max_drawdown = {result.get('max_drawdown_pct', 0):.1f}%")
        print(f"  sharpe       = {result.get('sharpe', 0):.2f}")
        print(f"  trust        = {result.get('trust')}")

        if result.get("n_trades", 0) == 0:
            print("[backtester] No trades simulated — check scan_candidates table")
            return

        if args.promote:
            run_promotion(strategy=args.strategy)
    else:
        run_promotion()


def run_promotion(strategy: str = None):
    from backtesting.promotion_engine import PromotionEngine

    print("\n[promotion] Evaluating all backtest runs...")
    engine = PromotionEngine()
    results = engine.evaluate_all(strategy=strategy)
    for r in results:
        tier = r.get("promotion_tier", "?")
        strat = r.get("strategy", "?")
        run_id = r.get("run_id", "?")
        reason = r.get("reason", "")
        print(f"  [{tier:12s}] {strat} ({run_id[:8]}...) — {reason}")
    if not results:
        print("  No backtest runs found to evaluate.")


if __name__ == "__main__":
    main()
