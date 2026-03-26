"""
scheduler/crypto_scanner.py — Crypto scan: candles → signals → ML gate → debate → execute.

Runs every CRYPTO_SCAN_INTERVAL_SECONDS (24/7).
8-signal composite gate, Bayesian conviction scoring, AI batch pre-screener,
ML P(win) filter, microstructure veto, 3-agent debate, mean-reversion path.
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
    ATR_FEE_FLOOR_PCT, SQUEEZE_MIN_BARS, RV_EXPANSION_THRESHOLD,
    KALMAN_ENTRY_DEV_PCT, AVWAP_ENTRY_DEV_PCT,
    OU_HALFLIFE_MIN_MINUTES, OU_HALFLIFE_MAX_MINUTES, KYLE_LAMBDA_LOW_PCT,
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
    _LEARNING_AVAILABLE, _ML_AVAILABLE, _PRESCREENER_AVAILABLE,
    _META_LEARNER_AVAILABLE, _BACKTEST_VALIDATOR_AVAILABLE,
    _CONTEXT_AVAILABLE, _MACRO_FEED_AVAILABLE,
    ML_SIGNAL_MIN_PROB,
    get_conviction_score, get_agent_accuracy_context, _archive_candles,
    prescreener_batch, get_prescreener_context, PRESCORE_THRESHOLD,
    get_latest_insight, trigger_background_backtest, get_recent_backtest_context,
    get_ml_signal,
    _get_macro_snapshot, get_context_for_debate, should_block_trade,
    get_current_session_context, format_session_context_for_debate,
)
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

    # ── AI BATCH PRE-SCREENER — score all pairs in ONE cheap Haiku call ──────
    _prescores: dict = {}
    if _PRESCREENER_AVAILABLE and engine:
        _pre_candidates = []
        for _pid in CRYPTO_PAIRS:
            try:
                _qdf = get_candles(_pid, CRYPTO_CANDLE_GRANULARITY, 30)
                if _qdf is None or len(_qdf) < 15:
                    continue
                _qdf_ind = add_all_indicators(_qdf)
                _qprice  = float(_qdf_ind.iloc[-1]['close'])
                _qmd     = _build_market_data(_pid, _qprice, _qdf_ind)
                _pre_candidates.append((_pid, _qmd))
            except Exception:
                pass
        if _pre_candidates:
            try:
                _prescores = prescreener_batch(_pre_candidates)
                _pre_summary = ', '.join(
                    f"{s}={r['score']}" for s, r in _prescores.items()
                )
                log_event('INFO', 'scan_feed',
                          f"[prescreener] scores: {_pre_summary} (threshold≥{PRESCORE_THRESHOLD})")
            except Exception as _pse:
                log_event('WARNING', 'scan_feed', f"[prescreener] batch failed: {_pse}")

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

            # ATR fee-floor guard
            _atr_check = market_data.get('atr', 0)
            _atr_pct   = _atr_check / price if price > 0 else 0
            if _atr_pct < ATR_FEE_FLOOR_PCT:
                log_event('INFO', 'scan_feed',
                          f"[crypto] {pid} ⏭ ATR={_atr_pct:.3%} < {ATR_FEE_FLOOR_PCT:.3%} fee floor — skip debate")
                continue

            # ── 8-signal composite gate ────────────────────────────────────────
            macd_sig = _crypto_strategy.generate_signal(pid, df_ind)
            macd_entry = macd_sig.action == 'BUY'

            williams_r = market_data.get('williams_r', -50)
            williams_extreme = williams_r <= -80

            momentum_breakout = (market_data.get('momentum_score', 0) > 0.6
                                 and market_data['vol_spike'] >= 1.5)

            _squeeze_fired = market_data.get('squeeze_fired', False)
            _squeeze_bars  = int(market_data.get('squeeze_bars', 0) or 0)
            _squeeze_dir   = int(market_data.get('squeeze_direction', 0) or 0)
            squeeze_breakout = bool(_squeeze_fired) and _squeeze_bars >= SQUEEZE_MIN_BARS and _squeeze_dir > 0

            _rv_ratio    = float(market_data.get('rv_ratio') or 0.0)
            rv_expansion = _rv_ratio >= RV_EXPANSION_THRESHOLD

            _kalman_dev    = float(market_data.get('kalman_dev', 0.0) or 0.0)
            kalman_oversold = _kalman_dev <= KALMAN_ENTRY_DEV_PCT

            _avwap_dev    = float(market_data.get('avwap_dev', 0.0) or 0.0)
            avwap_reclaim = _avwap_dev <= AVWAP_ENTRY_DEV_PCT

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

            # ── Conviction scoring ────────────────────────────────────────────
            _obi_cv   = market_data.get('obi') or 0.0
            _tfi_cv   = market_data.get('tfi') or 0.0
            _adx_cv   = market_data.get('adx', 0)
            _ac_cv    = market_data.get('autocorr_ret') or 0.0
            _tz_cv    = pytz.timezone(MARKET_TIMEZONE)
            _hour_et  = datetime.now(_tz_cv).hour

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

            # Hard block 2-3 AM ET — pre-London dead zone
            if 2 <= _hour_et < 3:
                log_event('INFO', 'scan_feed',
                          f"[crypto] {pid} ⛔ 2-3am hard block — pre-London dead zone, no new entries")
                continue

            # Conviction: Bayesian dynamic weights or hardcoded fallback
            market_data['macd_consensus'] = macd_entry
            market_data['tv_signal_active'] = False
            _tv_sig = get_recent_tv_signal(pid, max_age_seconds=TV_SIGNAL_MAX_AGE_SECONDS)
            if _tv_sig and _tv_sig.get('action') == 'buy':
                market_data['tv_signal_active'] = True

            if _LEARNING_AVAILABLE:
                conviction, _cv_breakdown = get_conviction_score(
                    market_data, regime=market_data.get('regime', 'any')
                )
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
                # Hardcoded fallback (v4.3 logic)
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
                if market_data.get('stochrsi_cross_up'):           conviction += 10
                if market_data.get('cvd_bull_div'):                conviction +=  8
                if market_data.get('vwap_lower_touch'):            conviction +=  8
                if market_data.get('ema_golden_cross'):            conviction += 10
                if market_data.get('ema9_above_21') and not market_data.get('ema_golden_cross'):
                                                                   conviction +=  3
                if _tv_sig and _tv_sig.get('action') == 'buy':
                                                                   conviction += TV_SIGNAL_BOOST_CONVICTION

            if conviction == 0:
                log_event('INFO', 'scan_feed',
                          f"[crypto] {pid} ⏭ conviction=0 — no signals fired, skip debate")
                continue

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

            # ── Build active_signals list ──────────────────────────────────────
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
            if market_data.get('stochrsi_cross_up'):    _active_signals.append('stochrsi_cross_up')
            if market_data.get('cvd_bull_div'):         _active_signals.append('cvd_bull_divergence')
            if market_data.get('vwap_lower_touch'):     _active_signals.append('vwap_lower_band')
            if market_data.get('ema_golden_cross'):     _active_signals.append('ema_golden_cross')
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
                      f"[crypto] {pid} conviction={conviction}/100 "
                      f"signals={len(_active_signals)} "
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

            # ── AI pre-screener gate ───────────────────────────────────────────
            _prescore = _prescores.get(pid, {})
            if _prescore and not _prescore.get('should_analyze', True):
                log_event('INFO', 'scan_feed',
                          f"[crypto] {pid} 🤖 AI pre-screen {_prescore['score']}/10 "
                          f"(need {PRESCORE_THRESHOLD}+) — {_prescore['reason']} — skip debate")
                continue

            # ── Tag all fired signals for agent context ────────────────────────
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
            if market_data.get('supertrend_bullish'):          signal_triggers.append('SuperTrend=bullish')
            if market_data.get('cloud_bullish'):               signal_triggers.append('Ichimoku=above_cloud')
            if market_data.get('wae_bullish') and market_data.get('wae_exploding'):
                signal_triggers.append('WAE=bullish_explosion')
            if market_data.get('fisher_cross_up'):
                signal_triggers.append(f'Fisher=cross_up({market_data.get("fisher",0):.2f})')
            if market_data.get('chop_trending'):
                signal_triggers.append(f'CHOP={market_data.get("chop",50):.1f}(trending)')
            if market_data.get('wt_oversold_cross'):
                signal_triggers.append(f'WaveTrend=oversold_cross(wt1={market_data.get("wt1",0):.1f})')
            _lrsi_v = float(market_data.get('lrsi') or 0.5)
            if _lrsi_v < 0.25:      signal_triggers.append(f'LaguerreRSI={_lrsi_v:.2f}')
            if market_data.get('stochrsi_cross_up'):
                signal_triggers.append(f'StochRSI_cross(k={market_data.get("stochrsi_k",50):.0f})')
            if market_data.get('cvd_bull_div'):        signal_triggers.append('CVD_bull_divergence')
            if market_data.get('vwap_lower_touch'):    signal_triggers.append('VWAP_lower2σ_touch')
            if market_data.get('ema_golden_cross'):    signal_triggers.append('EMA9/21_golden_cross')
            if _tv_sig and _tv_sig.get('action') == 'buy':
                signal_triggers.append(f'TV_signal({_tv_sig.get("signal","")[:40]})')
            market_data['signal_triggers'] = ', '.join(signal_triggers)

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
                _cv_context = (
                    f"CONVICTION SCORE: {conviction}/100 | "
                    f"SESSION BIAS: {_sess_bias} | "
                    f"SESSION MULTIPLIER: {_session_cv_mult:.2f}x "
                    f"({'AI bar lowered — strong session' if _session_cv_mult < 1.0 else 'AI bar raised — weak/risky session' if _session_cv_mult > 1.0 else 'neutral session'}). "
                    f"The conviction score is informational — AI decides whether it's sufficient."
                )
                _debate_context_parts.append(_cv_context)

                if _prescore:
                    _ps_ctx = get_prescreener_context(pid, _prescore)
                    if _ps_ctx:
                        _debate_context_parts.append(_ps_ctx)

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
                # MACD fallback path
                sig = _crypto_strategy.generate_signal(pid, df)
                log_signal('crypto_macd_consensus', pid, sig.action, sig.confidence,
                           sig.reason, price=sig.price)

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
