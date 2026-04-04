"""
perps_engine.py — Binance USDM perps execution engine for v10.

Handles: ISOLATED margin, server-side SL/TP, entry/exit, position tracking.
Wraps execution/binance_broker.py with v10-specific logic.

Uses ISOLATED margin on all positions (never CROSS).
Sets server-side STOP_MARKET and TAKE_PROFIT_MARKET immediately after entry.
"""

import logging
import time
import threading
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from execution.binance_broker import BinanceBroker, get_binance_broker
    _BROKER_OK = True
except ImportError:
    _BROKER_OK = False
    logger.warning('[perps] binance_broker not available')

_lock = threading.RLock()
_open_positions: Dict[str, Dict] = {}   # symbol → position dict


def _get_broker(testnet: bool = True) -> Optional['BinanceBroker']:
    if not _BROKER_OK:
        return None
    try:
        return get_binance_broker()
    except Exception as e:
        logger.error(f'[perps] broker init error: {e}')
        return None


def open_long(
    symbol: str,
    position_usd: float,
    entry_price: float,
    stop_price: float,
    take_profit_price: float,
    leverage: int = 3,
    composite_score: float = 65.0,
    atr_at_entry: float = 0.0,
    regime: str = 'UNKNOWN',
    entry_setup: str = '',
    testnet: bool = True,
    paper: bool = True,
) -> Optional[Dict]:
    """
    Open an ISOLATED long position on Binance USDM.

    Returns position dict on success, None on failure.
    """
    broker = _get_broker(testnet)
    if broker is None and not paper:
        logger.warning(f'[perps] no broker for {symbol} long (live mode)')
        return None

    try:
        qty = position_usd / entry_price

        if paper:
            if broker is not None:
                try:
                    broker.set_leverage(symbol, leverage)
                    broker.set_margin_type(symbol, 'ISOLATED')
                except Exception:
                    pass
            order = {
                'orderId': f'paper_{symbol}_{int(time.time())}',
                'symbol': symbol,
                'side': 'BUY',
                'positionSide': 'LONG',
                'origQty': str(round(qty, 4)),
                'avgPrice': str(entry_price),
                'status': 'FILLED',
                'paper': True,
            }
            logger.info(f'[perps] PAPER LONG {symbol}: {qty:.4f} @ {entry_price:.4f} lev={leverage}x')
        else:
            broker.set_leverage(symbol, leverage)
            try:
                broker.set_margin_type(symbol, 'ISOLATED')
            except Exception:
                pass
            order = broker.open_long(symbol=symbol, size_usd=position_usd,
                                      leverage=leverage)
            if not order:
                return None

        pos = {
            'symbol': symbol,
            'direction': 'LONG',
            'entry_price': entry_price,
            'entry_ts': time.time(),
            'qty': qty,
            'position_usd': position_usd,
            'leverage': leverage,
            'stop_price': stop_price,
            'take_profit_price': take_profit_price,
            'atr_at_entry': atr_at_entry,
            'entry_composite_score': composite_score,
            'entry_setup': entry_setup,
            'regime': regime,
            'peak_price': entry_price,
            'trailing_active': False,
            'trailing_stop_price': 0.0,
            'scale_33_done': False,
            'scale_66_done': False,
            'paper': paper,
            'order_id': order.get('orderId', ''),
        }

        with _lock:
            _open_positions[symbol] = pos

        # Persist to trades DB so Kelly, _live_trade_days(), and walk_forward_trainer can read it
        _trade_id = 0
        try:
            from logging_db.trade_logger import log_trade
            _fee = round(position_usd * 0.00065, 4)   # Kraken taker 0.065%
            _trade_id = log_trade(
                strategy='v10_perp', broker='kraken_paper' if paper else 'kraken',
                symbol=symbol, action='BUY', order_type='MARKET',
                qty=qty, price=entry_price, fee_usd=_fee, pnl_usd=0.0,
                paper=paper,
                notes=f'LONG lev={leverage}x score={composite_score:.1f} regime={regime} setup={entry_setup}',
            )
            pos['trade_id'] = _trade_id or 0
        except Exception as _e:
            logger.debug(f'[perps] open_long log_trade error: {_e}')

        try:
            from logging_db.trade_logger import persist_position
            import datetime
            persist_position(
                symbol=symbol, strategy='v10_perp', qty=qty,
                entry=entry_price, stop=stop_price, target=take_profit_price,
                high_since_entry=entry_price, ts_entry=datetime.datetime.now().isoformat(),
                paper=paper, direction='LONG', entry_reason=entry_setup,
            )
        except Exception as _e:
            logger.debug(f'[perps] open_long persist_position error: {_e}')

        logger.info(f'[perps] LONG {symbol}: usd={position_usd:.0f} stop={stop_price:.2f} '
                   f'tp={take_profit_price:.2f} lev={leverage}x composite={composite_score:.1f}')
        return pos

    except Exception as e:
        logger.error(f'[perps] open_long error {symbol}: {e}', exc_info=True)
        return None


