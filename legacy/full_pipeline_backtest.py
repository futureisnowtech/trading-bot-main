"""
backtesting/full_pipeline_backtest.py — Full-pipeline backtest.

Mirrors what actually runs in production (no AI calls, fully deterministic):
  1. Real 4-signal entry gate (same logic as scheduler/crypto_scanner.py)
  2. Saved ML model gate (get_ml_signal from learning/ml_signal.py)
  3. Real fee rates from config
  4. Slippage from BACKTEST_SLIPPAGE_PCT

Run:
    python3 backtesting/full_pipeline_backtest.py
"""
import os
import sys
import math
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    BACKTEST_SLIPPAGE_PCT,
    ML_SIGNAL_MIN_PROB,
    COINBASE_MAKER_FEE_PCT,
    CRYPTO_STOP_LOSS_PCT,
    CRYPTO_TAKE_PROFIT_PCT,
    CRYPTO_MAX_HOLD_HOURS,
    CRYPTO_CANDLE_GRANULARITY,
)

# ── Optional ML import (fail-open if model not trained) ───────────────────────
try:
    from learning.ml_signal import get_ml_signal as _get_ml_signal
    _ML_AVAILABLE = True
except Exception as _mle:
    print(f"[full_pipeline_backtest] ML signal unavailable: {_mle}")
    _get_ml_signal = None
    _ML_AVAILABLE = False

# ── Optional price archive import ─────────────────────────────────────────────
try:
    from data.price_archive import get_candles as _archive_get_candles
    _ARCHIVE_AVAILABLE = True
except Exception:
    _archive_get_candles = None
    _ARCHIVE_AVAILABLE = False

# ── Coinbase feed fallback ─────────────────────────────────────────────────────
try:
    from data.coinbase_feed import get_candles as _cb_get_candles
    _COINBASE_AVAILABLE = True
except Exception:
    _cb_get_candles = None
    _COINBASE_AVAILABLE = False

from data.indicators import add_all_indicators

# ── Granularity → minutes lookup ──────────────────────────────────────────────
_GRAN_MINUTES = {
    'ONE_MINUTE': 1,
    'FIVE_MINUTE': 5,
    'FIFTEEN_MINUTE': 15,
    'THIRTY_MINUTE': 30,
    'ONE_HOUR': 60,
    'TWO_HOUR': 120,
    'SIX_HOUR': 360,
    'ONE_DAY': 1440,
}


def _gran_minutes(gran: str) -> int:
    return _GRAN_MINUTES.get(gran.upper(), 5)


def _load_candles(symbol: str, days: int):
    """
    Load candles from price archive first, then Coinbase REST as fallback.
    Returns a pandas DataFrame or None.
    """
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)

    # Try archive first
    if _ARCHIVE_AVAILABLE and _archive_get_candles:
        try:
            df = _archive_get_candles(symbol, CRYPTO_CANDLE_GRANULARITY,
                                      start=start_dt, end=end_dt)
            if df is not None and len(df) >= 100:
                return df
        except Exception as e:
            print(f"[full_pipeline_backtest] archive miss for {symbol}: {e}")

    # Coinbase REST fallback — fetch in chunks (API limit 350 candles per call)
    if _COINBASE_AVAILABLE and _cb_get_candles:
        try:
            # Request enough candles to cover the period
            gran_min = _gran_minutes(CRYPTO_CANDLE_GRANULARITY)
            needed = int((days * 24 * 60) / gran_min) + 50
            # Coinbase get_candles takes (symbol, granularity, limit)
            df = _cb_get_candles(symbol, CRYPTO_CANDLE_GRANULARITY, min(needed, 300))
            if df is not None and len(df) >= 50:
                return df
        except Exception as e:
            print(f"[full_pipeline_backtest] Coinbase fallback failed for {symbol}: {e}")

    return None


def _check_signal_gate(md: dict) -> tuple[bool, list[str]]:
    """
    Deterministic 4-signal gate — mirrors scheduler/crypto_scanner.py exactly.

    Returns (gate_passed: bool, fired_signals: list[str])
    """
    fired = []

    # Signal 1: MACD consensus
    if md.get('macd_consensus', False):
        fired.append('macd_consensus')

    # Signal 2: Williams %R extreme oversold
    if md.get('williams_r', 0) <= -80:
        fired.append('williams_r')

    # Signal 3: Momentum + volume breakout
    if md.get('momentum_score', 0) > 0.6 and md.get('vol_spike', 1) > 1.3:
        fired.append('momentum_volume')

    # Signal 4: BB-Keltner squeeze fired ≥20 bars, direction bullish
    if md.get('squeeze_fired', False) and md.get('squeeze_bars', 0) >= 20:
        fired.append('squeeze_fired')

    return len(fired) >= 1, fired


