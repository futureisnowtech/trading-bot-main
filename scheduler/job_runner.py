"""
scheduler/job_runner.py — The engine. Runs forever.

Full pipeline:
  Equity (60s, market hours): auto_screener → debate → synthesize → execute
  Crypto (5min, 24/7):        candles → quick_debate → synthesize → execute
  Exits (every candle close):  extended_thinking exit review on ALL open positions
  Futures (60s, market hours): opening range breakout → debate confirmation → execute
  Watchdog (15min):           alert if no scan completed
"""
import time
import traceback
from datetime import datetime
from typing import Optional
import pytz
import schedule
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    CRYPTO_PAIRS, PAPER_TRADING, ACCOUNT_SIZE,
    EQUITY_SCAN_INTERVAL_SECONDS, CRYPTO_SCAN_INTERVAL_SECONDS,
    FUTURES_SCAN_INTERVAL_SECONDS, MARKET_TIMEZONE,
    EQUITY_POSITION_SIZE_USD, CRYPTO_POSITION_SIZE_USD,
    CRYPTO_CANDLE_GRANULARITY, FUTURES_ENABLED, ANTHROPIC_API_KEY,
    WATCHDOG_INTERVAL_SECONDS, COINBASE_MAKER_FEE_PCT
)
from data.market_data import is_market_open, is_in_no_trade_window, get_bars
from data.auto_screener import discover_candidates
from data.coinbase_feed import get_candles, get_current_price as cb_price
from data.indicators import add_all_indicators
from strategies.crypto_macd import CryptoMACDStrategy
from strategies.futures_scalper import FuturesScalperStrategy
from risk.risk_manager import get_risk_manager
from execution.webull_broker import get_webull_broker
from execution.coinbase_broker import get_coinbase_broker
from logging_db.trade_logger import (
    get_todays_trades, get_todays_pnl, get_todays_fees,
    log_event, log_signal, get_win_rate, get_all_time_stats, get_today_stats,
    get_monthly_api_cost,
)
from alerts.telegram_alert import alert_system, alert_daily_summary
from memory.trade_memory import retrieve_similar_experiences, format_memory_context, store_trade_experience

_crypto_strategy = CryptoMACDStrategy(variant='consensus')
_futures_strategy = FuturesScalperStrategy()


def _debate_available():
    if not ANTHROPIC_API_KEY:
        return None
    try:
        from strategies.ai_agents.debate_engine import run_debate, run_quick_debate
        from strategies.ai_agents.risk_synthesizer import synthesize_final_decision, should_use_full_debate
        from strategies.ai_agents.exit_review import run_exit_review
        return {
            'debate': run_debate, 'quick': run_quick_debate,
            'synthesize': synthesize_final_decision, 'full_check': should_use_full_debate,
            'exit': run_exit_review,
        }
    except Exception as e:
        print(f"[scheduler] Debate engine unavailable: {e}")
        return None


def _build_market_data(symbol, price, df_ind, change_pct=0, regime='ranging') -> dict:
    last = df_ind.iloc[-1]
    return {
        'price': price,
        'change_pct': change_pct,
        'vol_spike': float(last.get('vol_spike', 1) or 1),
        'rsi': float(last.get('rsi', 50) or 50),
        'macd_hist': float(last.get('macd_std_hist', 0) or last.get('macd1_hist', 0) or 0),
        'vwap': float(last.get('vwap', price) or price),
        'atr': float(last.get('atr', price * 0.01) or price * 0.01),
        'adx': float(last.get('adx', 25) or 25),
        'trend_20d': 'bullish' if float(last.get('ema20', 0) or 0) > float(last.get('ema50', 0) or 0) else 'bearish',
        'dollar_volume': price * float(last.get('volume', 0) or 0),
        'regime': regime,
    }


# ─── EXIT MONITOR — runs on every position every scan cycle ─────────────────

