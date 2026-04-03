"""
scheduler/mes_scanner.py — MES Futures scan and exit monitoring.

Runs every FUTURES_SCAN_INTERVAL_SECONDS when FUTURES_ENABLED=true.
Signal: mes_engine (ORB pullback + close auction).
Execution: tradovate_broker (demo API if credentials set, yfinance simulation otherwise).
Debate: quick 3-agent debate when signal confidence < 0.80.

Hard rules enforced in mes_engine.py — cannot be overridden:
  - No entries 11:30am–2:30pm ET
  - Max 2 trades/session
  - Daily goal: +6 pts | Daily stop: -5 pts
  - 1 contract only
  - 30-min HTF must align
"""
import os
import sys
from datetime import datetime
from typing import Optional

import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    FUTURES_ENABLED, PAPER_TRADING, MARKET_TIMEZONE,
    FUTURES_SCAN_INTERVAL_SECONDS,
)
from data.market_data import is_market_open, is_in_no_trade_window, get_bars
from data.indicators import add_all_indicators, get_htf_bias
from risk.risk_manager import get_risk_manager
from execution.ibkr_broker import get_ibkr_broker as get_tradovate_broker
from execution.ibkr_broker import MES_POINT_VALUE
from logging_db.trade_logger import log_event, log_signal
from strategies.futures.mes_engine import get_engine as get_mes_engine
from data.edge_monitor import get_edge_state, is_in_stop_cooldown, format_edge_context


def run_mes_scan() -> None:
    """Scan MES setup and execute if signal + debate confirm."""
    if not FUTURES_ENABLED:
        return
    if not is_market_open():
        return

    rm = get_risk_manager()
    if rm.is_halted:
        return

    engine = get_mes_engine()

    try:
        # ── Check / close existing position ───────────────────────────────────
        tb = get_tradovate_broker()
        pos = rm.get_position('futures_scalper', 'MES')
        if pos:
            _monitor_mes_exit(tb, rm, engine, pos)
            return

        # ── Early exit if daily limits hit ────────────────────────────────────
        if engine.trades_today >= 2 or engine.daily_pnl_pts >= engine.goal_pts:
            return

        # ── Edge gate (strategy-level) ────────────────────────────────────────
        edge_state = get_edge_state('futures_scalper', paper=PAPER_TRADING)
        if edge_state.get('should_block'):
            log_event('WARNING', 'mes_scan',
                      f"[mes] Edge gate BLOCK: {format_edge_context(edge_state)} "
                      f"— PF below 1.30, no new entries")
            rm.ping()
            return

        # ── Stop cooldown: no re-entry for 30 min after a full stop hit ──────
        in_cooldown, cooldown_reason = is_in_stop_cooldown(
            'futures_scalper', 'MES', paper=PAPER_TRADING
        )
        if in_cooldown:
            log_event('INFO', 'mes_scan',
                      f"[mes] MES ⛔ {cooldown_reason} — skipping entry")
            rm.ping()
            return

        # ── Fetch data ─────────────────────────────────────────────────────────
        df_raw = get_bars('ES=F', interval='5m', period='2d')
        if df_raw is None or len(df_raw) < 12:
            log_event('WARNING', 'mes_scan', "Insufficient ES data")
            return

        df = add_all_indicators(df_raw.copy())
        last = df.iloc[-1]
        price = float(last['close'])

        # ── Evaluate engine ────────────────────────────────────────────────────
        signal = engine.evaluate(price, df)

        log_signal(
            'futures_scalper', 'MES', signal.action, signal.confidence,
            signal.reason[:120], price=price,
        )

        if signal.action == 'HOLD':
            log_event('INFO', 'scan_feed',
                      f"[mes] MES ⏭ {signal.signal_type} — {signal.reason[:80]}")
            return

        # ── Risk pre-check ─────────────────────────────────────────────────────
        pre = rm.pre_check_entry(
            'futures_scalper', 'MES', signal.action,
            signal.entry_price, signal.confidence,
        )
        if not pre:
            log_event('INFO', 'scan_feed', f"[mes] MES ⛔ risk gate: {pre.reason}")
            return

        # ── AI debate with high-conviction gate (Sprint 5) ────────────────────
        # conf < 0.55: rule-based only — engine not confident enough to waste API tokens
        # conf >= 0.55: run MES-specific 3-agent debate with state chaining
        from scheduler._helpers import _debate_available, _build_market_data
        ai = _debate_available()
        debate_type = 'rule_based'

        if signal.confidence < 0.55:
            log_event('INFO', 'scan_feed',
                      f"[mes] Rule-based path: conf={signal.confidence:.0%} < 0.55")
        elif ai:
            debate_type = 'agents'
            md = _build_market_data('MES', price, df)
            md['dollar_volume'] = 1_000_000_000  # futures always liquid
            md['signal_type'] = signal.signal_type
            md['htf_bias'] = signal.htf_bias
            md['vix_regime'] = signal.vix_regime
            md['direction'] = signal.action
            md['daily_pts'] = engine.daily_pnl_pts
            md['trades_today'] = engine.trades_today

            try:
                # MES-specific agents auto-selected via asset_class='mes'
                debate = ai['quick']('MES', md, verbose=False, asset_class='mes')
                log_event('INFO', 'scan_feed',
                          f"[mes] Debate {debate.vote_breakdown} → {debate.synthesized_signal} "
                          f"(chained state, {len(debate.individual_signals)} agents)")
                if debate.synthesized_signal != 'BUY':
                    log_event('INFO', 'scan_feed',
                              f"[mes] Debate override → HOLD ({debate.synthesized_confidence:.0%})")
                    return
                if debate.synthesized_confidence > signal.confidence:
                    signal.confidence = debate.synthesized_confidence
            except Exception as e:
                log_event('WARNING', 'mes_scan', f"Debate failed, proceeding on engine: {e}")

        log_event('INFO', 'mes_debate_type',
                  f"[mes] debate_type={debate_type} conf={signal.confidence:.0%}")

        # ── Execute ───────────────────────────────────────────────────────────
        direction = signal.action  # 'LONG' or 'SHORT'
        stop_pts = signal.stop_pts
        target_pts = signal.target_pts

        log_event('INFO', 'scan_feed',
                  f"[mes] {direction} signal | conf={signal.confidence:.0%} | "
                  f"stop={stop_pts:.1f}pts target={target_pts:.1f}pts | {signal.reason[:60]}")

        if direction == 'LONG':
            result = tb.buy_mes(
                num_contracts=signal.contracts,
                order_type='Limit',
                limit_price=round(price - 0.25, 2),  # 1-tick below current for limit entry
                stop_loss_pts=stop_pts,
                take_profit_pts=target_pts,
                strategy='futures_scalper',
            )
        else:  # SHORT
            result = tb.short_mes(
                num_contracts=signal.contracts,
                limit_price=round(price + 0.25, 2),  # 1-tick above current for limit short
                stop_loss_pts=stop_pts,
                take_profit_pts=target_pts,
                strategy='futures_scalper',
            )

        if result:
            stop_price = (price - stop_pts) if direction == 'LONG' else (price + stop_pts)
            target_price = (price + target_pts) if direction == 'LONG' else (price - target_pts)
            rm.register_position(
                'futures_scalper', 'MES',
                float(signal.contracts),
                price, stop_price, target_price,
                direction=direction,
                entry_reason=signal.reason,
            )
            log_event('INFO', 'mes_entry',
                      f"[mes] 🟢 {direction} {signal.contracts} MES @ {price:.2f} | "
                      f"SL={stop_price:.2f} TP={target_price:.2f} | {signal.signal_type}")

    except Exception as e:
        log_event('ERROR', 'mes_scan', str(e))
        import traceback
        print(f"[mes_scan] Error: {e}\n{traceback.format_exc()}")

    rm.ping()


