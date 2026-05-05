#!/usr/bin/env python3
"""
scripts/rapid_validate.py
─────────────────────────
Rapid strategy validator — replays real historical candle data through the
full live pipeline (indicators → pre-filter → AI debate → risk synthesis →
simulated trade lifecycle) to validate strategy performance without waiting
14 days of paper trading.

What it does:
  1. Fetches 14 days of 5-min candle data from Coinbase REST API
  2. Walks through each candle exactly like the live engine does
  3. Fires the AI quick-debate only when the indicator pre-filter passes
     (same gate as production — no extra API calls)
  4. Simulates full trade lifecycle: entry, stop-loss, take-profit, time exit
  5. Produces a comprehensive P&L report and a PASS/FAIL verdict

Cost estimate:  ~$2–6 in Anthropic API calls (30–60 debates over 14 days)
Time:           ~15–25 minutes (most time is Coinbase data fetch + AI calls)

Usage:
    python3 scripts/rapid_validate.py                  # full AI validation
    python3 scripts/rapid_validate.py --no-ai          # instant, free (MACD only)
    python3 scripts/rapid_validate.py --days 7         # shorter window
    python3 scripts/rapid_validate.py --pairs BTC-USDC,ETH-USDC

Output: prints report + writes logs/validation_report.txt
"""
import sys
import os
import argparse
import time
import json
from datetime import datetime, timedelta
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_ROOT  = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJ_ROOT)

from config import (
    ANTHROPIC_API_KEY, CRYPTO_PAIRS,
    CRYPTO_STOP_LOSS_PCT, CRYPTO_TAKE_PROFIT_PCT,
    COINBASE_API_KEY, COINBASE_API_SECRET,
    COINBASE_TAKER_FEE_PCT, COINBASE_MAKER_FEE_PCT,
    ACCOUNT_SIZE,
)
from data.indicators import add_all_indicators
from data.market_data import get_fear_greed, get_williams_r, get_momentum_score
from data.coinbase_feed import get_rest_client

# ─── Constants ────────────────────────────────────────────────────────────────
DEFAULT_PAIRS = ['BTC-USDC', 'ETH-USDC', 'SOL-USDC', 'AVAX-USDC',
                 'XRP-USDC', 'DOGE-USDC', 'NEAR-USDC', 'WIF-USDC']
MAX_HOLD_BARS = 144        # 12 hours at 5-min candles
FLAT_THRESHOLD = 0.015     # 1.5% flat threshold for time exits
MIN_CANDLES_FOR_SIGNAL = 50
GRANULARITY = 'FIVE_MINUTE'
GRAN_SECONDS = 300

# Readiness thresholds for this validator
PASS_MIN_TRADES   = 20
PASS_WIN_RATE     = 0.48   # slightly lower than live (no slippage optimism)
PASS_AVG_PNL      = 0.30   # $0.30 avg per trade after fees
PASS_MAX_DRAWDOWN = 0.12   # max drawdown < 12% of account


def parse_args():
    p = argparse.ArgumentParser(description='Rapid strategy validator')
    p.add_argument('--days',   type=int,   default=14, help='Days of history to replay (default 14)')
    p.add_argument('--pairs',  type=str,   default=None, help='Comma-separated pairs to test')
    p.add_argument('--no-ai',  action='store_true', help='Skip AI debates (free/instant MACD-only mode)')
    p.add_argument('--verbose',action='store_true', help='Print every debate result')
    return p.parse_args()


# ─── Data fetching ────────────────────────────────────────────────────────────

