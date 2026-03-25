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
    CRYPTO_CANDLE_GRANULARITY, EQUITY_ENABLED, CRYPTO_ENABLED, FUTURES_ENABLED,
    PERP_ENABLED, PERP_PAIRS, PERP_POSITION_SIZE_USD, PERP_MAX_LEVERAGE,
    PERP_STOP_PCT, PERP_TAKE_PROFIT_PCT,
    ANTHROPIC_API_KEY,
    WATCHDOG_INTERVAL_SECONDS, COINBASE_MAKER_FEE_PCT,
    MAX_STRATEGY_LOSS_STREAK, EQUITY_MAX_HOLD_HOURS, CRYPTO_MAX_HOLD_HOURS,
    FLAT_POSITION_THRESHOLD_PCT, CRYPTO_MIN_HOLD_MINUTES, MEAN_REVERSION_ENABLED,
    MEAN_REVERSION_RSI_ENTRY, MEAN_REVERSION_ADX_MAX,
    ATR_FEE_FLOOR_PCT,
    SQUEEZE_MIN_BARS, RV_EXPANSION_THRESHOLD,
    KALMAN_ENTRY_DEV_PCT, AVWAP_ENTRY_DEV_PCT,
    OU_HALFLIFE_MIN_MINUTES, OU_HALFLIFE_MAX_MINUTES, KYLE_LAMBDA_LOW_PCT,
    TV_SIGNAL_BOOST_CONVICTION, TV_SIGNAL_MAX_AGE_SECONDS,
)
from data.market_data import (
    is_market_open, is_in_no_trade_window, get_bars,
    get_market_breadth, has_earnings_within_days, is_near_market_close,
    get_fear_greed, get_iv_rank, get_williams_r, get_momentum_score,
    check_minervini_setup, count_pullback_bars, get_cot_sentiment,
    get_daily_bars,
)
from data.auto_screener import discover_candidates
from data.coinbase_feed import get_candles, get_current_price as cb_price, get_microstructure_feed
from data.indicators import add_all_indicators
from strategies.crypto_macd import CryptoMACDStrategy
from strategies.futures_scalper import FuturesScalperStrategy
from risk.risk_manager import get_risk_manager
from execution.webull_broker import get_webull_broker
from execution.coinbase_broker import get_coinbase_broker
from logging_db.trade_logger import (
    get_todays_trades, get_todays_pnl, get_todays_fees,
    log_event, log_signal, get_win_rate, get_all_time_stats, get_today_stats,
    get_monthly_api_cost, get_strategy_consecutive_losses,
    get_recent_tv_signal,
)
from alerts.telegram_alert import alert_system, alert_daily_summary
from memory.trade_memory import retrieve_similar_experiences, format_memory_context, store_trade_experience
# ── Self-improving intelligence layer ────────────────────────────────────────
try:
    from learning.post_trade_analyzer import analyze_closed_trade
    from learning.dynamic_weights import get_conviction_score, invalidate_cache as _invalidate_weights
    from learning.signal_performance import get_agent_accuracy_context
    from data.price_archive import upsert_candles as _archive_candles
    _LEARNING_AVAILABLE = True
except Exception as _le:
    print(f"[scheduler] Learning layer unavailable: {_le}")
    _LEARNING_AVAILABLE = False

# ── Market context + session analyst ─────────────────────────────────────────
try:
    from data.market_context import get_context_for_debate, should_block_trade
    from strategies.ai_agents.session_analyst import (
        run_session_analysis, get_current_session_context,
        format_session_context_for_debate,
    )
    _CONTEXT_AVAILABLE = True
except Exception as _cte:
    print(f"[scheduler] Market context unavailable: {_cte}")
    _CONTEXT_AVAILABLE = False

_crypto_strategy = CryptoMACDStrategy(variant='consensus')
_futures_strategy = FuturesScalperStrategy()

# Symbol-level cooldown: after a losing crypto exit, block re-entry for 20 minutes.
# DB-based (not in-memory) so bot restarts don't reset the cooldown.
_SYMBOL_COOLDOWN_SEC = 20 * 60  # 20 minutes


def _is_in_cooldown(pid: str) -> bool:
    """True if the last closed trade for this symbol was a loss within _SYMBOL_COOLDOWN_SEC.
    Queries the trades DB directly — survives bot restarts, no in-memory state needed."""
    try:
        import sqlite3
        from config import DB_PATH, PAPER_TRADING as _PT
        conn = sqlite3.connect(DB_PATH)
        cur  = conn.cursor()
        cur.execute(
            "SELECT pnl_usd, ts FROM trades WHERE symbol=? AND paper=? AND pnl_usd != 0 "
            "ORDER BY ts DESC LIMIT 1",
            (pid, int(_PT))
        )
        row = conn.fetchone() if False else cur.fetchone()
        conn.close()
        if not row:
            return False
        pnl, ts = row[0], row[1]
        if pnl >= 0:
            return False  # last trade was a win — no cooldown
        from datetime import datetime, timezone
        trade_dt = datetime.fromisoformat(ts)
        if not trade_dt.tzinfo:
            trade_dt = trade_dt.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - trade_dt).total_seconds()
        return elapsed < _SYMBOL_COOLDOWN_SEC
    except Exception:
        return False


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


def _get_microstructure(symbol: str) -> dict:
    """Fetch live OBI/TFI/microprice/spread from WebSocket feed. Returns Nones if unavailable."""
    try:
        feed = get_microstructure_feed()
        return feed.get_microstructure(symbol)
    except Exception:
        return {'obi': None, 'tfi': None, 'microprice_premium_bps': None, 'spread_bps': None}