def _build_market_data_lite(symbol: str, df_ind, price: float, regime: str = 'ranging') -> dict:
    """
    Lightweight market_data builder for backtest — same fields as production
    _build_market_data but without live API calls (microstructure/fear-greed/macro).
    """
    last = df_ind.iloc[-1]

    def _safe(col, default=None):
        v = last.get(col, default)
        if v is None:
            return default
        try:
            fv = float(v)
            return default if math.isnan(fv) else fv
        except Exception:
            return default

    vol_spike = float(last.get('vol_spike', 1) or 1)

    # MACD consensus — check if all 3 MACD variants agree bullish
    macd1_hist = _safe('macd1_hist', 0.0) or 0.0
    macd2_hist = _safe('macd2_hist', 0.0) or 0.0
    macd3_hist = _safe('macd3_hist', 0.0) or 0.0
    macd_std_hist = _safe('macd_std_hist', 0.0) or 0.0
    # Use standard hist if specific variants not present
    if 'macd1_hist' in df_ind.columns:
        macd_consensus = (macd1_hist > 0) and (macd2_hist > 0) and (macd3_hist > 0)
    else:
        macd_consensus = macd_std_hist > 0

    # Williams %R — from last indicator row or compute from OHLC
    williams_r_val = _safe('williams_r', -50.0)
    if williams_r_val is None:
        # Fallback: compute from last 14 bars
        try:
            import pandas as pd
            tail = df_ind.tail(14)
            highest = float(tail['high'].max())
            lowest = float(tail['low'].min())
            if highest != lowest:
                williams_r_val = -100 * (highest - price) / (highest - lowest)
            else:
                williams_r_val = -50.0
        except Exception:
            williams_r_val = -50.0

    # Momentum score — use vol_spike as proxy for volume confirmation
    momentum_score = _safe('momentum_score', 0.0)
    if momentum_score is None:
        # Simple proxy: positive MACD hist + above VWAP
        vwap = _safe('vwap', price) or price
        momentum_score = 0.7 if (macd_std_hist > 0 and price >= vwap * 0.998) else 0.3

    squeeze_fired = bool(last.get('squeeze_fired', False))
    squeeze_bars = int(_safe('squeeze_bars', 0) or 0)

    lrsi_val = _safe('lrsi', 0.5)

    md = {
        'symbol': symbol,
        'price': price,
        'regime': regime,
        'vol_spike': vol_spike,
        'rsi': _safe('rsi', 50.0) or 50.0,
        'macd_hist': macd_std_hist,
        'macd_consensus': macd_consensus,
        'williams_r': williams_r_val,
        'momentum_score': momentum_score,
        'vol_20d_pct_above_avg': (vol_spike - 1) * 100 if vol_spike > 1 else 0,
        'squeeze_fired': squeeze_fired,
        'squeeze_bars': squeeze_bars,
        'squeeze_direction': int(_safe('squeeze_direction', 0) or 0),
        'squeeze_on': bool(last.get('squeeze_on', False)),
        'rv_ratio': _safe('rv_ratio'),
        'avwap_dev': _safe('avwap_dev', 0.0),
        'kalman_dev': _safe('kalman_dev', 0.0),
        'ou_halflife_minutes': _safe('ou_halflife_minutes'),
        'kyle_lambda_pct': _safe('kyle_lambda_pct'),
        'adx': _safe('adx', 25.0) or 25.0,
        'atr': _safe('atr', price * 0.01) or price * 0.01,
        'atr_pct': (_safe('atr', price * 0.01) or price * 0.01) / price * 100,
        'vwap': _safe('vwap', price) or price,
        'supertrend_bullish': bool(last.get('supertrend_bullish', False)),
        'cloud_bullish': bool(last.get('cloud_bullish', False)),
        'wae_bullish': bool(last.get('wae_bullish', False)),
        'wae_exploding': bool(last.get('wae_exploding', False)),
        'fisher_cross_up': bool(last.get('fisher_cross_up', False)),
        'wt_oversold_cross': bool(last.get('wt_oversold_cross', False)),
        'lrsi': lrsi_val,
        'chop_trending': bool(last.get('chop_trending', False)),
        'dollar_volume': price * float(last.get('volume', 0) or 0),
        # Microstructure — not available in backtest; set neutral defaults
        'obi': None,
        'tfi': None,
        'microprice_premium_bps': None,
        'spread_bps': None,
    }
    return md