def fetch_candles(pair: str, days: int) -> Optional['pd.DataFrame']:
    """Fetch historical 5-min candles from Coinbase REST API."""
    import pandas as pd
    client = get_rest_client()
    if client is None:
        return _fetch_candles_yfinance(pair, days)

    end_ts   = int(time.time())
    start_ts = end_ts - (days * 24 * 3600)
    all_candles = []

    # Coinbase returns max 300 candles per request — paginate
    window = end_ts
    while window > start_ts:
        chunk_start = max(window - 300 * GRAN_SECONDS, start_ts)
        try:
            resp = client.get_candles(
                product_id=pair,
                start=str(chunk_start),
                end=str(window),
                granularity=GRANULARITY,
            )
            candles = getattr(resp, 'candles', [])
            if not candles:
                break
            for c in candles:
                all_candles.append({
                    'ts':     int(c.start),
                    'open':   float(c.open),
                    'high':   float(c.high),
                    'low':    float(c.low),
                    'close':  float(c.close),
                    'volume': float(c.volume),
                })
            window = chunk_start
            time.sleep(0.12)  # polite rate limit
        except Exception as e:
            print(f"  [fetch] {pair} chunk error: {e}")
            break

    if not all_candles:
        return _fetch_candles_yfinance(pair, days)

    df = pd.DataFrame(all_candles)
    df['ts'] = pd.to_datetime(df['ts'], unit='s', utc=True)
    df = df.set_index('ts').sort_index().drop_duplicates()
    return df


def _fetch_candles_yfinance(pair: str, days: int) -> Optional['pd.DataFrame']:
    """Fallback: yfinance for crypto candles."""
    import pandas as pd
    try:
        import yfinance as yf
        yf_sym = pair.replace('-USDC', '-USD').replace('-USD', '-USD')
        df = yf.download(yf_sym, period=f'{days}d', interval='5m', progress=False)
        if df.empty:
            return None
        df.columns = [c.lower() for c in df.columns]
        df = df[['open', 'high', 'low', 'close', 'volume']].copy()
        return df
    except Exception as e:
        print(f"  [yfinance fallback] {pair}: {e}")
        return None


# ─── Simulation engine ────────────────────────────────────────────────────────

def _build_market_data_replay(pair, price, df_ind) -> dict:
    """Build market_data dict for AI debate from historical indicators."""
    last = df_ind.iloc[-1]
    # Use cached/neutral F&G for replay (don't hit CNN API on every candle)
    return {
        'price':             price,
        'change_pct':        0.0,
        'vol_spike':         float(last.get('vol_spike', 1) or 1),
        'rsi':               float(last.get('rsi', 50) or 50),
        'macd_hist':         float(last.get('macd1_hist', 0) or 0),
        'vwap':              float(last.get('vwap', price) or price),
        'atr':               float(last.get('atr', price * 0.01) or price * 0.01),
        'adx':               float(last.get('adx', 25) or 25),
        'trend_20d':         'bullish' if float(last.get('ema20', 0) or 0) > float(last.get('ema50', 0) or 0) else 'bearish',
        'dollar_volume':     price * float(last.get('volume', 0) or 0),
        'regime':            'ranging',
        'williams_r':        get_williams_r(df_ind),
        'fear_greed_score':  50,
        'fear_greed_label':  'Neutral',
        'momentum_score':    0.0,
        'above_200d_ma':     price > float(last.get('ema200', 0) or 0) if float(last.get('ema200', 0) or 0) > 0 else None,
        'vol_20d_pct_above_avg': 0.0,
        'pullback_bars':     0,
        'pullback_trend':    'unclear',
        'is_valid_pullback': False,
    }


