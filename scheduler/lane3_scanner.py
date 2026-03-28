"""
scheduler/lane3_scanner.py — Lane 3 prediction market scan.

Runs every 15 minutes (configurable via LANE3_SCAN_INTERVAL_SECONDS).

Pipeline per market:
  1. Fetch active markets (Polymarket + Kalshi)
  2. Filter to tradeable (volume, spread, days-to-expiry)
  3. Ensemble forecaster → our probability estimate (Claude, +GPT/Gemini if keys set)
  4. Calibrate probability (Platt scaling via pm_calibrator)
  5. Whale tracker edge boost (Polymarket CLOB trade history)
  6. Edge = calibrated_prob - market_prob → must exceed PM_MIN_EDGE_PCT (3%)
  7. Risk checks: position limits, daily loss gate, max size
  8. Place paper/live order via polymarket_broker or kalshi_broker
  9. Monitor open positions: check resolution + stop/target exits

Called by: scheduler/job_runner.py → setup_schedules()
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime

import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    PAPER_TRADING, MARKET_TIMEZONE,
    LANE3_ENABLED, POLYMARKET_ENABLED, KALSHI_ENABLED,
    PM_MAX_POSITION_USD, PM_MIN_EDGE_PCT, PM_MAX_POSITIONS,
    PM_MIN_VOLUME_USD, PM_MIN_DAYS, PM_MAX_DAYS,
    PM_STOP_LOSS_FRACTION, PM_TAKE_PROFIT_FRACTION,
    LANE3_SCAN_INTERVAL_SECONDS,
)
from logging_db.trade_logger import log_event
from alerts.telegram_alert import alert_system

logger = logging.getLogger(__name__)

# Lazy imports — only load when LANE3_ENABLED=true
_pm_broker  = None
_kx_broker  = None


def _get_polymarket_broker():
    global _pm_broker
    if _pm_broker is None:
        from execution.polymarket_broker import get_polymarket_broker
        _pm_broker = get_polymarket_broker()
    return _pm_broker


def _get_kalshi_broker():
    global _kx_broker
    if _kx_broker is None:
        from execution.kalshi_broker import get_kalshi_broker
        _kx_broker = get_kalshi_broker()
    return _kx_broker


# ── Position monitoring ───────────────────────────────────────────────────────

def _monitor_open_positions() -> None:
    """Check all open Lane 3 positions for resolution or stop/target exits."""
    brokers = []
    if POLYMARKET_ENABLED:
        brokers.append(_get_polymarket_broker())
    if KALSHI_ENABLED:
        brokers.append(_get_kalshi_broker())

    for broker in brokers:
        for pos in list(broker.get_positions()):
            try:
                # Check resolution first
                pos = broker.check_resolution(pos)
                if pos.resolved:
                    continue

                # Update mark-to-market price
                # Stop loss: exit if current price drops to stop_price
                if pos.current_price <= pos.stop_price:
                    logger.info(f"[lane3] STOP LOSS hit: {pos.market_id[:20]} current={pos.current_price:.3f} stop={pos.stop_price:.3f}")
                    broker.close_position(pos, reason="stop_loss")
                    log_event('INFO', 'lane3', f"STOP LOSS: {pos.market_id[:20]} {pos.side} @ {pos.current_price:.3f}")
                    continue

                # Take profit: exit if current price rises to target
                if pos.current_price >= pos.target_price:
                    logger.info(f"[lane3] TAKE PROFIT hit: {pos.market_id[:20]} current={pos.current_price:.3f} target={pos.target_price:.3f}")
                    broker.close_position(pos, reason="take_profit")
                    log_event('INFO', 'lane3', f"TAKE PROFIT: {pos.market_id[:20]} {pos.side} @ {pos.current_price:.3f}")

            except Exception as e:
                logger.warning(f"[lane3] Monitor position {pos.market_id[:20]}: {e}")


# ── Main scan ─────────────────────────────────────────────────────────────────

def _evaluate_market(
    market_id: str,
    question: str,
    category: str,
    market_prob: float,
    volume_usd: float,
    days_to_expiry: float,
    spread: float,
    platform: str,
    token_id: str = "",
) -> dict | None:
    """
    Run the full evaluation pipeline for one market.
    Returns a dict with signal details, or None if market should be skipped.
    """
    # ── Ensemble forecast ──────────────────────────────────────────────
    try:
        from strategies.ai_agents.ensemble_forecaster import forecast, get_edge_vs_market
        ensemble = forecast(
            question=question,
            category=category,
            market_prob=market_prob,
            volume_usd=volume_usd,
            days_to_expiry=days_to_expiry,
            spread=spread,
        )
    except Exception as e:
        logger.warning(f"[lane3] Ensemble forecast failed for {market_id[:20]}: {e}")
        return None

    # ── Calibrate probability ──────────────────────────────────────────
    try:
        from learning.pm_calibrator import calibrate_pm, get_adaptive_weights
        weights = get_adaptive_weights()
        calibrated_prob = calibrate_pm(
            raw_prob=ensemble.probability,
            model_name="claude",           # primary model
            spread=ensemble.spread,
        )
    except Exception as e:
        logger.warning(f"[lane3] Calibration error: {e}")
        calibrated_prob = ensemble.probability
        weights = {}

    # ── Whale tracker boost (Polymarket only) ─────────────────────────
    whale_boost = 0.0
    if platform == "polymarket" and token_id:
        try:
            from data.whale_tracker import get_whale_edge_boost
            whale_boost = get_whale_edge_boost(market_id, token_id, outcome="YES")
        except Exception as e:
            logger.debug(f"[lane3] Whale tracker error: {e}")

    final_prob = max(0.01, min(0.99, calibrated_prob + whale_boost))

    # ── Edge calculation ───────────────────────────────────────────────
    edge = final_prob - market_prob

    logger.info(
        f"[lane3] {platform} {market_id[:20]}… "
        f"mkt={market_prob:.3f} our={final_prob:.3f} edge={edge:+.3f} "
        f"conf={ensemble.confidence} whale_boost={whale_boost:+.3f}"
    )

    if abs(edge) < PM_MIN_EDGE_PCT:
        return None  # insufficient edge

    side = "YES" if edge > 0 else "NO"
    entry_price = market_prob if side == "YES" else (1.0 - market_prob)

    return {
        "market_id": market_id,
        "question": question,
        "platform": platform,
        "side": side,
        "edge": edge,
        "market_prob": market_prob,
        "our_prob": final_prob,
        "confidence": ensemble.confidence,
        "entry_price": entry_price,
        "reasoning": ensemble.reasoning[:300],
        "days_to_expiry": days_to_expiry,
        "volume_usd": volume_usd,
    }


def _place_order(signal: dict, broker) -> None:
    """Place a prediction market order via the appropriate broker."""
    from risk.risk_manager import get_risk_manager
    rm = get_risk_manager()

    # Daily loss gate
    if rm.is_halted:
        logger.info(f"[lane3] Skipping {signal['market_id'][:20]} — risk manager halted: {rm.halt_reason}")
        return

    # Position limit
    open_count = len(broker.get_positions())
    if open_count >= PM_MAX_POSITIONS:
        logger.info(f"[lane3] Skipping {signal['market_id'][:20]} — at max positions ({open_count}/{PM_MAX_POSITIONS})")
        return

    # Size the trade — scale by confidence
    conf_mult = {"HIGH": 1.0, "MEDIUM": 0.7, "LOW": 0.4}.get(signal["confidence"], 0.5)
    size_usd = PM_MAX_POSITION_USD * conf_mult
    size_usd = max(5.0, min(PM_MAX_POSITION_USD, size_usd))

    result = broker.place_order(
        market_id=signal["market_id"],
        side=signal["side"],
        size_usd=size_usd,
        price=signal["entry_price"],
    )

    if result.success:
        msg = (
            f"LANE3 {'PAPER' if broker.is_paper else 'LIVE'} {signal['platform'].upper()}: "
            f"{signal['side']} {signal['market_id'][:24]} "
            f"${size_usd:.2f} @ {signal['entry_price']:.3f} | "
            f"edge={signal['edge']:+.3f} conf={signal['confidence']}"
        )
        logger.info(f"[lane3] {msg}")
        log_event('INFO', 'lane3', msg)
        alert_system('INFO', msg)
    else:
        logger.warning(f"[lane3] Order failed for {signal['market_id'][:20]}: {result.error}")


# ── Entry point called by job_runner ─────────────────────────────────────────

def run_prediction_market_scan() -> None:
    """
    Full Lane 3 scan: discover markets, evaluate, place orders.
    Called every LANE3_SCAN_INTERVAL_SECONDS by the scheduler.
    """
    if not LANE3_ENABLED:
        return

    start = time.time()
    logger.info("[lane3] === Prediction Market Scan START ===")

    # 1. Monitor open positions first
    _monitor_open_positions()

    signals: list[dict] = []

    # 2. Polymarket markets
    if POLYMARKET_ENABLED:
        try:
            pm_broker = _get_polymarket_broker()
            snapshots = pm_broker.get_markets(min_volume=PM_MIN_VOLUME_USD, max_results=30)
            for snap in snapshots:
                if not (PM_MIN_DAYS <= snap.days_to_expiry <= PM_MAX_DAYS):
                    continue
                if not (0.03 <= snap.yes_price <= 0.97):
                    continue
                sig = _evaluate_market(
                    market_id=snap.market_id,
                    question=snap.question,
                    category=snap.market_type,
                    market_prob=snap.yes_price,
                    volume_usd=snap.volume_usd,
                    days_to_expiry=snap.days_to_expiry,
                    spread=snap.spread,
                    platform="polymarket",
                )
                if sig:
                    sig["broker"] = "polymarket"
                    signals.append(sig)
        except Exception as e:
            logger.error(f"[lane3] Polymarket scan error: {e}")

    # 3. Kalshi markets
    if KALSHI_ENABLED:
        try:
            kx_broker = _get_kalshi_broker()
            snapshots = kx_broker.get_markets(min_volume=500, max_results=30)
            for snap in snapshots:
                if not (PM_MIN_DAYS <= snap.days_to_expiry <= PM_MAX_DAYS):
                    continue
                if not (0.03 <= snap.yes_price <= 0.97):
                    continue
                sig = _evaluate_market(
                    market_id=snap.market_id,
                    question=snap.question,
                    category=snap.market_type,
                    market_prob=snap.yes_price,
                    volume_usd=snap.volume_usd,
                    days_to_expiry=snap.days_to_expiry,
                    spread=snap.spread,
                    platform="kalshi",
                )
                if sig:
                    sig["broker"] = "kalshi"
                    signals.append(sig)
        except Exception as e:
            logger.error(f"[lane3] Kalshi scan error: {e}")

    # 4. Sort by edge, take top signals
    signals.sort(key=lambda s: abs(s["edge"]), reverse=True)

    if signals:
        logger.info(f"[lane3] {len(signals)} markets with edge > {PM_MIN_EDGE_PCT:.1%}")
        for sig in signals[:3]:  # only top 3 to avoid overtrading
            try:
                broker = _get_polymarket_broker() if sig["broker"] == "polymarket" else _get_kalshi_broker()
                _place_order(sig, broker)
            except Exception as e:
                logger.error(f"[lane3] Order placement error: {e}")
    else:
        logger.info("[lane3] No markets with sufficient edge this cycle.")

    elapsed = time.time() - start
    log_event('INFO', 'lane3', f"Scan complete: {len(signals)} signals in {elapsed:.1f}s")
    logger.info(f"[lane3] === Scan DONE ({elapsed:.1f}s) ===")