def _build_market_data(symbol, price, df_ind, change_pct=0, regime='ranging') -> dict:
    last = df_ind.iloc[-1]
    fg = get_fear_greed()
    williams_r = get_williams_r(df_ind)
    momentum_sc = get_momentum_score(df_ind)

    # Above 200-day MA (use ema200 if available, otherwise skip)
    ema200 = float(last.get('ema200', 0) or 0)
    above_200d = (price > ema200) if ema200 > 0 else None

    # Volume above average on breakout
    vol_spike = float(last.get('vol_spike', 1) or 1)
    vol_20d_pct_above_avg = (vol_spike - 1) * 100 if vol_spike > 1 else 0

    # Landry pullback detection
    pullback = count_pullback_bars(df_ind)

    # ─── v3.5 advanced math signals from indicators.py ──────────────────────
    def _safe(col, default=None):
        v = last.get(col, default)
        if v is None:
            return default
        try:
            import math
            if math.isnan(float(v)):
                return default
        except Exception:
            pass
        return float(v) if default is not None or v is not None else v

    rv_ratio          = _safe('rv_ratio')
    avwap_utc         = _safe('avwap_utc', price)
    avwap_dev         = _safe('avwap_dev', 0.0)
    autocorr_ret      = _safe('autocorr_ret')          # AR(1) return autocorrelation
    ou_halflife_minutes = _safe('ou_halflife_minutes')  # OU mean-reversion half-life
    ou_zscore         = _safe('ou_zscore', 0.0)         # OU z-score: price deviation from 60-bar mean
    amihud_pct        = _safe('amihud_pct')
    kyle_lambda_pct   = _safe('kyle_lambda_pct')
    squeeze_on        = bool(last.get('squeeze_on', False))
    squeeze_fired     = bool(last.get('squeeze_fired', False))
    squeeze_bars      = int(_safe('squeeze_bars', 0) or 0)
    squeeze_direction = int(_safe('squeeze_direction', 0) or 0)
    kalman_price      = _safe('kalman_price', price)
    kalman_dev        = _safe('kalman_dev', 0.0)
    session_active    = bool(last.get('session_active', True))
    # ─── v4.3 new indicators ─────────────────────────────────────────────────
    supertrend_bullish  = bool(last.get('supertrend_bullish', False))
    cloud_bullish       = bool(last.get('cloud_bullish', False))
    cloud_bearish       = bool(last.get('cloud_bearish', False))
    wae_bullish         = bool(last.get('wae_bullish', False))
    wae_exploding       = bool(last.get('wae_exploding', False))
    fisher_cross_up     = bool(last.get('fisher_cross_up', False))
    fisher_val          = _safe('fisher', 0.0)
    chop_val            = _safe('chop', 50.0)
    chop_trending       = bool(last.get('chop_trending', False))
    chop_ranging        = bool(last.get('chop_ranging', False))
    wt1_val             = _safe('wt1', 0.0)
    wt_oversold_cross   = bool(last.get('wt_oversold_cross', False))
    lrsi_val            = _safe('lrsi', 0.5)
    lrsi_oversold       = lrsi_val is not None and float(lrsi_val) < 0.15
    # ────────────────────────────────────────────────────────────────────────

    return {
        'price': price,
        'change_pct': change_pct,
        'vol_spike': vol_spike,
        'rsi': float(last.get('rsi', 50) or 50),
        'macd_hist': float(last.get('macd_std_hist', 0) or last.get('macd1_hist', 0) or 0),
        'vwap': float(last.get('vwap', price) or price),
        'atr': float(last.get('atr', price * 0.01) or price * 0.01),
        'adx': float(last.get('adx', 25) or 25),
        'trend_20d': 'bullish' if float(last.get('ema20', 0) or 0) > float(last.get('ema50', 0) or 0) else 'bearish',
        'dollar_volume': price * float(last.get('volume', 0) or 0),
        'regime': regime,
        'williams_r': williams_r,
        'fear_greed_score': fg.get('score', 50),
        'fear_greed_label': fg.get('label', 'Neutral'),
        'momentum_score': momentum_sc,
        'above_200d_ma': above_200d,
        'vol_20d_pct_above_avg': vol_20d_pct_above_avg,
        'pullback_bars': pullback['pullback_bars'],
        'pullback_trend': pullback['trend'],
        'is_valid_pullback': pullback['is_valid_pullback'],
        # v3.5 advanced math signals
        'rv_ratio': rv_ratio,
        'avwap_utc': avwap_utc,
        'avwap_dev': avwap_dev,
        'autocorr_ret': autocorr_ret,
        'ou_halflife_minutes': ou_halflife_minutes,
        'ou_zscore': ou_zscore,
        'amihud_pct': amihud_pct,
        'kyle_lambda_pct': kyle_lambda_pct,
        'squeeze_on': squeeze_on,
        'squeeze_fired': squeeze_fired,
        'squeeze_bars': squeeze_bars,
        'squeeze_direction': squeeze_direction,
        'kalman_price': kalman_price,
        'kalman_dev': kalman_dev,
        'session_active': session_active,
        # v4.3 new indicators
        'supertrend_bullish': supertrend_bullish,
        'cloud_bullish': cloud_bullish,
        'cloud_bearish': cloud_bearish,
        'wae_bullish': wae_bullish,
        'wae_exploding': wae_exploding,
        'fisher_cross_up': fisher_cross_up,
        'fisher': fisher_val,
        'chop': chop_val,
        'chop_trending': chop_trending,
        'chop_ranging': chop_ranging,
        'wt1': wt1_val,
        'wt_oversold_cross': wt_oversold_cross,
        'lrsi': lrsi_val,
        # OBI/TFI/microprice/spread from live WebSocket microstructure feed
        **_get_microstructure(symbol),
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

            market_data_eq = _build_market_data(symbol, price, df_ind)

            # Hard rule check first
            should_exit, exit_reason = rm.should_exit('equity_momentum', symbol, price)
            if should_exit:
                _execute_equity_exit(wb, rm, symbol, pos, price, exit_reason, 'equity_momentum', market_data_eq)
                continue

            # AI exit review
            ts_entry = pos.get('ts_entry', '')
            try:
                from datetime import datetime as dt
                entry_dt = dt.fromisoformat(ts_entry)
                tz = pytz.timezone(MARKET_TIMEZONE)
                mins_in = int((datetime.now(tz) - entry_dt if entry_dt.tzinfo else entry_dt.replace(tzinfo=tz)).total_seconds() / 60)
            except Exception:
                mins_in = 0  # unknown age: assume just entered — block time exits

            # Time-based exit: release dead capital if flat for too long
            pnl_pct = (price - pos['entry']) / pos['entry'] if pos['entry'] > 0 else 0
            if abs(pnl_pct) <= FLAT_POSITION_THRESHOLD_PCT and mins_in >= EQUITY_MAX_HOLD_HOURS * 60:
                reason = (f"Time exit: {mins_in//60}h {mins_in%60}m in trade, "
                          f"only {pnl_pct:+.1%} — releasing dead capital")
                _execute_equity_exit(wb, rm, symbol, pos, price, reason, 'equity_momentum', market_data_eq)
                log_event('INFO', 'exit_monitor', reason)
                continue

            if engine and mins_in >= 5:  # Wait at least 5 min before AI exit review
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
            price = cb_price(pid) or 0
            if not price:
                continue
            rm.update_high('crypto_macd_consensus', pid, price)

            # Fetch indicators once — used for both exit decisions and memory storage
            cr_md = {}
            df_cr = get_candles(pid, CRYPTO_CANDLE_GRANULARITY, 50)
            if df_cr is not None and len(df_cr) >= 20:
                df_cr_ind = add_all_indicators(df_cr)
                cr_md = _build_market_data(pid, price, df_cr_ind)

            should_exit, exit_reason = rm.should_exit('crypto_macd_consensus', pid, price)
            if should_exit:
                _execute_crypto_exit(cb, rm, pid, pos, price, exit_reason, 'crypto_macd_consensus', cr_md)
                continue

            if engine and cr_md:
                ts_entry = pos.get('ts_entry', '')
                try:
                    from datetime import datetime as dt
                    entry_dt = dt.fromisoformat(ts_entry)
                    tz = pytz.timezone(MARKET_TIMEZONE)
                    mins_in = int((datetime.now(tz) - entry_dt if entry_dt.tzinfo else entry_dt.replace(tzinfo=tz)).total_seconds() / 60)
                except Exception:
                    mins_in = 0  # unknown age: assume just entered — block time exits

                pnl_pct = (price - pos['entry']) / pos['entry'] if pos['entry'] > 0 else 0

                # ── Stagnant trade early exit (45-min check) ──────────────────
                # On 1-min candles, a trade that hasn't reached 15% of its target
                # after 45 minutes has no momentum. The thesis is not playing out.
                # Exit now for small loss/breakeven rather than waiting 12h.
                _target = pos.get('target', pos['entry'] * 1.06)
                _target_range = _target - pos['entry']
                _target_progress = ((price - pos['entry']) / _target_range
                                    if _target_range > 0 else 0)
                if (mins_in >= 45
                        and _target_progress < 0.15
                        and pnl_pct < 0.005):
                    reason = (f"Stagnant exit: {mins_in}m in, {pnl_pct:+.2%} move, "
                              f"{_target_progress:.0%} of target — thesis not playing out")
                    _execute_crypto_exit(cb, rm, pid, pos, price, reason, 'crypto_macd_consensus', cr_md)
                    log_event('INFO', 'exit_monitor', reason)
                    continue

                # Time-based exit: release dead crypto capital if flat for too long
                if abs(pnl_pct) <= FLAT_POSITION_THRESHOLD_PCT and mins_in >= CRYPTO_MAX_HOLD_HOURS * 60:
                    reason = (f"Time exit: {mins_in//60}h {mins_in%60}m in trade, "
                              f"only {pnl_pct:+.1%} — releasing dead capital")
                    _execute_crypto_exit(cb, rm, pid, pos, price, reason, 'crypto_macd_consensus', cr_md)
                    log_event('INFO', 'exit_monitor', reason)
                    continue

                if mins_in >= 5:
                    review = engine['exit'](
                        symbol=pid, strategy='crypto_macd_consensus',
                        entry_price=pos['entry'], current_price=price,
                        stop_loss=pos['stop'], take_profit=pos['target'],
                        entry_reason=pos.get('entry_reason', ''),
                        time_in_trade_minutes=mins_in,
                        market_data=cr_md, verbose=False
                    )
                    if review.get('should_exit'):
                        _execute_crypto_exit(cb, rm, pid, pos, price, review['reason'], 'crypto_macd_consensus', cr_md)

        except Exception as e:
            print(f"[exit_monitor] crypto error {pid}: {e}")


def _execute_equity_exit(wb, rm, symbol, pos, price, reason, strategy, market_data=None):
    result = wb.sell_limit(symbol=symbol, qty=pos['qty'],
                           limit_price=price * 0.999, strategy=strategy,
                           entry_price=pos['entry'], reason=reason)
    if result:
        rm.close_position(strategy, symbol)
        pnl = (price - pos['entry']) * pos['qty']
        fee  = price * pos['qty'] * 0.001  # equity commission estimate
        md = market_data or {}
        store_trade_experience(
            symbol=symbol, strategy=strategy,
            entry_reason=pos.get('entry_reason', ''),
            exit_reason=reason, pnl_usd=pnl,
            rsi=md.get('rsi', 50), macd_hist=md.get('macd_hist', 0),
            adx=md.get('adx', 25), vol_spike=md.get('vol_spike', 1.0),
            regime=md.get('regime', 'unknown'),
        )
        # Post-trade attribution + Bayesian weight update
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
        print(f"[equity] ✅ EXITED {symbol} | {reason} | P&L: ${pnl:+.2f}")


def _execute_crypto_exit(cb, rm, pid, pos, price, reason, strategy, market_data=None):
    # Guard: re-verify position still open to prevent double-close (exit monitor + strategy SELL can both fire)
    if rm.get_position(strategy, pid) is None:
        return
    direction = pos.get('direction', 'LONG')
    md = market_data or {}
    if direction == 'SHORT':
        # Paper short exit: log as BUY to close, PnL = entry - exit
        pnl = (pos['entry'] - price) * pos['qty']
        if pnl < 0:
            log_event('INFO', 'exit_monitor',
                      f"[crypto] {pid} SHORT loss exit P&L=${pnl:+.2f} — 20-min cooldown active")
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
        # Post-trade attribution + Bayesian weight update (SHORT)
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
        if pnl < 0:
            log_event('INFO', 'exit_monitor',
                      f"[crypto] {pid} loss exit P&L=${pnl:+.2f} — 20-min cooldown active")
        fee_est = price * pos['qty'] * (COINBASE_MAKER_FEE_PCT + 0.006)  # round-trip estimate
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
        # Post-trade attribution + Bayesian weight update
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
        print(f"[crypto] ✅ EXITED {pid} | {reason} | P&L: ${pnl:+.2f}")


# ─── EQUITY SCAN ─────────────────────────────────────────────────────────────

def run_equity_scan() -> None:
    if not EQUITY_ENABLED:
        return
    if not is_market_open() or is_in_no_trade_window():
        return
    rm = get_risk_manager()
    if rm.is_halted:
        return

    # Market breadth filter: don't look for longs on bad macro days
    breadth = get_market_breadth()
    if not breadth['ok']:
        msg = f"SPY {breadth['spy_pct']:+.1f}% — breadth block, skipping equity longs"
        print(f"[equity] 📉 {msg}")
        log_event('INFO', 'scan_feed', f"[equity] {msg}")
        rm.ping()
        return
    log_event('INFO', 'scan_feed', f"[equity] SPY {breadth['spy_pct']:+.1f}% OK — scanning candidates")

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

    # ── Clenow momentum ranking: score all candidates, debate only top 3 ──────
    fg = get_fear_greed()
    fg_score = fg.get('score', 50)
    for c in candidates:
        try:
            df_daily_rank = get_daily_bars(c['symbol'], period='3mo')
            c['momentum_score'] = get_momentum_score(df_daily_rank) if df_daily_rank is not None else 0.0
        except Exception:
            c['momentum_score'] = 0.0
    candidates.sort(key=lambda x: x.get('momentum_score', 0), reverse=True)
    top_candidates = candidates[:3]
    log_event('INFO', 'scan_feed',
              f"[equity] Top momentum candidates: "
              + ', '.join(f"{c['symbol']}(mom={c.get('momentum_score',0):.3f})" for c in top_candidates))

    for candidate in top_candidates:
        symbol = candidate['symbol']
        if rm.get_position('equity_momentum', symbol):
            continue

        # Pre-flight risk check before spending API budget on debate
        pre = rm.pre_check_entry('equity_momentum', symbol, 'BUY', 0.0)
        if not pre:
            log_event('INFO', 'scan_feed', f"[equity] {symbol} ⛔ {pre.reason}")
            continue

        try:
            # Earnings check — skip only on earnings day itself
            if has_earnings_within_days(symbol, days=1):
                msg = f"{symbol} 📅 earnings today — skip"
                print(f"[equity] {msg}")
                log_event('INFO', 'scan_feed', f"[equity] {msg}")
                continue

            # ── Minervini SEPA filter: advisory only — log but don't block ────
            df_daily = get_daily_bars(symbol, period='1y')
            miner = check_minervini_setup(symbol, df_daily)
            if not miner['valid']:
                log_event('INFO', 'scan_feed',
                          f"[equity] {symbol} ⚠️ Minervini advisory: {miner['reason']} (proceeding anyway)")
            else:
                log_event('INFO', 'scan_feed',
                          f"[equity] {symbol} ✅ Minervini: {miner['reason']}")

            df_30m = get_bars(symbol, interval='30m', period='5d')
            if df_30m is None or len(df_30m) < 20:
                continue

            df_ind = add_all_indicators(df_30m)
            price = float(df_ind.iloc[-1]['close'])

            # ── Abdelmessih IV rank context ───────────────────────────────────
            iv_rank = get_iv_rank(symbol)
            if iv_rank is not None and iv_rank > 80:
                log_event('INFO', 'scan_feed',
                          f"[equity] {symbol} ⚠️ IV rank {iv_rank:.0f}/100 — elevated options risk, sizing down")

            market_data = _build_market_data(
                symbol, price, df_ind,
                change_pct=candidate.get('change_pct', 0)
            )
            market_data['iv_rank'] = iv_rank
            market_data['vol_20d_pct_above_avg'] = miner['vol_pct_above']

            pullback_info = f"pullback={market_data['pullback_bars']}bars/{market_data['pullback_trend']}"
            log_event('INFO', 'scan_feed',
                      f"[equity] Analyzing {symbol} ${price:.2f} | "
                      f"RSI={market_data['rsi']:.0f} ADX={market_data['adx']:.0f} "
                      f"vol={market_data['vol_spike']:.1f}x chg={market_data['change_pct']:+.1f}% "
                      f"{pullback_info} F&G={fg_score:.0f}")

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
                    verbose=True, memory_context=mem_ctx, asset_class='equity'
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
                    atr=market_data.get('atr', 0),
                )
                print(final)

                log_signal('equity_ai_debate', symbol, final.action, final.confidence,
                           final.reasoning, acted_on=(final.action == 'BUY'), price=price)
                vb = debate_result.vote_breakdown
                log_event('INFO', 'scan_feed',
                          f"[equity] {symbol} → {final.action} {final.confidence:.0%} | "
                          f"{vb.get('BUY',0)}B/{vb.get('HOLD',0)}H/{vb.get('SELL',0)}S | "
                          f"{final.reasoning[:80]}")

                if final.action != 'BUY':
                    continue

                # ── Hougaard F&G position scaling ─────────────────────────────
                # Only scale down at truly extreme greed (>90) — 10% reduction
                size_scalar = 0.90 if fg_score > 90 else 1.0
                if size_scalar < 1.0:
                    log_event('INFO', 'scan_feed',
                              f"[equity] {symbol} F&G Extreme Greed ({fg_score:.0f}) — sizing down 10%")
                # Abdelmessih: only scale down at extreme IV rank (>90)
                if iv_rank is not None and iv_rank > 90:
                    size_scalar *= 0.90

                adjusted_size = final.size_usd * size_scalar

                risk_check = rm.check_entry('equity_momentum', symbol, 'BUY',
                                            adjusted_size, price, final.confidence)
                if not risk_check:
                    log_event('INFO', 'scan_feed', f"[equity] {symbol} ⛔ {risk_check.reason}")
                    continue

                qty = max(int(risk_check.adjusted_size / price), 1)
                result = wb.buy_limit(symbol=symbol, qty=qty,
                                      limit_price=price * 1.002, strategy='equity_momentum',
                                      stop_loss=final.stop_loss, take_profit=final.take_profit)
                if result:
                    rm.register_position('equity_momentum', symbol, qty, price,
                                         final.stop_loss, final.take_profit,
                                         entry_reason=final.reasoning)

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
    if not CRYPTO_ENABLED:
        return
    rm = get_risk_manager()
    if rm.is_halted:
        return

    engine = _debate_available()
    cb = get_coinbase_broker()

    # Run exit monitor here — monitor_exits_with_ai() is normally called from
    # run_equity_scan(), but when EQUITY_ENABLED=false that never runs.
    # Crypto positions need trailing stops, AI exits, and time exits too.
    monitor_exits_with_ai(engine)

    # Strategy circuit breaker: pause if losing streak hits limit
    streak = get_strategy_consecutive_losses('crypto_macd_consensus', paper=PAPER_TRADING)
    if streak >= MAX_STRATEGY_LOSS_STREAK:
        msg = f"Circuit breaker: crypto_macd_consensus has {streak} consecutive losses — pausing scan"
        print(f"[crypto] {msg}")
        log_event('WARNING', 'crypto_scan', msg)
        rm.ping()
        return

    from strategies.ai_agents.regime_detector import detect_regime

    for pid in CRYPTO_PAIRS:
        try:
            df = get_candles(pid, CRYPTO_CANDLE_GRANULARITY, 100)
            if df is None or len(df) < 30:
                continue

            # Archive candles as they arrive — feeds the backtest data flywheel
            if _LEARNING_AVAILABLE:
                try:
                    _archive_candles(df, pid, CRYPTO_CANDLE_GRANULARITY)
                except Exception:
                    pass

            df_ind = add_all_indicators(df)
            price = float(df_ind.iloc[-1]['close'])

            pos = rm.get_position('crypto_macd_consensus', pid)
            if pos:
                # Exit monitoring handled in monitor_exits_with_ai
                # Strategy exit check as backup — but gate with min hold time
                # to prevent same-candle $0.00 P&L exits (fee-only churn)
                ts_entry = pos.get('ts_entry', '')
                try:
                    entry_dt = datetime.fromisoformat(ts_entry)
                    tz = pytz.timezone(MARKET_TIMEZONE)
                    _mins_held = int((datetime.now(tz) - (entry_dt if entry_dt.tzinfo else entry_dt.replace(tzinfo=tz))).total_seconds() / 60)
                except Exception:
                    _mins_held = CRYPTO_MIN_HOLD_MINUTES  # unknown age → allow
                sig = _crypto_strategy.generate_signal(pid, df)
                if sig.action == 'SELL':
                    if _mins_held >= CRYPTO_MIN_HOLD_MINUTES:
                        _execute_crypto_exit(cb, rm, pid, pos, price, sig.reason, 'crypto_macd_consensus')
                    else:
                        log_event('INFO', 'exit_monitor',
                                  f"[crypto] {pid} SELL signal but only {_mins_held}m in — waiting min hold ({CRYPTO_MIN_HOLD_MINUTES}m)")
                continue

            # ── Regime detection — hard gate on direction ─────────────────────
            regime_data = detect_regime(df=df_ind, intraday=True)  # crypto 5-min: tighter bb_width threshold
            regime = regime_data.get('regime', 'ranging')


            market_data = _build_market_data(pid, price, df_ind)
            market_data['regime'] = regime  # make sure debate sees it

            fg_score = market_data.get('fear_greed_score', 50)
            fg_label = market_data.get('fear_greed_label', 'Neutral')
            log_event('INFO', 'scan_feed',
                      f"[crypto] Scanning {pid} ${price:,.2f} | "
                      f"RSI={market_data['rsi']:.0f} ADX={market_data['adx']:.0f} "
                      f"MACD={'↑' if market_data['macd_hist'] > 0 else '↓'} "
                      f"W%R={market_data.get('williams_r', -50):.0f} "
                      f"F&G={fg_score:.0f}({fg_label}) vol={market_data['vol_spike']:.1f}x regime={regime}")

            # Pre-flight check before debate API call
            pre = rm.pre_check_entry('crypto_macd_consensus', pid, 'BUY', price)
            if not pre:
                log_event('INFO', 'scan_feed', f"[crypto] {pid} ⛔ {pre.reason}")
                continue

            # ── Multi-signal pre-filter v3: advanced math composite ──────────────
            vol_active = market_data['vol_spike'] >= 0.3
            if not vol_active:
                log_event('INFO', 'scan_feed',
                          f"[crypto] {pid} ⏭ vol={market_data['vol_spike']:.1f}x — dead volume, skip")
                continue

            # ATR fee-floor guard: skip symbols where expected move can't clear round-trip fees.
            # Deep research: need ATR/price ≥ 0.4% so 4×ATR target = 1.6% > 1.2% fee floor.
            _atr_check = market_data.get('atr', 0)
            _atr_pct   = _atr_check / price if price > 0 else 0
            if _atr_pct < ATR_FEE_FLOOR_PCT:
                log_event('INFO', 'scan_feed',
                          f"[crypto] {pid} ⏭ ATR={_atr_pct:.3%} < {ATR_FEE_FLOOR_PCT:.3%} fee floor — skip debate")
                continue

            # ── Signal paths (8 independent triggers) ────────────────────────
            # Signal 1: 3-variant MACD consensus (has own ADX/VWAP/RSI checks)
            macd_sig = _crypto_strategy.generate_signal(pid, df_ind)
            macd_entry = macd_sig.action == 'BUY'

            # Signal 2: Williams %R extreme oversold
            williams_r = market_data.get('williams_r', -50)
            williams_extreme = williams_r <= -80

            # Signal 3: Momentum + volume breakout
            momentum_breakout = (market_data.get('momentum_score', 0) > 0.6
                                 and market_data['vol_spike'] >= 1.5)

            # Signal 4: BB-Keltner squeeze fire → expansion (deep research: ≥20 bars required)
            _squeeze_fired = market_data.get('squeeze_fired', False)
            _squeeze_bars  = int(market_data.get('squeeze_bars', 0) or 0)
            _squeeze_dir   = int(market_data.get('squeeze_direction', 0) or 0)
            squeeze_breakout = bool(_squeeze_fired) and _squeeze_bars >= SQUEEZE_MIN_BARS and _squeeze_dir > 0

            # Signal 5: RV ratio ≥ 1.3 — short-window vol expanding vs long-window baseline
            _rv_ratio    = float(market_data.get('rv_ratio') or 0.0)
            rv_expansion = _rv_ratio >= RV_EXPANSION_THRESHOLD

            # Signal 6: Kalman filter deviation — price meaningfully below Kalman mean estimate
            _kalman_dev    = float(market_data.get('kalman_dev', 0.0) or 0.0)
            kalman_oversold = _kalman_dev <= KALMAN_ENTRY_DEV_PCT

            # Signal 7: AVWAP reclaim setup — price below AVWAP, potential reclaim trade
            _avwap_dev       = float(market_data.get('avwap_dev', 0.0) or 0.0)
            avwap_reclaim    = _avwap_dev <= AVWAP_ENTRY_DEV_PCT

            # ── Gate: at least ONE signal must fire to enter debate ───────────
            if not (macd_entry or williams_extreme or momentum_breakout
                    or squeeze_breakout or rv_expansion or kalman_oversold
                    or avwap_reclaim):
                log_event('INFO', 'scan_feed',
                          f"[crypto] {pid} ⏭ no signal "
                          f"(MACD={macd_sig.action} W%R={williams_r:.0f} "
                          f"mom={market_data.get('momentum_score',0):.2f} "
                          f"sqz={_squeeze_fired}/bars={_squeeze_bars} "
                          f"RV={_rv_ratio:.2f} Kal={_kalman_dev:.2f}% "
                          f"AVWAP={_avwap_dev:.2f}%) — skip debate")
                continue

            # ── Conviction score: weighted evidence ───────────────────────────────
            # Floor: 30 normal hours | 70 dead-zone (2-7am ET).
            # Dead-zone floor raised from 50→70: MACD(25)+Williams(20)+momentum(15)=60
            # still can't fire alone — needs vol spike OR OBI/TFI ON TOP.
            # This prevents the bot burning its daily fee budget overnight.
            _obi_cv   = market_data.get('obi') or 0.0
            _tfi_cv   = market_data.get('tfi') or 0.0
            _adx_cv   = market_data.get('adx', 0)
            _ac_cv    = market_data.get('autocorr_ret') or 0.0
            _tz_cv    = pytz.timezone(MARKET_TIMEZONE)
            _hour_et  = datetime.now(_tz_cv).hour
            _dead_zone = 2 <= _hour_et < 3   # Only the pre-London window is dead zone now

            # ── Session analyst context ────────────────────────────────────────
            _session_ctx = {}
            _session_cv_mult = 1.0
            _session_debate_notes = ''
            if _CONTEXT_AVAILABLE:
                try:
                    _session_ctx = get_current_session_context()
                    _session_cv_mult = float(_session_ctx.get('conviction_threshold_multiplier', 1.0))
                    _session_debate_notes = format_session_context_for_debate(_session_ctx)
                except Exception:
                    pass

            # Hard block on new entries 2-3 AM ET only — absolute lowest liquidity.
            # 3-8 AM ET = London session (HIGH quality window — best breakout time).
            # Exits/stops still work; only NEW entries are blocked pre-3am.
            if 2 <= _hour_et < 3:
                log_event('INFO', 'scan_feed',
                          f"[crypto] {pid} ⛔ 2-3am hard block — pre-London dead zone, no new entries")
                continue

            # ── Conviction scoring: Bayesian dynamic weights (learning layer) ──
            # Falls back to hardcoded priors if < MIN_FIRES_TO_LEARN data exists.
            # Weights shift toward observed win rates as evidence accumulates.
            market_data['macd_consensus'] = macd_entry
            market_data['tv_signal_active'] = False
            _tv_sig = get_recent_tv_signal(pid, max_age_seconds=TV_SIGNAL_MAX_AGE_SECONDS)
            if _tv_sig and _tv_sig.get('action') == 'buy':
                market_data['tv_signal_active'] = True

            if _LEARNING_AVAILABLE:
                conviction, _cv_breakdown = get_conviction_score(
                    market_data, regime=market_data.get('regime', 'any')
                )
                # Additional microstructure signals not in canonical signal set
                if _obi_cv > 0.15:  conviction += 10
                if _tfi_cv > 0.10:  conviction += 10
                if _adx_cv > 20:    conviction += 10
                if _ac_cv  > 0.10:  conviction +=  5
                if market_data.get('session_active', False): conviction += 5
                _ou_z = float(market_data.get('ou_zscore') or 0.0)
                if _ou_z <= -1.5:   conviction += 10
                if _tv_sig and _tv_sig.get('action') == 'buy':
                    conviction += TV_SIGNAL_BOOST_CONVICTION
                market_data['conviction_score'] = conviction
            else:
                # Full hardcoded fallback (original v4.3 logic)
                conviction = 0
                if macd_entry:                                     conviction += 25
                if williams_extreme:                               conviction += 20
                if momentum_breakout:                              conviction += 15
                if market_data['vol_spike'] >= 1.5:                conviction += 15
                if _obi_cv > 0.15:                                 conviction += 10
                if _tfi_cv > 0.10:                                 conviction += 10
                if _adx_cv > 20:                                   conviction += 10
                if _ac_cv  > 0.10:                                 conviction +=  5
                if market_data.get('session_active', False):       conviction +=  5
                if squeeze_breakout:                               conviction += 20
                if rv_expansion:                                   conviction += 15
                if kalman_oversold:                                conviction += 10
                if avwap_reclaim:                                  conviction += 10
                _ou_z = float(market_data.get('ou_zscore') or 0.0)
                if _ou_z <= -1.5:                                  conviction += 10
                _ou_hl = market_data.get('ou_halflife_minutes')
                if _ou_hl is not None and OU_HALFLIFE_MIN_MINUTES <= float(_ou_hl) <= OU_HALFLIFE_MAX_MINUTES:
                                                                   conviction +=  5
                _kyle = market_data.get('kyle_lambda_pct')
                if _kyle is not None and float(_kyle) <= KYLE_LAMBDA_LOW_PCT:
                                                                   conviction +=  5
                _lrsi_v = float(market_data.get('lrsi') or 0.5)
                if market_data.get('supertrend_bullish'):          conviction += 12
                if market_data.get('cloud_bullish'):               conviction +=  8
                if market_data.get('wae_bullish') and market_data.get('wae_exploding'):
                                                                   conviction += 10
                elif market_data.get('wae_bullish'):               conviction +=  5
                if market_data.get('fisher_cross_up'):             conviction +=  8
                if market_data.get('chop_trending'):               conviction +=  5
                if market_data.get('wt_oversold_cross'):           conviction += 12
                if _lrsi_v < 0.15:                                 conviction +=  8
                elif _lrsi_v < 0.25:                               conviction +=  4
                if _tv_sig and _tv_sig.get('action') == 'buy':
                                                                   conviction += TV_SIGNAL_BOOST_CONVICTION

            # ── AI-first gate: conviction is now CONTEXT, not a blocker ─────────
            # Old: skip debate if conviction < 30 (math decided for AI)
            # New: require only ONE signal to fire (conviction > 0), then let AI decide.
            # Dead zone (2-3am) already hard-blocked above.
            # The session multiplier and session_bias become context in the debate prompt,
            # not a numeric floor. AI agents see conviction score + session bias and decide.
            if conviction == 0:
                _sess_bias = _session_ctx.get('session_bias', 'NEUTRAL') if _session_ctx else 'N/A'
                log_event('INFO', 'scan_feed',
                          f"[crypto] {pid} ⏭ conviction=0 — no signals fired, skip debate")
                continue

            # ── Macro/news pre-debate gate ─────────────────────────────────────
            # should_block_trade() checks: RISK_OFF macro, HIGH news risk, VIX fear.
            # These are hard economic conditions where no signal is worth trading into.
            # Unlike conviction floor (which was math-gating AI), this is a genuine
            # macro-environment gate that even a human would respect.
            if _CONTEXT_AVAILABLE:
                try:
                    _macro_block, _macro_reason = should_block_trade(pid)
                    if _macro_block:
                        log_event('INFO', 'scan_feed',
                                  f"[crypto] {pid} ⛔ macro/news block: {_macro_reason}")
                        continue
                except Exception:
                    pass  # fail open — don't block on context errors

            # ── Build active_signals list for Bayesian signal stats in prompts ──
            # Canonical names that match signal_stats DB keys.
            _active_signals = []
            if macd_entry:          _active_signals.append('macd_consensus')
            if williams_extreme:    _active_signals.append('williams_r')
            if momentum_breakout:   _active_signals.append('momentum_volume')
            if squeeze_breakout:    _active_signals.append('squeeze_fired')
            if rv_expansion:        _active_signals.append('rv_expansion')
            if kalman_oversold:     _active_signals.append('kalman_deviation')
            if avwap_reclaim:       _active_signals.append('avwap_deviation')
            _ou_z_check = float(market_data.get('ou_zscore') or 0.0)
            if _ou_z_check <= -1.5: _active_signals.append('ou_zscore_entry')
            if market_data.get('supertrend_bullish'):   _active_signals.append('supertrend_bullish')
            if market_data.get('wt_oversold_cross'):    _active_signals.append('wavetrend_cross')
            if market_data.get('cloud_bullish'):        _active_signals.append('ichimoku_bullish')
            if market_data.get('fisher_cross_up'):      _active_signals.append('fisher_cross_up')
            _lrsi_check = float(market_data.get('lrsi') or 0.5)
            if _lrsi_check < 0.15:  _active_signals.append('lrsi_oversold')
            elif _lrsi_check < 0.25: _active_signals.append('lrsi_mild_oversold')
            if market_data.get('wae_bullish') and market_data.get('wae_exploding'):
                _active_signals.append('wae_bullish_exploding')
            elif market_data.get('wae_bullish'):
                _active_signals.append('wae_bullish')
            if market_data.get('chop_trending'):        _active_signals.append('chop_trending')
            market_data['active_signals'] = _active_signals

            # Pre-populate signal stats brief once (all agents share the same table)
            if _LEARNING_AVAILABLE and _active_signals:
                try:
                    from learning.signal_performance import get_active_signal_stats_brief
                    market_data['_signal_stats_brief'] = get_active_signal_stats_brief(
                        _active_signals, regime=regime
                    )
                except Exception:
                    market_data['_signal_stats_brief'] = ''

            # Log conviction with session context (now informational, not a gate)
            _sess_bias = _session_ctx.get('session_bias', 'NEUTRAL') if _session_ctx else 'N/A'
            log_event('INFO', 'scan_feed',
                      f"[crypto] {pid} conviction={conviction}/100 "
                      f"signals={len(_active_signals)} "
                      f"session={_sess_bias} mult={_session_cv_mult:.2f} — calling debate")

            # ── Symbol cooldown: skip re-entry 20 min after a losing exit ─────
            # DB-based check — survives bot restarts (in-memory dict would reset on reload)
            if _is_in_cooldown(pid):
                log_event('INFO', 'scan_feed',
                          f"[crypto] {pid} ⏳ loss cooldown (<{_SYMBOL_COOLDOWN_SEC//60}m since last loss) — skip")
                continue

            # ── Microstructure veto: if live order flow is strongly bearish, skip ──
            # OBI < -0.35 = 35%+ more sell-side book pressure.
            # TFI < -0.20 = tape dominated by sell-initiated trades.
            # Both together = smart money selling into any technical bounce.
            obi = market_data.get('obi')
            tfi = market_data.get('tfi')
            if obi is not None and tfi is not None:
                if obi < -0.35 and tfi < -0.20:
                    log_event('INFO', 'scan_feed',
                              f"[crypto] {pid} ⛔ microstructure VETO: OBI={obi:+.2f} TFI={tfi:+.2f} "
                              f"— sell-side dominates, skip debate")
                    continue

            # Tag all fired signals so agents have full context during debate
            signal_triggers = []
            if macd_entry:          signal_triggers.append('MACD_consensus')
            if williams_extreme:    signal_triggers.append(f'Williams_%R({williams_r:.0f})')
            if momentum_breakout:   signal_triggers.append(f'momentum_breakout({market_data["vol_spike"]:.1f}x)')
            if squeeze_breakout:    signal_triggers.append(f'squeeze_fire(bars={_squeeze_bars})')
            if rv_expansion:        signal_triggers.append(f'RV_expansion({_rv_ratio:.2f}x)')
            if kalman_oversold:     signal_triggers.append(f'kalman_dev={_kalman_dev:.2f}%')
            if avwap_reclaim:       signal_triggers.append(f'avwap_dev={_avwap_dev:.2f}%')
            if _ou_z <= -1.5:       signal_triggers.append(f'ou_zscore={_ou_z:.2f}')
            if obi is not None:     signal_triggers.append(f'OBI={obi:+.2f}')
            if tfi is not None:     signal_triggers.append(f'TFI={tfi:+.2f}')
            if _st_bull:            signal_triggers.append('SuperTrend=bullish')
            if _cloud_b:            signal_triggers.append('Ichimoku=above_cloud')
            if _wae_bull and _wae_exp: signal_triggers.append('WAE=bullish_explosion')
            if _fish_up:            signal_triggers.append(f'Fisher=cross_up({market_data.get("fisher",0):.2f})')
            if _chop_t:             signal_triggers.append(f'CHOP={market_data.get("chop",50):.1f}(trending)')
            if _wt_osc:             signal_triggers.append(f'WaveTrend=oversold_cross(wt1={market_data.get("wt1",0):.1f})')
            if _lrsi_v < 0.25:      signal_triggers.append(f'LaguerreRSI={_lrsi_v:.2f}')
            if _tv_sig and _tv_sig.get('action') == 'buy':
                signal_triggers.append(f'TV_signal({_tv_sig.get("signal","")[:40]})')
            market_data['signal_triggers'] = ', '.join(signal_triggers)

            if engine:
                mem_exps = retrieve_similar_experiences(pid, '', regime,
                                                        market_data['rsi'], market_data['macd_hist'],
                                                        market_data['adx'], market_data['vol_spike'])
                mem_ctx = format_memory_context(mem_exps)
                # Augment with agent accuracy + signal performance intelligence
                if _LEARNING_AVAILABLE:
                    try:
                        _acc_ctx = get_agent_accuracy_context(regime)
                        if _acc_ctx:
                            mem_ctx = mem_ctx + '\n\n' + _acc_ctx
                    except Exception:
                        pass

                # Assemble full AI context: macro + news + session bias + conviction
                _debate_context_parts = []
                if _CONTEXT_AVAILABLE:
                    try:
                        _macro_news_ctx = get_context_for_debate(pid, market_data)
                        if _macro_news_ctx:
                            _debate_context_parts.append(_macro_news_ctx)
                    except Exception:
                        pass
                if _session_debate_notes:
                    _debate_context_parts.append(_session_debate_notes)
                # Inject conviction score + session bias as readable AI context
                _cv_context = (
                    f"CONVICTION SCORE: {conviction}/100 | "
                    f"SESSION BIAS: {_sess_bias} | "
                    f"SESSION MULTIPLIER: {_session_cv_mult:.2f}x "
                    f"({'AI bar lowered — strong session' if _session_cv_mult < 1.0 else 'AI bar raised — weak/risky session' if _session_cv_mult > 1.0 else 'neutral session'}). "
                    f"The conviction score is informational — AI decides whether it's sufficient."
                )
                _debate_context_parts.append(_cv_context)
                _debate_context = '\n\n'.join(_debate_context_parts)

                # Full 5-agent debate for crypto — quick (3-agent) was missing
                # regime_volatility and manipulation_risk, the exact agents that
                # block bad regime entries and detect pump/dump/spoofing setups.
                debate_result = engine['debate'](symbol=pid, market_data=market_data,
                                                 context=_debate_context,
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
                    atr=market_data.get('atr', 0),
                )
                log_signal('crypto_ai_debate', pid, final.action, final.confidence,
                           final.reasoning, price=price)
                vb = debate_result.vote_breakdown
                log_event('INFO', 'scan_feed',
                          f"[crypto] {pid} → {final.action} {final.confidence:.0%} | "
                          f"{vb.get('BUY',0)}B/{vb.get('HOLD',0)}H/{vb.get('SELL',0)}S | "
                          f"regime={regime} | {final.reasoning[:70]}")

                # ── Regime gates ──────────────────────────────────────────────
                if final.action == 'BUY' and regime == 'trending_down':
                    log_event('INFO', 'scan_feed', f"[crypto] {pid} 🚫 regime block: trending_down, no longs")
                    continue
                if final.action == 'SHORT' and regime == 'trending_up':
                    log_event('INFO', 'scan_feed', f"[crypto] {pid} 🚫 regime block: trending_up, no shorts")
                    continue
                if regime == 'ranging' and final.confidence < 0.40:
                    log_event('INFO', 'scan_feed',
                              f"[crypto] {pid} 🚫 regime block: ranging needs 40%+ conf (got {final.confidence:.0%})")
                    continue

                if final.action == 'BUY':
                    risk_check = rm.check_entry('crypto_macd_consensus', pid, 'BUY',
                                                final.size_usd, price, final.confidence)
                    if not risk_check:
                        log_event('INFO', 'scan_feed', f"[crypto] {pid} ⛔ {risk_check.reason}")
                        continue
                    result = cb.buy_limit(pid, risk_check.adjusted_size, price * 1.001,
                                          'crypto_macd_consensus', final.stop_loss, final.take_profit)
                    if result:
                        rm.register_position('crypto_macd_consensus', pid,
                                             risk_check.adjusted_size / price, price,
                                             final.stop_loss, final.take_profit,
                                             direction='LONG', entry_reason=final.reasoning)

                elif final.action == 'SHORT':
                    risk_check = rm.check_entry('crypto_macd_consensus', pid, 'BUY',
                                                final.size_usd, price, final.confidence)
                    if not risk_check:
                        print(f"[crypto] ❌ {pid} blocked: {risk_check.reason}")
                        continue
                    qty = risk_check.adjusted_size / price
                    from logging_db.trade_logger import log_trade
                    log_trade('crypto_macd_consensus', 'coinbase', pid, 'SELL', 'LIMIT',
                              qty, price, fee_usd=price * qty * COINBASE_MAKER_FEE_PCT,
                              paper=PAPER_TRADING, notes=f'SHORT entry | {final.reasoning[:100]}')
                    rm.register_position('crypto_macd_consensus', pid, qty, price,
                                         final.stop_loss, final.take_profit,
                                         direction='SHORT', entry_reason=final.reasoning)
                    print(f"[crypto] 🔻 SHORT {pid} | qty={qty:.6f} @ ${price:,.4f} | "
                          f"stop=${final.stop_loss:,.4f} target=${final.take_profit:,.4f}")
            else:
                sig = _crypto_strategy.generate_signal(pid, df)
                log_signal('crypto_macd_consensus', pid, sig.action, sig.confidence,
                           sig.reason, price=sig.price)

                # ── Regime gates (MACD fallback path) ────────────────────────
                if sig.action == 'BUY' and regime == 'trending_down':
                    log_event('INFO', 'crypto_scan',
                              f"REGIME BLOCK {pid}: trending_down — no longs (MACD path)")
                    continue
                if sig.action == 'SHORT' and regime == 'trending_up':
                    log_event('INFO', 'crypto_scan',
                              f"REGIME BLOCK {pid}: trending_up — no shorts (MACD path)")
                    continue
                if regime == 'ranging' and sig.confidence < 0.40:
                    log_event('INFO', 'crypto_scan',
                              f"REGIME BLOCK {pid}: ranging requires 40%+ conf "
                              f"(got {sig.confidence:.0%}, MACD path)")
                    continue

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
                    else:
                        print(f"[crypto] ❌ {pid} blocked: {risk_check.reason}")

            # ── Mean-reversion path — only fires in ranging/volatile regimes ──
            if MEAN_REVERSION_ENABLED and regime in ('ranging', 'volatile'):
                try:
                    from strategies.crypto_mean_reversion import get_mean_reversion_signal
                    # Pass config-driven thresholds so they can be overridden via env
                    mr_market_data = dict(market_data)
                    mr_market_data['mr_rsi_entry'] = MEAN_REVERSION_RSI_ENTRY
                    mr_market_data['mr_adx_max']   = MEAN_REVERSION_ADX_MAX
                    mr_sig = get_mean_reversion_signal(pid, mr_market_data, df)
                    log_signal('crypto_mean_reversion', pid, mr_sig.action, mr_sig.confidence,
                               mr_sig.reason, price=price)
                    if mr_sig.action == 'BUY':
                        risk_check = rm.check_entry('crypto_mean_reversion', pid, 'BUY',
                                                    mr_sig.suggested_size_usd, price,
                                                    mr_sig.confidence)
                        if not risk_check:
                            log_event('INFO', 'scan_feed',
                                      f"[crypto] {pid} ⛔ MR blocked: {risk_check.reason}")
                        else:
                            result = cb.buy_limit(pid, risk_check.adjusted_size,
                                                  price * 1.001,
                                                  'crypto_mean_reversion',
                                                  mr_sig.stop_loss, mr_sig.take_profit)
                            if result:
                                rm.register_position('crypto_mean_reversion', pid,
                                                     risk_check.adjusted_size / price,
                                                     price, mr_sig.stop_loss,
                                                     mr_sig.take_profit,
                                                     direction='LONG',
                                                     entry_reason=mr_sig.reason)
                                log_event('INFO', 'scan_feed',
                                          f"[crypto] MR ENTRY {pid} @ ${price:,.4f} | "
                                          f"conf={mr_sig.confidence:.0%} "
                                          f"stop=${mr_sig.stop_loss:,.4f} "
                                          f"target=${mr_sig.take_profit:,.4f} | "
                                          f"{mr_sig.reason[:80]}")
                except Exception as mr_err:
                    log_event('ERROR', 'crypto_scan', f"[MR] {pid}: {mr_err}")

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
        # ── Williams COT filter: only trade MES in direction of commercial positioning
        cot = get_cot_sentiment()
        if not cot['is_bullish']:
            log_event('INFO', 'scan_feed',
                      f"[futures] COT: commercials net {cot['commercial_net']:+,} — bearish bias, skipping longs")

        from execution.tradovate_broker import get_tradovate_broker
        tb = get_tradovate_broker()
        sig = _futures_strategy.generate_signal('MES')
        if sig.action == 'BUY' and not cot['is_bullish']:
            log_event('INFO', 'scan_feed', f"[futures] COT bearish advisory — proceeding with BUY (signal confidence required)")
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
            tb.buy_mes(
                num_contracts=_futures_strategy.NUM_CONTRACTS,
                stop_loss_pts=_futures_strategy.STOP_LOSS_PTS,
                take_profit_pts=_futures_strategy.TAKE_PROFIT_PTS,
                strategy='futures_scalper',
            )
    except Exception as e:
        print(f"[futures_scan] {e}")
        log_event('ERROR', 'futures_scan', str(e))


