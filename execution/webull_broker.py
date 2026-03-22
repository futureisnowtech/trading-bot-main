"""
execution/webull_broker.py

Webull order execution layer.
Uses the unofficial webull Python library (pip install webull).

Supports:
  - Paper trading mode (paper_webull) — default
  - Live trading mode (webull)
  - Limit orders (preferred — no market orders per strategy rules)
  - Position queries
  - Order cancellation

IMPORTANT: Webull requires initial login + trading PIN unlock.
The session persists via device credentials stored in ~/.webull/.
"""
import time
import uuid
from typing import Optional
from datetime import datetime
import pytz

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    WEBULL_USERNAME, WEBULL_PASSWORD, WEBULL_TRADE_PIN,
    WEBULL_MFA, WEBULL_DEVICE_ID, PAPER_TRADING, MARKET_TIMEZONE
)
from logging_db.trade_logger import log_trade, log_event
from alerts.telegram_alert import alert_trade_opened, alert_trade_closed

try:
    if PAPER_TRADING:
        from webull import paper_webull as WebullClass
    else:
        from webull import webull as WebullClass
    WEBULL_AVAILABLE = True
except ImportError:
    WEBULL_AVAILABLE = False
    WebullClass = None
    print("[webull_broker] webull not installed. Run: pip install webull")


