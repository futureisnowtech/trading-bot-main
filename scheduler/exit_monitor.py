"""
scheduler/exit_monitor.py — AI-driven exit monitoring for all open positions.

Handles: hard-stop exits, AI exit reviews, stagnant trade exits, time exits,
EOD equity close, and post-trade attribution (Bayesian weight updates + LanceDB).
"""
import os
import sys
from datetime import datetime

import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    PAPER_TRADING, MARKET_TIMEZONE, COINBASE_MAKER_FEE_PCT,
    FLAT_POSITION_THRESHOLD_PCT, EQUITY_MAX_HOLD_HOURS, CRYPTO_MAX_HOLD_HOURS,
    BINANCE_TAKER_FEE_PCT,
)
from data.market_data import get_bars, is_near_market_close
from data.coinbase_feed import get_candles, get_current_price as cb_price
from data.indicators import add_all_indicators
from risk.risk_manager import get_risk_manager
# Equity removed in v5.0 — stub to prevent NameError in legacy equity exit paths
def get_webull_broker():
    return None
from execution.coinbase_broker import get_coinbase_broker
from logging_db.trade_logger import log_event
from memory.trade_memory import store_trade_experience
from scheduler._helpers import (
    _build_market_data,
    _LEARNING_AVAILABLE, _META_LEARNER_AVAILABLE, _ML_AVAILABLE,
    analyze_closed_trade, _invalidate_weights,
    maybe_run_meta_analysis, _ml_maybe_retrain,
    CRYPTO_CANDLE_GRANULARITY,
)


def _execute_equity_exit(wb, rm, symbol, pos, price, reason, strategy, market_data=None):
    result = wb.sell_limit(symbol=symbol, qty=pos['qty'],
                           limit_price=price * 0.999, strategy=strategy,
                           entry_price=pos['entry'], reason=reason)
    if result:
        rm.close_position(strategy, symbol)
        pnl = (price - pos['entry']) * pos['qty']
        fee = price * pos['qty'] * 0.001
        md = market_data or {}
        store_trade_experience(
            symbol=symbol, strategy=strategy,
            entry_reason=pos.get('entry_reason', ''),
            exit_reason=reason, pnl_usd=pnl,
            rsi=md.get('rsi', 50), macd_hist=md.get('macd_hist', 0),
            adx=md.get('adx', 25), vol_spike=md.get('vol_spike', 1.0),
            regime=md.get('regime', 'unknown'),
        )
        if _LEARNING_AVAILABLE:
            try:
                analyze_closed_trade(
                    symbol=symbol, strategy=strategy,
                    entry_price=pos['entry'], exit_price=price,
                    qty=pos['qty'], fee_usd=fee,
                    entry_ts=pos.get('ts_entry', ''),
                    exit_ts=datetime.now(pytz.timezone(MARKET_TIMEZONE)).isoformat(),
                    exit_reason=reason,
                    market_data_at_entry=md,
                    agent_votes=md.get('agent_votes', {}),
                    paper=PAPER_TRADING,
                    trade_ref=f"eq_{symbol}_{pos.get('ts_entry','')}",
                )
                _invalidate_weights()
            except Exception as _ale:
                print(f"[learning] equity attribution error: {_ale}")
        if _META_LEARNER_AVAILABLE:
            try:
                maybe_run_meta_analysis()
            except Exception:
                pass
        if _ML_AVAILABLE:
            try:
                _ml_maybe_retrain()
            except Exception:
                pass
        print(f"[equity] ✅ EXITED {symbol} | {reason} | P&L: ${pnl:+.2f}")


