"""
scheduler/v10_runner.py — v10 unified scanner + trade loop.

Runs 24/7 paper trading on Binance USDT perp universe.
Replaces v9 job_runner for v10 architecture.

Loop intervals:
  scan_and_trade:     every 5 minutes
  exit_monitor:       every 30 seconds
  hedge_rebalance:    every 5 minutes
  kill_switch_check:  every 60 seconds
  rbi_nightly:        once at 02:00 ET
  ml_retrain_check:   every 6 hours
"""

import logging
import threading
import time
import traceback
from datetime import datetime
from typing import Dict, Optional

import schedule

logger = logging.getLogger(__name__)

# ── Module-level state ────────────────────────────────────────────────────────

_scan_lock = threading.RLock()   # prevent parallel scan_and_trade runs
_initial_balance: float = 0.0   # set at startup from config
_paper: bool = True              # set at startup from config

# Regime multipliers for position sizing (applied on top of compute_position_size)
_REGIME_SIZE_MULT = {
    'TRENDING_UP':   1.00,
    'TRENDING_DOWN': 1.00,
    'RANGING':       0.85,
    'HIGH_VOL':      0.70,
    'ACCUMULATION':  0.90,
    'DISTRIBUTION':  0.90,
    'UNKNOWN':       0.90,
}

# Deduplicate TradingView signals across scan cycles (symbol_direction_ts key)
_seen_tv_signal_keys: set = set()


# ── TradingView signal helpers ────────────────────────────────────────────────

def _get_fresh_tv_signals(max_age_seconds: int = 300) -> list:
    """
    Query system_events for TradingView signals received in the last max_age_seconds.
    Returns list of dicts with: symbol, direction, indicator, strength, price, ts
    """
    try:
        from logging_db.trade_logger import get_logger
        db = get_logger()
        cutoff = time.time() - max_age_seconds
        rows = db.conn.execute("""
            SELECT message, ts FROM system_events
            WHERE source = 'tradingview'
              AND ts > datetime(?, 'unixepoch')
            ORDER BY ts DESC LIMIT 20
        """, (cutoff,)).fetchall()

        signals = []
        for msg, ts in rows:
            try:
                import json
                data = json.loads(msg) if isinstance(msg, str) else msg
                symbol = data.get('symbol', '').upper()
                direction = data.get('direction', 'LONG').upper()
                if not symbol or direction not in ('LONG', 'SHORT'):
                    continue
                # Normalize symbol: BTCUSD → BTCUSDT, BTC-USDT → BTCUSDT etc.
                if not symbol.endswith('USDT'):
                    symbol = symbol.replace('-', '').replace('USD', '') + 'USDT'
                signals.append({
                    'symbol': symbol,
                    'direction': direction,
                    'indicator': data.get('indicator', 'tv_alert'),
                    'strength': data.get('strength', 'moderate'),
                    'price': float(data.get('price', 0)),
                    'ts': ts,
                })
            except Exception:
                continue
        return signals
    except Exception:
        return []


# ── Lazy imports (all wrapped so import errors never crash the loop) ──────────

def _import_scanner():
    try:
        import scanner
        return scanner
    except Exception as e:
        logger.debug(f'[v10] scanner import error: {e}')
        return None


def _import_signal_engine():
    try:
        import signal_engine
        return signal_engine
    except Exception as e:
        logger.debug(f'[v10] signal_engine import error: {e}')
        return None


def _import_position_manager():
    try:
        import position_manager
        return position_manager
    except Exception as e:
        logger.debug(f'[v10] position_manager import error: {e}')
        return None


def _import_perps_engine():
    try:
        import perps_engine
        return perps_engine
    except Exception as e:
        logger.debug(f'[v10] perps_engine import error: {e}')
        return None


def _import_hedge_engine():
    try:
        import hedge_engine
        return hedge_engine
    except Exception as e:
        logger.debug(f'[v10] hedge_engine import error: {e}')
        return None


def _import_kill_switch():
    try:
        import kill_switch
        return kill_switch
    except Exception as e:
        logger.debug(f'[v10] kill_switch import error: {e}')
        return None


def _import_risk_engine():
    try:
        import risk_engine
        return risk_engine
    except Exception as e:
        logger.debug(f'[v10] risk_engine import error: {e}')
        return None


def _import_learning_loop():
    try:
        import learning_loop
        return learning_loop
    except Exception as e:
        logger.debug(f'[v10] learning_loop import error: {e}')
        return None


def _import_feature_builder():
    try:
        from ml.feature_builder import build_features, to_array
        return build_features, to_array
    except Exception as e:
        logger.debug(f'[v10] feature_builder import error: {e}')
        return None, None


def _import_regime_classifier():
    try:
        from ml.regime_classifier import classify_from_features
        return classify_from_features
    except Exception as e:
        logger.debug(f'[v10] regime_classifier import error: {e}')
        return None


def _import_get_candles():
    try:
        from data.historical_data import get_candles
        return get_candles
    except Exception as e:
        logger.debug(f'[v10] historical_data import error: {e}')
        return None


def _import_notification_engine():
    try:
        import notifications.notification_engine as ne
        return ne
    except Exception as e:
        logger.debug(f'[v10] notification_engine import error: {e}')
        return None


def _import_incubation_manager():
    try:
        from rbi.incubation_manager import get_size_multiplier
        return get_size_multiplier
    except Exception as e:
        logger.debug(f'[v10] incubation_manager import error: {e}')
        return None


