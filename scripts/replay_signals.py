#!/usr/bin/env python3
"""
scripts/replay_signals.py — Historical Replay Engine.

THE CORRECT WAY TO SEED SIGNAL STATS.

Why the backtesting seeder (seed_intelligence.py) is insufficient:
  - backtest_engine.py uses simplified proxy indicators (EMA50 as "Kalman proxy")
  - All trades get regime='unknown' → live system reads trending/ranging/volatile buckets
  - Bayesian weights seeded into 'unknown' are NEVER READ during live trading
  - Result: signal_stats is populated but has zero impact on live conviction scoring

What this engine does differently:
  - Loads real archived candles from price_archive.db
  - Runs add_all_indicators() ONCE on the full historical dataset (real Kalman, real WaveTrend,
    real SuperTrend, real squeeze — not proxies)
  - Slides bar-by-bar with a 200-bar warmup window
  - Runs detect_regime() every 10 bars with real ADX/Hurst/autocorr (real regime = trending/ranging/volatile)
  - Computes conviction from real indicator values (same formula as job_runner — no AI, just math)
  - Simulates trade outcome by walking forward bars to stop or target
  - Records attribution with correct regime bucket → signal_stats is now live-relevant

Result: Bayesian priors seeded into the SAME regime buckets that live trading reads from.
The system starts Day 1 with evidence-backed weights, not untouched priors.

Coverage: all symbols in price_archive that have ≥ min_bars of data.

Usage:
  python3 scripts/replay_signals.py                          # all archived symbols, 90 days
  python3 scripts/replay_signals.py --days 180               # longer window
  python3 scripts/replay_signals.py --symbol BTC-USDC        # one symbol
  python3 scripts/replay_signals.py --strategy mean_reversion # one strategy path
  python3 scripts/replay_signals.py --dry-run                 # show plan without running
  python3 scripts/replay_signals.py --min-conviction 40       # lower threshold = more trades
"""
import sys, os, argparse, time, json
from datetime import datetime, timezone, timedelta
from typing import Optional
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.indicators import add_all_indicators
from data.price_archive import get_candles, get_summary
from strategies.ai_agents.regime_detector import detect_regime, get_regime_brief
from learning.signal_performance import (
    record_trade_attribution, init_learning_tables, SIGNAL_PRIOR_PTS
)
from config import (
    CRYPTO_STOP_LOSS_PCT, CRYPTO_TAKE_PROFIT_PCT,
    SQUEEZE_MIN_BARS, RV_EXPANSION_THRESHOLD,
    KALMAN_ENTRY_DEV_PCT, AVWAP_ENTRY_DEV_PCT,
    OU_HALFLIFE_MIN_MINUTES, OU_HALFLIFE_MAX_MINUTES,
    KYLE_LAMBDA_LOW_PCT, MARKET_TIMEZONE,
)

# ── Constants ─────────────────────────────────────────────────────────────────
WARMUP_BARS       = 200    # bars needed before indicators are reliable
REGIME_RECALC_N   = 10     # recalculate regime every N bars (expensive but not per-bar)
MAX_HOLD_BARS     = 90     # max bars to hold before time-exit (90 min on 1-min bars)
MIN_BARS_FOR_REPLAY = 250  # skip symbol if archive has < this many bars
POSITION_SIZE_USD = 100.0  # notional for P&L calculation
FEE_RT_PCT        = 0.012  # round-trip fees (1.2%)

# Default conviction floor per strategy
CONVICTION_FLOORS = {
    'crypto':          30,
    'mean_reversion':  25,
    'futures':         30,
    'equity':          25,
}


# ── Signal extraction from indicator row ────────────────────────────────────

def _safe(row, col, default=0.0):
    v = row.get(col, default)
    if v is None:
        return default
    try:
        import math
        if math.isnan(float(v)):
            return default
    except Exception:
        pass
    return float(v)