def _execute_crypto_exit(cb, rm, pid, pos, price, reason, strategy, market_data=None):
    # Guard: prevent double-close (exit monitor + strategy SELL can both fire)
    if rm.get_position(strategy, pid) is None:
        return
    direction = pos.get('direction', 'LONG')
    md = market_data or {}
    if direction == 'SHORT':
        pnl = (pos['entry'] - price) * pos['qty']
        if pnl < 0:
            log_event('INFO', 'exit_monitor',
                      f"[crypto] {pid} SHORT loss exit P&L=${pnl:+.2f}")
        from logging_db.trade_logger import log_trade
        log_trade(strategy, 'coinbase', pid, 'BUY', 'LIMIT',
                  pos['qty'], price,
                  fee_usd=price * pos['qty'] * COINBASE_MAKER_FEE_PCT,
                  pnl_usd=pnl, paper=PAPER_TRADING,
                  notes=f'SHORT exit | {reason[:100]}')
        rm.close_position(strategy, pid)
        store_trade_experience(symbol=pid, strategy=strategy,
                               entry_reason=pos.get('entry_reason', ''),
                               exit_reason=reason, pnl_usd=pnl,
                               rsi=md.get('rsi', 50), macd_hist=md.get('macd_hist', 0),
                               adx=md.get('adx', 25), vol_spike=md.get('vol_spike', 1.0),
                               regime=md.get('regime', 'unknown'))
        if _LEARNING_AVAILABLE:
            try:
                fee_est_short = price * pos['qty'] * (COINBASE_MAKER_FEE_PCT + 0.006)
                analyze_closed_trade(
                    symbol=pid, strategy=strategy,
                    entry_price=pos['entry'], exit_price=price,
                    qty=pos['qty'], fee_usd=fee_est_short,
                    entry_ts=pos.get('ts_entry', ''),
                    exit_ts=datetime.now(pytz.timezone(MARKET_TIMEZONE)).isoformat(),
                    exit_reason=reason,
                    market_data_at_entry=md,
                    agent_votes=md.get('agent_votes', {}),
                    paper=PAPER_TRADING,
                    trade_ref=f"cr_short_{pid}_{pos.get('ts_entry','')}",
                )
                _invalidate_weights()
            except Exception as _ale:
                print(f"[learning] SHORT attribution error: {_ale}")
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
        if pnl < 0:
            log_event('INFO', 'exit_monitor',
                      f"[crypto] {pid} loss exit P&L=${pnl:+.2f}")
        fee_est = price * pos['qty'] * (COINBASE_MAKER_FEE_PCT + 0.006)
        if abs(pnl) < fee_est * 0.5:
            log_event('WARNING', 'exit_monitor',
                      f"[crypto] {pid} near-zero exit: P&L=${pnl:+.4f} vs fee~${fee_est:.4f} — churn trade")
        store_trade_experience(
            symbol=pid, strategy=strategy,
            entry_reason=pos.get('entry_reason', ''),
            exit_reason=reason, pnl_usd=pnl,
            rsi=md.get('rsi', 50), macd_hist=md.get('macd_hist', 0),
            adx=md.get('adx', 25), vol_spike=md.get('vol_spike', 1.0),
            regime=md.get('regime', 'unknown'),
        )
        if _LEARNING_AVAILABLE:
            try:
                analyze_closed_trade(
                    symbol=pid, strategy=strategy,
                    entry_price=pos['entry'], exit_price=price,
                    qty=pos['qty'], fee_usd=fee_est,
                    entry_ts=pos.get('ts_entry', ''),
                    exit_ts=datetime.now(pytz.timezone(MARKET_TIMEZONE)).isoformat(),
                    exit_reason=reason,
                    market_data_at_entry=md,
                    agent_votes=md.get('agent_votes', {}),
                    paper=PAPER_TRADING,
                    trade_ref=f"cr_{pid}_{pos.get('ts_entry','')}",
                )
                _invalidate_weights()
            except Exception as _ale:
                print(f"[learning] crypto attribution error: {_ale}")
        if _META_LEARNER_AVAILABLE:
            try:
                maybe_run_meta_analysis()
            except Exception:
                pass
        if _ML_AVAILABLE:
            try:
                _ml_maybe_retrain()
            except Exception:
                pass
        print(f"[crypto] ✅ EXITED {pid} | {reason} | P&L: ${pnl:+.2f}")