# ── Balance helpers ───────────────────────────────────────────────────────────

def _get_account_balance() -> float:
    """Try broker; fall back to config ACCOUNT_SIZE."""
    perps = _import_perps_engine()
    if perps is not None:
        try:
            broker = perps._get_broker(testnet=True)
            if broker is not None:
                bal = broker.get_account_balance()
                if bal and bal > 0:
                    return float(bal)
        except Exception as e:
            logger.debug(f'[v10] broker balance error: {e}')

    try:
        from config import ACCOUNT_SIZE
        return float(ACCOUNT_SIZE)
    except Exception:
        return 5000.0


def _get_deployed_usd(open_positions: Dict) -> float:
    """Sum notional of all open positions."""
    return sum(float(p.get('position_usd', 0)) for p in open_positions.values())


# ── scan_and_trade ────────────────────────────────────────────────────────────

def scan_and_trade():
    """
    Main 5-minute loop: run scanner, score candidates, open new positions.
    Protected by _scan_lock to prevent parallel runs.
    """
    if not _scan_lock.acquire(blocking=False):
        logger.debug('[v10] scan_and_trade skipped — previous run still active')
        return

    try:
        _scan_and_trade_inner()
    except Exception as e:
        logger.error(f'[v10] scan_and_trade fatal: {e}\n{traceback.format_exc()[:1000]}')
    finally:
        _scan_lock.release()


def _scan_and_trade_inner():
    """Inner body of scan_and_trade — separated so the lock release is guaranteed."""
    ks = _import_kill_switch()
    re = _import_risk_engine()
    scanner = _import_scanner()
    se = _import_signal_engine()
    pm = _import_position_manager()
    perps = _import_perps_engine()
    get_candles = _import_get_candles()
    build_features, _ = _import_feature_builder()
    classify_from_features = _import_regime_classifier()
    ne = _import_notification_engine()
    get_size_multiplier = _import_incubation_manager()

    # Kill switch check
    if ks is not None and ks.is_halted():
        logger.info(f'[v10] scan skipped — kill switch: {ks.get_halt_reason()}')
        return

    # Risk gate
    if re is not None:
        can_trade, reason = re.can_open_new_position()
        if not can_trade:
            logger.info(f'[v10] scan skipped — risk gate: {reason}')
            return

    # Account balance for scanner and sizing
    balance = _get_account_balance()

    # Get current open positions
    open_pos: Dict = {}
    if perps is not None:
        open_pos = perps.get_open_positions()

    open_symbols = list(open_pos.keys())
    deployed_usd = _get_deployed_usd(open_pos)

    # Check for fresh TradingView signals — promote them to priority candidates
    global _seen_tv_signal_keys
    tv_signals = _get_fresh_tv_signals(max_age_seconds=300)
    tv_candidates = []
    for tv in tv_signals:
        key = f"{tv['symbol']}_{tv['direction']}_{tv.get('ts', '')}"
        if key in _seen_tv_signal_keys:
            continue
        _seen_tv_signal_keys.add(key)
        # Keep set bounded
        if len(_seen_tv_signal_keys) > 500:
            _seen_tv_signal_keys.clear()
        # Build candidate dict matching scanner output format
        tv_candidates.append({
            'symbol': tv['symbol'],
            'direction': tv['direction'],
            'vol_spike': 1.5,           # TV signal = elevated priority
            'adx_15m': 25.0,            # assume trending (TV only fires on structured setups)
            'price_move_4h_pct': 1.0,
            'atr_15m': 0.0,             # will be computed from candles in _attempt_entry
            'stop_pct': 1.5,
            'target_pct': 4.5,
            'expected_profit': 5.0,
            'correlation_penalty': 1.0,
            'regime_penalty': 1.0,
            'spread_pct': 0.05,
            'tv_signal': True,
            'tv_strength': tv.get('strength', 'moderate'),
            'tv_indicator': tv.get('indicator', 'tv_alert'),
            'edge_score': 0.6,          # TV signal gets moderate edge score until validated
        })
        logger.info(f'[v10] TV signal: {tv["symbol"]} {tv["direction"]} '
                    f'indicator={tv.get("indicator")} strength={tv.get("strength")}')

    # Run scanner
    if scanner is None:
        logger.debug('[v10] scanner unavailable — skipping')
        if not tv_candidates:
            return
        candidates = tv_candidates
    else:
        scanner_candidates = scanner.scan(
            open_positions=open_symbols,
            account_balance=balance,
        )
        # TV candidates take priority; skip scanner duplicate symbols
        tv_symbols = {c['symbol'] for c in tv_candidates}
        candidates = tv_candidates + [c for c in scanner_candidates
                                      if c['symbol'] not in tv_symbols]

    if not candidates:
        logger.debug('[v10] scan returned 0 candidates')
        return

    logger.info(f'[v10] scan: {len(candidates)} candidates '
                f'(tv={len(tv_candidates)} scanner={len(candidates) - len(tv_candidates)}), '
                f'balance=${balance:.0f} deployed=${deployed_usd:.0f}')

    for candidate in candidates:
        symbol = candidate.get('symbol', '')
        direction = candidate.get('direction', 'LONG')

        # Skip if already in this position
        if perps is not None and perps.get_open_positions().get(symbol):
            logger.debug(f'[v10] {symbol} — already have position, skip')
            continue

        # Re-check risk gate before each entry attempt
        if re is not None:
            can_trade, reason = re.can_open_new_position()
            if not can_trade:
                logger.info(f'[v10] entry blocked by risk: {reason}')
                break   # stop trying more candidates

        try:
            _attempt_entry(
                candidate=candidate,
                symbol=symbol,
                direction=direction,
                balance=balance,
                deployed_usd=deployed_usd,
                perps=perps,
                se=se,
                pm=pm,
                get_candles=get_candles,
                build_features=build_features,
                classify_from_features=classify_from_features,
                ne=ne,
                get_size_multiplier=get_size_multiplier,
            )
        except Exception as e:
            logger.error(f'[v10] entry attempt error {symbol}: {e}\n'
                         f'{traceback.format_exc()[:800]}')
            continue

        # Update deployed after each successful entry
        if perps is not None:
            deployed_usd = _get_deployed_usd(perps.get_open_positions())