def _extract_signals_crypto(row) -> dict:
    """Extract all signal states for crypto MACD strategy."""
    williams_r   = _safe(row, 'williams_r',       -50)
    momentum_sc  = _safe(row, 'momentum_score',     0)
    vol_spike    = _safe(row, 'vol_spike',           1)
    squeeze_fired = bool(row.get('squeeze_fired', False))
    squeeze_bars  = int(_safe(row, 'squeeze_bars',  0))
    squeeze_dir   = int(_safe(row, 'squeeze_direction', 0))
    rv_ratio      = _safe(row, 'rv_ratio')
    kalman_dev    = _safe(row, 'kalman_dev',         0)
    avwap_dev     = _safe(row, 'avwap_dev',          0)
    ou_zscore     = _safe(row, 'ou_zscore',          0)
    ou_halflife   = _safe(row, 'ou_halflife_minutes')
    kyle_pct      = _safe(row, 'kyle_lambda_pct',  100)
    macd_hist     = _safe(row, 'macd_std_hist',      0) or _safe(row, 'macd1_hist', 0)
    rsi           = _safe(row, 'rsi',               50)
    adx           = _safe(row, 'adx',               20)
    vwap          = _safe(row, 'vwap',               0)
    close         = _safe(row, 'close',              0)
    autocorr      = _safe(row, 'autocorr_ret')

    return {
        'macd_consensus':        macd_hist > 0 and adx > 15,
        'williams_r':            williams_r <= -80,
        'momentum_volume':       momentum_sc > 0.6 and vol_spike >= 1.5,
        'squeeze_fired':         squeeze_fired and squeeze_bars >= SQUEEZE_MIN_BARS and squeeze_dir > 0,
        'rv_expansion':          rv_ratio is not None and rv_ratio >= RV_EXPANSION_THRESHOLD,
        'kalman_deviation':      kalman_dev <= KALMAN_ENTRY_DEV_PCT,
        'avwap_deviation':       avwap_dev <= AVWAP_ENTRY_DEV_PCT,
        'ou_halflife':           (ou_halflife is not None and
                                  OU_HALFLIFE_MIN_MINUTES <= ou_halflife <= OU_HALFLIFE_MAX_MINUTES),
        'kyle_lambda':           0 < kyle_pct <= KYLE_LAMBDA_LOW_PCT,
        'supertrend_bullish':    bool(row.get('supertrend_bullish', False)),
        'wavetrend_cross':       bool(row.get('wt_oversold_cross', False)),
        'ichimoku_bullish':      bool(row.get('cloud_bullish', False)),
        'fisher_cross_up':       bool(row.get('fisher_cross_up', False)),
        'lrsi_oversold':         _safe(row, 'lrsi', 0.5) < 0.15,
        'wae_bullish_exploding': bool(row.get('wae_bullish', False)) and bool(row.get('wae_exploding', False)),
        'wae_bullish':           bool(row.get('wae_bullish', False)) and not bool(row.get('wae_exploding', False)),
        'chop_trending':         bool(row.get('chop_trending', False)),
        'lrsi_mild_oversold':    0.15 <= _safe(row, 'lrsi', 0.5) < 0.25,
    }


def _extract_signals_mean_reversion(row) -> dict:
    ou_zscore  = _safe(row, 'ou_zscore',  0)
    autocorr   = _safe(row, 'autocorr_ret')
    ou_halflife = _safe(row, 'ou_halflife_minutes')
    williams_r = _safe(row, 'williams_r', -50)
    kalman_dev = _safe(row, 'kalman_dev',  0)
    avwap_dev  = _safe(row, 'avwap_dev',   0)
    rv_ratio   = _safe(row, 'rv_ratio')
    return {
        'ou_halflife':       (ou_halflife is not None and 3 <= ou_halflife <= 60),
        'ou_zscore_entry':   ou_zscore <= -1.5,
        'autocorr_negative': autocorr is not None and autocorr < -0.10,
        'williams_r':        williams_r <= -80,
        'kalman_deviation':  kalman_dev <= -0.01,
        'avwap_deviation':   avwap_dev <= -0.005,
        'mean_rev_kalman':   kalman_dev <= -0.005,
        'bb_proximity':      bool(row.get('lrsi', 0.5) is not None and
                                  _safe(row, 'lrsi', 0.5) < 0.20),
        'rv_compression':    rv_ratio is not None and rv_ratio <= 0.8,
        'lrsi_oversold':     _safe(row, 'lrsi', 0.5) < 0.15,
        'wavetrend_cross':   bool(row.get('wt_oversold_cross', False)),
    }