def close_equity_before_market_close() -> None:
    """
    Close all open equity positions 15 minutes before market close.
    Prevents overnight gap risk (a stock gapping down 10% bypasses a 5% stop).
    """
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


# ─── PERP SCAN ───────────────────────────────────────────────────────────────

def run_perp_scan() -> None:
    """Scan Bybit perp pairs for long/short entry signals."""
    if not PERP_ENABLED:
        return
    rm = get_risk_manager()
    if rm.is_halted:
        return

    from execution.bybit_broker import get_bybit_broker
    from strategies.crypto_perp_strategy import get_perp_signal
    from data.coinbase_feed import get_candles  # reuse candle fetcher for price data

    bb = get_bybit_broker()
    if not bb.is_connected():
        bb.connect()

    # Track OI between scans for trend detection
    if not hasattr(run_perp_scan, '_prev_oi'):
        run_perp_scan._prev_oi = {}

    for symbol in PERP_PAIRS:
        try:
            # Check for existing position first
            pos = rm.get_position('crypto_perp', symbol)
            if pos:
                _monitor_perp_exit(bb, rm, symbol, pos)
                continue

            # Fetch candle data — use Bybit mark price via broker, candles via yfinance
            base = symbol.replace('USDT', '').replace('USDC', '')
            cb_symbol = f"{base}-USDC"   # try Coinbase feed first
            df = None
            try:
                df = get_candles(cb_symbol, 'ONE_MINUTE', 60)
            except Exception:
                pass
            if df is None or len(df) < 25:
                try:
                    import yfinance as yf
                    hist = yf.Ticker(f'{base}-USD').history(period='2d', interval='1m')
                    if hist is not None and not hist.empty:
                        hist.columns = [c.lower() for c in hist.columns]
                        df = hist.reset_index(drop=True)
                except Exception:
                    pass
            if df is None or len(df) < 25:
                continue

            funding = bb.get_funding_rate(symbol)
            oi = bb.get_open_interest(symbol)
            prev_oi = run_perp_scan._prev_oi.get(symbol, oi)
            run_perp_scan._prev_oi[symbol] = oi

            sig = get_perp_signal(symbol, df, funding_rate=funding,
                                  open_interest=oi, open_interest_prev=prev_oi)

            log_signal('crypto_perp', symbol, sig.action, sig.confidence,
                       sig.reason, price=sig.price)

            if sig.action not in ('BUY', 'SELL'):
                log_event('INFO', 'scan_feed',
                          f"[perp] {symbol} HOLD | {sig.reason[:80]}")
                continue

            direction = sig.metadata.get('direction', 'LONG') if sig.metadata else 'LONG'
            log_event('INFO', 'scan_feed',
                      f"[perp] {symbol} → {direction} conf={sig.confidence:.0%} "
                      f"funding={funding*100:.4f}%/8h | {sig.reason[:80]}")

            risk_check = rm.pre_check_entry('crypto_perp', symbol, sig.action,
                                            sig.price, sig.confidence)
            if not risk_check:
                log_event('INFO', 'scan_feed', f"[perp] {symbol} ⛔ {risk_check.reason}")
                continue

            if direction == 'LONG':
                result = bb.open_long(symbol, PERP_POSITION_SIZE_USD,
                                      PERP_MAX_LEVERAGE, PERP_STOP_PCT,
                                      PERP_TAKE_PROFIT_PCT, 'crypto_perp')
            else:
                result = bb.open_short(symbol, PERP_POSITION_SIZE_USD,
                                       PERP_MAX_LEVERAGE, PERP_STOP_PCT,
                                       PERP_TAKE_PROFIT_PCT, 'crypto_perp')

            if result:
                rm.register_position(
                    'crypto_perp', symbol,
                    PERP_POSITION_SIZE_USD / sig.price,
                    sig.price, sig.stop_loss, sig.take_profit,
                    direction=direction, entry_reason=sig.reason
                )

        except Exception as e:
            print(f"[perp_scan] {symbol}: {e}")
            log_event('ERROR', 'perp_scan', f"{symbol}: {e}")

    rm.ping()