def _attempt_entry(candidate, symbol, direction, balance, deployed_usd,
                   perps, se, pm, get_candles, build_features,
                   classify_from_features, ne, get_size_multiplier):
    """Try to enter a position for one candidate. All exceptions propagate to caller."""
    if get_candles is None or build_features is None:
        logger.warning(f'[v10] {symbol} — get_candles={get_candles is not None} build_features={build_features is not None} — skip')
        return

    # Fetch 1h candles for feature building
    df = get_candles(symbol, '1h', 200)
    if df is None or len(df) < 20:
        logger.info(f'[v10] {symbol} — insufficient candle data ({len(df) if df is not None else 0} bars), skip')
        return

    current_price = float(df['close'].iloc[-1])
    if current_price <= 0:
        return

    # ATR from last 7 candles (high-low range proxy)
    atr_7 = float(df['high'].sub(df['low']).tail(7).mean())
    if atr_7 <= 0:
        atr_7 = current_price * 0.015   # 1.5% floor

    # ── Step 1: Build features ───────────────────────────────────────────────
    features = build_features(df, symbol)

    # Inject scanner-derived features
    scanner_vol_spike = float(candidate.get('vol_spike', 0.0))
    if scanner_vol_spike > 0:
        features['vol_spike_5c'] = scanner_vol_spike
    scanner_funding = float(candidate.get('funding_rate', 0.0))
    features['deriv_funding_rate'] = float(max(-1.0, min(1.0, scanner_funding / 0.005)))

    # Inject v4.3 indicator flags + squeeze state (needed for primary setup detection)
    try:
        from data.indicators import add_all_indicators as _add_ind
        _df_ind = _add_ind(df.copy())
        _last = _df_ind.iloc[-1]
        features['supertrend_bullish']  = 1.0 if _last.get('supertrend_bullish', False) else 0.0
        features['cloud_bullish']       = 1.0 if _last.get('cloud_bullish', False) else 0.0
        features['wae_bullish']         = 1.0 if _last.get('wae_bullish', False) else 0.0
        features['wae_exploding']       = 1.0 if _last.get('wae_exploding', False) else 0.0
        features['fisher_cross_up']     = 1.0 if _last.get('fisher_cross_up', False) else 0.0
        features['chop_trending']       = 1.0 if _last.get('chop_trending', False) else 0.0
        features['chop_ranging']        = 1.0 if _last.get('chop_ranging', False) else 0.0
        features['wt_oversold_cross']   = 1.0 if _last.get('wt_oversold_cross', False) else 0.0
        features['lrsi_value']          = float(_last.get('lrsi', 0.5))
        features['squeeze_fired']       = 1.0 if _last.get('squeeze_fired', False) else 0.0
        features['squeeze_direction']   = float(_last.get('squeeze_direction', 0))
        features['supertrend_bearish']  = 0.0 if bool(_last.get('supertrend_bullish', True)) else 1.0
        features['cloud_bearish']       = 0.0 if bool(_last.get('cloud_bullish', True)) else 1.0
        features['wae_bearish']         = 1.0 if _last.get('wae_trend_down', False) else 0.0
        features['fisher_cross_down']   = 1.0 if _last.get('fisher_cross_down', False) else 0.0
        features['wt_overbought']       = 1.0 if _last.get('wt_overbought', False) else 0.0
        # avwap_dev = (close - anchored_vwap) / anchored_vwap — used by ranging_mr setups
        features['vwap_session_dist_pct'] = float(_last.get('avwap_dev', 0.0)) * 100.0
        # KST oscillator (equity-origin, also useful on crypto for momentum direction)
        features['kst_value']           = float(_last.get('kst', 0.0))
        features['kst_signal_value']    = float(_last.get('kst_signal', 0.0))
        features['kst_bullish']         = 1.0 if float(_last.get('kst', 0.0)) > float(_last.get('kst_signal', 0.0)) else 0.0
    except Exception as _e:
        logger.debug(f'[v10] indicator enrichment error {symbol}: {_e}')

    if candidate.get('tv_signal'):
        features['tv_signal'] = 1.0

    # ── Step 2: Classify regime ──────────────────────────────────────────────
    regime = 'UNKNOWN'
    if classify_from_features is not None:
        try:
            regime = classify_from_features(features)
        except Exception as e:
            logger.debug(f'[v10] regime classify error {symbol}: {e}')

    # ── Step 3: Score (used for sizing, not gating) ──────────────────────────
    if se is None:
        return

    # model_store=None is intentional: ML tower returns 50.0 (neutral) until
    # walk_forward_trainer has ≥50 live trades with paper=0 to produce a valid model.
    result = se.score(features, direction, regime, model_store=None)
    composite = result['composite_score']

    # ── Step 4: Entry decision — Tier 1 setup OR Tier 2 score ───────────────
    from signal_engine import detect_primary_setup
    primary_setup = detect_primary_setup(features, direction)

    if primary_setup:
        # Tier 1: specific setup firing — enter regardless of composite score
        tier = 1
        size_mult = 1.0   # full position size
        logger.info(f'[v10] {symbol} {direction} TIER 1 — {primary_setup["label"]} '
                    f'(composite={composite:.1f} used for sizing only)')
    elif composite >= 50:
        # Tier 2: no primary setup but score clears floor — enter at reduced size
        tier = 2
        size_mult = 0.75
        logger.info(f'[v10] {symbol} {direction} TIER 2 — composite={composite:.1f} '
                    f'(tech={result.get("technical_score",0):.1f} ml={result.get("ml_score",50):.1f})')
    else:
        if composite > 44:
            logger.info(f'[v10] {symbol} {direction} score={composite:.1f} < 50, '
                        f'no primary setup — skip')
        return

    # ── Step 5: Economics gate (runs after setup quality known) ─────────────
    try:
        from risk.economics_gate import check as economics_check
        atr_pct = atr_7 / current_price if current_price > 0 else 0.015
        econ = economics_check(
            symbol=symbol,
            direction=direction,
            current_price=current_price,
            atr_pct=atr_pct,
            funding_rate=float(candidate.get('funding_rate', 0.0)) / (365 * 3),
            spread_pct=float(candidate.get('spread_pct', 0.1)) / 100.0,
            volume_24h_usd=float(candidate.get('vol_usd', candidate.get('volume_24h_usd', 50_000_000))),
            leverage=3,
            account_balance=balance,
            is_ranging=bool(features.get('chop_ranging', 0) > 0),
        )
        candidate['edge_score']    = econ.get('edge_score', 0.5)
        candidate['quality_tier']  = econ.get('quality_tier', 'B')

        if not econ.get('approved', True):
            reason = econ.get('reject_reason', 'economics veto')
            logger.info(f'[v10] {symbol} {direction} ECONOMICS VETO (Tier{tier}): {reason} '
                        f'(ev={econ.get("ev_pct", 0)*100:.3f}% '
                        f'fees={econ.get("fee_drag_pct", 0)*100:.3f}%)')
            if ne is not None:
                try:
                    ne.notify_rejection(symbol=symbol, direction=direction,
                                        reason=f'economics: {reason}')
                except Exception:
                    pass
            return
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f'[v10] economics gate error {symbol}: {e}')

    setup_str = primary_setup['label'] if primary_setup else f'composite={composite:.1f}'
    logger.info(f'[v10] {symbol} {direction} ENTRY SIGNAL: '
                f'{setup_str} composite={composite:.1f} tier={tier} regime={regime}')

    # Compute position size
    if pm is None:
        logger.warning(f'[v10] {symbol} — position_manager is None, skip')
        return

    regime_mult = _REGIME_SIZE_MULT.get(regime, 0.90)
    ml_score = result.get('ml_score', 50.0)

    # Pull live values from feature vector rather than hardcoding neutral defaults
    vol_regime_raw = features.get('regime_vol_mult', 1.0)
    # Map to int tier: <0.8→expanding(3), 0.8-1.1→normal(2), >1.1→compressing(1)
    if vol_regime_raw < 0.85:
        vol_regime_int = 3
    elif vol_regime_raw > 1.10:
        vol_regime_int = 1
    else:
        vol_regime_int = 2
    fg_current = float(features.get('regime_fg_current', 50.0))
    # edge_score sourced from economics gate result (passed via candidate dict)
    edge_score = float(candidate.get('edge_score', 0.5))

    sizing = pm.compute_position_size(
        account_balance=balance,
        current_price=current_price,
        atr_7=atr_7,
        stop_multiplier=1.5,
        vol_regime=vol_regime_int,
        ml_score=ml_score,
        fg_current=fg_current,
        composite_score=composite,
        correlation_penalty=float(candidate.get('correlation_penalty', 1.0)),
        edge_score=edge_score,
        cascade_risk_score=0,
        deployed_usd=deployed_usd,
        paper=_paper,
    )

    size_usd = sizing['position_usd'] * regime_mult * size_mult

    # Apply RBI incubation multiplier
    rbi_mult = 1.0
    if get_size_multiplier is not None:
        try:
            rbi_mult = get_size_multiplier(symbol, [])
        except Exception as e:
            logger.debug(f'[v10] RBI multiplier error: {e}')
    size_usd *= rbi_mult

    if size_usd < 10.0:
        logger.debug(f'[v10] {symbol} size ${size_usd:.2f} too small, skip')
        return

    leverage = sizing.get('leverage', 3)
    stop_distance = sizing.get('stop_distance', atr_7 * 1.5)

    if direction == 'LONG':
        stop_price = current_price - stop_distance
        take_profit_price = current_price + stop_distance * 2.0
    else:
        stop_price = current_price + stop_distance
        take_profit_price = current_price - stop_distance * 2.0

    # Execute entry
    if perps is None:
        return

    entry_setup_name = primary_setup['name'] if primary_setup else ''

    if direction == 'LONG':
        pos = perps.open_long(
            symbol=symbol,
            position_usd=size_usd,
            entry_price=current_price,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            leverage=leverage,
            composite_score=composite,
            atr_at_entry=atr_7,
            regime=regime,
            entry_setup=entry_setup_name,
            paper=_paper,
        )
    else:
        pos = perps.open_short(
            symbol=symbol,
            position_usd=size_usd,
            entry_price=current_price,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            leverage=leverage,
            composite_score=composite,
            atr_at_entry=atr_7,
            regime=regime,
            entry_setup=entry_setup_name,
            paper=_paper,
        )

    if pos is None:
        logger.warning(f'[v10] {symbol} entry returned None — execution failed')
        return

    setup_tag = f' setup={entry_setup_name}' if entry_setup_name else ' tier2:score'
    logger.info(f'[v10] ENTERED {direction} {symbol}: '
                f'${size_usd:.0f} @ ${current_price:.4f} '
                f'stop=${stop_price:.4f} tp=${take_profit_price:.4f} '
                f'lev={leverage}x composite={composite:.1f}{setup_tag}')

    # Post-entry notification
    if ne is not None:
        try:
            top_3 = [k for k, v in sorted(result.get('components', {}).items(),
                                           key=lambda x: abs(x[1]), reverse=True)[:3]]
            ne.notify_trade_open(
                symbol=symbol,
                direction=direction,
                size_usd=size_usd,
                entry_price=current_price,
                score=composite,
                top_3=top_3,
                features=features,
                regime=regime,
            )
        except Exception as e:
            logger.debug(f'[v10] trade_open notify error: {e}')


