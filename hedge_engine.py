"""
hedge_engine.py — Delta-neutral hedging with BTC as hedge instrument.

Triggers when net delta (sum of all long/short notional) > 40% of account.
Opens BTC hedge at 1x leverage in opposite direction.
Rebalances every 5 minutes.
"""

import logging
import time
import threading
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_HEDGE_SYMBOL = 'BTCUSDT'
_HEDGE_LEVERAGE = 1
_DELTA_TRIGGER_PCT = 0.40   # net delta > 40% of account → hedge
_REBALANCE_INTERVAL = 300   # 5 minutes
_lock = threading.RLock()
_hedge_position: Optional[Dict] = None
_last_rebalance_ts: float = 0.0


def _compute_net_delta(open_positions: Dict, btc_price: float) -> float:
    """
    Compute net delta as signed USD notional.
    Long positions: positive. Short positions: negative.
    """
    net = 0.0
    for sym, pos in open_positions.items():
        notional = pos.get('position_usd', 0)
        direction = pos.get('direction', 'LONG')
        if direction == 'LONG':
            net += notional
        else:
            net -= notional
    return net


def should_hedge(open_positions: Dict, account_balance: float,
                  btc_price: float) -> Optional[Dict]:
    """
    Determine if a hedge is needed.

    Returns:
        None if no hedge needed.
        Dict with {direction, size_usd, reason} if hedge needed.
    """
    net_delta = _compute_net_delta(open_positions, btc_price)
    threshold = account_balance * _DELTA_TRIGGER_PCT

    if abs(net_delta) < threshold:
        return None

    # Hedge in opposite direction to net delta
    if net_delta > 0:
        hedge_direction = 'SHORT'
        hedge_size = abs(net_delta) * 0.5   # hedge 50% of net delta
    else:
        hedge_direction = 'LONG'
        hedge_size = abs(net_delta) * 0.5

    return {
        'direction': hedge_direction,
        'size_usd': round(hedge_size, 2),
        'net_delta': round(net_delta, 2),
        'reason': f'Net delta ${net_delta:.0f} > threshold ${threshold:.0f}',
    }


def rebalance(open_positions: Dict, account_balance: float,
               btc_price: float, paper: bool = True) -> Optional[str]:
    """
    Check delta and apply hedge if needed. Called every 5 minutes.

    Returns status string for logging.
    """
    global _hedge_position, _last_rebalance_ts

    with _lock:
        if time.time() - _last_rebalance_ts < _REBALANCE_INTERVAL:
            return None
        _last_rebalance_ts = time.time()

    hedge_needed = should_hedge(open_positions, account_balance, btc_price)

    if not hedge_needed:
        # If we have a hedge but no longer need it, close it
        with _lock:
            if _hedge_position:
                logger.info('[hedge] Delta normalized — closing BTC hedge')
                _hedge_position = None
                return 'hedge_closed'
        return 'no_hedge_needed'

    direction = hedge_needed['direction']
    size_usd  = hedge_needed['size_usd']

    with _lock:
        current_hedge = _hedge_position

    # Check if existing hedge is in right direction and close enough in size
    if current_hedge:
        existing_dir  = current_hedge.get('direction')
        existing_size = current_hedge.get('size_usd', 0)
        size_ratio = size_usd / (existing_size + 1e-9)

        if existing_dir == direction and 0.7 <= size_ratio <= 1.3:
            return 'hedge_unchanged'

        # Need to rebalance — close existing and reopen
        logger.info(f'[hedge] Rebalancing: existing={existing_dir} ${existing_size:.0f} '
                   f'→ new={direction} ${size_usd:.0f}')

    # Open new hedge
    if paper:
        hedge = {
            'symbol': _HEDGE_SYMBOL,
            'direction': direction,
            'size_usd': size_usd,
            'entry_price': btc_price,
            'entry_ts': time.time(),
            'leverage': _HEDGE_LEVERAGE,
            'paper': True,
            'reason': hedge_needed['reason'],
        }
        with _lock:
            _hedge_position = hedge
        logger.info(f'[hedge] PAPER {direction} BTC hedge: ${size_usd:.0f} @ {btc_price:.0f}')
        return f'hedge_opened_{direction.lower()}'
    else:
        try:
            import perps_engine
            if direction == 'LONG':
                pos = perps_engine.open_long(
                    _HEDGE_SYMBOL, size_usd, btc_price,
                    stop_price=btc_price * 0.90,
                    take_profit_price=btc_price * 1.10,
                    leverage=_HEDGE_LEVERAGE,
                    composite_score=50.0,
                    regime='HEDGE',
                    testnet=False,
                )
            else:
                pos = perps_engine.open_short(
                    _HEDGE_SYMBOL, size_usd, btc_price,
                    stop_price=btc_price * 1.10,
                    take_profit_price=btc_price * 0.90,
                    leverage=_HEDGE_LEVERAGE,
                    composite_score=50.0,
                    regime='HEDGE',
                    testnet=False,
                )

            with _lock:
                _hedge_position = pos
            return f'hedge_opened_{direction.lower()}'
        except Exception as e:
            logger.error(f'[hedge] Failed to open hedge: {e}')
            return 'hedge_failed'


def get_hedge_status() -> Optional[Dict]:
    """Return current hedge position, or None."""
    with _lock:
        return dict(_hedge_position) if _hedge_position else None
