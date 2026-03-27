"""
scheduler/crypto_scanner.py — Crypto scan: candles → 4-signal engine → ML gate → debate → execute.

Runs every CRYPTO_SCAN_INTERVAL_SECONDS (24/7) on 5-minute bars.
Signal hierarchy: cascade → divergence → obi → macd_fallback (strategies/crypto/crypto_engine.py)
Each signal tier carries a size_multiplier that unified_sizer.py scales into final position size.
"""
import os
import sys
from datetime import datetime

import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    CRYPTO_PAIRS, PAPER_TRADING, ACCOUNT_SIZE, MARKET_TIMEZONE,
    CRYPTO_CANDLE_GRANULARITY, CRYPTO_ENABLED,
    COINBASE_MAKER_FEE_PCT, CRYPTO_POSITION_SIZE_USD,
    MAX_STRATEGY_LOSS_STREAK,
    MEAN_REVERSION_ENABLED, MEAN_REVERSION_RSI_ENTRY, MEAN_REVERSION_ADX_MAX,
    TV_SIGNAL_BOOST_CONVICTION, TV_SIGNAL_MAX_AGE_SECONDS,
)
from data.coinbase_feed import get_candles
from data.indicators import add_all_indicators
from risk.risk_manager import get_risk_manager
from execution.coinbase_broker import get_coinbase_broker
from logging_db.trade_logger import (
    log_event, log_signal,
    get_todays_pnl, get_todays_trades, get_all_time_stats,
    get_strategy_consecutive_losses, get_recent_tv_signal,
)
from memory.trade_memory import retrieve_similar_experiences, format_memory_context
from scheduler._helpers import (
    _debate_available, _build_market_data, _crypto_strategy,
    _LEARNING_AVAILABLE, _ML_AVAILABLE,
    _META_LEARNER_AVAILABLE, _BACKTEST_VALIDATOR_AVAILABLE,
    _CONTEXT_AVAILABLE, _MACRO_FEED_AVAILABLE,
    ML_SIGNAL_MIN_PROB,
    get_agent_accuracy_context, _archive_candles,
    get_latest_insight, trigger_background_backtest, get_recent_backtest_context,
    get_ml_signal,
    _get_macro_snapshot, get_context_for_debate, should_block_trade,
    get_current_session_context, format_session_context_for_debate,
)
from strategies.crypto.crypto_engine import evaluate as engine_evaluate, get_signal_tags
from risk.unified_sizer import get_position_size as unified_get_size
from scheduler.exit_monitor import monitor_exits_with_ai, _execute_crypto_exit