# ── exit_monitor ──────────────────────────────────────────────────────────────

def exit_monitor():
    """
    30-second loop: evaluate 6-priority exit stack for all open positions.
    """
    try:
        _exit_monitor_inner()
    except Exception as e:
        logger.error(f'[v10] exit_monitor fatal: {e}\n{traceback.format_exc()[:1000]}')


def _exit_monitor_inner():
    perps = _import_perps_engine()
    pm = _import_position_manager()
    get_candles = _import_get_candles()
    build_features, _ = _import_feature_builder()
    classify_from_features = _import_regime_classifier()
    ll = _import_learning_loop()
    ne = _import_notification_engine()
    ks = _import_kill_switch()

    if perps is None or pm is None:
        return

    open_positions = perps.get_open_positions()
    if not open_positions:
        return

    kill_triggered = False
    if ks is not None:
        kill_triggered = ks.is_halted()

    balance = _get_account_balance()
    deployed_usd = _get_deployed_usd(open_positions)

    for symbol, pos in list(open_positions.items()):
        try:
            _evaluate_position_exit(
                symbol=symbol,
                pos=pos,
                perps=perps,
                pm=pm,
                get_candles=get_candles,
                build_features=build_features,
                classify_from_features=classify_from_features,
                ll=ll,
                ne=ne,
                balance=balance,
                deployed_usd=deployed_usd,
                kill_triggered=kill_triggered,
            )
        except Exception as e:
            logger.error(f'[v10] exit eval error {symbol}: {e}\n'
                         f'{traceback.format_exc()[:800]}')