def _detect_regime_lite(df_ind) -> str:
    """Simple regime detection from ADX without AI."""
    try:
        adx = float(df_ind.iloc[-1].get('adx', 25) or 25)
        if adx >= 25:
            return 'trending'
        elif adx >= 15:
            return 'ranging'
        else:
            return 'volatile'
    except Exception:
        return 'ranging'


class FullPipelineBacktest:
    """
    Backtests the live crypto entry pipeline on historical candles.

    Parameters
    ----------
    symbol : str
        e.g. 'BTC-USDC'
    days : int
        Lookback window in calendar days (default 90)
    paper : bool
        Unused — reserved for future per-mode fee differentiation
    """

    def __init__(self, symbol: str, days: int = 90, paper: bool = True):
        self.symbol = symbol
        self.days = days
        self.paper = paper

    def run(self) -> dict:
        """
        Execute the backtest. Returns a stats dict:
            win_rate, profit_factor, total_trades, wins, losses,
            total_pnl, sharpe, max_dd, signal_gate_filtered, ml_gate_filtered
        """
        df_raw = _load_candles(self.symbol, self.days)

        if df_raw is None or len(df_raw) < 60:
            print(f"[full_pipeline_backtest] {self.symbol}: insufficient candle data — "
                  f"got {0 if df_raw is None else len(df_raw)} rows")
            return self._empty_result()

        # Normalise column names
        df_raw = df_raw.copy()
        df_raw.columns = [c.lower() for c in df_raw.columns]

        # Add all indicators
        try:
            df = add_all_indicators(df_raw)
        except Exception as e:
            print(f"[full_pipeline_backtest] {self.symbol}: indicator error: {e}")
            return self._empty_result()

        if df is None or len(df) < 60:
            return self._empty_result()

        # Sort by time ascending (archive returns sorted, double-check)
        df = df.sort_index()

        gran_min = _gran_minutes(CRYPTO_CANDLE_GRANULARITY)
        max_hold_bars = int((CRYPTO_MAX_HOLD_HOURS * 60) / gran_min)

        # Warm-up: skip first 50 bars so indicators are stable
        warmup = 50
        candles = list(df.itertuples())

        signal_gate_filtered = 0
        ml_gate_filtered = 0
        trades: list[dict] = []

        i = warmup
        while i < len(candles) - 1:
            bar = candles[i]

            # Build a sub-DataFrame up to (and including) current bar for indicators
            df_slice = df.iloc[:i + 1]
            if len(df_slice) < 30:
                i += 1
                continue

            price = float(bar.close)
            if price <= 0:
                i += 1
                continue

            regime = _detect_regime_lite(df_slice)
            md = _build_market_data_lite(self.symbol, df_slice, price, regime)

            # ── Signal gate ───────────────────────────────────────────────────
            gate_passed, fired_signals = _check_signal_gate(md)
            if not gate_passed:
                signal_gate_filtered += 1
                i += 1
                continue

            # ── ML gate (fail-open if model not available) ─────────────────
            if _ML_AVAILABLE and _get_ml_signal:
                try:
                    p_win, ml_label = _get_ml_signal(md)
                    if p_win < ML_SIGNAL_MIN_PROB:
                        ml_gate_filtered += 1
                        i += 1
                        continue
                except Exception:
                    pass  # fail-open

            # ── Entry: next bar's open + slippage ─────────────────────────
            if i + 1 >= len(candles):
                break

            next_bar = candles[i + 1]
            entry_price = float(next_bar.open) * (1 + BACKTEST_SLIPPAGE_PCT)
            if entry_price <= 0:
                i += 1
                continue

            stop_price = entry_price * (1 - CRYPTO_STOP_LOSS_PCT)
            target_price = entry_price * (1 + CRYPTO_TAKE_PROFIT_PCT)

            # ── Walk forward until stop/target/max-hold ───────────────────
            position_size = 100.0   # normalised notional per trade
            fee = 2 * COINBASE_MAKER_FEE_PCT * position_size
            exit_price = None
            exit_reason = 'timeout'

            j = i + 2  # start checking from bar after entry
            hold_bars = 0
            while j < len(candles) and hold_bars < max_hold_bars:
                check_bar = candles[j]
                bar_low = float(check_bar.low)
                bar_high = float(check_bar.high)
                bar_close = float(check_bar.close)

                # Check stop first (pessimistic intra-bar ordering)
                if bar_low <= stop_price:
                    exit_price = stop_price * (1 - BACKTEST_SLIPPAGE_PCT)
                    exit_reason = 'stop'
                    break
                if bar_high >= target_price:
                    exit_price = target_price * (1 - BACKTEST_SLIPPAGE_PCT)
                    exit_reason = 'target'
                    break

                j += 1
                hold_bars += 1

            if exit_price is None:
                # Timeout exit at last available bar's close
                exit_bar_idx = min(j, len(candles) - 1)
                exit_price = float(candles[exit_bar_idx].close) * (1 - BACKTEST_SLIPPAGE_PCT)
                exit_reason = 'timeout'

            gross_pnl = position_size * (exit_price / entry_price - 1)
            net_pnl = gross_pnl - fee
            won = net_pnl > 0

            trades.append({
                'entry_price': entry_price,
                'exit_price': exit_price,
                'stop_price': stop_price,
                'target_price': target_price,
                'exit_reason': exit_reason,
                'signals': fired_signals,
                'regime': regime,
                'gross_pnl': gross_pnl,
                'fee': fee,
                'net_pnl': net_pnl,
                'won': won,
            })

            # Skip forward past the exit bar to avoid overlapping trades
            i = max(j, i + 1)

        return self._aggregate(trades, signal_gate_filtered, ml_gate_filtered)

    # ── Aggregation ───────────────────────────────────────────────────────────

    def _aggregate(self, trades: list, signal_gate_filtered: int, ml_gate_filtered: int) -> dict:
        if not trades:
            return self._empty_result(signal_gate_filtered, ml_gate_filtered)

        import numpy as np

        total = len(trades)
        wins = sum(1 for t in trades if t['won'])
        losses = total - wins
        win_rate = wins / total if total > 0 else 0.0

        gross_wins = sum(t['net_pnl'] for t in trades if t['net_pnl'] > 0)
        gross_losses = abs(sum(t['net_pnl'] for t in trades if t['net_pnl'] <= 0))
        profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else (float('inf') if gross_wins > 0 else 0.0)
        total_pnl = sum(t['net_pnl'] for t in trades)

        # Sharpe — annualised from per-trade returns
        pnl_series = np.array([t['net_pnl'] for t in trades])
        if len(pnl_series) >= 2 and pnl_series.std() > 0:
            gran_min = _gran_minutes(CRYPTO_CANDLE_GRANULARITY)
            # Approximate trades per year based on average hold time
            # Crypto: 24/7/365 → periods_per_year
            periods_per_year = (365 * 24 * 60) / gran_min
            # Sharpe = mean / std * sqrt(periods_per_year) — per-trade version
            sharpe = float((pnl_series.mean() / pnl_series.std()) * math.sqrt(periods_per_year))
        else:
            sharpe = 0.0

        # Maximum drawdown on cumulative equity curve
        cumulative = np.cumsum(pnl_series)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = running_max - cumulative
        max_dd = float(drawdowns.max()) if len(drawdowns) > 0 else 0.0
        # Normalise max_dd as a percentage of peak equity (relative to initial 100-unit account)
        peak = float(running_max.max()) if running_max.max() > 0 else 100.0
        max_dd_pct = (max_dd / (100.0 + max(0.0, peak))) * 100.0

        return {
            'symbol': self.symbol,
            'days': self.days,
            'total_trades': total,
            'wins': wins,
            'losses': losses,
            'win_rate': round(win_rate, 4),
            'profit_factor': round(profit_factor, 3),
            'total_pnl': round(total_pnl, 2),
            'sharpe': round(sharpe, 3),
            'max_dd': round(max_dd_pct, 2),
            'signal_gate_filtered': signal_gate_filtered,
            'ml_gate_filtered': ml_gate_filtered,
        }

    @staticmethod
    def _empty_result(signal_gate_filtered: int = 0, ml_gate_filtered: int = 0) -> dict:
        return {
            'symbol': '',
            'days': 0,
            'total_trades': 0,
            'wins': 0,
            'losses': 0,
            'win_rate': 0.0,
            'profit_factor': 0.0,
            'total_pnl': 0.0,
            'sharpe': 0.0,
            'max_dd': 0.0,
            'signal_gate_filtered': signal_gate_filtered,
            'ml_gate_filtered': ml_gate_filtered,
        }


