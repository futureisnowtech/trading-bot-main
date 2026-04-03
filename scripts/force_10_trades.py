#!/usr/bin/env python3
"""
scripts/force_10_trades.py — Force 10 paper trades through the full pipeline.

Usage: python3 scripts/force_10_trades.py

Why: verifies the full entry→score→gate→execute→close lifecycle works end-to-end.
Lowers composite threshold to 38 (normal = 50) so near-miss candidates enter.
Holds each position 8 seconds then closes at +0.15% (simulates a small win).
Stops after MAX_TRADES entries.
"""
import sys, os, time, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger('force_trades')

MAX_TRADES      = 10
FORCE_THRESHOLD = 38     # normal = 50; lowered to let near-misses through
HOLD_SECONDS    = 8      # hold each position before closing
SLIPPAGE_PCT    = 0.0015 # +0.15% exit price vs entry (small win)

def main():
    logger.info('=' * 60)
    logger.info(f'FORCE TRADE TEST  max={MAX_TRADES}  threshold={FORCE_THRESHOLD}')
    logger.info('=' * 60)

    # ── Imports ───────────────────────────────────────────────────────────────
    try:
        import scanner as _sc
        import perps_engine as perps
        import signal_engine as _se_mod
        from ml.feature_builder import build_features
        from ml.regime_classifier import classify_from_features
        from data.historical_data import get_candles
        from data.indicators import add_all_indicators
    except Exception as e:
        logger.error(f'Import failed: {e}')
        return

    se = _se_mod  # module-level score() function, not a class

    # ── Fresh scan ────────────────────────────────────────────────────────────
    logger.info('Running fresh scanner scan (Kraken + Hyperliquid)...')
    try:
        candidates = _sc.scan(account_balance=5000.0)
    except Exception as e:
        logger.error(f'Scanner failed: {e}')
        return

    if not candidates:
        logger.error('Scanner returned 0 candidates — check Kraken/HL connectivity')
        return

    logger.info(f'Scanner: {len(candidates)} candidates  '
                f'(kraken={sum(1 for c in candidates if c.get("exchange")=="kraken")}  '
                f'hl={sum(1 for c in candidates if c.get("exchange")=="hyperliquid")})')

    entries_made = 0

    for i, cand in enumerate(candidates):
        if entries_made >= MAX_TRADES:
            break

        symbol    = cand['symbol']
        direction = cand['direction']
        exchange  = cand.get('exchange', 'kraken')
        setup     = cand.get('primary_setup', 'momentum')

        logger.info(f'\n[{entries_made+1}/{MAX_TRADES}] {symbol} {direction} ({exchange}) setup={setup}')

        # ── Historical data (now includes HL fallback) ────────────────────────
        df = get_candles(symbol, '1h', 200)
        if df is None or len(df) < 20:
            logger.info(f'  SKIP — insufficient data ({0 if df is None else len(df)} bars)')
            continue

        current_price = float(df['close'].iloc[-1])
        if current_price <= 0:
            logger.info(f'  SKIP — bad price {current_price}')
            continue

        atr_7 = float(df['high'].sub(df['low']).tail(7).mean())
        if atr_7 <= 0:
            atr_7 = current_price * 0.015

        # ── Feature build ─────────────────────────────────────────────────────
        try:
            features = build_features(df, symbol)
            try:
                df_ind = add_all_indicators(df.copy())
                last   = df_ind.iloc[-1]
                features['supertrend_bullish']  = 1.0 if last.get('supertrend_bullish', False) else 0.0
                features['cloud_bullish']       = 1.0 if last.get('cloud_bullish', False) else 0.0
                features['wae_bullish']         = 1.0 if last.get('wae_bullish', False) else 0.0
                features['chop_trending']       = 1.0 if last.get('chop_trending', False) else 0.0
                features['kst_bullish']         = 1.0 if float(last.get('kst', 0)) > float(last.get('kst_signal', 0)) else 0.0
            except Exception:
                pass  # indicator enrichment optional
            # Scanner-derived features
            features['vol_spike_5c'] = float(cand.get('vol_spike', 1.0))
            fund_ann = float(cand.get('funding_rate', 0.0))
            features['deriv_funding_rate'] = float(
                max(-1.0, min(1.0, fund_ann / (365.0 * 3.0) / 0.002))
            )
        except Exception as e:
            logger.warning(f'  Feature build error: {e}  — using defaults')
            features = {}

        # ── Score ─────────────────────────────────────────────────────────────
        try:
            regime    = classify_from_features(features)
            result    = se.score(features, direction, regime, model_store=None)
            composite = result['composite_score']
            tech      = result.get('technical_score', 0)
            ml_s      = result.get('ml_score', 50)
        except Exception as e:
            logger.warning(f'  Scoring error: {e}  — using default 45.0')
            composite, tech, ml_s, regime = 45.0, 45.0, 50.0, 'UNKNOWN'

        logger.info(f'  score={composite:.1f}  tech={tech:.1f}  ml={ml_s:.1f}  '
                    f'regime={regime}  threshold={FORCE_THRESHOLD}')

        if composite < FORCE_THRESHOLD:
            logger.info(f'  SKIP — score {composite:.1f} < {FORCE_THRESHOLD}')
            continue

        # ── Sizing ────────────────────────────────────────────────────────────
        stop_pct    = max(atr_7 / current_price * 1.5, 0.008)  # floor at 0.8%
        target_pct  = stop_pct * 3.0
        position_usd = min(5000.0 * 0.01 / stop_pct, 5000.0 * 0.10)  # 1% risk, cap 10%

        if direction == 'LONG':
            stop_price   = round(current_price * (1 - stop_pct),  6)
            target_price = round(current_price * (1 + target_pct), 6)
            exit_sim     = round(current_price * (1 + SLIPPAGE_PCT), 6)
        else:
            stop_price   = round(current_price * (1 + stop_pct),  6)
            target_price = round(current_price * (1 - target_pct), 6)
            exit_sim     = round(current_price * (1 - SLIPPAGE_PCT), 6)

        logger.info(f'  entry={current_price:.6g}  stop={stop_price:.6g}  '
                    f'target={target_price:.6g}  size=${position_usd:.0f}')

        # ── Execute entry ─────────────────────────────────────────────────────
        try:
            if direction == 'LONG':
                pos = perps.open_long(
                    symbol=symbol, position_usd=position_usd,
                    entry_price=current_price, stop_price=stop_price,
                    take_profit_price=target_price, leverage=3,
                    composite_score=composite, atr_at_entry=atr_7,
                    regime=regime, entry_setup=f'force_test_{setup}',
                    paper=True,
                )
            else:
                pos = perps.open_short(
                    symbol=symbol, position_usd=position_usd,
                    entry_price=current_price, stop_price=stop_price,
                    take_profit_price=target_price, leverage=3,
                    composite_score=composite, atr_at_entry=atr_7,
                    regime=regime, entry_setup=f'force_test_{setup}',
                    paper=True,
                )
        except Exception as e:
            logger.error(f'  ENTRY ERROR: {e}')
            continue

        if pos is None:
            logger.warning(f'  open_long/short returned None — skip')
            continue

        entries_made += 1
        logger.info(f'  ✓ ENTERED  ({entries_made}/{MAX_TRADES})')

        # ── Hold then close ───────────────────────────────────────────────────
        logger.info(f'  Holding {HOLD_SECONDS}s...')
        time.sleep(HOLD_SECONDS)

        try:
            perps.update_position_price(symbol, exit_sim)
            result = perps.close_position(symbol, reason='force_test_close', paper=True)
            pnl = result.get('pnl_usd', 0) if result else 0
            logger.info(f'  ✓ CLOSED @ {exit_sim:.6g}  pnl=${pnl:+.2f}')
        except Exception as e:
            logger.error(f'  CLOSE ERROR: {e}')

        # Small gap between trades
        time.sleep(1)

    # ── Summary ───────────────────────────────────────────────────────────────
    logger.info('')
    logger.info('=' * 60)
    logger.info(f'Done: {entries_made}/{MAX_TRADES} trades completed')
    if entries_made < MAX_TRADES:
        logger.info(f'Note: {MAX_TRADES - entries_made} skipped — '
                    f'likely insufficient candle data or score < {FORCE_THRESHOLD}')
    logger.info('Check dashboard → CRYPTO PERPS tab → Trade Log for results')
    logger.info('=' * 60)


if __name__ == '__main__':
    main()