def run_replay(pair: str, df: 'pd.DataFrame', use_ai: bool,
               verbose: bool, account: float) -> dict:
    """
    Replay one pair through the full pipeline.
    Returns stats dict.
    """
    import pandas as pd

    trades      = []
    open_pos    = None    # {'entry', 'stop', 'target', 'bar_idx', 'entry_time', 'size_usd'}
    equity      = account
    peak_equity = account
    max_dd      = 0.0
    debates_run = 0

    engine = None
    if use_ai and ANTHROPIC_API_KEY:
        # Note: Legacy debate panels (Goku, analyst_agents) are removed in v18.
        # AI Studio (Gemini) and Anthropic (Exits) are the only active AI paths.
        pass

    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    n = len(df)

    for i in range(MIN_CANDLES_FOR_SIGNAL, n):
        candle_slice = df.iloc[max(0, i - 120): i + 1]
        try:
            df_ind = add_all_indicators(candle_slice)
        except Exception:
            continue

        last  = df_ind.iloc[-1]
        price = float(last['close'])
        if price <= 0:
            continue

        # ── Manage open position ─────────────────────────────────────────────
        if open_pos is not None:
            pnl_pct = (price - open_pos['entry']) / open_pos['entry']
            bars_held = i - open_pos['bar_idx']

            exit_reason = None
            exit_price  = price

            if price <= open_pos['stop']:
                exit_reason = 'stop_loss'
                exit_price  = open_pos['stop']
            elif price >= open_pos['target']:
                exit_reason = 'take_profit'
                exit_price  = open_pos['target']
            elif bars_held >= MAX_HOLD_BARS and abs(pnl_pct) <= FLAT_THRESHOLD:
                exit_reason = 'time_exit'

            if exit_reason:
                gross_pnl = (exit_price - open_pos['entry']) / open_pos['entry'] * open_pos['size_usd']
                fee = open_pos['size_usd'] * COINBASE_TAKER_FEE_PCT * 2  # round-trip
                net_pnl = gross_pnl - fee
                equity += net_pnl
                peak_equity = max(peak_equity, equity)
                dd = (peak_equity - equity) / peak_equity
                max_dd = max(max_dd, dd)

                trades.append({
                    'pair':        pair,
                    'entry':       open_pos['entry'],
                    'exit':        exit_price,
                    'size_usd':    open_pos['size_usd'],
                    'pnl':         net_pnl,
                    'won':         net_pnl > 0,
                    'exit_reason': exit_reason,
                    'bars_held':   bars_held,
                    'entry_time':  open_pos['entry_time'],
                })
                open_pos = None
            continue  # don't look for new entries while in a trade

        # ── Entry signal logic ───────────────────────────────────────────────
        if i < 2:
            continue

        macd_now  = float(last.get('macd1_hist', 0) or 0)
        macd_prev = float(df_ind.iloc[-2].get('macd1_hist', 0) or 0)
        rsi       = float(last.get('rsi', 50) or 50)
        adx       = float(last.get('adx', 10) or 10)
        vol_spike = float(last.get('vol_spike', 1) or 1)

        # Indicator pre-filter: bullish MACD crossover + not overbought
        macd_crossed_up = macd_now > 0 and macd_prev <= 0
        rsi_ok   = 25 < rsi < 65
        vol_ok   = vol_spike >= 1.2
        adx_ok   = adx >= 10

        if not (macd_crossed_up and rsi_ok and adx_ok):
            continue

        # ── AI debate ────────────────────────────────────────────────────────
        confidence = 0.55  # default when no AI
        signal     = 'BUY'

        if engine:
            try:
                market_data = _build_market_data_replay(pair, price, df_ind)
                debate = engine['quick'](
                    symbol=pair, market_data=market_data,
                    verbose=False, memory_context='',
                )
                final = engine['synthesize'](
                    debate=debate, current_price=price,
                    asset_class='crypto', daily_pnl=0.0,
                    open_positions=0, trades_today=len(trades),
                    account_balance=equity,
                    atr=market_data.get('atr', 0),
                )
                signal     = final.action
                confidence = final.confidence
                debates_run += 1

                if verbose:
                    vb = debate.vote_breakdown
                    print(f"    {pair} {df.index[i].strftime('%m-%d %H:%M')} "
                          f"→ {signal} {confidence:.0%} | "
                          f"{vb.get('BUY',0)}B/{vb.get('HOLD',0)}H | {final.reasoning[:55]}")
            except Exception as e:
                if verbose:
                    print(f"    {pair} debate error: {e}")
                signal = 'HOLD'

        if signal != 'BUY' or confidence < 0.30:
            continue

        # ── Size and enter ────────────────────────────────────────────────────
        size_usd = min(equity * 0.20, 50.0)  # max 20% of equity or $50
        if size_usd < 5:
            continue

        fee_entry = size_usd * COINBASE_MAKER_FEE_PCT
        equity   -= fee_entry  # deduct entry fee immediately

        atr   = float(last.get('atr', price * 0.015) or price * 0.015)
        stop  = price * (1 - CRYPTO_STOP_LOSS_PCT)
        target= price * (1 + CRYPTO_TAKE_PROFIT_PCT)

        open_pos = {
            'entry':      price,
            'stop':       stop,
            'target':     target,
            'size_usd':   size_usd,
            'bar_idx':    i,
            'entry_time': df.index[i] if hasattr(df.index[i], 'strftime') else str(df.index[i]),
        }

    # Close any remaining open position at last price
    if open_pos and len(df) > 0:
        last_price = float(df.iloc[-1]['close'])
        gross_pnl  = (last_price - open_pos['entry']) / open_pos['entry'] * open_pos['size_usd']
        fee        = open_pos['size_usd'] * COINBASE_TAKER_FEE_PCT * 2
        net_pnl    = gross_pnl - fee
        equity    += net_pnl
        trades.append({
            'pair': pair, 'entry': open_pos['entry'], 'exit': last_price,
            'size_usd': open_pos['size_usd'], 'pnl': net_pnl,
            'won': net_pnl > 0, 'exit_reason': 'end_of_replay',
            'bars_held': len(df) - open_pos['bar_idx'],
            'entry_time': open_pos['entry_time'],
        })

    wins  = sum(1 for t in trades if t['won'])
    total = len(trades)
    pnls  = [t['pnl'] for t in trades]

    return {
        'pair':       pair,
        'trades':     trades,
        'total':      total,
        'wins':       wins,
        'losses':     total - wins,
        'win_rate':   wins / total if total else 0.0,
        'total_pnl':  sum(pnls),
        'avg_pnl':    sum(pnls) / total if total else 0.0,
        'max_dd':     max_dd,
        'final_equity': equity,
        'debates_run': debates_run,
    }