def _evaluate_position_exit(symbol, pos, perps, pm, get_candles,
                             build_features, classify_from_features,
                             ll, ne, balance, deployed_usd, kill_triggered):
    """Evaluate and act on exit signals for one position."""
    # Get current price from recent 1m candles
    current_price: Optional[float] = None
    current_features: Optional[Dict] = None
    current_df = None

    if get_candles is not None:
        current_df = get_candles(symbol, '1m', 5)
        if current_df is not None and len(current_df) > 0:
            current_price = float(current_df['close'].iloc[-1])

    if current_price is None or current_price <= 0:
        # Fall back to last known price in position dict
        current_price = float(pos.get('last_price', pos.get('entry_price', 0)))

    if current_price <= 0:
        return

    # Update last_price in position
    perps.update_position_price(symbol, current_price)

    # Build current features for thesis check (use 1h data for richer features)
    if get_candles is not None and build_features is not None:
        try:
            df_1h = get_candles(symbol, '1h', 60)
            if df_1h is not None and len(df_1h) >= 20:
                current_features = build_features(df_1h, symbol)
        except Exception as e:
            logger.debug(f'[v10] feature build for exit {symbol}: {e}')

    # Evaluate exit stack
    exit_decision = pm.check_exits(
        position=pos,
        current_price=current_price,
        current_features=current_features,
        model_store=None,
        account_balance=balance,
        total_deployed_usd=deployed_usd,
        margin_utilization_pct=0.0,
        drawdown_pct=0.0,
        kill_switch_triggered=kill_triggered,
    )

    # Handle trailing stop activation (non-exit signal from check_exits priority 1)
    if not exit_decision.should_exit and exit_decision.exit_type == 'trailing_activated':
        try:
            pm.activate_trailing(pos, current_price)
            logger.debug(f'[v10] {symbol} trailing stop activated @ {current_price:.4f}')
        except Exception as e:
            logger.debug(f'[v10] trailing activate error {symbol}: {e}')
        return

    # Update trailing stop if active
    if pos.get('trailing_active', False):
        try:
            pm.update_trailing_stop(pos, current_price)
        except Exception as e:
            logger.debug(f'[v10] trailing update error {symbol}: {e}')

    if not exit_decision.should_exit:
        return

    # Execute close
    direction = pos.get('direction', 'LONG')
    exit_reason = exit_decision.reason
    partial_pct = exit_decision.partial_pct

    logger.info(f'[v10] EXIT {symbol} {direction}: '
                f'priority={exit_decision.priority} type={exit_decision.exit_type} '
                f'partial={partial_pct:.0%} reason={exit_reason[:80]}')

    close_result = perps.close_position(
        symbol=symbol,
        reason=exit_decision.exit_type,
        partial_pct=partial_pct,
        paper=_paper,
    )

    if close_result is None:
        logger.warning(f'[v10] close_position returned None for {symbol}')
        return

    pnl_usd = float(close_result.get('pnl_usd', 0))
    exit_price = float(close_result.get('exit_price', current_price))
    entry_price = float(pos.get('entry_price', exit_price))
    pnl_pct = (pnl_usd / (pos.get('position_usd', 1) + 1e-9))

    logger.info(f'[v10] CLOSED {direction} {symbol}: '
                f'pnl=${pnl_usd:+.2f} ({pnl_pct:+.1%}) @ {exit_price:.4f}')

    # Learning loop record
    if ll is not None and partial_pct >= 1.0:
        try:
            trade_id = int(time.time())   # approximate; real DB inserts use auto-increment
            features_snap = current_features or {}
            regime = pos.get('regime', 'UNKNOWN')
            entry_score = float(pos.get('entry_composite_score', 0.0))
            ll.record_closed_trade(
                trade_id=trade_id,
                symbol=symbol,
                direction=direction,
                won=pnl_usd > 0,
                pnl_usd=pnl_usd,
                entry_price=entry_price,
                exit_price=exit_price,
                entry_score=entry_score,
                exit_score=0.0,
                regime=regime,
                features=features_snap,
            )
        except Exception as e:
            logger.debug(f'[v10] learning record error {symbol}: {e}')

    # Notification
    if ne is not None:
        try:
            top_3 = [exit_decision.exit_type, exit_reason[:50]]
            ne.notify_trade_close(
                symbol=symbol,
                direction=direction,
                pnl_usd=pnl_usd,
                pnl_pct=pnl_pct,
                exit_type=exit_decision.exit_type,
                top_3=top_3,
                features=current_features or {},
                regime=pos.get('regime', 'UNKNOWN'),
                score=float(pos.get('entry_composite_score', 0.0)),
            )
        except Exception as e:
            logger.debug(f'[v10] trade_close notify error: {e}')

    # Handle scale-out partial flags
    if partial_pct < 1.0:
        if exit_decision.exit_type == 'scale_out_33':
            pos['scale_33_done'] = True
        elif exit_decision.exit_type == 'scale_out_66':
            pos['scale_66_done'] = True


