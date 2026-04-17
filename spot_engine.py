"""
spot_engine.py — Coinbase spot execution engine (BTC/ETH only, starter lane).

Manages one spot position at a time (BTC or ETH).  No leverage, no shorting.
Wraps execution/coinbase_spot_broker.py with v10-style persistence.

Blocked reasons (returned as strings in result dicts):
  spot_position_already_open
  spot_deployment_cap_exceeded
  spot_size_below_minimum
  spot_symbol_not_allowed
  spot_lane_disabled
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from execution.coinbase_spot_broker import (
        CoinbaseSpotBroker,
        get_spot_broker,
        SPOT_SUPPORTED_SYMBOLS,
    )

    _BROKER_OK = True
except ImportError:
    _BROKER_OK = False
    logger.warning("[spot_engine] coinbase_spot_broker not available")

# ── Config defaults (overridden by config.py when imported) ───────────────────
_SPOT_MAX_DEPLOYED_PCT: float = 0.40  # 40% of spot USD balance
_SPOT_MIN_ORDER_USD: float = 10.0
_SPOT_SYMBOLS: list = ["BTC", "ETH"]
_SPOT_LANE_ACTIVE: bool = False


def _load_config() -> None:
    global _SPOT_MAX_DEPLOYED_PCT, _SPOT_MIN_ORDER_USD, _SPOT_SYMBOLS, _SPOT_LANE_ACTIVE
    try:
        from config import (
            SPOT_MAX_DEPLOYED_PCT,
            SPOT_MIN_ORDER_USD,
            SPOT_SYMBOLS,
            SPOT_LANE_ACTIVE,
        )

        _SPOT_MAX_DEPLOYED_PCT = float(SPOT_MAX_DEPLOYED_PCT)
        _SPOT_MIN_ORDER_USD = float(SPOT_MIN_ORDER_USD)
        _SPOT_SYMBOLS = list(SPOT_SYMBOLS)
        _SPOT_LANE_ACTIVE = bool(SPOT_LANE_ACTIVE)
    except Exception:
        pass  # use defaults above


_load_config()


def _get_broker(paper: bool) -> Optional["CoinbaseSpotBroker"]:
    if not _BROKER_OK:
        return None
    try:
        broker = get_spot_broker()
        # Override paper flag if broker was constructed with different mode
        broker._paper = paper
        return broker
    except Exception as e:
        logger.error(f"[spot_engine] broker init error: {e}")
        return None


# ── open_spot ─────────────────────────────────────────────────────────────────


def open_spot(symbol: str, size_usd: float, paper: bool = True) -> Optional[Dict]:
    """
    Open a spot position for symbol (BTC or ETH).
    Returns position dict on success, None with logged reason on failure.

    Blocks:
      - SPOT_LANE_ACTIVE=False  → spot_lane_disabled
      - symbol not in SPOT_SYMBOLS → spot_symbol_not_allowed
      - already holding that symbol (DB) → spot_position_already_open
      - size_usd < SPOT_MIN_ORDER_USD → spot_size_below_minimum
      - size_usd > SPOT_MAX_DEPLOYED_PCT * usd_balance → spot_deployment_cap_exceeded
    """
    _load_config()

    clean = symbol.upper().replace("USDT", "").replace("USD", "").replace("-USD", "")

    # Lane gate
    if not _SPOT_LANE_ACTIVE:
        logger.info(f"[spot_engine] {clean} blocked — spot_lane_disabled")
        return None

    # Symbol gate
    if clean not in _SPOT_SYMBOLS:
        logger.warning(f"[spot_engine] {clean} blocked — spot_symbol_not_allowed")
        return None

    # Duplicate position gate (DB)
    existing = _load_spot_positions_from_db(paper=paper)
    for pos in existing:
        if pos.get("symbol", "").upper() == clean:
            logger.warning(
                f"[spot_engine] {clean} blocked — spot_position_already_open"
            )
            return None

    # Size minimum
    if size_usd < _SPOT_MIN_ORDER_USD:
        logger.warning(
            f"[spot_engine] {clean} blocked — spot_size_below_minimum "
            f"(${size_usd:.2f} < ${_SPOT_MIN_ORDER_USD})"
        )
        return None

    # Deployment cap
    broker = _get_broker(paper)
    if broker is not None and not paper:
        bal = broker.get_spot_balance()
        usd_avail = float(bal.get("usd_available", 0))
        if usd_avail > 0:
            cap = usd_avail * _SPOT_MAX_DEPLOYED_PCT
            if size_usd > cap:
                logger.warning(
                    f"[spot_engine] {clean} blocked — spot_deployment_cap_exceeded "
                    f"(${size_usd:.2f} > {_SPOT_MAX_DEPLOYED_PCT:.0%} of ${usd_avail:.2f})"
                )
                return None

    # Execute
    if broker is None:
        logger.error(f"[spot_engine] {clean} — broker unavailable")
        return None

    order = broker.buy_spot(clean, size_usd)
    if not order:
        logger.error(f"[spot_engine] {clean} buy_spot returned None")
        return None

    price = broker.get_mark_price(clean)
    qty = size_usd / price if price > 0 else float(order.get("filled_size", 0))

    pos = {
        "symbol": clean,
        "strategy": f"spot_{clean.lower()}",
        "broker": "coinbase_spot",
        "qty": qty,
        "entry": price,
        "entry_ts": time.time(),
        "size_usd": size_usd,
        "order_id": order.get("order_id", ""),
        "paper": paper,
    }

    # Persist to trades table
    try:
        from logging_db.trade_logger import log_trade

        log_trade(
            strategy=f"spot_{clean.lower()}",
            broker="coinbase_spot",
            symbol=clean,
            action="BUY",
            order_type="MARKET",
            qty=qty,
            price=price,
            fee_usd=0.0,  # Coinbase spot fees vary; 0 for now
            pnl_usd=0.0,
            paper=paper,
            notes=f"spot_buy size_usd={size_usd:.2f}",
        )
    except Exception as e:
        logger.debug(f"[spot_engine] log_trade error: {e}")

    # Persist to open_positions
    try:
        from logging_db.trade_logger import persist_position
        import datetime

        persist_position(
            symbol=clean,
            strategy=f"spot_{clean.lower()}",
            qty=qty,
            entry=price,
            stop=0.0,
            target=0.0,
            high_since_entry=price,
            ts_entry=datetime.datetime.now().isoformat(),
            paper=paper,
            direction="LONG",
            entry_reason="spot_buy",
            atr_at_entry=0.0,
            composite_score=0.0,
            leverage=1,
        )
    except Exception as e:
        logger.debug(f"[spot_engine] persist_position error: {e}")

    logger.info(
        f"[spot_engine] {'PAPER' if paper else 'LIVE'} BUY {clean}: "
        f"${size_usd:.2f} = {qty:.6f} @ {price:.4f}"
    )
    return pos


# ── close_spot ────────────────────────────────────────────────────────────────


def close_spot(symbol: str, paper: bool = True) -> Optional[Dict]:
    """
    Close the spot position for symbol.
    Returns close result dict or None.
    """
    _load_config()

    clean = symbol.upper().replace("USDT", "").replace("USD", "").replace("-USD", "")

    # Find position in DB
    existing = _load_spot_positions_from_db(paper=paper)
    pos = next((p for p in existing if p.get("symbol", "").upper() == clean), None)
    if not pos:
        logger.warning(f"[spot_engine] close_spot {clean}: no open position found")
        return None

    qty = float(pos.get("qty", 0))
    entry_price = float(pos.get("entry", 0))
    if qty <= 0:
        logger.warning(f"[spot_engine] close_spot {clean}: qty=0")
        return None

    broker = _get_broker(paper)
    if broker is None:
        logger.error(f"[spot_engine] close_spot {clean}: broker unavailable")
        return None

    order = broker.sell_spot(clean, qty)
    if not order:
        logger.error(f"[spot_engine] close_spot {clean}: sell_spot returned None")
        return None

    exit_price = broker.get_mark_price(clean)
    if exit_price <= 0:
        exit_price = entry_price
    pnl_usd = (exit_price - entry_price) * qty

    # Persist close to trades table
    try:
        from logging_db.trade_logger import log_trade

        log_trade(
            strategy=f"spot_{clean.lower()}",
            broker="coinbase_spot",
            symbol=clean,
            action="SELL",
            order_type="MARKET",
            qty=qty,
            price=exit_price,
            fee_usd=0.0,
            pnl_usd=pnl_usd,
            paper=paper,
            won=1 if pnl_usd > 0 else 0,
            notes=f"spot_sell exit={exit_price:.4f} pnl={pnl_usd:.2f}",
        )
    except Exception as e:
        logger.debug(f"[spot_engine] close log_trade error: {e}")

    # Remove from open_positions
    try:
        from logging_db.trade_logger import delete_position

        delete_position(clean, strategy=f"spot_{clean.lower()}", paper=paper)
    except Exception as e:
        logger.debug(f"[spot_engine] delete_position error: {e}")

    result = {
        "symbol": clean,
        "exit_price": exit_price,
        "entry_price": entry_price,
        "qty": qty,
        "pnl_usd": round(pnl_usd, 4),
        "order_id": order.get("order_id", ""),
        "paper": paper,
    }
    logger.info(
        f"[spot_engine] {'PAPER' if paper else 'LIVE'} SELL {clean}: "
        f"{qty:.6f} @ {exit_price:.4f} pnl=${pnl_usd:.2f}"
    )
    return result


# ── get_spot_positions ────────────────────────────────────────────────────────


def get_spot_positions(paper: bool = True) -> List[Dict]:
    """
    Return open spot positions (strategy='spot_*' — no perp contamination).
    """
    return _load_spot_positions_from_db(paper=paper)


def _load_spot_positions_from_db(paper: bool = True) -> List[Dict]:
    """
    Load spot open positions from open_positions table.
    Filtered by strategy starting with 'spot_' — no contamination with perp positions
    (perp strategy = 'v10_perp', spot strategies = 'spot_btc' / 'spot_eth').
    """
    try:
        from logging_db.trade_logger import load_open_positions

        rows = load_open_positions(paper=paper)
        return [r for r in rows if str(r.get("strategy", "")).startswith("spot_")]
    except Exception as e:
        logger.debug(f"[spot_engine] load_spot_positions error: {e}")
        return []
