"""
backtesting/backtest_engine.py

Backtesting module. Run this standalone to validate strategies before live.
Uses backtesting.py (simple, readable) with vectorbt fallback for speed.

Usage:
  python run_backtest.py --strategy crypto_macd --symbol BTC-USD --period 6mo
  python run_backtest.py --strategy equity --symbol AAPL --period 1y

Both strategies are wired here. Results printed + saved to logs/backtest/.
"""
import os
import sys
import json
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.indicators import add_all_indicators

BACKTEST_DIR = 'logs/backtest'
os.makedirs(BACKTEST_DIR, exist_ok=True)

# ─── backtesting.py strategies ───────────────────────────────────────────────

try:
    import sys as _sys
    _SITE = '/Library/Frameworks/Python.framework/Versions/3.14/lib/python3.14/site-packages'
    # Temporarily hide the local 'backtesting' package from the module cache
    # so the installed library can be imported instead.
    _saved = {k: v for k, v in _sys.modules.items()
              if k == 'backtesting' or k.startswith('backtesting.')}
    for _k in _saved:
        del _sys.modules[_k]
    _sys.path.insert(0, _SITE)
    from backtesting import Backtest, Strategy
    from backtesting.lib import crossover
    # Re-register under a private alias to avoid future collisions
    _sys.modules['_bt_installed'] = _sys.modules.pop('backtesting')
    # Restore the local package
    _sys.path.pop(0)
    for _k, _v in _saved.items():
        _sys.modules[_k] = _v
    BACKTESTING_PY = True
except Exception as _e:
    BACKTESTING_PY = False
    print(f"[backtest] backtesting.py not installed. Run: pip install backtesting ({_e})")


if BACKTESTING_PY:

    class CryptoMACDWorkhorse(Strategy):
        """
        MACD(3/15/3) Histogram > 0 strategy.
        Mirrors Moon Dev's Variant 1 — the workhorse.
        Trades every signal. Long only.
        """
        fast = 3
        slow = 15
        signal_period = 3

        def init(self):
            close = pd.Series(self.data.Close)

            def macd_hist(prices, f, s, sig):
                ema_f = prices.ewm(span=f, adjust=False).mean()
                ema_s = prices.ewm(span=s, adjust=False).mean()
                line = ema_f - ema_s
                signal = line.ewm(span=sig, adjust=False).mean()
                return (line - signal).values

            self.macd_histogram = self.I(
                macd_hist, close, self.fast, self.slow, self.signal_period
            )

        def next(self):
            if self.macd_histogram[-1] > 0 and not self.position:
                self.buy()
            elif self.macd_histogram[-1] < 0 and self.position:
                self.position.close()


    class CryptoMACDClassic(Strategy):
        """
        MACD(4/16/3) Line vs Signal crossover.
        Variant 2 — classic crossover.
        """
        fast = 4
        slow = 16
        signal_period = 3

        def init(self):
            close = pd.Series(self.data.Close)

            def macd_line(prices, f, s):
                return (prices.ewm(span=f, adjust=False).mean()
                        - prices.ewm(span=s, adjust=False).mean()).values

            def signal_line(prices, f, s, sig):
                ml = pd.Series(macd_line(prices, f, s))
                return ml.ewm(span=sig, adjust=False).mean().values

            self.macd = self.I(macd_line, close, self.fast, self.slow)
            self.signal = self.I(signal_line, close, self.fast, self.slow, self.signal_period)

        def next(self):
            if crossover(self.macd, self.signal) and not self.position:
                self.buy()
            elif crossover(self.signal, self.macd) and self.position:
                self.position.close()


    class CryptoMACDSniper(Strategy):
        """
        MACD(6/20/5) Histogram > threshold.
        Variant 3 — sniper, high win rate, low frequency.
        """
        fast = 6
        slow = 20
        signal_period = 5
        threshold_pct = 0.0001  # 0.01% of price

        def init(self):
            close = pd.Series(self.data.Close)

            def macd_hist(prices, f, s, sig):
                ema_f = prices.ewm(span=f, adjust=False).mean()
                ema_s = prices.ewm(span=s, adjust=False).mean()
                line = ema_f - ema_s
                signal = line.ewm(span=sig, adjust=False).mean()
                return (line - signal).values

            self.macd_histogram = self.I(
                macd_hist, close, self.fast, self.slow, self.signal_period
            )

        def next(self):
            threshold = self.data.Close[-1] * self.threshold_pct
            if self.macd_histogram[-1] > threshold and not self.position:
                self.buy()
            elif self.macd_histogram[-1] < -threshold and self.position:
                self.position.close()


    class EquityMomentum(Strategy):
        """
        Equity momentum with MACD + RSI + volume filter.
        Approximates the full strategy logic for backtesting purposes.
        """
        macd_fast = 12
        macd_slow = 26
        macd_signal = 9
        rsi_period = 14
        rsi_overbought = 70
        rsi_oversold = 35
        vol_spike_min = 1.5

        def init(self):
            close = pd.Series(self.data.Close)
            volume = pd.Series(self.data.Volume)

            def rsi(prices, n):
                delta = prices.diff()
                gain = delta.clip(lower=0).rolling(n).mean()
                loss = (-delta.clip(upper=0)).rolling(n).mean()
                rs = gain / loss.replace(0, np.nan)
                return (100 - 100 / (1 + rs)).values

            def macd_hist(prices, f, s, sig):
                ema_f = prices.ewm(span=f, adjust=False).mean()
                ema_s = prices.ewm(span=s, adjust=False).mean()
                line = ema_f - ema_s
                signal = line.ewm(span=sig, adjust=False).mean()
                return (line - signal).values

            def vol_spike(vol, n=20):
                ma = vol.rolling(n).mean()
                return (vol / ma.replace(0, np.nan)).values

            self.macd_hist = self.I(
                macd_hist, close, self.macd_fast, self.macd_slow, self.macd_signal
            )
            self.rsi = self.I(rsi, close, self.rsi_period)
            self.vol_spike = self.I(vol_spike, volume)

        def next(self):
            rsi = self.rsi[-1]
            hist = self.macd_hist[-1]
            spike = self.vol_spike[-1]

            if (hist > 0
                    and 35 <= rsi <= 65
                    and spike >= self.vol_spike_min
                    and not self.position):
                self.buy()
            elif (rsi > self.rsi_overbought or hist < 0) and self.position:
                self.position.close()