def monitor_exits_with_ai(engine) -> None:
    """Check all open positions for AI-driven exit signals."""
    rm = get_risk_manager()
    all_pos = rm.get_all_positions()
    wb = get_webull_broker()
    cb = get_coinbase_broker()

    for symbol, pos in list(all_pos.get('equity', {}).items()):
        try:
            if wb is None:
                # Equity broker not configured — close orphaned positions to prevent monitor crash
                strat = pos.get('strategy', 'equity_momentum')
                rm.close_position(strat, symbol)
                log_event('WARNING', 'exit_monitor',
                          f"[equity] {symbol} — equity broker unavailable, removed orphaned position")
                continue
            df = get_bars(symbol, interval='5m', period='1d')
            if df is None or df.empty:
                continue
            df_ind = add_all_indicators(df)
            last = df_ind.iloc[-1]
            price = float(last['close'])
            rm.update_high('equity_momentum', symbol, price)

            market_data_eq = _build_market_data(symbol, price, df_ind)

            should_exit, exit_reason = rm.should_exit('equity_momentum', symbol, price)
            if should_exit:
                _execute_equity_exit(wb, rm, symbol, pos, price, exit_reason, 'equity_momentum', market_data_eq)
                continue

            ts_entry = pos.get('ts_entry', '')
            try:
                from datetime import datetime as dt
                entry_dt = dt.fromisoformat(ts_entry)
                tz = pytz.timezone(MARKET_TIMEZONE)
                mins_in = int((datetime.now(tz) - entry_dt if entry_dt.tzinfo else entry_dt.replace(tzinfo=tz)).total_seconds() / 60)
            except Exception:
                mins_in = 0

            pnl_pct = (price - pos['entry']) / pos['entry'] if pos['entry'] > 0 else 0
            if abs(pnl_pct) <= FLAT_POSITION_THRESHOLD_PCT and mins_in >= EQUITY_MAX_HOLD_HOURS * 60:
                reason = (f"Time exit: {mins_in//60}h {mins_in%60}m in trade, "
                          f"only {pnl_pct:+.1%} — releasing dead capital")
                _execute_equity_exit(wb, rm, symbol, pos, price, reason, 'equity_momentum', market_data_eq)
                log_event('INFO', 'exit_monitor', reason)
                continue

            if engine and mins_in >= 5:
                review = engine['exit'](
                    symbol=symbol, strategy='equity_momentum',
                    entry_price=pos['entry'], current_price=price,
                    stop_loss=pos['stop'], take_profit=pos['target'],
                    entry_reason=pos.get('entry_reason', ''),
                    time_in_trade_minutes=mins_in,
                    market_data=market_data_eq, verbose=True
                )
                if review.get('should_exit'):
                    _execute_equity_exit(wb, rm, symbol, pos, price, review['reason'], 'equity_momentum', market_data_eq)

        except Exception as e:
            print(f"[exit_monitor] equity error {symbol}: {e}")

    for pid, pos in list(all_pos.get('crypto', {}).items()):
        try:
            # Use the strategy that opened this position — prevents DB key mismatch on close
            strategy = pos.get('strategy', 'crypto_macd_consensus')
            price = cb_price(pid) or 0
            if not price:
                continue
            rm.update_high(strategy, pid, price)

            cr_md = {}
            df_cr = get_candles(pid, CRYPTO_CANDLE_GRANULARITY, 50)
            if df_cr is not None and len(df_cr) >= 20:
                df_cr_ind = add_all_indicators(df_cr)
                cr_md = _build_market_data(pid, price, df_cr_ind)

            should_exit, exit_reason = rm.should_exit(strategy, pid, price)
            if should_exit:
                _execute_crypto_exit(cb, rm, pid, pos, price, exit_reason, strategy, cr_md)
                continue

            if engine and cr_md:
                ts_entry = pos.get('ts_entry', '')
                try:
                    from datetime import datetime as dt
                    entry_dt = dt.fromisoformat(ts_entry)
                    tz = pytz.timezone(MARKET_TIMEZONE)
                    mins_in = int((datetime.now(tz) - entry_dt if entry_dt.tzinfo else entry_dt.replace(tzinfo=tz)).total_seconds() / 60)
                except Exception:
                    mins_in = 0

                pnl_pct = (price - pos['entry']) / pos['entry'] if pos['entry'] > 0 else 0

                _target = pos.get('target', pos['entry'] * 1.06)
                _target_range = _target - pos['entry']
                _target_progress = ((price - pos['entry']) / _target_range
                                    if _target_range > 0 else 0)
                if (mins_in >= 45
                        and _target_progress < 0.15
                        and pnl_pct < 0.005):
                    reason = (f"Stagnant exit: {mins_in}m in, {pnl_pct:+.2%} move, "
                              f"{_target_progress:.0%} of target — thesis not playing out")
                    _execute_crypto_exit(cb, rm, pid, pos, price, reason, strategy, cr_md)
                    log_event('INFO', 'exit_monitor', reason)
                    continue

                if abs(pnl_pct) <= FLAT_POSITION_THRESHOLD_PCT and mins_in >= CRYPTO_MAX_HOLD_HOURS * 60:
                    reason = (f"Time exit: {mins_in//60}h {mins_in%60}m in trade, "
                              f"only {pnl_pct:+.1%} — releasing dead capital")
                    _execute_crypto_exit(cb, rm, pid, pos, price, reason, strategy, cr_md)
                    log_event('INFO', 'exit_monitor', reason)
                    continue

                if mins_in >= 5:
                    review = engine['exit'](
                        symbol=pid, strategy=strategy,
                        entry_price=pos['entry'], current_price=price,
                        stop_loss=pos['stop'], take_profit=pos['target'],
                        entry_reason=pos.get('entry_reason', ''),
                        time_in_trade_minutes=mins_in,
                        market_data=cr_md, verbose=False
                    )
                    if review.get('should_exit'):
                        _execute_crypto_exit(cb, rm, pid, pos, price, review['reason'], strategy, cr_md)

        except Exception as e:
            print(f"[exit_monitor] crypto error {pid}: {e}")

    # ── Perp positions: AI exit review + stagnant exit ────────────────────────
    for symbol, pos in list(all_pos.get('perp', {}).items()):
        try:
            from execution.binance_broker import get_binance_broker as _get_bb_exit
            bb_exit = _get_bb_exit()
            current_price = bb_exit.get_mark_price(symbol)
            if not current_price:
                continue

            rm.update_high('crypto_perp', symbol, current_price)
            should_exit_perp, exit_reason_perp = rm.should_exit('crypto_perp', symbol, current_price)
            if should_exit_perp:
                _execute_perp_exit(bb_exit, rm, symbol, pos, exit_reason_perp)
                continue

            if engine:
                ts_entry = pos.get('ts_entry', '')
                try:
                    from datetime import datetime as _dt2
                    entry_dt = _dt2.fromisoformat(ts_entry)
                    tz = pytz.timezone(MARKET_TIMEZONE)
                    mins_in = int((datetime.now(tz) - entry_dt if entry_dt.tzinfo
                                   else entry_dt.replace(tzinfo=tz)).total_seconds() / 60)
                except Exception:
                    mins_in = 0

                pnl_pct = ((current_price - pos['entry']) / pos['entry']
                           if pos.get('direction', 'LONG') == 'LONG'
                           else (pos['entry'] - current_price) / pos['entry'])

                # 4h flat exit (also in perp_scanner — belt and suspenders)
                if mins_in >= 240 and abs(pnl_pct) < 0.005:
                    reason = f"Perp stagnant exit: {mins_in}m, flat ({pnl_pct:+.2%})"
                    _execute_perp_exit(bb_exit, rm, symbol, pos, reason)
                    continue

                if mins_in >= 5:
                    perp_md = {'direction': pos.get('direction', 'LONG'),
                               'regime': 'unknown', 'rsi': 50, 'adx': 25}
                    review = engine['exit'](
                        symbol=symbol, strategy='crypto_perp',
                        entry_price=pos['entry'], current_price=current_price,
                        stop_loss=pos['stop'], take_profit=pos['target'],
                        entry_reason=pos.get('entry_reason', ''),
                        time_in_trade_minutes=mins_in,
                        market_data=perp_md, verbose=False,
                    )
                    if review.get('should_exit'):
                        _execute_perp_exit(bb_exit, rm, symbol, pos, review['reason'])

        except Exception as e:
            print(f"[exit_monitor] perp error {symbol}: {e}")