def _extract_signals_equity(row) -> dict:
    macd_hist = _safe(row, 'macd_std_hist', 0) or _safe(row, 'macd1_hist', 0)
    rsi       = _safe(row, 'rsi',  50)
    adx       = _safe(row, 'adx',  20)
    vol_spike = _safe(row, 'vol_spike', 1)
    close     = _safe(row, 'close', 0)
    vwap      = _safe(row, 'vwap', close)
    return {
        'equity_macd_positive': macd_hist > 0 and adx > 20,
        'equity_vwap_above':    close > vwap and vwap > 0,
        'equity_rsi_range':     35 <= rsi <= 65,
        'equity_vol_spike':     vol_spike >= 1.5,
        'equity_kst_cross':     bool(row.get('fisher_cross_up', False)),
        'supertrend_bullish':   bool(row.get('supertrend_bullish', False)),
        'chop_trending':        bool(row.get('chop_trending', False)),
        'wavetrend_cross':      bool(row.get('wt_oversold_cross', False)),
    }


SIGNAL_EXTRACTORS = {
    'crypto':          _extract_signals_crypto,
    'mean_reversion':  _extract_signals_mean_reversion,
    'equity':          _extract_signals_equity,
}


def _compute_conviction(signals: dict, strategy: str) -> int:
    """
    Compute conviction score from signal dict using the same weights as job_runner.
    No AI — pure math. Returns 0-100 int.
    """
    c = 0
    pts = SIGNAL_PRIOR_PTS  # maps signal_name → prior_pts

    if strategy in ('crypto', 'mean_reversion'):
        if signals.get('macd_consensus'):         c += 25
        if signals.get('williams_r'):             c += 20
        if signals.get('momentum_volume'):        c += 15
        if signals.get('squeeze_fired'):          c += 20
        if signals.get('rv_expansion'):           c += 15
        if signals.get('kalman_deviation'):       c += 10
        if signals.get('avwap_deviation'):        c += 10
        if signals.get('ou_halflife'):            c +=  5
        if signals.get('ou_zscore_entry'):        c += 10
        if signals.get('autocorr_negative'):      c +=  8
        if signals.get('mean_rev_kalman'):        c +=  8
        if signals.get('bb_proximity'):           c +=  8
        if signals.get('supertrend_bullish'):     c += 12
        if signals.get('wavetrend_cross'):        c += 12
        if signals.get('ichimoku_bullish'):       c +=  8
        if signals.get('fisher_cross_up'):        c +=  8
        if signals.get('lrsi_oversold'):          c +=  8
        if signals.get('wae_bullish_exploding'):  c += 10
        if signals.get('wae_bullish'):            c +=  5
        if signals.get('chop_trending'):          c +=  5
        if signals.get('lrsi_mild_oversold'):     c +=  4

    elif strategy == 'equity':
        if signals.get('equity_macd_positive'):   c += 25
        if signals.get('equity_vwap_above'):      c += 15
        if signals.get('equity_rsi_range'):       c += 10
        if signals.get('equity_vol_spike'):       c += 15
        if signals.get('equity_kst_cross'):       c += 12
        if signals.get('supertrend_bullish'):     c += 12
        if signals.get('chop_trending'):          c +=  8
        if signals.get('wavetrend_cross'):        c += 10

    return min(c, 100)


# ── Trade simulation ──────────────────────────────────────────────────────────