def run_crypto_scan() -> None:
    if not CRYPTO_ENABLED:
        return
    rm = get_risk_manager()
    if rm.is_halted:
        return

    engine = _debate_available()
    cb = get_coinbase_broker()

    # Exit monitor: runs here when EQUITY_ENABLED=false (otherwise equity_scanner calls it)
    monitor_exits_with_ai(engine)

    # Strategy circuit breaker
    streak = get_strategy_consecutive_losses('crypto_macd_consensus', paper=PAPER_TRADING)
    if streak >= MAX_STRATEGY_LOSS_STREAK:
        msg = f"Circuit breaker: crypto_macd_consensus has {streak} consecutive losses — pausing scan"
        print(f"[crypto] {msg}")
        log_event('WARNING', 'crypto_scan', msg)
        rm.ping()
        return

    from strategies.ai_agents.regime_detector import detect_regime

    # Kick off 30-day rolling backtest in background (every 4h)
    if _BACKTEST_VALIDATOR_AVAILABLE:
        try:
            trigger_background_backtest()
        except Exception:
            pass

    # ── BTC reference price — used by divergence signal in crypto_engine ────────
    _btc_change_pct = None
    try:
        _btc_df = get_candles('BTC-USDC', CRYPTO_CANDLE_GRANULARITY, 5)
        if _btc_df is not None and len(_btc_df) >= 2:
            _btc_open  = float(_btc_df.iloc[-2]['close'])
            _btc_close = float(_btc_df.iloc[-1]['close'])
            if _btc_open > 0:
                _btc_change_pct = (_btc_close / _btc_open - 1) * 100
    except Exception:
        pass

    for pid in CRYPTO_PAIRS:
        try:
            df = get_candles(pid, CRYPTO_CANDLE_GRANULARITY, 100)
            if df is None or len(df) < 30:
                continue

            # Archive candles — feeds the backtest data flywheel
            if _LEARNING_AVAILABLE:
                try:
                    _archive_candles(df, pid, CRYPTO_CANDLE_GRANULARITY)
                except Exception:
                    pass

            df_ind = add_all_indicators(df)
            price = float(df_ind.iloc[-1]['close'])

            pos = rm.get_position('crypto_macd_consensus', pid)
            if pos:
                # Position already open — monitor_exits_with_ai (called above) handles all exits.
                # MACD SELL is an entry signal, not an exit trigger. Skip entry logic.
                continue

            # ── Regime detection ──────────────────────────────────────────────
            regime_data = detect_regime(df=df_ind, intraday=True)
            regime = regime_data.get('regime', 'ranging')

            market_data = _build_market_data(pid, price, df_ind)
            market_data['regime'] = regime

            # ── Enrich with funding rate + macro ─────────────────────────────
            if _MACRO_FEED_AVAILABLE:
                try:
                    _macro = _get_macro_snapshot(symbols_of_interest=[pid])
                    _fr = _macro.get('funding_rates', {}).get(pid, {})
                    market_data['funding_rate_pct'] = _fr.get('rate_pct')
                    market_data['funding_signal']   = _fr.get('signal', 'unknown')
                    market_data['macro_score']      = _macro.get('macro_score', 0)
                    market_data['vix_regime']       = _macro.get('vix_regime', 'unknown')
                    market_data['dxy_change']       = _macro.get('dxy_change')
                    market_data['spy_change']       = _macro.get('spy_change')
                except Exception:
                    pass

            fg_score = market_data.get('fear_greed_score', 50)
            fg_label = market_data.get('fear_greed_label', 'Neutral')
            log_event('INFO', 'scan_feed',
                      f"[crypto] Scanning {pid} ${price:,.2f} | "
                      f"RSI={market_data['rsi']:.0f} ADX={market_data['adx']:.0f} "
                      f"MACD={'↑' if market_data['macd_hist'] > 0 else '↓'} "
                      f"W%R={market_data.get('williams_r', -50):.0f} "
                      f"F&G={fg_score:.0f}({fg_label}) vol={market_data['vol_spike']:.1f}x regime={regime}")

            pre = rm.pre_check_entry('crypto_macd_consensus', pid, 'BUY', price)
            if not pre:
                log_event('INFO', 'scan_feed', f"[crypto] {pid} ⛔ {pre.reason}")
                continue

            # Volume gate
            if market_data['vol_spike'] < 0.3:
                log_event('INFO', 'scan_feed',
                          f"[crypto] {pid} ⏭ vol={market_data['vol_spike']:.1f}x — dead volume, skip")
                continue

            # Hard block 2-3 AM ET — pre-London dead zone
            _tz_cv   = pytz.timezone(MARKET_TIMEZONE)
            _hour_et = datetime.now(_tz_cv).hour
            if 2 <= _hour_et < 3:
                log_event('INFO', 'scan_feed',
                          f"[crypto] {pid} ⛔ 2-3am hard block — pre-London dead zone, no new entries")
                continue

            # ── 4-signal engine ───────────────────────────────────────────────
            # Inject macd_consensus flag (read by engine signal 4)
            _macd_sig = _crypto_strategy.generate_signal(pid, df_ind)
            market_data['macd_consensus'] = _macd_sig.action == 'BUY'

            eng_signal = engine_evaluate(pid, market_data, btc_change_pct=_btc_change_pct)

            if eng_signal.action != 'BUY':
                log_event('INFO', 'scan_feed',
                          f"[crypto] {pid} ⏭ engine={eng_signal.signal_type} — {eng_signal.reason[:80]}")
                continue

            # Stamp engine signal onto market_data for agents + learning layer
            market_data['signal_type']           = eng_signal.signal_type
            market_data['signal_size_multiplier'] = eng_signal.size_multiplier

            # ── Session analyst context ───────────────────────────────────────
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

            # TV signal enrichment
            _tv_sig = get_recent_tv_signal(pid, max_age_seconds=TV_SIGNAL_MAX_AGE_SECONDS)
            market_data['tv_signal_active'] = bool(_tv_sig and _tv_sig.get('action') == 'buy')

            # ── Macro/news pre-debate gate ─────────────────────────────────────
            if _CONTEXT_AVAILABLE:
                try:
                    _macro_block, _macro_reason = should_block_trade(pid)
                    if _macro_block:
                        log_event('INFO', 'scan_feed',
                                  f"[crypto] {pid} ⛔ macro/news block: {_macro_reason}")
                        continue
                except Exception:
                    pass

            # ── Signal tags from engine (for learning layer + agent context) ────
            _active_signals = list(eng_signal.fired_signals)  # ['cascade', 'obi', ...]
            market_data['active_signals'] = _active_signals

            # Pre-populate signal stats brief (shared across all agents)
            if _LEARNING_AVAILABLE and _active_signals:
                try:
                    from learning.signal_performance import get_active_signal_stats_brief
                    market_data['_signal_stats_brief'] = get_active_signal_stats_brief(
                        _active_signals, regime=regime
                    )
                except Exception:
                    market_data['_signal_stats_brief'] = ''

            _sess_bias = _session_ctx.get('session_bias', 'NEUTRAL') if _session_ctx else 'N/A'
            log_event('INFO', 'scan_feed',
                      f"[crypto] {pid} engine={eng_signal.signal_type} size={eng_signal.size_multiplier:.2f}x "
                      f"conf={eng_signal.confidence:.0%} signals={_active_signals} "
                      f"session={_sess_bias} mult={_session_cv_mult:.2f} — calling debate")

            # ── ML signal gate ─────────────────────────────────────────────────
            if _ML_AVAILABLE:
                try:
                    _p_win, _ml_conf = get_ml_signal(market_data)
                    market_data['ml_p_win']      = _p_win
                    market_data['ml_confidence'] = _ml_conf
                    if _ml_conf != 'no_model' and _p_win < ML_SIGNAL_MIN_PROB:
                        log_event('INFO', 'scan_feed',
                                  f"[crypto] {pid} 🧠 ML P(win)={_p_win:.1%} < {ML_SIGNAL_MIN_PROB:.0%} — skip debate")
                        continue
                except Exception:
                    pass

            # ── Microstructure veto ────────────────────────────────────────────
            obi = market_data.get('obi')
            tfi = market_data.get('tfi')
            if obi is not None and tfi is not None:
                if obi < -0.35 and tfi < -0.20:
                    log_event('INFO', 'scan_feed',
                              f"[crypto] {pid} ⛔ microstructure VETO: OBI={obi:+.2f} TFI={tfi:+.2f} "
                              f"— sell-side dominates, skip debate")
                    continue

            # ── Tag fired signals for agent context ───────────────────────────
            _signal_tags = get_signal_tags(eng_signal)
            if _tv_sig and _tv_sig.get('action') == 'buy':
                _signal_tags.append(f'TV_signal({_tv_sig.get("signal","")[:40]})')
            market_data['signal_triggers'] = ', '.join(_signal_tags)

            if engine:
                mem_exps = retrieve_similar_experiences(pid, '', regime,
                                                        market_data['rsi'], market_data['macd_hist'],
                                                        market_data['adx'], market_data['vol_spike'])
                mem_ctx = format_memory_context(mem_exps)
                if _LEARNING_AVAILABLE:
                    try:
                        _acc_ctx = get_agent_accuracy_context(regime)
                        if _acc_ctx:
                            mem_ctx = mem_ctx + '\n\n' + _acc_ctx
                    except Exception:
                        pass

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
                _engine_context = (
                    f"ENGINE SIGNAL: {eng_signal.signal_type} | "
                    f"SIZE MULTIPLIER: {eng_signal.size_multiplier:.2f}x | "
                    f"CONFIDENCE: {eng_signal.confidence:.0%} | "
                    f"REASON: {eng_signal.reason} | "
                    f"SESSION BIAS: {_sess_bias} | "
                    f"SESSION MULTIPLIER: {_session_cv_mult:.2f}x "
                    f"({'AI bar lowered — strong session' if _session_cv_mult < 1.0 else 'AI bar raised — weak/risky session' if _session_cv_mult > 1.0 else 'neutral session'})."
                )
                _debate_context_parts.append(_engine_context)

                if _BACKTEST_VALIDATOR_AVAILABLE:
                    try:
                        _bt_ctx = get_recent_backtest_context(pid)
                        if _bt_ctx:
                            _debate_context_parts.append(_bt_ctx)
                    except Exception:
                        pass

                if _META_LEARNER_AVAILABLE:
                    try:
                        _ml_insight = get_latest_insight()
                        if _ml_insight:
                            _debate_context_parts.append(_ml_insight)
                    except Exception:
                        pass

                _debate_context = '\n\n'.join(_debate_context_parts)

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

                # ── Regime gates ───────────────────────────────────────────────
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
                    # Unified sizer: base × engine.size_multiplier × vol/edge/time/Kelly
                    _base_usd = unified_get_size(
                        strategy='crypto_ai',
                        symbol=pid,
                        base_size=CRYPTO_POSITION_SIZE_USD * eng_signal.size_multiplier,
                        confidence=final.confidence,
                        current_price=price,
                        funding_rate=float(market_data.get('funding_rate_pct') or 0.0) / 100,
                    )
                    risk_check = rm.check_entry('crypto_macd_consensus', pid, 'BUY',
                                                _base_usd, price, final.confidence)
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
                    _short_usd = unified_get_size(
                        strategy='crypto_ai',
                        symbol=pid,
                        base_size=CRYPTO_POSITION_SIZE_USD * eng_signal.size_multiplier,
                        confidence=final.confidence,
                        current_price=price,
                        funding_rate=float(market_data.get('funding_rate_pct') or 0.0) / 100,
                    )
                    risk_check = rm.check_entry('crypto_macd_consensus', pid, 'BUY',
                                                _short_usd, price, final.confidence)
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

            # ── Mean-reversion path (ranging/volatile regimes only) ────────────
            if MEAN_REVERSION_ENABLED and regime in ('ranging', 'volatile'):
                try:
                    from strategies.crypto_mean_reversion import get_mean_reversion_signal
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