# ─── Backtest runner ─────────────────────────────────────────────────────────

def fetch_data(symbol: str, period: str = '6mo', interval: str = '5m') -> Optional[pd.DataFrame]:
    """Fetch OHLCV data for backtesting via yfinance."""
    print(f"[backtest] Fetching {symbol} {interval} data ({period})...")
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False)
        if df.empty:
            print(f"[backtest] No data returned for {symbol}")
            return None
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        # backtesting.py needs these exact column names
        df = df.rename(columns={
            'open': 'Open', 'high': 'High', 'low': 'Low',
            'close': 'Close', 'volume': 'Volume'
        })
        df.columns = [c.title() for c in df.columns]
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
        print(f"[backtest] Got {len(df)} candles for {symbol}")
        return df
    except Exception as e:
        print(f"[backtest] Data fetch error: {e}")
        return None


def run_crypto_backtest(
    symbol: str = 'BTC-USD',
    period: str = '6mo',
    interval: str = '5m',
    cash: float = 500,
    commission: float = 0.006,
    variant: str = 'all'
) -> dict:
    """
    Run all three MACD crypto strategies and return comparison.
    variant: 'all' | 'workhorse' | 'classic' | 'sniper'
    """
    if not BACKTESTING_PY:
        return {'error': 'backtesting.py not installed'}

    df = fetch_data(symbol, period=period, interval=interval)
    if df is None:
        return {'error': f'No data for {symbol}'}

    results = {}
    strategies = {
        'workhorse': (CryptoMACDWorkhorse, 'MACD(3/15/3) Histogram > 0'),
        'classic':   (CryptoMACDClassic,   'MACD(4/16/3) Line vs Signal'),
        'sniper':    (CryptoMACDSniper,     'MACD(6/20/5) Histogram > threshold'),
    }

    if variant != 'all':
        strategies = {variant: strategies[variant]}

    for name, (StratClass, description) in strategies.items():
        print(f"\n[backtest] Running {description} on {symbol}...")
        try:
            bt = Backtest(
                df, StratClass,
                cash=cash,
                commission=commission,
                exclusive_orders=True
            )
            stats = bt.run()
            result = _parse_stats(stats, symbol, name, description)
            results[name] = result
            _print_result(result)

            # Save plot if possible
            try:
                plot_path = os.path.join(BACKTEST_DIR, f'{symbol}_{name}.html')
                bt.plot(filename=plot_path, open_browser=False)
                result['plot_path'] = plot_path
            except Exception:
                pass

        except Exception as e:
            print(f"[backtest] Error running {name}: {e}")
            results[name] = {'error': str(e)}

    # Save results to JSON
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    results_path = os.path.join(BACKTEST_DIR, f'results_{symbol}_{ts}.json')
    with open(results_path, 'w') as f:
        json.dump({k: {kk: str(vv) for kk, vv in v.items()} for k, v in results.items()}, f, indent=2)
    print(f"\n[backtest] Results saved to {results_path}")

    return results