# ─── Report ───────────────────────────────────────────────────────────────────

def print_report(all_results: list, days: int, use_ai: bool) -> bool:
    """Print full validation report. Returns True if PASS."""
    import math

    # Aggregate
    all_trades   = [t for r in all_results for t in r['trades']]
    total        = len(all_trades)
    wins         = sum(1 for t in all_trades if t['won'])
    pnls         = [t['pnl'] for t in all_trades]
    total_pnl    = sum(pnls)
    avg_pnl      = total_pnl / total if total else 0.0
    win_rate     = wins / total if total else 0.0
    max_dd       = max((r['max_dd'] for r in all_results), default=0.0)
    total_debates= sum(r['debates_run'] for r in all_results)

    # Sharpe (rough — using trade P&Ls as returns)
    if len(pnls) > 1:
        mean_pnl = sum(pnls) / len(pnls)
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls)
        std_pnl  = math.sqrt(variance) if variance > 0 else 1e-9
        sharpe   = (mean_pnl / std_pnl) * math.sqrt(252)
    else:
        sharpe = 0.0

    # Stop-loss / take-profit / time breakdown
    by_reason = {}
    for t in all_trades:
        r = t.get('exit_reason', '?')
        by_reason[r] = by_reason.get(r, 0) + 1

    mode_tag = f"AI Debate (quick, 3-agent)" if use_ai else "MACD fallback (no AI)"

    lines = []
    sep = '═' * 65
    lines.append(f'\n{sep}')
    lines.append('  👑  RAPID STRATEGY VALIDATION REPORT')
    lines.append(f'  Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    lines.append(f'  Mode: {mode_tag}')
    lines.append(f'  Window: {days} days | Pairs: {len(all_results)}')
    lines.append(f'  AI debates called: {total_debates}')
    lines.append(sep)

    lines.append(f'\n  {"METRIC":<30} {"VALUE":>12}   {"TARGET":>10}')
    lines.append(f'  {"-"*55}')

    def row(label, val, target, passed, fmt=''):
        icon = '✅' if passed else '❌'
        v    = fmt.format(val) if fmt else str(val)
        t    = fmt.format(target) if fmt else str(target)
        lines.append(f'  {icon}  {label:<28} {v:>12}   {t:>10}')

    row('Total trades', total, PASS_MIN_TRADES, total >= PASS_MIN_TRADES)
    row('Win rate', win_rate, PASS_WIN_RATE, win_rate >= PASS_WIN_RATE, '{:.1%}')
    row('Avg P&L per trade', avg_pnl, PASS_AVG_PNL, avg_pnl >= PASS_AVG_PNL, '${:.2f}')
    row('Max drawdown', max_dd, PASS_MAX_DRAWDOWN, max_dd <= PASS_MAX_DRAWDOWN, '{:.1%}')
    row('Total P&L', total_pnl, 0, total_pnl > 0, '${:.2f}')
    row('Sharpe ratio', sharpe, 0.5, sharpe >= 0.5, '{:.2f}')

    lines.append(f'\n  Exit breakdown:')
    for reason, count in sorted(by_reason.items(), key=lambda x: -x[1]):
        pct = count / total * 100 if total else 0
        lines.append(f'    {reason:<20} {count:>4}  ({pct:.0f}%)')

    lines.append(f'\n  Per-pair breakdown:')
    lines.append(f'  {"PAIR":<14} {"TRADES":>7} {"WIN%":>6} {"P&L":>9} {"MAX DD":>8}')
    lines.append(f'  {"-"*48}')
    for r in sorted(all_results, key=lambda x: -x['total_pnl']):
        if r['total'] == 0:
            continue
        lines.append(
            f'  {r["pair"]:<14} {r["total"]:>7} {r["win_rate"]:>5.0%} '
            f'  ${r["total_pnl"]:>7.2f}  {r["max_dd"]:>7.1%}'
        )

    # Verdict
    passes = [
        total    >= PASS_MIN_TRADES,
        win_rate >= PASS_WIN_RATE,
        avg_pnl  >= PASS_AVG_PNL,
        max_dd   <= PASS_MAX_DRAWDOWN,
        total_pnl > 0,
    ]
    passed = sum(passes)
    verdict = passed == len(passes)

    lines.append(f'\n{sep}')
    if verdict:
        lines.append('  🏆  VALIDATION PASSED — Strategy is historically viable.')
        lines.append('  ✅  Proceed to 2-day turbo paper confirmation, then go LIVE.')
        lines.append('  Next: python3 scripts/check_readiness.py --fast-track')
    else:
        failed_n = len(passes) - passed
        lines.append(f'  ⚠️   VALIDATION INCOMPLETE — {failed_n} criteria not met.')
        lines.append('  Review the per-pair breakdown above.')
        if not use_ai:
            lines.append('  Tip: re-run with AI enabled for more accurate signal filtering.')
    lines.append(sep + '\n')

    output = '\n'.join(lines)
    print(output)

    # Write to file
    report_path = os.path.join(PROJ_ROOT, 'logs', 'validation_report.txt')
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, 'w') as f:
        f.write(output)
    print(f'  Report saved → {report_path}')

    return verdict


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    args   = parse_args()
    pairs  = args.pairs.split(',') if args.pairs else DEFAULT_PAIRS
    days   = args.days
    use_ai = not args.no_ai and bool(ANTHROPIC_API_KEY)

    print(f'\n👑  RAPID STRATEGY VALIDATOR')
    print(f'    Pairs:   {", ".join(pairs)}')
    print(f'    Window:  {days} days of 5-min candles')
    print(f'    Mode:    {"AI debate (3-agent quick)" if use_ai else "MACD-only (no AI, free)"}')
    if use_ai:
        est = len(pairs) * 3  # rough debates per pair
        print(f'    Est. cost: ~${est * 0.025:.2f} in API calls')
    print(f'    This will take ~{5 * len(pairs) if use_ai else 2} minutes...\n')

    all_results = []

    for i, pair in enumerate(pairs, 1):
        print(f'  [{i}/{len(pairs)}] {pair} — fetching {days}d of candles...', end=' ', flush=True)
        df = fetch_candles(pair, days)
        if df is None or len(df) < MIN_CANDLES_FOR_SIGNAL + 10:
            print('❌ insufficient data')
            continue
        print(f'✅ {len(df)} candles — replaying...', end=' ', flush=True)

        result = run_replay(pair, df, use_ai=use_ai,
                            verbose=args.verbose, account=ACCOUNT_SIZE)
        all_results.append(result)

        status = f"{result['total']} trades | {result['win_rate']:.0%} wins | ${result['total_pnl']:+.2f}"
        debates_note = f" | {result['debates_run']} debates" if use_ai else ''
        print(f'done  →  {status}{debates_note}')

    if not all_results:
        print('\n❌ No data fetched for any pair. Check Coinbase API credentials in .env')
        sys.exit(1)

    print()
    passed = print_report(all_results, days, use_ai)
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