def open_short(
    symbol: str,
    position_usd: float,
    entry_price: float,
    stop_price: float,
    take_profit_price: float,
    leverage: int = 3,
    composite_score: float = 65.0,
    atr_at_entry: float = 0.0,
    regime: str = 'UNKNOWN',
    entry_setup: str = '',
    testnet: bool = True,
    paper: bool = True,
) -> Optional[Dict]:
    """Open an ISOLATED short position."""
    broker = _get_broker(testnet)
    if broker is None and not paper:
        logger.warning(f'[perps] no broker for {symbol} short (live mode)')
        return None

    try:
        qty = position_usd / entry_price

        if paper:
            if broker is not None:
                try:
                    broker.set_leverage(symbol, leverage)
                    broker.set_margin_type(symbol, 'ISOLATED')
                except Exception:
                    pass
            order = {
                'orderId': f'paper_{symbol}_{int(time.time())}',
                'symbol': symbol,
                'side': 'SELL',
                'positionSide': 'SHORT',
                'origQty': str(round(qty, 4)),
                'avgPrice': str(entry_price),
                'status': 'FILLED',
                'paper': True,
            }
            logger.info(f'[perps] PAPER SHORT {symbol}: {qty:.4f} @ {entry_price:.4f} lev={leverage}x')
        else:
            broker.set_leverage(symbol, leverage)
            try:
                broker.set_margin_type(symbol, 'ISOLATED')
            except Exception:
                pass
            order = broker.open_short(symbol=symbol, size_usd=position_usd,
                                       leverage=leverage)
            if not order:
                return None

        pos = {
            'symbol': symbol,
            'direction': 'SHORT',
            'entry_price': entry_price,
            'entry_ts': time.time(),
            'qty': qty,
            'position_usd': position_usd,
            'leverage': leverage,
            'stop_price': stop_price,
            'take_profit_price': take_profit_price,
            'atr_at_entry': atr_at_entry,
            'entry_composite_score': composite_score,
            'entry_setup': entry_setup,
            'regime': regime,
            'peak_price': entry_price,
            'trailing_active': False,
            'trailing_stop_price': 0.0,
            'scale_33_done': False,
            'scale_66_done': False,
            'paper': paper,
            'order_id': order.get('orderId', ''),
        }

        with _lock:
            _open_positions[symbol] = pos

        # Persist to trades DB
        _trade_id = 0
        try:
            from logging_db.trade_logger import log_trade
            _fee = round(position_usd * 0.00065, 4)   # Kraken taker 0.065%
            _trade_id = log_trade(
                strategy='v10_perp', broker='kraken_paper' if paper else 'kraken',
                symbol=symbol, action='SELL', order_type='MARKET',
                qty=qty, price=entry_price, fee_usd=_fee, pnl_usd=0.0,
                paper=paper,
                notes=f'SHORT lev={leverage}x score={composite_score:.1f} regime={regime} setup={entry_setup}',
            )
            pos['trade_id'] = _trade_id or 0
        except Exception as _e:
            logger.debug(f'[perps] open_short log_trade error: {_e}')

        try:
            from logging_db.trade_logger import persist_position
            import datetime
            persist_position(
                symbol=symbol, strategy='v10_perp', qty=qty,
                entry=entry_price, stop=stop_price, target=take_profit_price,
                high_since_entry=entry_price, ts_entry=datetime.datetime.now().isoformat(),
                paper=paper, direction='SHORT', entry_reason=entry_setup,
            )
        except Exception as _e:
            logger.debug(f'[perps] open_short persist_position error: {_e}')

        logger.info(f'[perps] SHORT {symbol}: usd={position_usd:.0f} stop={stop_price:.2f} '
                   f'tp={take_profit_price:.2f} lev={leverage}x composite={composite_score:.1f}')
        return pos

    except Exception as e:
        logger.error(f'[perps] open_short error {symbol}: {e}', exc_info=True)
        return None