# ── kill_switch_monitor ───────────────────────────────────────────────────────

def kill_switch_monitor():
    """60-second loop: check account balance against kill threshold."""
    try:
        ks = _import_kill_switch()
        if ks is None:
            return
        current = _get_account_balance()
        ks.check_balance(current, _initial_balance, paper=_paper)
    except Exception as e:
        logger.debug(f'[v10] kill_switch_monitor error: {e}')


# ── hedge_rebalance ───────────────────────────────────────────────────────────

def hedge_rebalance():
    """5-minute loop: rebalance delta-neutral hedge position."""
    try:
        he = _import_hedge_engine()
        perps = _import_perps_engine()
        if he is None or perps is None:
            return
        open_positions = perps.get_open_positions()
        balance = _get_account_balance()
        he.rebalance(open_positions, balance, paper=_paper)
    except Exception as e:
        logger.debug(f'[v10] hedge_rebalance error: {e}')


# ── ml_retrain_check ──────────────────────────────────────────────────────────

def ml_retrain_check():
    """6-hour loop: trigger walk-forward retrains for slots with enough new data."""
    try:
        ll = _import_learning_loop()
        if ll is None:
            return
        triggered = ll.maybe_trigger_retrains(paper=_paper)
        if triggered:
            logger.info(f'[v10] ml_retrain_check: triggered {len(triggered)} retrains: '
                        f'{triggered}')
    except Exception as e:
        logger.debug(f'[v10] ml_retrain_check error: {e}')


# ── mes_futures_scan ─────────────────────────────────────────────────────────

def mes_futures_scan():
    """
    2-minute loop (US market hours only): MES opening-range breakout scanner.

    Strategy:
      - Hard block 9:30–10:00 ET (opening chaos)
      - Track the 9:30–10:00 opening range (high/low of first 30 min)
      - Enter LONG on breakout above OR1 high with volume confirmation
      - Enter SHORT on breakdown below OR1 low with volume confirmation
      - Stop: other side of opening range + 1 point buffer
      - Target: 2× stop distance (min 4 points, ≈ $20/contract)
      - Max 1 position at a time, max 2 contracts
      - Daily loss limit: $150 (10 pts × $5 × 3 contracts)
      - Hard stop at 3:45 PM ET — close any open MES position
    """
    try:
        _mes_scan_inner()
    except Exception as e:
        logger.error(f'[mes] scan fatal: {e}\n{traceback.format_exc()[:800]}')


# Opening range state (resets each trading day)
_mes_or_high: float    = 0.0
_mes_or_low: float     = float('inf')
_mes_or_locked: bool   = False   # True once 10:00 ET passes
_mes_or_date: str      = ''
_mes_daily_pnl: float  = 0.0
_mes_daily_date: str   = ''