def monitor_exits_with_ai(engine) -> None:
    """Check all open positions for AI-driven exit signals."""
    rm = get_risk_manager()
    all_pos = rm.get_all_positions()
    wb = get_webull_broker()
    cb = get_coinbase_broker()

    for symbol, pos in list(all_pos.get('equity', {}).items()):
        try:
            df = get_bars(symbol, interval='5m', period='1d')
            if df is None or df.empty:
                continue
            df_ind = add_all_indicators(df)
            last = df_ind.iloc[-1]
            price = float(last['close'])
            rm.update_high('equity_momentum', symbol, price)

            # Hard rule check first
            should_exit, exit_reason = rm.should_exit('equity_momentum', symbol, price)
            if should_exit:
                _execute_equity_exit(wb, rm, symbol, pos, price, exit_reason, 'equity_momentum')
                continue

            # AI exit review
            ts_entry = pos.get('ts_entry', '')
            try:
                from datetime import datetime as dt
                entry_dt = dt.fromisoformat(ts_entry)
                tz = pytz.timezone(MARKET_TIMEZONE)
                mins_in = int((datetime.now(tz) - entry_dt.replace(tzinfo=tz if not entry_dt.tzinfo else None)).total_seconds() / 60)
            except Exception:
                mins_in = 30

            if engine and mins_in >= 5:  # Wait at least 5 min before AI exit review
                market_data = _build_market_data(symbol, price, df_ind)
                review = engine['exit'](
                    symbol=symbol, strategy='equity_momentum',
                    entry_price=pos['entry'], current_price=price,
                    stop_loss=pos['stop'], take_profit=pos['target'],
                    entry_reason=pos.get('entry_reason', ''),
                    time_in_trade_minutes=mins_in,
                    market_data=market_data, verbose=True
                )
                if review.get('should_exit'):
                    _execute_equity_exit(wb, rm, symbol, pos, price, review['reason'], 'equity_momentum')

        except Exception as e:
            print(f"[exit_monitor] equity error {symbol}: {e}")

    for pid, pos in list(all_pos.get('crypto', {}).items()):
        try:
            price = cb_price(pid) or 0
            if not price:
                continue
            rm.update_high('crypto_macd_consensus', pid, price)

            should_exit, exit_reason = rm.should_exit('crypto_macd_consensus', pid, price)
            if should_exit:
                _execute_crypto_exit(cb, rm, pid, pos, price, exit_reason, 'crypto_macd_consensus')
                continue

            if engine:
                df = get_candles(pid, CRYPTO_CANDLE_GRANULARITY, 50)
                if df is not None and len(df) >= 20:
                    df_ind = add_all_indicators(df)
                    market_data = _build_market_data(pid, price, df_ind)
                    ts_entry = pos.get('ts_entry', '')
                    try:
                        from datetime import datetime as dt
                        entry_dt = dt.fromisoformat(ts_entry)
                        tz = pytz.timezone(MARKET_TIMEZONE)
                        mins_in = int((datetime.now(tz) - entry_dt.replace(tzinfo=tz if not entry_dt.tzinfo else None)).total_seconds() / 60)
                    except Exception:
                        mins_in = 10

                    if mins_in >= 5:
                        review = engine['exit'](
                            symbol=pid, strategy='crypto_macd_consensus',
                            entry_price=pos['entry'], current_price=price,
                            stop_loss=pos['stop'], take_profit=pos['target'],
                            entry_reason=pos.get('entry_reason', ''),
                            time_in_trade_minutes=mins_in,
                            market_data=market_data, verbose=False
                        )
                        if review.get('should_exit'):
                            _execute_crypto_exit(cb, rm, pid, pos, price, review['reason'], 'crypto_macd_consensus')

        except Exception as e:
            print(f"[exit_monitor] crypto error {pid}: {e}")


def _execute_equity_exit(wb, rm, symbol, pos, price, reason, strategy):
    result = wb.sell_limit(symbol=symbol, qty=pos['qty'],
                           limit_price=price * 0.999, strategy=strategy,
                           entry_price=pos['entry'], reason=reason)
    if result:
        closed = rm.close_position(strategy, symbol)
        pnl = (price - pos['entry']) * pos['qty']
        store_trade_experience(
            symbol=symbol, strategy=strategy,
            entry_reason=pos.get('entry_reason', ''),
            exit_reason=reason, pnl_usd=pnl,
            rsi=0, macd_hist=0, adx=0, vol_spike=0,
        )
        print(f"[equity] ✅ EXITED {symbol} | {reason} | P&L: ${pnl:+.2f}")


