"""
execution/coinbase_broker.py

Coinbase Advanced Trade order execution.
Uses the official coinbase-advanced-py library.

Preferences:
  - Limit orders (maker fee = 0.4% vs taker 0.6%)
  - Always checks position before entering (no double-entry)
  - Cancels all orders for symbol before placing new ones
  - Logs every trade to SQLite + CSV
  - Sends Telegram alerts on fills
"""
import uuid
import time
from typing import Optional
from datetime import datetime
import pytz

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    COINBASE_API_KEY, COINBASE_API_SECRET,
    PAPER_TRADING, MARKET_TIMEZONE,
    COINBASE_TAKER_FEE_PCT, COINBASE_MAKER_FEE_PCT,
    CRYPTO_POSITION_SIZE_USD
)
from logging_db.trade_logger import log_trade, log_event
from alerts.telegram_alert import alert_trade_opened, alert_trade_closed

try:
    from coinbase.rest import RESTClient
    COINBASE_AVAILABLE = True
except ImportError:
    COINBASE_AVAILABLE = False
    RESTClient = None
    print("[coinbase_broker] coinbase-advanced-py not installed. Run: pip install coinbase-advanced-py")


class CoinbaseBroker:
    """
    Coinbase Advanced Trade execution layer.
    Tracks open positions in-memory (Coinbase has no server-side stops).
    """

    def __init__(self):
        self._client: Optional['RESTClient'] = None
        self._connected = False
        self._open_positions: dict = {}  # product_id -> {'qty', 'entry', 'stop', 'target'}

    def connect(self) -> bool:
        if not COINBASE_AVAILABLE:
            print("[CoinbaseBroker] coinbase-advanced-py not installed")
            return False
        if not COINBASE_API_KEY or not COINBASE_API_SECRET:
            print("[CoinbaseBroker] API credentials not configured in .env")
            return False
        try:
            self._client = RESTClient(
                api_key=COINBASE_API_KEY,
                api_secret=COINBASE_API_SECRET
            )
            # Verify connection
            accounts = self._client.get_accounts()
            self._connected = True
            print("[CoinbaseBroker] Connected to Coinbase Advanced Trade ✅")
            log_event('INFO', 'CoinbaseBroker', 'Connected to Coinbase')
            return True
        except Exception as e:
            print(f"[CoinbaseBroker] Connection failed: {e}")
            log_event('ERROR', 'CoinbaseBroker', f"Connection failed: {e}")
            return False

    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    # ─── Buy orders ───────────────────────────────────────────────────────────

    def buy_limit(
        self,
        product_id: str,
        size_usd: float,
        limit_price: float,
        strategy: str = 'crypto_macd',
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
    ) -> Optional[dict]:
        """
        Place a limit buy order.
        size_usd = dollar amount to buy (e.g. $50)
        Calculates base_size from price.
        """
        # Always prefer limit orders (lower fees = longer account lifespan)
        base_size = round(size_usd / limit_price, 8)
        if base_size <= 0:
            print(f"[CoinbaseBroker] Buy size too small for {product_id}: ${size_usd:.2f} @ ${limit_price}")
            return None

        if PAPER_TRADING or not self.is_connected():
            return self._paper_buy(product_id, base_size, limit_price,
                                   strategy, stop_loss, take_profit)

        try:
            # Cancel existing orders for this pair
            self._cancel_product_orders(product_id)

            # Check no existing position
            if product_id in self._open_positions:
                print(f"[CoinbaseBroker] Already holding {product_id} — no double-entry")
                return None

            order_id = str(uuid.uuid4())
            order = self._client.limit_order_gtc_buy(
                client_order_id=order_id,
                product_id=product_id,
                base_size=str(base_size),
                limit_price=str(round(limit_price, 2))
            )

            # Verify the order was accepted by the exchange before registering position.
            # Limit orders don't fill instantly — check status after brief wait.
            time.sleep(1.5)
            try:
                order_status = self._client.get_order(order_id=order_id)
                status = getattr(order_status, 'status', 'UNKNOWN')
                filled_size = float(getattr(order_status, 'filled_size', 0) or 0)
                if status in ('CANCELLED', 'FAILED', 'EXPIRED'):
                    print(f"[CoinbaseBroker] Order {order_id} {status} — not registering position")
                    log_event('WARNING', 'CoinbaseBroker',
                              f"Buy order {status} for {product_id}: {order_id}")
                    return None
                if filled_size > 0 and filled_size < base_size * 0.9:
                    print(f"[CoinbaseBroker] Partial fill {product_id}: {filled_size:.6f}/{base_size:.6f}")
                    log_event('WARNING', 'CoinbaseBroker',
                              f"Partial fill {product_id}: {filled_size:.6f} of {base_size:.6f}")
                    base_size = filled_size  # track only what actually filled
            except Exception as status_err:
                print(f"[CoinbaseBroker] Order status check failed (proceeding): {status_err}")

            fee = size_usd * COINBASE_MAKER_FEE_PCT
            print(f"[CoinbaseBroker] BUY LIMIT {base_size:.6f} {product_id} @ ${limit_price:,.2f} | fee≈${fee:.3f}")

            # Track position in memory
            self._open_positions[product_id] = {
                'qty': base_size,
                'entry': limit_price,
                'stop': stop_loss,
                'target': take_profit,
                'high_since_entry': limit_price,
                'order_id': order_id,
            }

            log_trade(
                strategy=strategy,
                broker='coinbase',
                symbol=product_id,
                action='BUY',
                order_type='LIMIT',
                qty=base_size,
                price=limit_price,
                fee_usd=fee,
                paper=False,
                order_id=order_id,
                notes=f"SL={stop_loss:.4f} TP={take_profit:.4f}"
            )

            alert_trade_opened(
                strategy=strategy,
                symbol=product_id,
                action='BUY',
                qty=base_size,
                price=limit_price,
                stop_loss=stop_loss,
                take_profit=take_profit
            )

            return order

        except Exception as e:
            print(f"[CoinbaseBroker] Buy failed {product_id}: {e}")
            log_event('ERROR', 'CoinbaseBroker', f"Buy failed {product_id}: {e}")
            return None

    def sell_limit(
        self,
        product_id: str,
        base_size: float,
        limit_price: float,
        strategy: str = 'crypto_macd',
        entry_price: float = 0.0,
        reason: str = 'Signal'
    ) -> Optional[dict]:
        """Close a long position with a limit sell."""
        pnl = (limit_price - entry_price) * base_size if entry_price > 0 else 0

        if PAPER_TRADING or not self.is_connected():
            return self._paper_sell(product_id, base_size, limit_price,
                                    strategy, entry_price, reason)

        try:
            self._cancel_product_orders(product_id)

            order_id = str(uuid.uuid4())
            order = self._client.limit_order_gtc_sell(
                client_order_id=order_id,
                product_id=product_id,
                base_size=str(round(base_size, 8)),
                limit_price=str(round(limit_price, 2))
            )

            fee = limit_price * base_size * COINBASE_MAKER_FEE_PCT
            print(f"[CoinbaseBroker] SELL LIMIT {base_size:.6f} {product_id} @ ${limit_price:,.2f} | P&L: ${pnl:+.2f}")

            # Remove from position tracking
            self._open_positions.pop(product_id, None)

            log_trade(
                strategy=strategy,
                broker='coinbase',
                symbol=product_id,
                action='SELL',
                order_type='LIMIT',
                qty=base_size,
                price=limit_price,
                fee_usd=fee,
                pnl_usd=pnl,
                paper=False,
                order_id=order_id,
                notes=f"reason={reason}"
            )

            alert_trade_closed(
                strategy=strategy,
                symbol=product_id,
                action='SELL',
                qty=base_size,
                entry_price=entry_price,
                exit_price=limit_price,
                pnl_usd=pnl,
                reason=reason
            )

            return order

        except Exception as e:
            print(f"[CoinbaseBroker] Sell failed {product_id}: {e}")
            log_event('ERROR', 'CoinbaseBroker', f"Sell failed {product_id}: {e}")
            return None

    def sell_market(
        self,
        product_id: str,
        base_size: float,
        strategy: str,
        entry_price: float = 0.0,
        reason: str = 'Stop loss'
    ) -> Optional[dict]:
        """Market sell for urgent exit (stop loss). Uses taker fee."""
        pnl = 0.0
        if entry_price > 0:
            from data.coinbase_feed import get_current_price
            current = get_current_price(product_id) or entry_price
            pnl = (current - entry_price) * base_size

        if PAPER_TRADING or not self.is_connected():
            from data.coinbase_feed import get_current_price
            price = get_current_price(product_id) or entry_price
            return self._paper_sell_market(product_id, base_size, price or entry_price,
                                           strategy, entry_price, reason)

        try:
            self._cancel_product_orders(product_id)

            order_id = str(uuid.uuid4())
            order = self._client.market_order_sell(
                client_order_id=order_id,
                product_id=product_id,
                base_size=str(round(base_size, 8))
            )

            fee = (base_size * (entry_price or 1)) * COINBASE_TAKER_FEE_PCT
            self._open_positions.pop(product_id, None)
            log_trade(strategy=strategy, broker='coinbase', symbol=product_id,
                      action='SELL', order_type='MARKET', qty=base_size,
                      price=entry_price, fee_usd=fee, pnl_usd=pnl, paper=False,
                      order_id=order_id, notes=f"reason={reason}")
            print(f"[CoinbaseBroker] SELL MARKET (emergency) {base_size:.6f} {product_id} | {reason}")
            log_event('WARNING', 'CoinbaseBroker', f"Market sell {product_id}: {reason}")

            return order

        except Exception as e:
            print(f"[CoinbaseBroker] Market sell failed {product_id}: {e}")
            return None

    # ─── Position management ──────────────────────────────────────────────────

    def get_position(self, product_id: str) -> Optional[dict]:
        return self._open_positions.get(product_id)

    def get_all_positions(self) -> dict:
        return dict(self._open_positions)

    def update_position_high(self, product_id: str, current_price: float) -> None:
        if product_id in self._open_positions:
            self._open_positions[product_id]['high_since_entry'] = max(
                self._open_positions[product_id].get('high_since_entry', current_price),
                current_price
            )

    def get_usdc_balance(self) -> float:
        if not self.is_connected():
            return 0.0
        try:
            from data.coinbase_feed import get_account_balance
            return get_account_balance('USDC')
        except Exception:
            return 0.0

    # ─── Order cancellation ───────────────────────────────────────────────────

    def _cancel_product_orders(self, product_id: str) -> None:
        if not self.is_connected():
            return
        try:
            orders = self._client.list_orders(
                product_id=product_id,
                order_status='OPEN'
            )
            order_ids = []
            for order in getattr(orders, 'orders', []):
                order_ids.append(order.order_id)
            if order_ids:
                self._client.cancel_orders(order_ids=order_ids)
                print(f"[CoinbaseBroker] Cancelled {len(order_ids)} open orders for {product_id}")
        except Exception as e:
            print(f"[CoinbaseBroker] Cancel orders error: {e}")

    # ─── Paper trading ────────────────────────────────────────────────────────

    def _paper_buy(self, product_id, base_size, price, strategy, stop, target) -> dict:
        fee = price * base_size * COINBASE_MAKER_FEE_PCT

        self._open_positions[product_id] = {
            'qty': base_size,
            'entry': price,
            'stop': stop,
            'target': target,
            'high_since_entry': price,
            'order_id': f'PAPER_{uuid.uuid4().hex[:8]}',
        }

        log_trade(
            strategy=strategy, broker='coinbase_paper',
            symbol=product_id, action='BUY', order_type='LIMIT',
            qty=base_size, price=price, fee_usd=fee, paper=True,
            order_id=self._open_positions[product_id]['order_id'],
            notes=f"SL={stop:.4f} TP={target:.4f}"
        )

        print(f"[PAPER] 🟢 BUY {base_size:.6f} {product_id} @ ${price:,.2f} | fee=${fee:.3f}")
        alert_trade_opened(strategy, product_id, 'BUY', base_size, price, stop, target)
        return {'paper_fill': True, 'qty': base_size, 'price': price}

    def _paper_sell_market(self, product_id, base_size, price, strategy,
                           entry_price, reason) -> dict:
        """Paper market sell — uses taker fee (emergency stop path)."""
        fee = price * base_size * COINBASE_TAKER_FEE_PCT
        pnl = (price - entry_price) * base_size if entry_price > 0 else 0
        self._open_positions.pop(product_id, None)
        log_trade(strategy=strategy, broker='coinbase_paper',
                  symbol=product_id, action='SELL', order_type='MARKET',
                  qty=base_size, price=price, fee_usd=fee, pnl_usd=pnl, paper=True,
                  order_id=f'PAPER_{uuid.uuid4().hex[:8]}', notes=f"reason={reason}")
        print(f"[PAPER] 🔴 SELL MARKET {base_size:.6f} {product_id} @ ${price:,.2f} | P&L: ${pnl:+.2f}")
        alert_trade_closed(strategy, product_id, 'SELL', base_size, entry_price, price, pnl, reason)
        return {'paper_fill': True, 'qty': base_size, 'price': price}

    def _paper_sell(self, product_id, base_size, price, strategy,
                    entry_price, reason) -> dict:
        fee = price * base_size * COINBASE_MAKER_FEE_PCT
        pnl = (price - entry_price) * base_size if entry_price > 0 else 0

        self._open_positions.pop(product_id, None)

        log_trade(
            strategy=strategy, broker='coinbase_paper',
            symbol=product_id, action='SELL', order_type='LIMIT',
            qty=base_size, price=price, fee_usd=fee, pnl_usd=pnl, paper=True,
            order_id=f'PAPER_{uuid.uuid4().hex[:8]}', notes=f"reason={reason}"
        )

        print(f"[PAPER] 🔴 SELL {base_size:.6f} {product_id} @ ${price:,.2f} | P&L: ${pnl:+.2f}")
        alert_trade_closed(strategy, product_id, 'SELL', base_size, entry_price, price, pnl, reason)
        return {'paper_fill': True, 'qty': base_size, 'price': price}


# ─── Module-level singleton ───────────────────────────────────────────────────
_coinbase_broker: Optional[CoinbaseBroker] = None


def get_coinbase_broker() -> CoinbaseBroker:
    global _coinbase_broker
    if _coinbase_broker is None:
        _coinbase_broker = CoinbaseBroker()
    return _coinbase_broker
