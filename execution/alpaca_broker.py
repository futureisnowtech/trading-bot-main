"""
execution/alpaca_broker.py

Alpaca equity broker — replaces webull_broker.py for stock trading.
Alpaca has an official, working Python API unlike Webull's blocked unofficial library.

Free paper account: alpaca.markets → sign up → Paper Trading
Free live account: same, add bank funding after paper results pass readiness check

To get API keys:
  1. Go to alpaca.markets and create a free account
  2. In dashboard: click "Paper Trading" (left sidebar)
  3. Click "Generate API Keys"
  4. Add to .env:
       ALPACA_API_KEY=PKxxxxxxxxxxxxxxxx
       ALPACA_SECRET_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  5. For live: generate SEPARATE live keys from the "Live Trading" section

Notes:
  - Paper and live use different base URLs (handled automatically)
  - No PDT restriction on margin accounts
  - Commission-free stock trading on live
  - $0 minimum for paper, $0 minimum for live (cash account)
"""
import os
import sys
import uuid
from typing import Optional
from datetime import datetime
import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PAPER_TRADING, MARKET_TIMEZONE
from logging_db.trade_logger import log_trade, log_event
from alerts.telegram_alert import alert_trade_opened, alert_trade_closed

ALPACA_API_KEY:    str = os.getenv('ALPACA_API_KEY', '')
ALPACA_SECRET_KEY: str = os.getenv('ALPACA_SECRET_KEY', '')

# Alpaca URLs
ALPACA_PAPER_URL = 'https://paper-api.alpaca.markets'
ALPACA_LIVE_URL  = 'https://api.alpaca.markets'