def _execute_crypto_exit(cb, rm, pid, pos, price, reason, strategy):
    direction = pos.get('direction', 'LONG')
    if direction == 'SHORT':
        # Paper short exit: log as BUY to close, PnL = entry - exit
        pnl = (pos['entry'] - price) * pos['qty']
        from logging_db.trade_logger import log_trade
        log_trade(strategy, 'coinbase', pid, 'BUY', 'LIMIT',
                  pos['qty'], price,
                  fee_usd=price * pos['qty'] * COINBASE_MAKER_FEE_PCT,
                  pnl_usd=pnl, paper=PAPER_TRADING,
                  notes=f'SHORT exit | {reason[:100]}')
        rm.close_position(strategy, pid)
        store_trade_experience(symbol=pid, strategy=strategy,
                               entry_reason=pos.get('entry_reason', ''),
                               exit_reason=reason, pnl_usd=pnl)
        # Alert was missing for SHORT exits — fixed
        try:
            from alerts.telegram_alert import alert_trade_closed
            alert_trade_closed(strategy, pid, 'SELL', pos['qty'],
                               pos['entry'], price, pnl, reason)
        except Exception:
            pass
        print(f"[crypto] ✅ SHORT CLOSED {pid} | {reason} | P&L: ${pnl:+.2f}")
        return

    result = cb.sell_limit(product_id=pid, base_size=pos['qty'],
                           limit_price=price * 0.999, strategy=strategy,
                           entry_price=pos['entry'], reason=reason)
    if result:
        rm.close_position(strategy, pid)
        pnl = (price - pos['entry']) * pos['qty']
        store_trade_experience(
            symbol=pid, strategy=strategy,
            entry_reason=pos.get('entry_reason', ''),
            exit_reason=reason, pnl_usd=pnl,
        )
        print(f"[crypto] ✅ EXITED {pid} | {reason} | P&L: ${pnl:+.2f}")


# ─── EQUITY SCAN ─────────────────────────────────────────────────────────────

def run_equity_scan() -> None:
    if not is_market_open() or is_in_no_trade_window():
        return
    rm = get_risk_manager()
    if rm.is_halted:
        return

    engine = _debate_available()
    wb = get_webull_broker()

    # Exit monitor first
    monitor_exits_with_ai(engine)

    # Auto-discover candidates
    try:
        candidates = discover_candidates(max_results=5)
    except Exception as e:
        print(f"[equity_scan] Screener error: {e}")
        rm.ping()
        return

    win_rate = get_win_rate(lookback_days=14, paper=PAPER_TRADING)
    use_full = engine['full_check'](ACCOUNT_SIZE, win_rate) if engine else False

    for candidate in candidates[:3]:
        symbol = candidate['symbol']
        if rm.get_position('equity_momentum', symbol):
            continue

        try:
            df_30m = get_bars(symbol, interval='30m', period='5d')
            if df_30m is None or len(df_30m) < 20:
                continue

            df_ind = add_all_indicators(df_30m)
            price = float(df_ind.iloc[-1]['close'])
            market_data = _build_market_data(
                symbol, price, df_ind,
                change_pct=candidate.get('change_pct', 0)
            )

            if engine:
                # Retrieve memory context
                mem_exps = retrieve_similar_experiences(
                    symbol=symbol,
                    entry_reason=candidate.get('source', ''),
                    regime=market_data.get('regime', 'ranging'),
                    rsi=market_data['rsi'],
                    macd_hist=market_data['macd_hist'],
                    adx=market_data['adx'],
                    vol_spike=market_data['vol_spike'],
                )
                mem_ctx = format_memory_context(mem_exps)

                debate_fn = engine['debate'] if use_full else engine['quick']
                debate_result = debate_fn(
                    symbol=symbol, market_data=market_data,
                    context=f"Source: {candidate.get('source','auto')} | Score: {candidate.get('momentum_score',0):.2f}",
                    verbose=True, memory_context=mem_ctx
                )

                daily_pnl = get_todays_pnl(paper=PAPER_TRADING)
                trades_today = len([t for t in get_todays_trades(paper=PAPER_TRADING) if t.get('action') == 'BUY'])
                _atstats = get_all_time_stats(paper=PAPER_TRADING)
                real_balance = ACCOUNT_SIZE + _atstats['total_pnl']
                final = engine['synthesize'](
                    debate=debate_result, current_price=price,
                    asset_class='equity', daily_pnl=daily_pnl,
                    open_positions=len(rm.get_all_positions()['equity']),
                    trades_today=trades_today, account_balance=real_balance,
                )
                print(final)

                log_signal('equity_ai_debate', symbol, final.action, final.confidence,
                           final.reasoning, acted_on=(final.action == 'BUY'), price=price)

                if final.action != 'BUY':
                    continue

                risk_check = rm.check_entry('equity_momentum', symbol, 'BUY',
                                            final.size_usd, price, final.confidence)
                if not risk_check:
                    print(f"[equity] Blocked {symbol}: {risk_check.reason}")
                    continue

                qty = max(int(risk_check.adjusted_size / price), 1)
                result = wb.buy_limit(symbol=symbol, qty=qty,
                                      limit_price=price * 1.002, strategy='equity_momentum',
                                      stop_loss=final.stop_loss, take_profit=final.take_profit)
                if result:
                    pos_entry = {
                        'qty': qty, 'entry': price,
                        'stop': final.stop_loss, 'target': final.take_profit,
                        'high_since_entry': price, 'ts_entry': datetime.now(pytz.timezone(MARKET_TIMEZONE)).isoformat(),
                        'entry_reason': final.reasoning[:200],
                    }
                    rm.register_position('equity_momentum', symbol, qty, price,
                                         final.stop_loss, final.take_profit)
                    rm.get_all_positions()['equity'][symbol]['entry_reason'] = final.reasoning[:200]

            else:
                # Fallback: MACD strategy
                from strategies.equity_momentum import EquityMomentumStrategy
                strat = EquityMomentumStrategy()
                sig = strat.generate_signal(symbol, df_30m)
                log_signal('equity_momentum', symbol, sig.action, sig.confidence,
                           sig.reason, price=sig.price)
                if sig.action == 'BUY':
                    risk_check = rm.check_entry('equity_momentum', symbol, 'BUY',
                                                EQUITY_POSITION_SIZE_USD, sig.price, sig.confidence)
                    if risk_check:
                        qty = max(int(risk_check.adjusted_size / sig.price), 1)
                        result = wb.buy_limit(symbol, qty, sig.price * 1.002, 'equity_momentum',
                                              sig.stop_loss, sig.take_profit)
                        if result:
                            rm.register_position('equity_momentum', symbol, qty,
                                                  sig.price, sig.stop_loss, sig.take_profit)

        except Exception as e:
            print(f"[equity_scan] {symbol} error: {e}")
            log_event('ERROR', 'equity_scan', f"{symbol}: {e}")

    rm.ping()