def _monitor_perp_exit(bb, rm, symbol: str, pos: dict) -> None:
    """Check if an open perp position should be closed."""
    try:
        current_price = bb.get_mark_price(symbol)
        if not current_price:
            return

        rm.update_high('crypto_perp', symbol, current_price)
        should_exit, reason = rm.should_exit('crypto_perp', symbol, current_price)

        if not should_exit:
            # Time exit: perp positions accrue funding every 8h — don't hold losers
            import pytz as _ptz
            from datetime import datetime as _dt
            ts_entry = pos.get('ts_entry', '')
            try:
                entry_dt = _dt.fromisoformat(ts_entry)
                tz = _ptz.timezone(MARKET_TIMEZONE)
                mins_in = int((_dt.now(tz) - (entry_dt if entry_dt.tzinfo
                               else entry_dt.replace(tzinfo=tz))).total_seconds() / 60)
            except Exception:
                mins_in = 0
            pnl_pct = abs(current_price - pos['entry']) / pos['entry']
            if mins_in >= 240 and pnl_pct < 0.005:   # 4h flat
                should_exit = True
                reason = f"Perp time exit: {mins_in}m, flat ({pnl_pct:.2%}) — funding cost drain"

        if should_exit:
            result = bb.close_position(symbol, strategy='crypto_perp', reason=reason)
            if result is not None:
                rm.close_position('crypto_perp', symbol)
                log_event('INFO', 'perp_exit', f"[perp] CLOSED {symbol} | {reason}")

    except Exception as e:
        log_event('ERROR', 'perp_exit', f"{symbol}: {e}")


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

