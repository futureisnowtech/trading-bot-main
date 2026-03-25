"""
execution/bybit_broker.py

Bybit USDT-margined perpetual futures execution.
Supports LONG and SHORT, isolated margin, configurable leverage.

Exchange: Bybit (bybit.com)
Instrument type: Linear perpetual (USDT-margined)
API library: pybit >= 5.x   pip install pybit

To configure:
  1. Create API key at bybit.com → Account → API Management
     Permissions: Contracts (Read + Trade)
  2. Start on testnet: testnet.bybit.com → same flow
  3. Add to .env:
       BYBIT_API_KEY=...
       BYBIT_API_SECRET=...
       BYBIT_TESTNET=true   (change to false for live)

Symbol format: AVAXUSDT, BTCUSDT (no hyphen, no USDC)
Leverage: Set per-symbol before placing order.
Margin mode: Isolated (each position has its own margin — one trade can't
             wipe the whole account).

Fee accounting (USDT perp, standard tier):
  Taker: 0.055%   Maker: 0.020%
  Round-trip taker: ~0.11%
  With 20x leverage: 0.11% nominal = 2.2% of margin — budget accordingly.

With $5,000 account and PERP_POSITION_SIZE_USD=100, PERP_MAX_LEVERAGE=20:
  Notional: $2,000  |  Margin required: ~$100  |  2% of account per position
  Stop at 1.5% notional: $30 loss (0.6% account risk) — conservative
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
    PAPER_TRADING, MARKET_TIMEZONE,
    BYBIT_API_KEY, BYBIT_API_SECRET, BYBIT_TESTNET,
    PERP_MAX_LEVERAGE, PERP_POSITION_SIZE_USD,
    BYBIT_TAKER_FEE_PCT, BYBIT_MAKER_FEE_PCT,
)
from logging_db.trade_logger import log_trade, log_event
from alerts.telegram_alert import alert_trade_opened, alert_trade_closed

try:
    from pybit.unified_trading import HTTP as BybitHTTP
    PYBIT_AVAILABLE = True
except ImportError:
    PYBIT_AVAILABLE = False
    BybitHTTP = None


def _bybit_symbol_to_base(symbol: str) -> str:
    """AVAXUSDT → AVAX  (for yfinance fallback price lookup)."""
    return symbol.replace('USDT', '').replace('USDC', '')


class BybitBroker:
    """
    Bybit linear perpetual execution layer.
    Handles LONG and SHORT entries with isolated margin + leverage.
    Paper mode: uses real Bybit public market prices (no auth required).
    Live mode: requires BYBIT_API_KEY + BYBIT_API_SECRET in .env.
    """

    def __init__(self):
        self._client: Optional['BybitHTTP'] = None
        self._connected = False
        self._open_positions: dict = {}  # symbol → position dict

    def connect(self) -> bool:
        if not PYBIT_AVAILABLE:
            print("[BybitBroker] pybit not installed — run: pip install pybit")
            print("[BybitBroker] Falling back to simulated paper mode")
            self._connected = True
            return True

        if not BYBIT_API_KEY or not BYBIT_API_SECRET:
            print("[BybitBroker] API credentials not set — simulated paper mode")
            self._connected = True
            return True

        try:
            self._client = BybitHTTP(
                testnet=BYBIT_TESTNET,
                api_key=BYBIT_API_KEY,
                api_secret=BYBIT_API_SECRET,
            )
            # Verify: get account balance
            resp = self._client.get_wallet_balance(accountType='CONTRACT')
            if resp.get('retCode') != 0:
                print(f"[BybitBroker] Auth check failed: {resp.get('retMsg')}")
                self._connected = True  # allow paper fallback
                return True

            self._connected = True
            mode = 'TESTNET' if BYBIT_TESTNET else 'LIVE'
            print(f"[BybitBroker] Connected to Bybit {mode} ✅")
            log_event('INFO', 'BybitBroker', f'Connected ({mode})')
            return True

        except Exception as e:
            print(f"[BybitBroker] Connection error: {e}")
            print("[BybitBroker] Falling back to simulated paper mode")
            self._connected = True
            return True

    def is_connected(self) -> bool:
        return self._connected

    # ─── Order placement ──────────────────────────────────────────────────────

    def open_long(
        self,
        symbol: str,
        size_usd: float = PERP_POSITION_SIZE_USD,
        leverage: int = PERP_MAX_LEVERAGE,
        stop_pct: float = 0.015,
        take_profit_pct: float = 0.03,
        strategy: str = 'crypto_perp',
    ) -> Optional[dict]:
        """
        Open a leveraged LONG position on a linear perp.
        symbol: Bybit format, e.g. 'AVAXUSDT'
        size_usd: USD notional (margin = size_usd / leverage)
        """
        if PAPER_TRADING or not self._client:
            return self._paper_open(
                symbol, 'LONG', size_usd, leverage,
                stop_pct, take_profit_pct, strategy
            )

        try:
            price = self._get_mark_price(symbol)
            if not price:
                return None

            qty = round(size_usd / price, 3)
            self._set_leverage(symbol, leverage)

            resp = self._client.place_order(
                category='linear',
                symbol=symbol,
                side='Buy',
                orderType='Market',
                qty=str(qty),
                timeInForce='IOC',
                isLeverage=1,
                positionIdx=0,  # one-way mode
            )
            if resp.get('retCode') != 0:
                print(f"[BybitBroker] Long order failed: {resp.get('retMsg')}")
                log_event('ERROR', 'BybitBroker', f"Long {symbol}: {resp.get('retMsg')}")
                return None

            order_id = resp['result'].get('orderId', f'BY_{uuid.uuid4().hex[:8]}')
            stop = price * (1 - stop_pct)
            target = price * (1 + take_profit_pct)
            fee = size_usd * BYBIT_TAKER_FEE_PCT

            # Server-side stop loss (Bybit supports conditional OCO)
            self._set_trading_stop(symbol, stop, target)

            self._open_positions[symbol] = {
                'side': 'LONG', 'qty': qty, 'entry': price,
                'stop': stop, 'target': target,
                'leverage': leverage, 'size_usd': size_usd,
                'order_id': order_id,
            }
            log_trade(
                strategy=strategy, broker='bybit',
                symbol=symbol, action='BUY', order_type='MARKET',
                qty=qty, price=price, fee_usd=fee, paper=False,
                order_id=order_id,
                notes=f"LONG lev={leverage}x SL={stop:.4f} TP={target:.4f}"
            )
            alert_trade_opened(strategy, symbol, 'BUY', qty, price, stop, target)
            print(f"[BybitBroker] LONG {qty} {symbol} @ {price:.4f} lev={leverage}x | SL={stop:.4f} TP={target:.4f}")
            return resp

        except Exception as e:
            print(f"[BybitBroker] open_long {symbol} failed: {e}")
            log_event('ERROR', 'BybitBroker', f"open_long {symbol}: {e}")
            return None

    def open_short(
        self,
        symbol: str,
        size_usd: float = PERP_POSITION_SIZE_USD,
        leverage: int = PERP_MAX_LEVERAGE,
        stop_pct: float = 0.015,
        take_profit_pct: float = 0.03,
        strategy: str = 'crypto_perp',
    ) -> Optional[dict]:
        """
        Open a leveraged SHORT position on a linear perp.
        stop_pct / take_profit_pct are measured from entry on the notional.
        """
        if PAPER_TRADING or not self._client:
            return self._paper_open(
                symbol, 'SHORT', size_usd, leverage,
                stop_pct, take_profit_pct, strategy
            )

        try:
            price = self._get_mark_price(symbol)
            if not price:
                return None

            qty = round(size_usd / price, 3)
            self._set_leverage(symbol, leverage)

            resp = self._client.place_order(
                category='linear',
                symbol=symbol,
                side='Sell',
                orderType='Market',
                qty=str(qty),
                timeInForce='IOC',
                isLeverage=1,
                positionIdx=0,
            )
            if resp.get('retCode') != 0:
                print(f"[BybitBroker] Short order failed: {resp.get('retMsg')}")
                log_event('ERROR', 'BybitBroker', f"Short {symbol}: {resp.get('retMsg')}")
                return None

            order_id = resp['result'].get('orderId', f'BY_{uuid.uuid4().hex[:8]}')
            stop = price * (1 + stop_pct)    # stop above entry for short
            target = price * (1 - take_profit_pct)  # target below entry
            fee = size_usd * BYBIT_TAKER_FEE_PCT

            self._set_trading_stop(symbol, stop, target)

            self._open_positions[symbol] = {
                'side': 'SHORT', 'qty': qty, 'entry': price,
                'stop': stop, 'target': target,
                'leverage': leverage, 'size_usd': size_usd,
                'order_id': order_id,
            }
            log_trade(
                strategy=strategy, broker='bybit',
                symbol=symbol, action='SELL', order_type='MARKET',
                qty=qty, price=price, fee_usd=fee, paper=False,
                order_id=order_id,
                notes=f"SHORT lev={leverage}x SL={stop:.4f} TP={target:.4f}"
            )
            alert_trade_opened(strategy, symbol, 'SELL', qty, price, stop, target)
            print(f"[BybitBroker] SHORT {qty} {symbol} @ {price:.4f} lev={leverage}x | SL={stop:.4f} TP={target:.4f}")
            return resp

        except Exception as e:
            print(f"[BybitBroker] open_short {symbol} failed: {e}")
            log_event('ERROR', 'BybitBroker', f"open_short {symbol}: {e}")
            return None

    def close_position(
        self,
        symbol: str,
        strategy: str = 'crypto_perp',
        reason: str = 'Signal',
    ) -> Optional[dict]:
        """Close an open position (long or short) at market."""
        pos = self._open_positions.get(symbol)
        if not pos:
            return None

        if PAPER_TRADING or not self._client:
            return self._paper_close(symbol, strategy, reason)

        try:
            # Closing a long = sell; closing a short = buy
            close_side = 'Sell' if pos['side'] == 'LONG' else 'Buy'
            resp = self._client.place_order(
                category='linear',
                symbol=symbol,
                side=close_side,
                orderType='Market',
                qty=str(pos['qty']),
                timeInForce='IOC',
                reduceOnly=True,
                positionIdx=0,
            )
            if resp.get('retCode') != 0:
                print(f"[BybitBroker] Close failed: {resp.get('retMsg')}")
                return None

            exit_price = self._get_mark_price(symbol) or pos['entry']
            if pos['side'] == 'LONG':
                pnl = (exit_price - pos['entry']) * pos['qty'] * pos['leverage']
            else:
                pnl = (pos['entry'] - exit_price) * pos['qty'] * pos['leverage']

            fee = pos['size_usd'] * BYBIT_TAKER_FEE_PCT
            self._open_positions.pop(symbol, None)

            log_trade(
                strategy=strategy, broker='bybit',
                symbol=symbol, action='BUY' if pos['side'] == 'SHORT' else 'SELL',
                order_type='MARKET', qty=pos['qty'],
                price=exit_price, fee_usd=fee, pnl_usd=pnl,
                paper=False, notes=f"reason={reason}"
            )
            alert_trade_closed(
                strategy, symbol, pos['side'],
                pos['qty'], pos['entry'], exit_price, pnl, reason
            )
            print(f"[BybitBroker] CLOSED {pos['side']} {symbol} @ {exit_price:.4f} | P&L: ${pnl:+.2f} | {reason}")
            return resp

        except Exception as e:
            print(f"[BybitBroker] close_position {symbol} failed: {e}")
            return None

    def get_position(self, symbol: str) -> Optional[dict]:
        return self._open_positions.get(symbol)

    def get_all_positions(self) -> dict:
        return dict(self._open_positions)

    def get_mark_price(self, symbol: str) -> float:
        """Public method — wraps internal."""
        return self._get_mark_price(symbol) or 0.0

    def get_wallet_balance(self) -> float:
        if not self._client:
            return 0.0
        try:
            resp = self._client.get_wallet_balance(accountType='CONTRACT')
            if resp.get('retCode') == 0:
                coins = resp['result']['list'][0].get('coin', [])
                for c in coins:
                    if c.get('coin') == 'USDT':
                        return float(c.get('walletBalance', 0))
        except Exception:
            pass
        return 0.0

    def get_funding_rate(self, symbol: str) -> float:
        """Fetch current funding rate for a symbol (% per 8h)."""
        try:
            if self._client:
                resp = self._client.get_tickers(category='linear', symbol=symbol)
                if resp.get('retCode') == 0:
                    items = resp['result'].get('list', [])
                    if items:
                        return float(items[0].get('fundingRate', 0))
            # Public fallback (no auth needed)
            if PYBIT_AVAILABLE:
                pub = BybitHTTP(testnet=BYBIT_TESTNET)
                resp = pub.get_tickers(category='linear', symbol=symbol)
                if resp.get('retCode') == 0:
                    items = resp['result'].get('list', [])
                    if items:
                        return float(items[0].get('fundingRate', 0))
        except Exception:
            pass
        return 0.0

    def get_open_interest(self, symbol: str) -> float:
        """Open interest in USD — rising OI confirms momentum."""
        try:
            if PYBIT_AVAILABLE:
                client = self._client or BybitHTTP(testnet=BYBIT_TESTNET)
                resp = client.get_tickers(category='linear', symbol=symbol)
                if resp.get('retCode') == 0:
                    items = resp['result'].get('list', [])
                    if items:
                        return float(items[0].get('openInterestValue', 0))
        except Exception:
            pass
        return 0.0

    # ─── Internal helpers ─────────────────────────────────────────────────────

    def _get_mark_price(self, symbol: str) -> Optional[float]:
        """Get current mark price. Falls back to yfinance if API unavailable."""
        try:
            if PYBIT_AVAILABLE:
                client = self._client or BybitHTTP(testnet=BYBIT_TESTNET)
                resp = client.get_tickers(category='linear', symbol=symbol)
                if resp.get('retCode') == 0:
                    items = resp['result'].get('list', [])
                    if items:
                        return float(items[0].get('markPrice', 0) or items[0].get('lastPrice', 0))
        except Exception:
            pass

        # yfinance fallback
        try:
            import yfinance as yf
            base = _bybit_symbol_to_base(symbol)
            hist = yf.Ticker(f'{base}-USD').history(period='1d', interval='1m')
            if hist is not None and not hist.empty:
                return float(hist['Close'].iloc[-1])
        except Exception:
            pass
        return None

    def _set_leverage(self, symbol: str, leverage: int) -> None:
        if not self._client:
            return
        try:
            self._client.set_leverage(
                category='linear',
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage),
            )
        except Exception as e:
            print(f"[BybitBroker] set_leverage {symbol} {leverage}x: {e}")

    def _set_trading_stop(self, symbol: str, stop: float, target: float) -> None:
        """Set server-side stop-loss and take-profit after entry."""
        if not self._client:
            return
        try:
            self._client.set_trading_stop(
                category='linear',
                symbol=symbol,
                stopLoss=str(round(stop, 6)),
                takeProfit=str(round(target, 6)),
                positionIdx=0,
            )
        except Exception as e:
            print(f"[BybitBroker] set_trading_stop {symbol}: {e}")

    # ─── Paper trading ────────────────────────────────────────────────────────

    def _paper_open(self, symbol, side, size_usd, leverage,
                    stop_pct, take_profit_pct, strategy) -> dict:
        price = self._get_mark_price(symbol) or 0
        if not price:
            print(f"[PAPER PERP] No price for {symbol} — paper trade skipped")
            return {}

        qty = round(size_usd / price, 6)
        if side == 'LONG':
            stop = price * (1 - stop_pct)
            target = price * (1 + take_profit_pct)
        else:
            stop = price * (1 + stop_pct)
            target = price * (1 - take_profit_pct)

        fee = size_usd * BYBIT_TAKER_FEE_PCT
        order_id = f'PAPER_{uuid.uuid4().hex[:8]}'

        self._open_positions[symbol] = {
            'side': side, 'qty': qty, 'entry': price,
            'stop': stop, 'target': target,
            'leverage': leverage, 'size_usd': size_usd,
            'order_id': order_id,
        }

        action = 'BUY' if side == 'LONG' else 'SELL'
        log_trade(
            strategy=strategy, broker='bybit_paper',
            symbol=symbol, action=action, order_type='MARKET',
            qty=qty, price=price, fee_usd=fee, paper=True,
            order_id=order_id,
            notes=f"{side} lev={leverage}x SL={stop:.4f} TP={target:.4f} notional=${size_usd:.0f}"
        )

        emoji = '🟢' if side == 'LONG' else '🔴'
        print(f"[PAPER PERP] {emoji} {side} {qty:.4f} {symbol} @ {price:.4f} "
              f"lev={leverage}x | SL={stop:.4f} TP={target:.4f}")
        alert_trade_opened(strategy, symbol, action, qty, price, stop, target)
        return {'paper': True, 'price': price, 'qty': qty}

    def _paper_close(self, symbol, strategy, reason) -> dict:
        pos = self._open_positions.pop(symbol, {})
        if not pos:
            return {}

        entry = pos['entry']
        exit_price = self._get_mark_price(symbol) or entry
        side = pos.get('side', 'LONG')
        qty = pos.get('qty', 0)
        leverage = pos.get('leverage', 1)
        size_usd = pos.get('size_usd', qty * entry)

        if side == 'LONG':
            pnl = (exit_price - entry) * qty * leverage
        else:
            pnl = (entry - exit_price) * qty * leverage

        fee = size_usd * BYBIT_TAKER_FEE_PCT
        close_action = 'SELL' if side == 'LONG' else 'BUY'

        log_trade(
            strategy=strategy, broker='bybit_paper',
            symbol=symbol, action=close_action, order_type='MARKET',
            qty=qty, price=exit_price, fee_usd=fee, pnl_usd=pnl,
            paper=True, order_id=f'PAPER_{uuid.uuid4().hex[:8]}',
            notes=f"reason={reason}"
        )

        emoji = '🟢' if pnl >= 0 else '🔴'
        print(f"[PAPER PERP] {emoji} CLOSE {side} {symbol} @ {exit_price:.4f} | P&L: ${pnl:+.2f} | {reason}")
        alert_trade_closed(strategy, symbol, close_action, qty, entry, exit_price, pnl, reason)
        return {'paper': True, 'pnl': pnl}


# ─── Singleton ────────────────────────────────────────────────────────────────
_bybit_broker: Optional[BybitBroker] = None


def get_bybit_broker() -> BybitBroker:
    global _bybit_broker
    if _bybit_broker is None:
        _bybit_broker = BybitBroker()
    return _bybit_broker