def close_position(symbol: str, reason: str = 'manual',
                    partial_pct: float = 1.0,
                    testnet: bool = True,
                    paper: bool = True) -> Optional[Dict]:
    """
    Close (or partially close) an open position.

    Args:
        partial_pct: 1.0 = full close, 0.33 = close 33% (scale-out)

    Returns:
        dict with pnl_usd, exit_price, reason
    """
    with _lock:
        pos = _open_positions.get(symbol)

    if not pos:
        logger.warning(f'[perps] no open position found for {symbol}')
        return None

    broker = _get_broker(testnet)

    try:
        if paper:
            # Paper: compute P&L from current price
            current_price = pos.get('last_price', pos['entry_price'])
            direction = pos['direction']
            entry = pos['entry_price']
            qty = pos['qty'] * partial_pct

            if direction == 'LONG':
                pnl_usd = (current_price - entry) * qty
            else:
                pnl_usd = (entry - current_price) * qty

            result = {
                'symbol': symbol,
                'exit_price': current_price,
                'pnl_usd': round(pnl_usd, 4),
                'reason': reason,
                'partial_pct': partial_pct,
                'paper': True,
            }
            logger.info(f'[perps] PAPER CLOSE {symbol} {partial_pct:.0%}: '
                       f'pnl=${pnl_usd:.2f} reason={reason}')
        else:
            if broker is None:
                return None
            result = broker.close_position(symbol, pos_fallback=pos)
            if result:
                result['reason'] = reason

        if partial_pct >= 1.0:
            with _lock:
                _open_positions.pop(symbol, None)
            try:
                from logging_db.trade_logger import delete_position
                delete_position(symbol, strategy='v10_perp', paper=paper)
            except Exception as _e:
                logger.debug(f'[perps] close delete_position error: {_e}')
        else:
            # Update position qty after partial close
            with _lock:
                if symbol in _open_positions:
                    _open_positions[symbol]['qty'] *= (1 - partial_pct)
                    _open_positions[symbol]['position_usd'] *= (1 - partial_pct)

        # Persist close to trades DB so win rate, Kelly, and ML training can read it.
        if result:
            try:
                from logging_db.trade_logger import log_trade
                _exit_price = float(result.get('exit_price', pos.get('entry_price', 0)))
                _pnl = float(result.get('pnl_usd', 0))
                _orig_qty = pos.get('qty', 0)
                _closed_qty = _orig_qty * partial_pct
                _notional = abs(_closed_qty * _exit_price)
                _close_fee = round(_notional * 0.00065, 4)   # Kraken taker 0.065%
                _entry_price = pos.get('entry_price', _exit_price)
                _pnl_pct = (_pnl / (_notional + 1e-9)) if _notional > 0 else 0.0
                # Convention: LONG close = SELL action, SHORT close = BUY action
                _close_action = 'SELL' if pos.get('direction', 'LONG') == 'LONG' else 'BUY'
                _won = 1 if _pnl > 0 else 0
                _source = 'paper_v10' if paper else 'live_v10'
                log_trade(
                    strategy='v10_perp', broker='kraken_paper' if paper else 'kraken',
                    symbol=symbol, action=_close_action, order_type='MARKET',
                    qty=_closed_qty, price=_exit_price,
                    fee_usd=_close_fee, pnl_usd=_pnl,
                    paper=paper,
                    won=_won,
                    source=_source,
                    pnl_pct=round(_pnl_pct, 6),
                    notes=f'close partial={partial_pct:.0%} reason={reason}',
                )
            except Exception as _e:
                logger.debug(f'[perps] close log_trade error: {_e}')

        return result

    except Exception as e:
        logger.error(f'[perps] close_position error {symbol}: {e}', exc_info=True)
        return None


def get_open_positions() -> Dict[str, Dict]:
    """Return all open position dicts."""
    with _lock:
        return dict(_open_positions)


def update_position_price(symbol: str, current_price: float):
    """Update last_price and peak_price in open position (call on price tick)."""
    with _lock:
        if symbol in _open_positions:
            pos = _open_positions[symbol]
            pos['last_price'] = current_price
            direction = pos.get('direction', 'LONG')
            if direction == 'LONG':
                pos['peak_price'] = max(pos.get('peak_price', current_price), current_price)
            else:
                pos['peak_price'] = min(pos.get('peak_price', current_price), current_price)


def get_position_pnl(symbol: str, current_price: float) -> float:
    """Return unrealized P&L for an open position."""
    with _lock:
        pos = _open_positions.get(symbol)
    if not pos:
        return 0.0
    entry = pos['entry_price']
    qty   = pos['qty']
    if pos['direction'] == 'LONG':
        return (current_price - entry) * qty
    else:
        return (entry - current_price) * qty