# ─── CRYPTO SCAN ─────────────────────────────────────────────────────────────

def run_crypto_scan() -> None:
    rm = get_risk_manager()
    if rm.is_halted:
        return

    engine = _debate_available()
    cb = get_coinbase_broker()

    for pid in CRYPTO_PAIRS:
        try:
            df = get_candles(pid, CRYPTO_CANDLE_GRANULARITY, 100)
            if df is None or len(df) < 30:
                continue

            df_ind = add_all_indicators(df)
            price = float(df_ind.iloc[-1]['close'])

            pos = rm.get_position('crypto_macd_consensus', pid)
            if pos:
                # Exit monitoring handled in monitor_exits_with_ai
                # Strategy exit check as backup
                sig = _crypto_strategy.generate_signal(pid, df)
                if sig.action == 'SELL':
                    _execute_crypto_exit(cb, rm, pid, pos, price, sig.reason, 'crypto_macd_consensus')
                continue

            market_data = _build_market_data(pid, price, df_ind)

            if engine:
                mem_exps = retrieve_similar_experiences(pid, '', market_data.get('regime',''),
                                                        market_data['rsi'], market_data['macd_hist'],
                                                        market_data['adx'], market_data['vol_spike'])
                mem_ctx = format_memory_context(mem_exps)

                debate_result = engine['quick'](symbol=pid, market_data=market_data,
                                                verbose=False, memory_context=mem_ctx)
                daily_pnl = get_todays_pnl(paper=PAPER_TRADING)
                _atstats = get_all_time_stats(paper=PAPER_TRADING)
                real_balance = ACCOUNT_SIZE + _atstats['total_pnl']
                final = engine['synthesize'](
                    debate=debate_result, current_price=price, asset_class='crypto',
                    daily_pnl=daily_pnl,
                    open_positions=len(rm.get_all_positions()['crypto']),
                    trades_today=len(get_todays_trades(paper=PAPER_TRADING)),
                    account_balance=real_balance,
                    allow_short=PAPER_TRADING,
                )
                log_signal('crypto_ai_debate', pid, final.action, final.confidence,
                           final.reasoning, price=price)

                if final.action == 'BUY':
                    risk_check = rm.check_entry('crypto_macd_consensus', pid, 'BUY',
                                                final.size_usd, price, final.confidence)
                    if not risk_check:
                        continue
                    result = cb.buy_limit(pid, risk_check.adjusted_size, price * 1.001,
                                          'crypto_macd_consensus', final.stop_loss, final.take_profit)
                    if result:
                        rm.register_position('crypto_macd_consensus', pid,
                                             risk_check.adjusted_size / price, price,
                                             final.stop_loss, final.take_profit,
                                             direction='LONG')

                elif final.action == 'SHORT':
                    risk_check = rm.check_entry('crypto_macd_consensus', pid, 'BUY',
                                                final.size_usd, price, final.confidence)
                    if not risk_check:
                        continue
                    # Paper short: log as SELL entry (short open), track direction
                    qty = risk_check.adjusted_size / price
                    from logging_db.trade_logger import log_trade
                    log_trade('crypto_macd_consensus', 'coinbase', pid, 'SELL', 'LIMIT',
                              qty, price, fee_usd=price * qty * COINBASE_MAKER_FEE_PCT,
                              paper=PAPER_TRADING, notes=f'SHORT entry | {final.reasoning[:100]}')
                    rm.register_position('crypto_macd_consensus', pid, qty, price,
                                         final.stop_loss, final.take_profit,
                                         direction='SHORT')
                    print(f"[crypto] 🔻 SHORT {pid} | qty={qty:.6f} @ ${price:,.4f} | "
                          f"stop=${final.stop_loss:,.4f} target=${final.take_profit:,.4f}")
            else:
                sig = _crypto_strategy.generate_signal(pid, df)
                log_signal('crypto_macd_consensus', pid, sig.action, sig.confidence,
                           sig.reason, price=sig.price)
                if sig.action == 'BUY':
                    risk_check = rm.check_entry('crypto_macd_consensus', pid, 'BUY',
                                                CRYPTO_POSITION_SIZE_USD, sig.price, sig.confidence)
                    if risk_check:
                        result = cb.buy_limit(pid, risk_check.adjusted_size, sig.price * 1.001,
                                              'crypto_macd_consensus', sig.stop_loss, sig.take_profit)
                        if result:
                            rm.register_position('crypto_macd_consensus', pid,
                                                  risk_check.adjusted_size / sig.price,
                                                  sig.price, sig.stop_loss, sig.take_profit)

        except Exception as e:
            print(f"[crypto_scan] {pid}: {e}")
            log_event('ERROR', 'crypto_scan', f"{pid}: {e}")

    rm.ping()