def run_mes_premarket() -> None:
    """Pre-market setup: refresh HTF bias + reset daily state + analyze accumulation."""
    engine = get_mes_engine()
    engine.reset_daily()

    try:
        # 30-min HTF bias from last 5 days
        df_30m = get_bars('ES=F', interval='30m', period='5d')
        if df_30m is not None and len(df_30m) >= 20:
            bias_data = get_htf_bias(add_all_indicators(df_30m.copy()))
            engine.set_htf_bias(bias_data.get('bias', 'NEUTRAL'), bias_data.get('strength', 0.0))
            print(f"[mes_engine] HTF bias: {bias_data.get('bias')} "
                  f"(strength={bias_data.get('strength', 0):.2f})")
    except Exception as e:
        log_event('WARNING', 'mes_premarket', f"HTF bias fetch failed: {e}")

    try:
        # Pre-market candles for accumulation signal (6:00-9:30am ET)
        df_pm = get_bars('ES=F', interval='5m', period='1d')
        if df_pm is not None and len(df_pm) >= 5:
            engine.update_premarket_bias(df_pm.tail(20))
    except Exception as e:
        log_event('WARNING', 'mes_premarket', f"Premarket bias failed: {e}")

    log_event('INFO', 'mes_premarket', "Pre-market setup complete")


def run_mes_opening_range() -> None:
    """Called at 9:35 ET — set opening range from first 5-min candle."""
    engine = get_mes_engine()
    try:
        df = get_bars('ES=F', interval='5m', period='1d')
        if df is not None and len(df) >= 1:
            last = df.iloc[-1]
            engine.set_opening_range(float(last['high']), float(last['low']))
            log_event('INFO', 'mes_opening_range',
                      f"OR set: [{last['low']:.2f}–{last['high']:.2f}]")
    except Exception as e:
        log_event('ERROR', 'mes_opening_range', str(e))


# ─── Exit monitoring ──────────────────────────────────────────────────────────

def _monitor_mes_exit(tb, rm, engine: 'MESEngine', pos: dict) -> None:
    """Check if open MES position should be closed."""
    try:
        # Get current price
        current_price = tb._get_real_es_price()
        if not current_price:
            return

        rm.update_high('futures_scalper', 'MES', current_price)
        should_exit, reason = rm.should_exit('futures_scalper', 'MES', current_price)

        if not should_exit:
            # Time-based: close by 3:45pm ET (no overnight)
            tz = pytz.timezone(MARKET_TIMEZONE)
            now_et = datetime.now(tz).time()
            from datetime import time as dtime
            if now_et >= dtime(15, 45):
                should_exit = True
                reason = "EOD exit: 3:45pm ET — no overnight MES positions"

        if should_exit:
            entry_price = pos.get('entry', current_price)
            direction = pos.get('direction', 'LONG')

            result = tb.sell_mes(
                num_contracts=1,
                strategy='futures_scalper',
                reason=reason,
                entry_price=entry_price,
            )

            if result is not None:
                pnl_usd = (current_price - entry_price) * MES_POINT_VALUE * 1
                if direction == 'SHORT':
                    pnl_usd = -pnl_usd
                pnl_pts = pnl_usd / MES_POINT_VALUE
                rm.close_position('futures_scalper', 'MES')
                engine.record_trade_result(pnl_pts)
                log_event('INFO', 'mes_exit',
                          f"[mes] CLOSED MES @ {current_price:.2f} | "
                          f"P&L={pnl_pts:+.1f}pts (${pnl_usd:+.2f}) | {reason}")

    except Exception as e:
        log_event('ERROR', 'mes_exit', str(e))