def run_equity_backtest(
    symbol: str = 'AAPL',
    period: str = '1y',
    interval: str = '30m',
    cash: float = 500,
) -> dict:
    """Run equity momentum strategy backtest."""
    if not BACKTESTING_PY:
        return {'error': 'backtesting.py not installed'}

    df = fetch_data(symbol, period=period, interval=interval)
    if df is None:
        return {'error': f'No data for {symbol}'}

    print(f"\n[backtest] Running Equity Momentum on {symbol}...")
    try:
        bt = Backtest(df, EquityMomentum, cash=cash, commission=0.0)
        stats = bt.run()
        result = _parse_stats(stats, symbol, 'equity_momentum', 'Equity Momentum MACD+RSI+Volume')
        _print_result(result)
        return result
    except Exception as e:
        print(f"[backtest] Error: {e}")
        return {'error': str(e)}


def _parse_stats(stats, symbol, name, description) -> dict:
    """Extract key metrics from backtesting.py stats."""
    def safe_float(key):
        try:
            v = stats[key]
            return float(v) if v is not None and str(v) != 'nan' else 0.0
        except Exception:
            return 0.0

    def safe_int(key):
        try:
            return int(stats[key])
        except Exception:
            return 0

    win_rate = safe_float('Win Rate [%]')
    return {
        'symbol': symbol,
        'strategy': name,
        'description': description,
        'start': str(stats.get('Start', '')),
        'end': str(stats.get('End', '')),
        'total_return_pct': safe_float('Return [%]'),
        'buy_hold_return_pct': safe_float('Buy & Hold Return [%]'),
        'sharpe_ratio': safe_float('Sharpe Ratio'),
        'max_drawdown_pct': safe_float('Max. Drawdown [%]'),
        'win_rate_pct': win_rate,
        'total_trades': safe_int('# Trades'),
        'avg_trade_return_pct': safe_float('Avg. Trade [%]'),
        'best_trade_pct': safe_float('Best Trade [%]'),
        'worst_trade_pct': safe_float('Worst Trade [%]'),
        'final_equity': safe_float('Equity Final [$]'),
        'profit_factor': safe_float('Profit Factor'),
        'calmar_ratio': safe_float('Calmar Ratio'),
        'exposure_pct': safe_float('Exposure Time [%]'),
    }


def _print_result(result: dict) -> None:
    """Print formatted backtest result."""
    print(f"""
╔══════════════════════════════════════════════════════════
║  BACKTEST: {result['description']}
║  Symbol: {result['symbol']} | {result.get('start','')} → {result.get('end','')}
╠══════════════════════════════════════════════════════════
║  Return:        {result['total_return_pct']:+.2f}%  (B&H: {result['buy_hold_return_pct']:+.2f}%)
║  Win Rate:      {result['win_rate_pct']:.1f}%
║  Total Trades:  {result['total_trades']}
║  Sharpe:        {result['sharpe_ratio']:.2f}
║  Max Drawdown:  {result['max_drawdown_pct']:.2f}%
║  Avg Trade:     {result['avg_trade_return_pct']:+.2f}%
║  Final Equity:  ${result['final_equity']:,.2f}
╚══════════════════════════════════════════════════════════""")