# ─── FUTURES SCAN ────────────────────────────────────────────────────────────

def run_futures_scan() -> None:
    if not FUTURES_ENABLED or not is_market_open() or is_in_no_trade_window():
        return
    rm = get_risk_manager()
    if rm.is_halted:
        return
    try:
        from execution.tradovate_broker import get_tradovate_broker
        tb = get_tradovate_broker()
        sig = _futures_strategy.generate_signal('MES')
        if sig.action == 'BUY':
            log_signal('futures_scalper', 'MES', sig.action, sig.confidence, sig.reason, price=sig.price)
            engine = _debate_available()
            if engine and sig.confidence < 0.75:
                df = get_bars('ES=F', '5m', '2d')
                if df is not None:
                    df_ind = add_all_indicators(df)
                    md = _build_market_data('MES', sig.price, df_ind)
                    md['dollar_volume'] = 1_000_000_000
                    debate = engine['quick']('MES', md, verbose=False)
                    if debate.synthesized_signal != 'BUY':
                        print(f"[futures] Debate override → HOLD")
                        return
            tb.place_mes_order(
                direction=sig.metadata.get('direction', 'LONG'),
                stop_pts=_futures_strategy.STOP_LOSS_PTS,
                target_pts=_futures_strategy.TAKE_PROFIT_PTS,
            )
    except Exception as e:
        print(f"[futures_scan] {e}")
        log_event('ERROR', 'futures_scan', str(e))


# ─── WATCHDOG ────────────────────────────────────────────────────────────────