def _execute_perp_exit(bb, rm, symbol: str, pos: dict, reason: str) -> None:
    """Close a perp position and fire attribution pipeline."""
    try:
        result = bb.close_position(symbol, strategy='crypto_perp', reason=reason)
        if result is None:
            return
        rm.close_position('crypto_perp', symbol)
        log_event('INFO', 'perp_exit', f"[perp] CLOSED {symbol} | {reason}")

        # Post-trade attribution — feed learning layer
        exit_price = bb.get_mark_price(symbol) or pos['entry']
        side = pos.get('direction', 'LONG')
        pnl = ((exit_price - pos['entry']) if side == 'LONG'
               else (pos['entry'] - exit_price)) * pos.get('qty', 0)

        store_trade_experience(
            symbol=symbol, strategy='crypto_perp',
            entry_reason=pos.get('entry_reason', ''),
            exit_reason=reason, pnl_usd=pnl,
            rsi=50, macd_hist=0, adx=25, vol_spike=1.0, regime='unknown',
        )
        if _LEARNING_AVAILABLE:
            try:
                from datetime import datetime as _dt
                _perp_fee = pos.get('qty', 0) * pos.get('entry', 0) * BINANCE_TAKER_FEE_PCT
                analyze_closed_trade(
                    symbol=symbol, strategy='crypto_perp',
                    entry_price=pos['entry'], exit_price=exit_price,
                    qty=pos.get('qty', 0), fee_usd=_perp_fee,
                    entry_ts=pos.get('ts_entry', ''),
                    exit_ts=_dt.now(pytz.timezone(MARKET_TIMEZONE)).isoformat(),
                    exit_reason=reason,
                    market_data_at_entry={},
                    agent_votes={},
                    paper=PAPER_TRADING,
                    trade_ref=f"perp_{symbol}_{pos.get('ts_entry','')}",
                )
                _invalidate_weights()
            except Exception as _ale:
                print(f"[learning] perp attribution error: {_ale}")
    except Exception as e:
        log_event('ERROR', 'perp_exit', f"{symbol} execute_perp_exit: {e}")


def close_equity_before_market_close() -> None:
    """Close all open equity positions 15 minutes before market close."""
    if not is_near_market_close(minutes_before=15):
        return
    rm = get_risk_manager()
    all_pos = rm.get_all_positions()
    equity_pos = all_pos.get('equity', {})
    if not equity_pos:
        return
    wb = get_webull_broker()
    for symbol, pos in list(equity_pos.items()):
        try:
            df = get_bars(symbol, '1m', '1d')
            if df is None or df.empty:
                continue
            price = float(df.iloc[-1]['close'])
            reason = "EOD close: no overnight gap risk"
            _execute_equity_exit(wb, rm, symbol, pos, price, reason, 'equity_momentum')
            log_event('INFO', 'eod_close', f"Closed {symbol} at EOD | price=${price:.2f}")
            print(f"[eod_close] Closed {symbol} before market close")
        except Exception as e:
            print(f"[eod_close] {symbol} error: {e}")
            log_event('ERROR', 'eod_close', f"{symbol}: {e}")