def _simulate_trade(
    future_df: pd.DataFrame,
    entry_price: float,
    stop_pct: float,
    target_pct: float,
) -> tuple:
    """
    Walk future bars to determine trade outcome.
    Returns: (won: bool, exit_price: float, exit_reason: str, hold_bars: int)
    """
    stop   = entry_price * (1 - stop_pct)
    target = entry_price * (1 + target_pct)

    for i, (ts, row) in enumerate(future_df.iterrows()):
        low   = float(row.get('low',   row.get('close', entry_price)))
        high  = float(row.get('high',  row.get('close', entry_price)))
        close = float(row.get('close', entry_price))

        # Check stop hit first (conservative — stops worse than targets)
        if low <= stop:
            return False, stop, 'stop_loss', i + 1
        # Check target
        if high >= target:
            return True, target, 'take_profit', i + 1

    # Time exit: exit at last close
    last_close = float(future_df.iloc[-1].get('close', entry_price))
    won = last_close > entry_price * (1 + FEE_RT_PCT)
    return won, last_close, 'time_exit', len(future_df)


# ── Per-symbol replay ─────────────────────────────────────────────────────────

def replay_symbol(
    symbol: str,
    timeframe: str,
    strategy: str,
    days: int = 90,
    min_conviction: int = 30,
    verbose: bool = False,
) -> dict:
    """
    Run the replay engine for one symbol + strategy.
    Returns summary dict with trades_attributed, wins, losses, regime breakdown.
    """
    from datetime import datetime, timezone, timedelta
    end_dt   = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)

    df_raw = get_candles(symbol, timeframe, start=start_dt, end=end_dt)
    if df_raw is None or len(df_raw) < MIN_BARS_FOR_REPLAY:
        return {'symbol': symbol, 'strategy': strategy, 'skipped': True,
                'reason': f'insufficient data ({len(df_raw) if df_raw is not None else 0} bars)'}

    # Run add_all_indicators ONCE on full dataset (real indicators, not proxies)
    try:
        df_ind = add_all_indicators(df_raw)
    except Exception as e:
        return {'symbol': symbol, 'strategy': strategy, 'skipped': True,
                'reason': f'indicator error: {e}'}

    n = len(df_ind)
    if n < WARMUP_BARS + 10:
        return {'symbol': symbol, 'strategy': strategy, 'skipped': True,
                'reason': f'too few bars after indicators ({n})'}

    extractor = SIGNAL_EXTRACTORS.get(strategy, _extract_signals_crypto)
    floor = max(min_conviction, CONVICTION_FLOORS.get(strategy, 30))

    trades_attributed = 0
    wins, losses = 0, 0
    regime_counts: dict = {}
    last_regime = 'ranging'
    last_regime_calc = 0
    i_last_entry = -MAX_HOLD_BARS  # prevent overlapping trades

    if verbose:
        print(f"  Replaying {symbol} | {strategy} | {n} bars | floor={floor}")

    for i in range(WARMUP_BARS, n - MAX_HOLD_BARS):
        # Rate-limit regime detection (expensive)
        if i - last_regime_calc >= REGIME_RECALC_N:
            window_regime = df_ind.iloc[max(0, i-100):i]
            try:
                rd = detect_regime(df=window_regime, intraday=True)
                last_regime = rd.get('regime', 'ranging')
            except Exception:
                last_regime = 'ranging'
            last_regime_calc = i

        # No overlapping trades — wait until previous trade would have closed
        if i - i_last_entry < MAX_HOLD_BARS:
            continue

        row = df_ind.iloc[i]
        price = float(row.get('close', 0))
        if price <= 0:
            continue

        # Skip trending_down (no longs in downtrend)
        if last_regime == 'trending_down':
            continue

        signals = extractor(row)
        active_count = sum(1 for v in signals.values() if v)
        if active_count == 0:
            continue

        conviction = _compute_conviction(signals, strategy)
        if conviction < floor:
            continue

        # Simulate trade outcome using real future bars
        future = df_ind.iloc[i+1:i+1+MAX_HOLD_BARS]
        if len(future) < 5:
            continue

        stop_pct   = CRYPTO_STOP_LOSS_PCT   if strategy != 'equity' else 0.025
        target_pct = CRYPTO_TAKE_PROFIT_PCT if strategy != 'equity' else 0.075

        won, exit_price, exit_reason, hold_bars = _simulate_trade(
            future, price, stop_pct, target_pct
        )

        pnl_usd = (exit_price - price) * (POSITION_SIZE_USD / price)
        fee_usd = price * (POSITION_SIZE_USD / price) * FEE_RT_PCT
        net_pnl = pnl_usd - fee_usd
        pnl_pct = (exit_price - price) / price

        # Timestamps
        entry_ts = row.name.isoformat() if hasattr(row.name, 'isoformat') else str(row.name)
        exit_row = future.iloc[min(hold_bars - 1, len(future) - 1)]
        exit_ts  = exit_row.name.isoformat() if hasattr(exit_row.name, 'isoformat') else str(exit_row.name)
        hold_min = hold_bars   # on 1-min candles, bars ≈ minutes

        # Record attribution with REAL regime (not 'unknown')
        try:
            record_trade_attribution(
                symbol=symbol,
                strategy=strategy,
                regime=last_regime,
                signals=signals,
                won=won,
                pnl_usd=round(pnl_usd, 4),
                pnl_pct=pnl_pct,
                fee_usd=round(fee_usd, 4),
                conviction=conviction,
                entry_price=price,
                exit_price=exit_price,
                entry_ts=entry_ts,
                exit_ts=exit_ts,
                exit_reason=exit_reason,
                hold_minutes=hold_min,
                source='replay',
                paper=True,
                trade_ref=f"replay_{symbol}_{strategy}_{entry_ts[:16]}",
                lesson='',
            )
            trades_attributed += 1
            if net_pnl > 0:
                wins += 1
            else:
                losses += 1
            regime_counts[last_regime] = regime_counts.get(last_regime, 0) + 1
            i_last_entry = i
        except Exception as e:
            if verbose:
                print(f"    attribution error bar {i}: {e}")

    win_rate = wins / max(trades_attributed, 1)
    return {
        'symbol': symbol,
        'strategy': strategy,
        'skipped': False,
        'trades_attributed': trades_attributed,
        'wins': wins,
        'losses': losses,
        'win_rate': win_rate,
        'regime_counts': regime_counts,
    }