class AlpacaBroker:
    """
    Alpaca equity broker.
    Drop-in replacement for WebullBroker — same public interface.
    """

    def __init__(self):
        self._client         = None
        self._authenticated  = False
        self._base_url       = ALPACA_PAPER_URL if PAPER_TRADING else ALPACA_LIVE_URL

    # ─── Authentication ───────────────────────────────────────────────────────

    def connect(self) -> bool:
        if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
            print("[AlpacaBroker] API keys not set — add ALPACA_API_KEY + ALPACA_SECRET_KEY to .env")
            print("[AlpacaBroker]   Get them free at alpaca.markets → Paper Trading → Generate API Keys")
            return False

        try:
            from alpaca.trading.client import TradingClient
            self._client = TradingClient(
                api_key=ALPACA_API_KEY,
                secret_key=ALPACA_SECRET_KEY,
                paper=PAPER_TRADING
            )
            acct = self._client.get_account()
            self._authenticated = True
            mode = 'PAPER' if PAPER_TRADING else 'LIVE'
            cash = float(acct.cash)
            equity = float(acct.equity)
            print(f"[AlpacaBroker] Connected ({mode}) ✅ | Cash: ${cash:,.2f} | Equity: ${equity:,.2f}")
            log_event('INFO', 'AlpacaBroker', f"Connected ({mode}) cash=${cash:.2f}")
            return True

        except Exception as e:
            print(f"[AlpacaBroker] Connection error: {e}")
            if 'forbidden' in str(e).lower() or '403' in str(e):
                print("[AlpacaBroker] Check API keys — paper keys don't work on live endpoint and vice versa")
            log_event('ERROR', 'AlpacaBroker', f"Connection failed: {e}")
            return False

    def is_connected(self) -> bool:
        return self._authenticated and self._client is not None

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
        """Place a limit buy order."""
        if not self.is_connected():
            self._log_paper_trade(symbol, 'BUY', 'LIMIT', qty, limit_price, strategy)
            return {'paper_fill': True, 'price': limit_price, 'qty': qty}

        try:
            from alpaca.trading.requests import LimitOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            # Cancel any existing open orders for this symbol first
            self._cancel_symbol_orders(symbol)

            req = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY,
                type='limit',
                time_in_force=TimeInForce.DAY,
                limit_price=round(limit_price, 2),
            )
            order = self._client.submit_order(req)
            order_id = str(order.id)

            print(f"[AlpacaBroker] BUY LIMIT: {qty} {symbol} @ ${limit_price:.2f} | ID: {order_id}")

            # Place bracket stop immediately after entry order
            if stop_loss > 0 and take_profit > 0:
                self._place_oco_bracket(symbol, qty, stop_loss, take_profit)

            log_trade(
                strategy=strategy, broker='alpaca',
                symbol=symbol, action='BUY', order_type='LIMIT',
                qty=qty, price=limit_price, fee_usd=0.0,
                paper=PAPER_TRADING, order_id=order_id,
                notes=f"SL={stop_loss:.2f} TP={take_profit:.2f}"
            )
            alert_trade_opened(strategy, symbol, 'BUY', qty, limit_price, stop_loss, take_profit)
            return {'id': order_id, 'status': str(order.status)}

        except Exception as e:
            print(f"[AlpacaBroker] Buy order failed for {symbol}: {e}")
            log_event('ERROR', 'AlpacaBroker', f"Buy {symbol} failed: {e}")
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
            self._log_paper_trade(symbol, 'SELL', 'LIMIT', qty, limit_price, strategy, pnl_usd=pnl)
            return {'paper_fill': True, 'price': limit_price, 'qty': qty}

        try:
            from alpaca.trading.requests import LimitOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            self._cancel_symbol_orders(symbol)

            req = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                type='limit',
                time_in_force=TimeInForce.DAY,
                limit_price=round(limit_price, 2),
            )
            order = self._client.submit_order(req)
            order_id = str(order.id)
            pnl = (limit_price - entry_price) * qty if entry_price > 0 else 0

            print(f"[AlpacaBroker] SELL LIMIT: {qty} {symbol} @ ${limit_price:.2f} | P&L: ${pnl:+.2f}")
            log_trade(
                strategy=strategy, broker='alpaca',
                symbol=symbol, action='SELL', order_type='LIMIT',
                qty=qty, price=limit_price, pnl_usd=pnl,
                paper=PAPER_TRADING, order_id=order_id,
                notes=f"reason={reason}"
            )
            alert_trade_closed(strategy, symbol, 'SELL', qty, entry_price, limit_price, pnl, reason)
            return {'id': order_id, 'status': str(order.status)}

        except Exception as e:
            print(f"[AlpacaBroker] Sell order failed for {symbol}: {e}")
            log_event('ERROR', 'AlpacaBroker', f"Sell {symbol} failed: {e}")
            return None

    def sell_market(
        self,
        symbol: str,
        qty: float,
        strategy: str,
        entry_price: float = 0.0,
        reason: str = 'Stop loss'
    ) -> Optional[dict]:
        """Market sell for stop loss execution — guarantees fill."""
        if not self.is_connected():
            from data.market_data import get_current_price
            price = get_current_price(symbol) or entry_price
            pnl = (price - entry_price) * qty if entry_price > 0 else 0
            self._log_paper_trade(symbol, 'SELL', 'MARKET', qty, price, strategy, pnl_usd=pnl)
            return {'paper_fill': True, 'price': price, 'qty': qty}

        try:
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            self._cancel_symbol_orders(symbol)
            req = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
            order = self._client.submit_order(req)
            print(f"[AlpacaBroker] SELL MARKET (stop): {qty} {symbol} | reason: {reason}")
            log_event('WARNING', 'AlpacaBroker', f"Market sell {symbol}: {reason}")
            return {'id': str(order.id)}

        except Exception as e:
            print(f"[AlpacaBroker] Market sell failed for {symbol}: {e}")
            return None

    # ─── Position / account queries ───────────────────────────────────────────

    def get_position(self, symbol: str) -> Optional[dict]:
        if not self.is_connected():
            return None
        try:
            pos = self._client.get_open_position(symbol)
            return {
                'symbol': symbol,
                'position': float(pos.qty),
                'avg_entry': float(pos.avg_entry_price),
                'market_value': float(pos.market_value),
                'unrealized_pl': float(pos.unrealized_pl),
            }
        except Exception:
            return None

    def get_all_positions(self) -> list:
        if not self.is_connected():
            return []
        try:
            return [
                {
                    'symbol': p.symbol,
                    'position': float(p.qty),
                    'avg_entry': float(p.avg_entry_price),
                    'market_value': float(p.market_value),
                    'unrealized_pl': float(p.unrealized_pl),
                }
                for p in self._client.get_all_positions()
            ]
        except Exception:
            return []

    def get_account_value(self) -> float:
        if not self.is_connected():
            return 0.0
        try:
            return float(self._client.get_account().equity)
        except Exception:
            return 0.0

    def get_cash_balance(self) -> float:
        if not self.is_connected():
            return 0.0
        try:
            return float(self._client.get_account().cash)
        except Exception:
            return 0.0

    # ─── Order management ─────────────────────────────────────────────────────

    def cancel_all_orders(self) -> None:
        if not self.is_connected():
            return
        try:
            self._client.cancel_orders()
            print("[AlpacaBroker] All open orders cancelled")
        except Exception as e:
            print(f"[AlpacaBroker] Cancel all orders failed: {e}")

    def _cancel_symbol_orders(self, symbol: str) -> None:
        if not self.is_connected():
            return
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])
            orders = self._client.get_orders(req)
            for order in orders:
                self._client.cancel_order_by_id(order.id)
        except Exception:
            pass

    def _place_oco_bracket(self, symbol: str, qty: float,
                           stop_price: float, take_profit_price: float) -> None:
        """Place OCO (one-cancels-other) stop + target after a buy fill."""
        try:
            from alpaca.trading.requests import TakeProfitRequest, StopLossRequest, LimitOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

            req = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                type='limit',
                time_in_force=TimeInForce.GTC,
                limit_price=round(take_profit_price, 2),
                order_class=OrderClass.OCO,
                stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
            )
            self._client.submit_order(req)
            print(f"[AlpacaBroker] OCO bracket set: SL={stop_price:.2f} TP={take_profit_price:.2f}")
        except Exception as e:
            print(f"[AlpacaBroker] OCO bracket failed for {symbol}: {e}")
            log_event('WARNING', 'AlpacaBroker', f"OCO bracket failed {symbol}: {e}")

    # ─── Paper trade helper ───────────────────────────────────────────────────

    def _log_paper_trade(self, symbol, action, order_type, qty, price,
                         strategy, pnl_usd=0.0) -> None:
        log_trade(
            strategy=strategy, broker='alpaca_paper',
            symbol=symbol, action=action, order_type=order_type,
            qty=qty, price=price, pnl_usd=pnl_usd,
            paper=True, order_id=f'PAPER_{uuid.uuid4().hex[:8]}',
        )
        emoji = '🟢' if action == 'BUY' else '🔴'
        print(f"[PAPER] {emoji} {action} {qty} {symbol} @ ${price:.4f} | P&L: ${pnl_usd:+.2f}")


# ─── Singleton ────────────────────────────────────────────────────────────────
_alpaca_broker: Optional[AlpacaBroker] = None


def get_alpaca_broker() -> AlpacaBroker:
    global _alpaca_broker
    if _alpaca_broker is None:
        _alpaca_broker = AlpacaBroker()
    return _alpaca_broker


# Alias so anything that imported get_webull_broker still works
get_webull_broker = get_alpaca_broker