def _mes_scan_inner():
    global _mes_or_high, _mes_or_low, _mes_or_locked, _mes_or_date
    global _mes_daily_pnl, _mes_daily_date

    from config import FUTURES_ENABLED, FUTURES_NUM_CONTRACTS
    if not FUTURES_ENABLED:
        return

    try:
        import pytz
        et = pytz.timezone('America/New_York')
        now_et = datetime.now(et)
    except Exception:
        return

    # Only run during US regular session 9:30–15:45 ET on weekdays
    if now_et.weekday() >= 5:
        return
    h, m = now_et.hour, now_et.minute
    if not ((h == 9 and m >= 30) or (10 <= h <= 15) or (h == 15 and m <= 45)):
        return

    today_str = now_et.strftime('%Y-%m-%d')

    # Reset opening range and daily P&L each new day
    if _mes_or_date != today_str:
        _mes_or_high   = 0.0
        _mes_or_low    = float('inf')
        _mes_or_locked = False
        _mes_or_date   = today_str

    if _mes_daily_date != today_str:
        _mes_daily_pnl  = 0.0
        _mes_daily_date = today_str

    # Import broker
    try:
        from execution.ibkr_broker import IBKRBroker
    except Exception as e:
        logger.debug(f'[mes] ibkr_broker import error: {e}')
        return

    broker = IBKRBroker()
    if not broker.connect():
        logger.warning('[mes] IBKR connection failed — skipping cycle')
        return

    try:
        # Get current MES price
        price = broker.get_price('MES')
        if not price or price <= 0:
            return

        # Build / extend opening range (9:30–10:00 ET)
        if h == 9 and m < 60:  # still 9:xx
            if not _mes_or_locked:
                _mes_or_high = max(_mes_or_high, price)
                _mes_or_low  = min(_mes_or_low,  price)
                logger.debug(f'[mes] OR building: {_mes_or_low:.2f}–{_mes_or_high:.2f}')

        # Lock OR at 10:00 ET
        if h >= 10 and not _mes_or_locked and _mes_or_high > 0 and _mes_or_low < float('inf'):
            _mes_or_locked = True
            or_range = _mes_or_high - _mes_or_low
            logger.info(f'[mes] Opening range locked: {_mes_or_low:.2f}–{_mes_or_high:.2f} '
                        f'({or_range:.2f} pts)')

        # Don't trade before OR is locked
        if not _mes_or_locked:
            return

        # Hard stop at 15:45 — close any position
        if h == 15 and m >= 45:
            pos = broker.get_position('MES')
            if pos and pos.get('qty', 0) != 0:
                logger.info('[mes] EOD close — 15:45 ET hard stop')
                qty = abs(int(pos['qty']))
                if pos['qty'] > 0:
                    broker.sell_mes(qty=qty, reason='eod_close')
                else:
                    broker.cover_mes(qty=qty, reason='eod_close')
            return

        # Daily loss limit: $150
        if _mes_daily_pnl < -150:
            logger.info(f'[mes] Daily loss limit hit: ${_mes_daily_pnl:.2f} — no new trades')
            return

        or_range   = _mes_or_high - _mes_or_low
        or_mid     = (_mes_or_high + _mes_or_low) / 2
        min_range  = 2.0   # opening range must be at least 2 points to be meaningful
        if or_range < min_range:
            logger.debug(f'[mes] OR too tight ({or_range:.2f} pts) — skip')
            return

        n_contracts = min(int(FUTURES_NUM_CONTRACTS), 2)
        pos         = broker.get_position('MES')
        has_pos     = pos is not None and pos.get('qty', 0) != 0

        if has_pos:
            # Monitor existing position for stop/target
            entry    = float(pos.get('entry_price', or_mid))
            qty      = int(pos.get('qty', 0))
            stop     = float(pos.get('stop', 0))
            target   = float(pos.get('target', 0))
            is_long  = qty > 0

            if is_long:
                if price <= stop:
                    pnl = (price - entry) * abs(qty) * 5 - IBKR_COMMISSION_RT * abs(qty)
                    _mes_daily_pnl += pnl
                    broker.sell_mes(qty=abs(qty), reason='stop_hit')
                    logger.info(f'[mes] STOP HIT LONG @ {price:.2f} pnl=${pnl:.2f}')
                elif price >= target:
                    pnl = (price - entry) * abs(qty) * 5 - IBKR_COMMISSION_RT * abs(qty)
                    _mes_daily_pnl += pnl
                    broker.sell_mes(qty=abs(qty), reason='target_hit')
                    logger.info(f'[mes] TARGET HIT LONG @ {price:.2f} pnl=${pnl:.2f}')
            else:
                if price >= stop:
                    pnl = (entry - price) * abs(qty) * 5 - IBKR_COMMISSION_RT * abs(qty)
                    _mes_daily_pnl += pnl
                    broker.cover_mes(qty=abs(qty), reason='stop_hit')
                    logger.info(f'[mes] STOP HIT SHORT @ {price:.2f} pnl=${pnl:.2f}')
                elif price <= target:
                    pnl = (entry - price) * abs(qty) * 5 - IBKR_COMMISSION_RT * abs(qty)
                    _mes_daily_pnl += pnl
                    broker.cover_mes(qty=abs(qty), reason='target_hit')
                    logger.info(f'[mes] TARGET HIT SHORT @ {price:.2f} pnl=${pnl:.2f}')
            return

        # Look for breakout entry
        buffer     = 0.25   # 1 tick above/below OR
        long_entry = _mes_or_high + buffer
        short_entry = _mes_or_low  - buffer

        if price >= long_entry:
            stop_price   = _mes_or_low - buffer        # below OR low
            stop_dist    = price - stop_price
            target_price = price + max(stop_dist * 2, 4.0)  # 2R or 4 pts min
            logger.info(f'[mes] LONG BREAKOUT @ {price:.2f} stop={stop_price:.2f} '
                        f'target={target_price:.2f} contracts={n_contracts}')
            broker.buy_mes(
                qty=n_contracts,
                stop_price=stop_price,
                target_price=target_price,
                reason=f'or_breakout_long OR={_mes_or_low:.2f}-{_mes_or_high:.2f}',
            )

        elif price <= short_entry:
            stop_price   = _mes_or_high + buffer
            stop_dist    = stop_price - price
            target_price = price - max(stop_dist * 2, 4.0)
            logger.info(f'[mes] SHORT BREAKDOWN @ {price:.2f} stop={stop_price:.2f} '
                        f'target={target_price:.2f} contracts={n_contracts}')
            broker.short_mes(
                qty=n_contracts,
                stop_price=stop_price,
                target_price=target_price,
                reason=f'or_breakdown_short OR={_mes_or_low:.2f}-{_mes_or_high:.2f}',
            )

    finally:
        try:
            broker.disconnect()
        except Exception:
            pass


