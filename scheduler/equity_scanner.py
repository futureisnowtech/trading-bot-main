"""
scheduler/equity_scanner.py — Equity scan: discover → rank → debate → execute.

Runs on EQUITY_SCAN_INTERVAL_SECONDS during market hours.
Clenow momentum ranking, Minervini SEPA advisory, Hougaard/Abdelmessih sizing,
IV rank context, and AI debate or MACD fallback entry.
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    EQUITY_ENABLED, PAPER_TRADING, ACCOUNT_SIZE,
    EQUITY_POSITION_SIZE_USD,
)
from data.market_data import (
    is_market_open, is_in_no_trade_window, get_bars,
    get_market_breadth, has_earnings_within_days,
    get_fear_greed, get_iv_rank, get_daily_bars,
    check_minervini_setup,
)
from data.auto_screener import discover_candidates
from data.indicators import add_all_indicators
from risk.risk_manager import get_risk_manager
from execution.alpaca_broker import get_alpaca_broker as get_webull_broker
from logging_db.trade_logger import (
    log_event, log_signal,
    get_todays_pnl, get_todays_trades, get_all_time_stats,
    get_win_rate,
)
from memory.trade_memory import retrieve_similar_experiences, format_memory_context
from data.market_data import get_momentum_score
from scheduler._helpers import _debate_available, _build_market_data
from scheduler.exit_monitor import monitor_exits_with_ai


def run_equity_scan() -> None:
    if not EQUITY_ENABLED:
        return
    if not is_market_open() or is_in_no_trade_window():
        return
    rm = get_risk_manager()
    if rm.is_halted:
        return

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

    # Exit monitor runs here — covers case when EQUITY_ENABLED=true
    monitor_exits_with_ai(engine)

    try:
        candidates = discover_candidates(max_results=5)
    except Exception as e:
        print(f"[equity_scan] Screener error: {e}")
        rm.ping()
        return

    win_rate = get_win_rate(lookback_days=14, paper=PAPER_TRADING)
    use_full = engine['full_check'](ACCOUNT_SIZE, win_rate) if engine else False

    # ── Clenow momentum ranking: score all, debate top 3 ─────────────────────
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

        pre = rm.pre_check_entry('equity_momentum', symbol, 'BUY', 0.0)
        if not pre:
            log_event('INFO', 'scan_feed', f"[equity] {symbol} ⛔ {pre.reason}")
            continue

        try:
            if has_earnings_within_days(symbol, days=1):
                msg = f"{symbol} 📅 earnings today — skip"
                print(f"[equity] {msg}")
                log_event('INFO', 'scan_feed', f"[equity] {msg}")
                continue

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

                # Hougaard F&G position scaling + Abdelmessih IV sizing
                size_scalar = 0.90 if fg_score > 90 else 1.0
                if size_scalar < 1.0:
                    log_event('INFO', 'scan_feed',
                              f"[equity] {symbol} F&G Extreme Greed ({fg_score:.0f}) — sizing down 10%")
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