def run_session_open_analysis(session_name: str) -> None:
    """
    Fire the AI Session Analyst at each session open.
    Runs async-style — failure is silent to not disrupt trading.
    """
    if not _CONTEXT_AVAILABLE:
        return
    try:
        print(f"\n[session_analyst] {session_name} open — running session analysis...")
        ctx = run_session_analysis(session_name=session_name, force=True)
        bias = ctx.get('session_bias', 'NEUTRAL')
        mult = ctx.get('conviction_threshold_multiplier', 1.0)
        notes = ctx.get('session_notes', '')[:100]
        msg = f"[{session_name}] bias={bias} | cv_threshold×{mult:.2f} | {notes}"
        print(f"  {msg}")
        log_event('INFO', 'session_analyst', msg)
    except Exception as e:
        print(f"[session_analyst] {session_name} analysis error: {e}")


def setup_schedules() -> None:
    days = [schedule.every().monday, schedule.every().tuesday, schedule.every().wednesday,
            schedule.every().thursday, schedule.every().friday]
    for d in days:
        d.at('08:30').do(run_premarket)
        d.at('09:35').do(run_opening_range)
        d.at('16:15').do(run_daily_close)

    schedule.every(EQUITY_SCAN_INTERVAL_SECONDS).seconds.do(run_equity_scan)
    schedule.every(EQUITY_SCAN_INTERVAL_SECONDS).seconds.do(close_equity_before_market_close)
    schedule.every(CRYPTO_SCAN_INTERVAL_SECONDS).seconds.do(run_crypto_scan)
    schedule.every(WATCHDOG_INTERVAL_SECONDS).seconds.do(run_watchdog)

    if FUTURES_ENABLED:
        schedule.every(FUTURES_SCAN_INTERVAL_SECONDS).seconds.do(run_futures_scan)

    if PERP_ENABLED:
        schedule.every(CRYPTO_SCAN_INTERVAL_SECONDS).seconds.do(run_perp_scan)

    # ── Session-open analysis triggers (24/7 — crypto never closes) ──────────
    # Asia open:   8:00 PM ET (20:00) — JPY/KRW flows, BTC/ETH active
    # London open: 3:00 AM ET (03:00) — Best breakout window for crypto
    # NY pre-mkt:  8:30 AM ET (08:30) — US session sets the tone
    schedule.every().day.at('20:00').do(
        lambda: run_session_open_analysis('ASIA')
    )
    schedule.every().day.at('03:00').do(
        lambda: run_session_open_analysis('LONDON')
    )
    schedule.every().day.at('08:30').do(
        lambda: run_session_open_analysis('NY_OPEN')
    )

    print(f"[scheduler] Equity: {EQUITY_SCAN_INTERVAL_SECONDS}s | Crypto: {CRYPTO_SCAN_INTERVAL_SECONDS}s | "
          f"Perp: {'ON' if PERP_ENABLED else 'OFF'} | Watchdog: {WATCHDOG_INTERVAL_SECONDS}s | "
          f"Session Analysis: ASIA 8pm / LONDON 3am / NY 8:30am ET")


def run_forever() -> None:
    # Start microstructure WebSocket feed (OBI/TFI/microprice) for all crypto pairs
    if CRYPTO_ENABLED:
        try:
            from config import CRYPTO_PAIRS
            ms_feed = get_microstructure_feed(CRYPTO_PAIRS)
            log_event('INFO', 'scheduler', f"[microstructure] WebSocket feed started for {len(CRYPTO_PAIRS)} pairs")
        except Exception as e:
            log_event('WARNING', 'scheduler', f"[microstructure] Feed startup failed: {e} — OBI/TFI will be None")

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