# ── Main entry point ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Historical Replay Engine — seeds signal_stats with real regime data')
    parser.add_argument('--days',           type=int,   default=90,
                        help='Days of history to replay (default: 90)')
    parser.add_argument('--symbol',         default=None,
                        help='Replay one symbol only')
    parser.add_argument('--strategy',       default=None,
                        choices=['crypto', 'mean_reversion', 'equity'],
                        help='Replay one strategy only')
    parser.add_argument('--min-conviction', type=int,   default=30,
                        help='Minimum conviction to record an entry (default: 30)')
    parser.add_argument('--timeframe',      default='ONE_MINUTE',
                        help='Timeframe to use from price_archive (default: ONE_MINUTE)')
    parser.add_argument('--dry-run',        action='store_true',
                        help='Show plan without running')
    parser.add_argument('--verbose',        action='store_true',
                        help='Print per-symbol progress')
    args = parser.parse_args()

    # ── Discover available symbols ────────────────────────────────────────────
    summary = get_summary()
    if not summary:
        print("No data in price_archive. Run the live system first to accumulate candles,")
        print("or run: python3 scripts/seed_intelligence.py --days 90 (to fetch + archive data)")
        return

    # Filter by requested timeframe
    available = [s for s in summary if s['timeframe'] == args.timeframe]
    if args.symbol:
        available = [s for s in available if s['symbol'] == args.symbol]

    if not available:
        print(f"No archived data for timeframe={args.timeframe}")
        print("Available:", [(s['symbol'], s['timeframe'], s['rows']) for s in summary[:10]])
        return

    # Build work plan
    strategies_to_run = [args.strategy] if args.strategy else ['crypto', 'mean_reversion']
    work_plan = []
    for sym_info in available:
        sym = sym_info['symbol']
        rows = sym_info['rows']
        if rows < MIN_BARS_FOR_REPLAY:
            continue
        for strat in strategies_to_run:
            # Equity strategy only makes sense for equity symbols
            if strat == 'equity' and ('-USDC' in sym or '-USD' in sym):
                continue
            if strat in ('crypto', 'mean_reversion') and '-' not in sym and '=' not in sym:
                continue  # skip equity symbols for crypto strategies
            work_plan.append({
                'symbol': sym,
                'strategy': strat,
                'rows': rows,
            })

    total = len(work_plan)
    print(f"\n{'='*65}")
    print(f"  HISTORICAL REPLAY ENGINE — {total} runs | {args.days}d | {args.timeframe}")
    print(f"  Correctly seeds signal_stats with real regime-bucketed attributions")
    print(f"{'='*65}\n")

    if args.dry_run:
        print("DRY RUN — work plan:")
        for i, item in enumerate(work_plan):
            print(f"  {i+1:3}. {item['symbol']:18} {item['strategy']:18} ({item['rows']:,} bars)")
        print(f"\n{total} total runs. Remove --dry-run to execute.")
        return

    # ── Init tables + run ─────────────────────────────────────────────────────
    init_learning_tables()

    all_results = []
    total_attributed = 0
    errors = 0

    for i, item in enumerate(work_plan):
        sym   = item['symbol']
        strat = item['strategy']
        print(f"\n  [{i+1}/{total}] {sym} | {strat}...", end=' ', flush=True)
        try:
            result = replay_symbol(
                symbol=sym,
                timeframe=args.timeframe,
                strategy=strat,
                days=args.days,
                min_conviction=args.min_conviction,
                verbose=args.verbose,
            )
            all_results.append(result)

            if result.get('skipped'):
                print(f"SKIPPED — {result.get('reason','')}")
            else:
                ta = result['trades_attributed']
                wr = result['win_rate']
                rc = result.get('regime_counts', {})
                total_attributed += ta
                print(f"attributed={ta} wr={wr:.1%} regimes={rc}")
        except Exception as e:
            print(f"ERROR: {e}")
            errors += 1
        time.sleep(0.1)

    # ── Summary ───────────────────────────────────────────────────────────────
    done    = [r for r in all_results if not r.get('skipped')]
    skipped = [r for r in all_results if r.get('skipped')]

    regime_totals: dict = {}
    for r in done:
        for regime, cnt in r.get('regime_counts', {}).items():
            regime_totals[regime] = regime_totals.get(regime, 0) + cnt

    print(f"\n{'='*65}")
    print(f"  REPLAY COMPLETE")
    print(f"{'='*65}")
    print(f"  Runs:       {len(done)} completed | {len(skipped)} skipped | {errors} errors")
    print(f"  Attributed: {total_attributed:,} trades into signal_stats (source='replay')")
    print(f"  Regime breakdown: {json.dumps(regime_totals)}")
    print()
    print(f"  These attributions go to REAL regime buckets (trending/ranging/volatile).")
    print(f"  Live conviction scoring now has evidence-backed Bayesian priors.")
    print()

    # Print signal leaderboard
    try:
        from learning.signal_performance import get_signal_report
        report = get_signal_report(min_fires=3, source='replay')
        if report:
            print(f"  Signal Leaderboard (replay source, ≥3 fires):")
            print(f"  {'Signal':<28} {'Regime':<12} {'Fires':>5} {'Win%':>6} {'Avg P&L':>8} {'Bayes':>7}")
            print(f"  {'-'*28} {'-'*12} {'-'*5} {'-'*6} {'-'*8} {'-'*7}")
            for s in sorted(report, key=lambda x: x.get('bayesian_pts') or 0, reverse=True)[:15]:
                wr = f"{s['win_rate']*100:.0f}%" if s['win_rate'] else 'N/A'
                ap = f"${s['avg_pnl']:+.3f}" if s['avg_pnl'] else 'N/A'
                bp = f"{s['bayesian_pts']:.1f}" if s['bayesian_pts'] else 'N/A'
                print(f"  {s['signal_name']:<28} {(s.get('regime') or 'any'):<12} "
                      f"{s['fires']:>5} {wr:>6} {ap:>8} {bp:>7}")
    except Exception as e:
        print(f"  Signal report unavailable: {e}")

    print(f"\nRun 'python3 main.py' to start trading with evidence-backed signal weights.\n")


if __name__ == '__main__':
    main()