# ─── Auto-optimizer ───────────────────────────────────────────────────────────

def _update_env(updates: dict) -> None:
    """Write key=value pairs into .env, creating or updating each line."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    if not os.path.exists(env_path):
        print(f"[optimizer] .env not found at {env_path}")
        return
    with open(env_path, 'r') as f:
        lines = f.readlines()
    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if '=' in stripped and not stripped.startswith('#'):
            key = stripped.split('=', 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                updated_keys.add(key)
                continue
        new_lines.append(line)
    # Append any keys not already in file
    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}\n")
    with open(env_path, 'w') as f:
        f.writelines(new_lines)
    print(f"[optimizer] .env updated: {updates}")


def optimize_crypto(
    symbol: str = 'BTC-USD',
    period: str = '1y',
    interval: str = '1h',
    cash: float = 500,
    commission: float = 0.006,
    write_to_env: bool = True,
) -> dict:
    """
    Sweep MACD parameter combinations on 1h candles (up to 1 year).
    Finds the params with the best Win Rate × Sharpe composite score.
    Optionally writes winning params to .env / config.

    Returns dict with best params and their stats.
    """
    if not BACKTESTING_PY:
        return {'error': 'backtesting.py not installed'}

    # 1h candles: yfinance gives ~2 years — cap at 1y
    df = fetch_data(symbol, period=period, interval=interval)
    if df is None:
        return {'error': f'No data for {symbol}'}

    print(f"\n[optimizer] Optimizing crypto MACD on {symbol} ({interval}, {period}) — {len(df)} candles")
    print("[optimizer] Sweeping: fast=[3,5,8,10,12], slow=[13,15,20,26], signal=[3,5,9]")
    print("[optimizer] Metric: Win Rate × Sharpe  (composite — avoids overfitting to either alone)\n")

    best_score = -999
    best_params = {}
    best_stats = {}
    results_table = []

    fast_range  = [3, 5, 8, 10, 12]
    slow_range  = [13, 15, 20, 26]
    sig_range   = [3, 5, 9]

    for fast in fast_range:
        for slow in slow_range:
            if fast >= slow:
                continue
            for sig in sig_range:
                try:
                    # Dynamically set class params
                    CryptoMACDWorkhorse.fast = fast
                    CryptoMACDWorkhorse.slow = slow
                    CryptoMACDWorkhorse.signal_period = sig

                    bt = Backtest(df, CryptoMACDWorkhorse, cash=cash,
                                  commission=commission, exclusive_orders=True)
                    stats = bt.run()

                    wr = float(stats['Win Rate [%]']) if stats['Win Rate [%]'] is not None else 0.0
                    sharpe = float(stats['Sharpe Ratio']) if stats['Sharpe Ratio'] is not None else 0.0
                    trades = int(stats['# Trades']) if stats['# Trades'] is not None else 0
                    ret = float(stats['Return [%]']) if stats['Return [%]'] is not None else 0.0
                    dd = float(stats['Max. Drawdown [%]']) if stats['Max. Drawdown [%]'] is not None else 0.0

                    # Need at least 10 trades for meaningful stats
                    if trades < 10:
                        continue

                    score = (wr / 100.0) * max(sharpe, 0)
                    results_table.append({
                        'fast': fast, 'slow': slow, 'signal': sig,
                        'win_rate': wr, 'sharpe': sharpe, 'trades': trades,
                        'return_pct': ret, 'max_dd': dd, 'score': score
                    })

                    if score > best_score:
                        best_score = score
                        best_params = {'fast': fast, 'slow': slow, 'signal': sig}
                        best_stats = {'win_rate': wr, 'sharpe': sharpe, 'trades': trades,
                                      'return_pct': ret, 'max_dd': dd, 'score': score}

                except Exception as e:
                    pass

    # Restore defaults
    CryptoMACDWorkhorse.fast = 3
    CryptoMACDWorkhorse.slow = 15
    CryptoMACDWorkhorse.signal_period = 3

    if not best_params:
        return {'error': 'No valid results — try a longer period or different symbol'}

    # Sort table by score
    results_table.sort(key=lambda x: x['score'], reverse=True)

    print(f"\n{'─'*70}")
    print(f"  TOP 5 CRYPTO MACD PARAMETER SETS ({symbol} {period} {interval})")
    print(f"{'─'*70}")
    print(f"  {'FAST':>4} {'SLOW':>4} {'SIG':>3} | {'WIN%':>6} {'SHARPE':>7} {'RETURN%':>8} {'DD%':>7} {'TRADES':>6} | SCORE")
    print(f"{'─'*70}")
    for r in results_table[:5]:
        marker = ' ◀ BEST' if r is results_table[0] else ''
        print(f"  {r['fast']:>4} {r['slow']:>4} {r['signal']:>3} | "
              f"{r['win_rate']:>5.1f}% {r['sharpe']:>7.2f} {r['return_pct']:>+7.2f}% "
              f"{r['max_dd']:>6.2f}% {r['trades']:>6} | {r['score']:.4f}{marker}")
    print(f"{'─'*70}")

    print(f"\n✅ WINNER: MACD({best_params['fast']}/{best_params['slow']}/{best_params['signal']})")
    print(f"   Win Rate: {best_stats['win_rate']:.1f}%  |  Sharpe: {best_stats['sharpe']:.2f}  "
          f"|  Return: {best_stats['return_pct']:+.2f}%  |  Trades: {best_stats['trades']}")

    if write_to_env:
        env_updates = {
            'CRYPTO_MACD1_FAST':   str(best_params['fast']),
            'CRYPTO_MACD1_SLOW':   str(best_params['slow']),
            'CRYPTO_MACD1_SIGNAL': str(best_params['signal']),
        }
        _update_env(env_updates)
        print(f"\n📝 Written to .env — restart main.py to apply.")
    else:
        print(f"\n  (Run with write_to_env=True to apply these params automatically)")

    # Save full results to JSON
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = {
        'symbol': symbol, 'period': period, 'interval': interval,
        'best_params': best_params, 'best_stats': best_stats,
        'all_results': results_table,
    }
    path = os.path.join(BACKTEST_DIR, f'optimize_crypto_{symbol}_{ts}.json')
    with open(path, 'w') as f:
        json.dump(out, f, indent=2)
    print(f"📁 Full results → {path}\n")

    return out


def optimize_equity(
    symbol: str = 'SPY',
    period: str = '1y',
    interval: str = '1h',
    cash: float = 500,
    write_to_env: bool = True,
) -> dict:
    """
    Sweep MACD + RSI parameter combinations for the equity momentum strategy.
    Uses 1h candles for up to 1 year of data.
    Writes winning params to .env.
    """
    if not BACKTESTING_PY:
        return {'error': 'backtesting.py not installed'}

    df = fetch_data(symbol, period=period, interval=interval)
    if df is None:
        return {'error': f'No data for {symbol}'}

    print(f"\n[optimizer] Optimizing equity momentum on {symbol} ({interval}, {period}) — {len(df)} candles")
    print("[optimizer] Sweeping: macd_fast, macd_slow, macd_signal, rsi_oversold")
    print("[optimizer] Metric: Win Rate × Sharpe\n")

    best_score = -999
    best_params = {}
    best_stats = {}
    results_table = []

    fast_range   = [8, 10, 12, 14]
    slow_range   = [20, 24, 26, 30]
    sig_range    = [7, 9, 11]
    rsi_os_range = [30, 35, 40]

    for fast in fast_range:
        for slow in slow_range:
            if fast >= slow:
                continue
            for sig in sig_range:
                for rsi_os in rsi_os_range:
                    try:
                        EquityMomentum.macd_fast    = fast
                        EquityMomentum.macd_slow    = slow
                        EquityMomentum.macd_signal  = sig
                        EquityMomentum.rsi_oversold = rsi_os

                        bt = Backtest(df, EquityMomentum, cash=cash,
                                      commission=0.0, exclusive_orders=True)
                        stats = bt.run()

                        wr     = float(stats['Win Rate [%]'])     if stats['Win Rate [%]']     is not None else 0.0
                        sharpe = float(stats['Sharpe Ratio'])     if stats['Sharpe Ratio']     is not None else 0.0
                        trades = int(stats['# Trades'])           if stats['# Trades']         is not None else 0
                        ret    = float(stats['Return [%]'])       if stats['Return [%]']       is not None else 0.0
                        dd     = float(stats['Max. Drawdown [%]']) if stats['Max. Drawdown [%]'] is not None else 0.0

                        if trades < 5:
                            continue

                        score = (wr / 100.0) * max(sharpe, 0)
                        results_table.append({
                            'fast': fast, 'slow': slow, 'signal': sig, 'rsi_oversold': rsi_os,
                            'win_rate': wr, 'sharpe': sharpe, 'trades': trades,
                            'return_pct': ret, 'max_dd': dd, 'score': score
                        })

                        if score > best_score:
                            best_score = score
                            best_params = {'fast': fast, 'slow': slow, 'signal': sig, 'rsi_oversold': rsi_os}
                            best_stats  = {'win_rate': wr, 'sharpe': sharpe, 'trades': trades,
                                           'return_pct': ret, 'max_dd': dd, 'score': score}

                    except Exception:
                        pass

    # Restore defaults
    EquityMomentum.macd_fast    = 12
    EquityMomentum.macd_slow    = 26
    EquityMomentum.macd_signal  = 9
    EquityMomentum.rsi_oversold = 35

    if not best_params:
        return {'error': 'No valid results'}

    results_table.sort(key=lambda x: x['score'], reverse=True)

    print(f"\n{'─'*80}")
    print(f"  TOP 5 EQUITY MOMENTUM PARAMETER SETS ({symbol} {period} {interval})")
    print(f"{'─'*80}")
    print(f"  {'FAST':>4} {'SLOW':>4} {'SIG':>3} {'RSI_OS':>6} | {'WIN%':>6} {'SHARPE':>7} {'RETURN%':>8} {'DD%':>7} {'TRADES':>6} | SCORE")
    print(f"{'─'*80}")
    for r in results_table[:5]:
        marker = ' ◀ BEST' if r is results_table[0] else ''
        print(f"  {r['fast']:>4} {r['slow']:>4} {r['signal']:>3} {r['rsi_oversold']:>6} | "
              f"{r['win_rate']:>5.1f}% {r['sharpe']:>7.2f} {r['return_pct']:>+7.2f}% "
              f"{r['max_dd']:>6.2f}% {r['trades']:>6} | {r['score']:.4f}{marker}")
    print(f"{'─'*80}")

    print(f"\n✅ WINNER: MACD({best_params['fast']}/{best_params['slow']}/{best_params['signal']}) "
          f"RSI_oversold={best_params['rsi_oversold']}")
    print(f"   Win Rate: {best_stats['win_rate']:.1f}%  |  Sharpe: {best_stats['sharpe']:.2f}  "
          f"|  Return: {best_stats['return_pct']:+.2f}%  |  Trades: {best_stats['trades']}")

    if write_to_env:
        _update_env({
            'EQUITY_RSI_OVERSOLD': str(best_params['rsi_oversold']),
        })
        # MACD params aren't in .env for equity — they're hardcoded in config.py
        # Update config.py directly
        _update_config_py({
            'EQUITY_RSI_OVERSOLD': best_params['rsi_oversold'],
        })
        print(f"\n📝 Written to .env — restart main.py to apply.")
    else:
        print(f"\n  (Run with write_to_env=True to apply these params automatically)")

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = {
        'symbol': symbol, 'period': period, 'interval': interval,
        'best_params': best_params, 'best_stats': best_stats,
        'all_results': results_table,
    }
    path = os.path.join(BACKTEST_DIR, f'optimize_equity_{symbol}_{ts}.json')
    with open(path, 'w') as f:
        json.dump(out, f, indent=2)
    print(f"📁 Full results → {path}\n")

    return out


def _update_config_py(updates: dict) -> None:
    """Update hardcoded values in config.py for params not driven by .env."""
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.py')
    if not os.path.exists(config_path):
        return
    with open(config_path, 'r') as f:
        content = f.read()
    import re
    for key, val in updates.items():
        pattern = rf'^({re.escape(key)}\s*:\s*\w+\s*=\s*)[\d.]+$'
        replacement = rf'\g<1>{val}'
        content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
    with open(config_path, 'w') as f:
        f.write(content)
    print(f"[optimizer] config.py updated: {updates}")