class WebullBroker:
    """
    Webull broker interface.
    Handles login, order placement, position tracking.
    All orders are logged to SQLite automatically.
    """

    def __init__(self):
        self._wb = None
        self._authenticated = False
        self._positions: dict = {}

    # ─── Authentication ───────────────────────────────────────────────────────

    def connect(self) -> bool:
        """Login to Webull. Returns True if successful."""
        if not WEBULL_AVAILABLE:
            print("[WebullBroker] webull library not available")
            return False

        if not WEBULL_USERNAME or not WEBULL_PASSWORD:
            print("[WebullBroker] Credentials not set in .env — running in data-only mode")
            return False

        try:
            self._wb = WebullClass()

            # Attempt login
            login_result = self._wb.login(
                username=WEBULL_USERNAME,
                password=WEBULL_PASSWORD,
                device_name=WEBULL_DEVICE_ID,
                mfa=WEBULL_MFA if WEBULL_MFA else ''
            )

            if not login_result or 'accessToken' not in str(login_result):
                print(f"[WebullBroker] Login failed: {login_result}")
                return False

            # Unlock trading with PIN
            if WEBULL_TRADE_PIN and not PAPER_TRADING:
                trade_token = self._wb.get_trade_token(password=WEBULL_TRADE_PIN)
                if not trade_token:
                    print("[WebullBroker] Trade token unlock failed — check PIN")
                    return False

            self._authenticated = True
            mode = 'PAPER' if PAPER_TRADING else 'LIVE'
            print(f"[WebullBroker] Connected in {mode} mode ✅")
            log_event('INFO', 'WebullBroker',
                      f"Connected to Webull ({mode} mode)")
            return True

        except Exception as e:
            print(f"[WebullBroker] Connection error: {e}")
            log_event('ERROR', 'WebullBroker', f"Connection failed: {e}")
            return False

    def is_connected(self) -> bool:
        return self._authenticated and self._wb is not None

    # ─── Order placement ──────────────────────────────────────────────────────

    def buy_limit(
        self,
        symbol: str,
        qty: float,
        limit_price: float,
        strategy: str = 'equity_momentum',
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
    ) -> Optional[dict]:
        """
        Place a limit buy order.
        Per strategy rules: NEVER use market orders.
        Sit on the bid (or slightly above to ensure fill).
        """
        if not self.is_connected():
            self._log_paper_trade(symbol, 'BUY', 'LIMIT', qty, limit_price, strategy)
            return {'paper_fill': True, 'price': limit_price, 'qty': qty}

        try:
            # Cancel any existing orders for this symbol before entering
            self._cancel_symbol_orders(symbol)

            # Check no existing position
            existing = self.get_position(symbol)
            if existing and float(existing.get('position', 0)) != 0:
                print(f"[WebullBroker] Already holding {symbol} — no double-entry")
                return None

            order = self._wb.place_order(
                stock=symbol,
                action='BUY',
                orderType='LMT',
                price=round(limit_price, 2),
                quant=int(qty),
                enforce='DAY'
            )

            order_id = str(order.get('orderId', ''))
            print(f"[WebullBroker] BUY LIMIT placed: {qty} {symbol} @ ${limit_price:.2f} | ID: {order_id}")

            # Set stop loss immediately after order (critical rule: no exceptions)
            if stop_loss > 0 and not PAPER_TRADING:
                time.sleep(1)  # Brief wait for order to register
                self._place_stop_loss(symbol, qty, stop_loss)

            log_trade(
                strategy=strategy,
                broker='webull',
                symbol=symbol,
                action='BUY',
                order_type='LIMIT',
                qty=qty,
                price=limit_price,
                fee_usd=0.0,
                paper=PAPER_TRADING,
                order_id=order_id,
                notes=f"SL={stop_loss:.2f} TP={take_profit:.2f}"
            )

            alert_trade_opened(
                strategy=strategy,
                symbol=symbol,
                action='BUY',
                qty=qty,
                price=limit_price,
                stop_loss=stop_loss,
                take_profit=take_profit
            )

            return order

        except Exception as e:
            print(f"[WebullBroker] Buy order failed for {symbol}: {e}")
            log_event('ERROR', 'WebullBroker', f"Buy order failed {symbol}: {e}")
            return None

    def sell_limit(
        self,
        symbol: str,
        qty: float,
        limit_price: float,
        strategy: str = 'equity_momentum',
        entry_price: float = 0.0,
        reason: str = 'Signal'
    ) -> Optional[dict]:
        """Place a limit sell order to close a long position."""
        if not self.is_connected():
            pnl = (limit_price - entry_price) * qty if entry_price > 0 else 0
            self._log_paper_trade(symbol, 'SELL', 'LIMIT', qty, limit_price,
                                  strategy, pnl_usd=pnl)
            return {'paper_fill': True, 'price': limit_price, 'qty': qty}

        try:
            self._cancel_symbol_orders(symbol)

            order = self._wb.place_order(
                stock=symbol,
                action='SELL',
                orderType='LMT',
                price=round(limit_price, 2),
                quant=int(qty),
                enforce='DAY'
            )

            order_id = str(order.get('orderId', ''))
            pnl = (limit_price - entry_price) * qty if entry_price > 0 else 0

            print(f"[WebullBroker] SELL LIMIT placed: {qty} {symbol} @ ${limit_price:.2f} | P&L: ${pnl:+.2f}")

            log_trade(
                strategy=strategy,
                broker='webull',
                symbol=symbol,
                action='SELL',
                order_type='LIMIT',
                qty=qty,
                price=limit_price,
                pnl_usd=pnl,
                paper=PAPER_TRADING,
                order_id=order_id,
                notes=f"reason={reason}"
            )

            alert_trade_closed(
                strategy=strategy,
                symbol=symbol,
                action='SELL',
                qty=qty,
                entry_price=entry_price,
                exit_price=limit_price,
                pnl_usd=pnl,
                reason=reason
            )

            return order

        except Exception as e:
            print(f"[WebullBroker] Sell order failed for {symbol}: {e}")
            log_event('ERROR', 'WebullBroker', f"Sell order failed {symbol}: {e}")
            return None

    def sell_market(self, symbol: str, qty: float, strategy: str,
                    entry_price: float = 0.0, reason: str = 'Stop loss') -> Optional[dict]:
        """
        Market sell for stop loss execution only.
        Strategy rule: prefer limit orders, but stops use market to guarantee fill.
        """
        if not self.is_connected():
            from data.market_data import get_current_price
            price = get_current_price(symbol) or entry_price
            pnl = (price - entry_price) * qty if entry_price > 0 else 0
            self._log_paper_trade(symbol, 'SELL', 'MARKET', qty, price, strategy, pnl_usd=pnl)
            return {'paper_fill': True, 'price': price, 'qty': qty}

        try:
            self._cancel_symbol_orders(symbol)

            order = self._wb.place_order(
                stock=symbol,
                action='SELL',
                orderType='MKT',
                quant=int(qty),
                enforce='DAY'
            )

            order_id = str(order.get('orderId', ''))
            print(f"[WebullBroker] SELL MARKET (stop): {qty} {symbol} | reason: {reason}")
            log_event('WARNING', 'WebullBroker', f"Market sell {symbol}: {reason}")

            return order

        except Exception as e:
            print(f"[WebullBroker] Market sell failed for {symbol}: {e}")
            return None

    # ─── Position queries ─────────────────────────────────────────────────────

    def get_position(self, symbol: str) -> Optional[dict]:
        """Get current position for a symbol."""
        if not self.is_connected():
            return None
        try:
            positions = self._wb.get_positions()
            for pos in positions:
                ticker = pos.get('ticker', {})
                if ticker.get('symbol', '') == symbol:
                    return pos
            return None
        except Exception as e:
            print(f"[WebullBroker] Error getting position for {symbol}: {e}")
            return None

    def get_all_positions(self) -> list:
        if not self.is_connected():
            return []
        try:
            return self._wb.get_positions() or []
        except Exception:
            return []

    def get_account_value(self) -> float:
        if not self.is_connected():
            return 0.0
        try:
            acct = self._wb.get_account()
            return float(acct.get('netLiquidation', 0))
        except Exception:
            return 0.0

    def get_cash_balance(self) -> float:
        if not self.is_connected():
            return 0.0
        try:
            acct = self._wb.get_account()
            return float(acct.get('cashBalance', 0))
        except Exception:
            return 0.0

    # ─── Order management ─────────────────────────────────────────────────────

    def cancel_all_orders(self) -> None:
        if not self.is_connected():
            return
        try:
            self._wb.cancel_all_orders()
            print("[WebullBroker] All open orders cancelled")
        except Exception as e:
            print(f"[WebullBroker] Cancel all orders failed: {e}")

    def _cancel_symbol_orders(self, symbol: str) -> None:
        if not self.is_connected():
            return
        try:
            orders = self._wb.get_open_orders() or []
            for order in orders:
                ticker = order.get('ticker', {})
                if ticker.get('symbol', '') == symbol:
                    order_id = order.get('orderId')
                    if order_id:
                        self._wb.cancel_order(order_id)
        except Exception as e:
            print(f"[WebullBroker] Error cancelling {symbol} orders: {e}")

    def _place_stop_loss(self, symbol: str, qty: float, stop_price: float) -> None:
        """Place a stop market sell order immediately after buying."""
        try:
            self._wb.place_order(
                stock=symbol,
                action='SELL',
                orderType='STP',
                price=round(stop_price, 2),
                quant=int(qty),
                enforce='GTC'
            )
            print(f"[WebullBroker] Stop loss set: {symbol} @ ${stop_price:.2f}")
        except Exception as e:
            print(f"[WebullBroker] Failed to set stop loss for {symbol}: {e}")
            log_event('WARNING', 'WebullBroker', f"Stop loss placement failed {symbol}: {e}")

    # ─── Paper trade helper ───────────────────────────────────────────────────

    def _log_paper_trade(self, symbol, action, order_type, qty, price,
                         strategy, pnl_usd=0.0) -> None:
        log_trade(
            strategy=strategy,
            broker='webull_paper',
            symbol=symbol,
            action=action,
            order_type=order_type,
            qty=qty,
            price=price,
            pnl_usd=pnl_usd,
            paper=True,
            order_id=f'PAPER_{uuid.uuid4().hex[:8]}',
        )
        emoji = '🟢' if action == 'BUY' else '🔴'
        print(f"[PAPER] {emoji} {action} {qty} {symbol} @ ${price:.4f} | P&L: ${pnl_usd:+.2f}")


# ─── Module-level singleton ───────────────────────────────────────────────────
_webull_broker: Optional[WebullBroker] = None


def get_webull_broker() -> WebullBroker:
    global _webull_broker
    if _webull_broker is None:
        _webull_broker = WebullBroker()
    return _webull_broker