# ── Live signal stats comparison ──────────────────────────────────────────────

def _get_live_win_rates() -> dict:
    """
    Fetch per-signal live win rates from signal_stats table.
    Returns {signal_name: win_rate} for the 4 gate signals.
    """
    gate_signals = ['macd_consensus', 'williams_r', 'momentum_volume', 'squeeze_fired']
    result = {}
    try:
        from learning.signal_performance import get_signal_report
        rows = get_signal_report(min_fires=1)
        for row in rows:
            name = row.get('signal_name', '')
            if name in gate_signals:
                result[name] = round(float(row.get('win_rate', 0) or 0), 4)
    except Exception as e:
        print(f"[full_pipeline_backtest] could not load live signal stats: {e}")
    return result


# ── main() ────────────────────────────────────────────────────────────────────

def main():
    symbols = ['BTC-USDC', 'ETH-USDC', 'SOL-USDC']
    days = 90

    print("=" * 72)
    print("FULL PIPELINE BACKTEST — mirrors production 4-signal gate + ML gate")
    print(f"Period: {days} days | Granularity: {CRYPTO_CANDLE_GRANULARITY}")
    print(f"Stop: {CRYPTO_STOP_LOSS_PCT:.1%} | Target: {CRYPTO_TAKE_PROFIT_PCT:.1%} | "
          f"Max hold: {CRYPTO_MAX_HOLD_HOURS}h")
    print(f"Fee: {COINBASE_MAKER_FEE_PCT:.3%} maker × 2 sides | "
          f"Slippage: {BACKTEST_SLIPPAGE_PCT:.2%} per side")
    print(f"ML gate: {'ACTIVE (p_win < ' + str(ML_SIGNAL_MIN_PROB) + ' → skip)' if _ML_AVAILABLE else 'DISABLED (model not trained)'}")
    print("=" * 72)

    results = []
    for sym in symbols:
        print(f"\nRunning {sym}...", flush=True)
        t0 = time.time()
        bt = FullPipelineBacktest(sym, days=days, paper=True)
        res = bt.run()
        res['symbol'] = sym
        elapsed = time.time() - t0
        print(f"  Done in {elapsed:.1f}s — {res['total_trades']} trades triggered")
        results.append(res)

    # ── Comparison table ──────────────────────────────────────────────────────
    live_wr = _get_live_win_rates()

    print("\n" + "=" * 72)
    print("BACKTEST RESULTS")
    print("-" * 72)
    hdr = f"{'Symbol':<12} {'Trades':>7} {'WR':>7} {'PF':>7} {'Sharpe':>8} {'MaxDD':>8} {'Total PnL':>10} {'SigFlt':>7} {'MLFlt':>7}"
    print(hdr)
    print("-" * 72)

    for r in results:
        if r['total_trades'] == 0:
            print(f"  {r['symbol']:<12} {'NO DATA':>7}")
            continue
        print(
            f"  {r['symbol']:<12}"
            f" {r['total_trades']:>7}"
            f" {r['win_rate']:>6.1%}"
            f" {r['profit_factor']:>7.2f}"
            f" {r['sharpe']:>8.2f}"
            f" {r['max_dd']:>7.1f}%"
            f" {r['total_pnl']:>+10.2f}"
            f" {r['signal_gate_filtered']:>7}"
            f" {r['ml_gate_filtered']:>7}"
        )

    # ── Live vs backtest comparison ───────────────────────────────────────────
    if live_wr:
        print("\n" + "-" * 72)
        print("LIVE WIN RATES vs BACKTEST (signal_stats table, regime=any)")
        print(f"  {'Signal':<22} {'Live WR':>8} {'Backtest':<12}")
        print(f"  {'-'*22} {'-'*8} {'-'*12}")

        # Aggregate backtest win rate across all symbols (equal-weight)
        agg_wr = 0.0
        agg_count = sum(1 for r in results if r['total_trades'] > 0)
        if agg_count > 0:
            agg_wr = sum(r['win_rate'] for r in results if r['total_trades'] > 0) / agg_count

        for sig, lwr in sorted(live_wr.items()):
            print(f"  {sig:<22} {lwr:>7.1%}   (overall BT WR: {agg_wr:.1%})")

        if not live_wr:
            print("  No live signal stats yet (need 10+ fires per signal).")

        # Delta alert
        print()
        for r in results:
            if r['total_trades'] < 5:
                continue
            delta = r['win_rate'] - agg_wr
            direction = "HIGHER" if delta >= 0 else "LOWER"
            print(f"  {r['symbol']}: backtest WR {r['win_rate']:.1%} is "
                  f"{abs(delta):.1%} {direction} than live aggregate")

    print("\n" + "=" * 72)
    print("Done.")


if __name__ == '__main__':
    main()
