"""
run_backtest.py — Standalone backtest runner.

Usage:
  python3 run_backtest.py                         → BTC crypto, all 3 MACD variants
  python3 run_backtest.py --strategy equity       → Equity momentum on SPY
  python3 run_backtest.py --symbol AAPL           → Equity on AAPL
  python3 run_backtest.py --symbol BTC-USD        → Crypto on BTC
  python3 run_backtest.py --period 60d            → 60-day lookback
  python3 run_backtest.py --variant sniper        → Only MACD sniper variant
  python3 run_backtest.py --oos                   → Chan out-of-sample 70/30 split

Note on data limits:
  yfinance 5-min bars are only available for ~60 days.
  For crypto backtests, period is capped at 60d automatically.
  For equity 30-min bars, up to 2 years is available.

Results saved to: logs/backtest/
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtesting.backtest_engine import (
    run_crypto_backtest, run_equity_backtest,
    optimize_crypto, optimize_equity,
    run_backtest_oos_split,
)


def main():
    parser = argparse.ArgumentParser(description='Algo Trading Backtester')
    parser.add_argument('--strategy', choices=['crypto', 'equity', 'all'], default='crypto')
    parser.add_argument('--symbol', default=None)
    parser.add_argument('--period', default=None)
    parser.add_argument('--variant', choices=['all', 'workhorse', 'classic', 'sniper'], default='all')
    parser.add_argument('--cash', type=float, default=500)
    parser.add_argument('--optimize', action='store_true',
                        help='Sweep parameter combinations and write best to .env automatically')
    parser.add_argument('--dry-run', action='store_true',
                        help='With --optimize: show best params but do NOT write to .env')
    parser.add_argument('--oos', action='store_true',
                        help='Chan out-of-sample validation: 70%% train / 30%% test split')
    args = parser.parse_args()

    print(f"\n🧪 {'OPTIMIZER' if args.optimize else 'Backtest'} — strategy={args.strategy} cash=${args.cash:,.2f}\n")

    write = not args.dry_run

    if args.optimize:
        # ── Optimizer mode ───────────────────────────────────────────────────
        if args.strategy in ('crypto', 'all'):
            symbol = args.symbol or 'BTC-USD'
            period = args.period or '1y'
            print(f"🔍 OPTIMIZE CRYPTO: {symbol} | {period} | 1h candles")
            optimize_crypto(symbol=symbol, period=period, interval='1h',
                            cash=args.cash, commission=0.006, write_to_env=write)

        if args.strategy in ('equity', 'all'):
            symbol = args.symbol or 'SPY'
            period = args.period or '1y'
            print(f"\n🔍 OPTIMIZE EQUITY: {symbol} | {period} | 1h candles")
            optimize_equity(symbol=symbol, period=period, interval='1h',
                            cash=args.cash, write_to_env=write)

        if write:
            print("✅ Best params written to .env. Restart main.py to apply.\n")
        else:
            print("✅ Dry run complete — nothing written. Remove --dry-run to apply.\n")

    elif args.oos:
        # ── Chan out-of-sample validation ────────────────────────────────────
        strat = 'equity' if args.strategy == 'equity' else 'crypto'
        symbol = args.symbol or ('SPY' if strat == 'equity' else 'BTC-USD')
        period = args.period or ('1y' if strat == 'equity' else '60d')
        interval = '30m' if strat == 'equity' else '5m'
        print(f"\n🔬 OOS VALIDATION: {symbol} | {period} | {interval} | 70/30 split")
        run_backtest_oos_split(
            symbol=symbol, strategy=strat, period=period, interval=interval,
            cash=args.cash, commission=0.006, slippage=0.002,
        )

    else:
        # ── Normal backtest mode ─────────────────────────────────────────────
        if args.strategy in ('crypto', 'all'):
            symbol = args.symbol or 'BTC-USD'
            period = args.period or '60d'
            if period in ('6mo', '1y', '2y'):
                print("[backtest] 5-min yfinance data limited to ~60 days — using 60d\n")
                period = '60d'

            print(f"📊 CRYPTO: {symbol} | {period} | 5m candles")
            results = run_crypto_backtest(
                symbol=symbol, period=period, interval='5m',
                cash=args.cash, commission=0.006, variant=args.variant
            )
            if 'error' not in results:
                print("\nSUMMARY:")
                for name, r in results.items():
                    if 'error' not in r:
                        print(f"  {name:12s}: {r.get('total_return_pct',0):+.2f}%  "
                              f"WR={r.get('win_rate_pct',0):.1f}%  "
                              f"Trades={r.get('total_trades',0)}  "
                              f"Sharpe={r.get('sharpe_ratio',0):.2f}")

        if args.strategy in ('equity', 'all'):
            symbol = args.symbol or 'SPY'
            period = args.period or '1y'
            print(f"\n📈 EQUITY: {symbol} | {period} | 30m candles")
            run_equity_backtest(symbol=symbol, period=period, interval='30m', cash=args.cash)

    print("\n✅ Done. Results in logs/backtest/\n")


if __name__ == '__main__':
    main()