IBKR_COMMISSION_RT = 0.47 * 2   # round-trip commission per contract


# ── rbi_nightly ───────────────────────────────────────────────────────────────

def rbi_nightly():
    """2:00 AM ET nightly: run RBI research + backtest pipeline on BTCUSDT."""
    logger.info('[v10] rbi_nightly: starting BTCUSDT RBI pipeline')
    try:
        ll = _import_learning_loop()
        if ll is None:
            return
        results = ll.run_nightly_rbi(symbol='BTCUSDT', paper=_paper)
        logger.info(f'[v10] rbi_nightly done: {results}')
        ne = _import_notification_engine()
        if ne is not None:
            ne.notify_system(
                title='RBI Nightly Complete',
                detail=(f"promoted={results.get('promoted', 0)} "
                        f"passed={results.get('passed', 0)} "
                        f"error={results.get('error', 'none')}"),
            )
    except Exception as e:
        logger.error(f'[v10] rbi_nightly error: {e}\n{traceback.format_exc()[:800]}')


# ── Startup ───────────────────────────────────────────────────────────────────

def _init_globals():
    """Set module-level globals from config at startup."""
    global _initial_balance, _paper
    try:
        from config import PAPER_TRADING, ACCOUNT_SIZE
        _paper = bool(PAPER_TRADING)
        _initial_balance = float(ACCOUNT_SIZE)
    except Exception as e:
        logger.warning(f'[v10] config read error: {e} — using defaults')
        _paper = True
        _initial_balance = 5000.0

    logger.info(f'[v10] mode={"PAPER" if _paper else "LIVE"} '
                f'initial_balance=${_initial_balance:.0f}')


def _startup_notification():
    """Send system-start notification."""
    try:
        ne = _import_notification_engine()
        if ne is not None:
            ne.notify_system(
                title='v10 System Started',
                detail=(f'mode={"PAPER" if _paper else "LIVE"} '
                        f'balance=${_initial_balance:.0f}'),
            )
    except Exception:
        pass


def run_forever():
    """
    Set up all schedules and run the v10 loop forever.

    Schedule:
      - scan_and_trade:    every 5 minutes
      - exit_monitor:      every 30 seconds
      - hedge_rebalance:   every 5 minutes (offset by 2.5 min to avoid collision with scan)
      - kill_switch_check: every 60 seconds
      - ml_retrain_check:  every 6 hours
      - rbi_nightly:       daily at 02:00 ET (scheduled as UTC 07:00 which covers ET 02:00)
    """
    _init_globals()

    # Initialise DB tables for learning loop
    ll = _import_learning_loop()
    if ll is not None:
        try:
            ll._ensure_tables()
        except Exception:
            pass

    # Log startup
    _startup_notification()
    logger.info('[v10] Scheduler starting — wiring schedules...')

    # Wire schedules
    schedule.every(5).minutes.do(scan_and_trade)
    schedule.every(30).seconds.do(exit_monitor)
    schedule.every(5).minutes.do(hedge_rebalance)
    schedule.every(60).seconds.do(kill_switch_monitor)
    schedule.every(6).hours.do(ml_retrain_check)
    schedule.every().day.at('07:00').do(rbi_nightly)   # 07:00 UTC ≈ 02:00 ET

    from config import FUTURES_ENABLED
    if FUTURES_ENABLED:
        schedule.every(2).minutes.do(mes_futures_scan)
        logger.info('[v10] MES futures scanner wired (every 2 min)')

    logger.info('[v10] All schedules wired. Running scan immediately...')

    # Run immediately on startup (don't wait 5 minutes for first scan)
    scan_and_trade()

    logger.info('[v10] Main loop running. Press Ctrl+C to stop.')
    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except KeyboardInterrupt:
            logger.info('[v10] Shutdown requested via KeyboardInterrupt')
            raise
        except Exception as e:
            logger.error(f'[v10] Scheduler loop error: {e}\n{traceback.format_exc()[:800]}')
            time.sleep(5)   # brief back-off before resuming