def run_watchdog() -> None:
    rm = get_risk_manager()
    if not rm.watchdog_ok(WATCHDOG_INTERVAL_SECONDS):
        msg = f"⚠️ WATCHDOG: No scan completed in {WATCHDOG_INTERVAL_SECONDS//60} minutes. Bot may be stuck."
        print(msg)
        log_event('WARNING', 'watchdog', msg)
        try:
            from alerts.telegram_alert import alert_system
            alert_system('WARNING', msg)
        except Exception:
            pass


# ─── PRE-MARKET ──────────────────────────────────────────────────────────────

def run_premarket() -> None:
    print("\n📊 Pre-market analysis...")
    _futures_strategy.update_htf_bias()
    _futures_strategy.reset_daily()
    try:
        from data.indicators import get_htf_bias
        from data.market_data import get_daily_bars
        df_spy = get_daily_bars('SPY', '3mo')
        if df_spy is not None:
            bias = get_htf_bias(df_spy)
            msg = f"Pre-market: SPY HTF={bias['bias']} ADX={bias['strength']:.2f}"
            print(f"  {msg}")
            alert_system('INFO', msg)
    except Exception as e:
        print(f"[premarket] {e}")


def run_opening_range() -> None:
    try:
        df = get_bars('ES=F', '5m', '1d')
        if df is not None and len(df) >= 1:
            last = df.iloc[-1]
            _futures_strategy.set_opening_range(float(last['high']), float(last['low']))
    except Exception as e:
        print(f"[opening_range] {e}")


def run_daily_close() -> None:
    ts = get_today_stats(paper=PAPER_TRADING)
    pnl = ts['gross_pnl']
    fees = ts['fees']
    # Use all-time P&L to compute real running balance
    all_stats = get_all_time_stats(paper=PAPER_TRADING)
    real_balance = ACCOUNT_SIZE + all_stats['total_pnl']
    alert_daily_summary(ts['total'], ts['wins'], ts['losses'], pnl, fees, real_balance)
    log_event('INFO', 'daily_close', f"P&L=${pnl:+.2f} | {ts['total']} closed trades")
    rm = get_risk_manager()
    if rm.is_halted and 'Daily loss' in rm.halt_reason:
        rm.resume()


# ─── SCHEDULER SETUP & LOOP ──────────────────────────────────────────────────

def setup_schedules() -> None:
    days = [schedule.every().monday, schedule.every().tuesday, schedule.every().wednesday,
            schedule.every().thursday, schedule.every().friday]
    for d in days:
        d.at('08:30').do(run_premarket)
        d.at('09:35').do(run_opening_range)
        d.at('16:15').do(run_daily_close)

    schedule.every(EQUITY_SCAN_INTERVAL_SECONDS).seconds.do(run_equity_scan)
    schedule.every(CRYPTO_SCAN_INTERVAL_SECONDS).seconds.do(run_crypto_scan)
    schedule.every(WATCHDOG_INTERVAL_SECONDS).seconds.do(run_watchdog)

    if FUTURES_ENABLED:
        schedule.every(FUTURES_SCAN_INTERVAL_SECONDS).seconds.do(run_futures_scan)

    print(f"[scheduler] Equity: {EQUITY_SCAN_INTERVAL_SECONDS}s | Crypto: {CRYPTO_SCAN_INTERVAL_SECONDS}s | Watchdog: {WATCHDOG_INTERVAL_SECONDS}s")


def run_forever() -> None:
    setup_schedules()
    try:
        from dashboard.terminal import render as render_terminal
        _terminal_ok = True
    except Exception:
        _terminal_ok = False

    _last_render = 0.0
    RENDER_INTERVAL = 5   # seconds between dashboard refreshes

    print("\n🚀 System online. Press Ctrl+C to stop.\n")
    while True:
        try:
            schedule.run_pending()
            if _terminal_ok and time.time() - _last_render >= RENDER_INTERVAL:
                try:
                    render_terminal()
                except Exception:
                    pass
                _last_render = time.time()
            time.sleep(1)
        except KeyboardInterrupt:
            print("\n[scheduler] Shutting down.")
            break
        except Exception as e:
            print(f"[scheduler] Error: {e}")
            traceback.print_exc()
            log_event('ERROR', 'scheduler', str(e))
            time.sleep(10)
