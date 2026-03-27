"""
execution/binance_broker.py — Binance USD-M perpetual futures execution.
Replaces bybit_broker.py (Sprint 1 overhaul).

Supports LONG and SHORT, isolated margin, configurable leverage.
Exchange: Binance USD-M Futures (fapi.binance.com)
API library: python-binance >= 1.0.19   pip install python-binance

To configure:
  1. Create API key at binance.com → Profile → API Management
     Permissions: Enable Futures
  2. For testnet: testnet.binancefuture.com (separate API keys)
  3. Add to .env:
       BINANCE_API_KEY=...
       BINANCE_API_SECRET=...
       BINANCE_TESTNET=true   (change to false for live)

Symbol format: AVAXUSDT, BTCUSDT (no hyphen — same as previous Bybit format)
Leverage: Set per-symbol before placing order (isolated margin mode).

Fee accounting (USD-M futures, standard tier):
  Taker: 0.040%   Maker: 0.020%
  Round-trip taker: ~0.08% (cheaper than Bybit's 0.11%)
  With 10x leverage: 0.08% nominal = 0.8% of margin — budget accordingly.
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
    BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_TESTNET,
    PERP_MAX_LEVERAGE, PERP_POSITION_SIZE_USD,
    BINANCE_TAKER_FEE_PCT, BINANCE_MAKER_FEE_PCT,
)
from logging_db.trade_logger import log_trade, log_event
from alerts.telegram_alert import alert_trade_opened, alert_trade_closed

try:
    from binance.client import Client as BinanceClient
    from binance.exceptions import BinanceAPIException
    BINANCE_AVAILABLE = True
except ImportError:
    BINANCE_AVAILABLE = False
    BinanceClient = None
    BinanceAPIException = Exception


def _binance_symbol_to_base(symbol: str) -> str:
    """AVAXUSDT → AVAX  (for yfinance fallback price lookup)."""
    return symbol.replace('USDT', '').replace('USDC', '')


class BinanceBroker:
    """
    Binance USD-M perpetual futures execution layer.
    Handles LONG and SHORT entries with isolated margin + leverage.
    Paper mode: uses real Binance public prices (no auth required).
    Live mode: requires BINANCE_API_KEY + BINANCE_API_SECRET in .env.

    Drop-in replacement for BybitBroker — identical public API.
    """

    def __init__(self):
        self._client: Optional['BinanceClient'] = None
        self._connected = False
        self._open_positions: dict = {}  # symbol → position dict

    def connect(self) -> bool:
        if not BINANCE_AVAILABLE:
            print("[BinanceBroker] python-binance not installed — run: pip install python-binance")
            print("[BinanceBroker] Falling back to simulated paper mode")
            self._connected = True
            return True

        if not BINANCE_API_KEY or not BINANCE_API_SECRET:
            print("[BinanceBroker] API credentials not set — simulated paper mode")
            self._connected = True
            return True

        try:
            if BINANCE_TESTNET:
                self._client = BinanceClient(
                    api_key=BINANCE_API_KEY,
                    api_secret=BINANCE_API_SECRET,
                    testnet=True,
                )
                self._client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'
            else:
                self._client = BinanceClient(
                    api_key=BINANCE_API_KEY,
                    api_secret=BINANCE_API_SECRET,
                )

            # Verify: get futures account balance
            balance = self._client.futures_account_balance()
            usdt = next((b for b in balance if b['asset'] == 'USDT'), None)
            self._connected = True
            mode = 'TESTNET' if BINANCE_TESTNET else 'LIVE'
            bal_str = f"${float(usdt['balance']):.2f}" if usdt else "unknown"
            print(f"[BinanceBroker] Connected to Binance Futures {mode} ✅  USDT balance: {bal_str}")
            log_event('INFO', 'BinanceBroker', f'Connected ({mode}) balance={bal_str}')
            return True

        except Exception as e:
            print(f"[BinanceBroker] Connection error: {e}")
            print("[BinanceBroker] Falling back to simulated paper mode")
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
        """Open a leveraged LONG position on a USD-M perp."""
        if PAPER_TRADING or not self._client:
            return self._paper_open(symbol, 'LONG', size_usd, leverage,
                                    stop_pct, take_profit_pct, strategy)
        try:
            price = self._get_mark_price(symbol)
            if not price:
                return None

            self._set_leverage(symbol, leverage)
            self._set_margin_type(symbol, 'ISOLATED')

            qty = round(size_usd / price, 3)
            resp = self._client.futures_create_order(
                symbol=symbol,
                side='BUY',
                type='LIMIT',
                quantity=qty,
                price=round(price, 4),
                timeInForce='GTC',
                positionSide='BOTH',
            )
            order_id = resp.get('orderId', f'BN_{uuid.uuid4().hex[:8]}')
            fill_price = float(resp.get('avgPrice', 0) or 0) or price
            stop = fill_price * (1 - stop_pct)
            target = fill_price * (1 + take_profit_pct)
            fee = size_usd * BINANCE_MAKER_FEE_PCT

            # Server-side stop-loss
            self._set_stop_loss(symbol, 'SELL', stop)
            # Server-side take-profit
            self._set_take_profit(symbol, 'SELL', target)

            self._open_positions[symbol] = {
                'side': 'LONG', 'qty': qty, 'entry': fill_price,
                'stop': stop, 'target': target,
                'leverage': leverage, 'size_usd': size_usd,
                'order_id': str(order_id),
            }
            log_trade(
                strategy=strategy, broker='binance',
                symbol=symbol, action='BUY', order_type='LIMIT',
                qty=qty, price=fill_price, fee_usd=fee, paper=False,
                order_id=str(order_id),
                notes=f"LONG lev={leverage}x SL={stop:.4f} TP={target:.4f}"
            )
            alert_trade_opened(strategy, symbol, 'BUY', qty, fill_price, stop, target)
            print(f"[BinanceBroker] LONG {qty} {symbol} @ {fill_price:.4f} lev={leverage}x "
                  f"| SL={stop:.4f} TP={target:.4f}")
            return resp

        except Exception as e:
            print(f"[BinanceBroker] open_long {symbol} failed: {e}")
            log_event('ERROR', 'BinanceBroker', f"open_long {symbol}: {e}")
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
        """Open a leveraged SHORT position on a USD-M perp."""
        if PAPER_TRADING or not self._client:
            return self._paper_open(symbol, 'SHORT', size_usd, leverage,
                                    stop_pct, take_profit_pct, strategy)
        try:
            price = self._get_mark_price(symbol)
            if not price:
                return None

            self._set_leverage(symbol, leverage)
            self._set_margin_type(symbol, 'ISOLATED')

            qty = round(size_usd / price, 3)
            resp = self._client.futures_create_order(
                symbol=symbol,
                side='SELL',
                type='LIMIT',
                quantity=qty,
                price=round(price, 4),
                timeInForce='GTC',
                positionSide='BOTH',
            )
            order_id = resp.get('orderId', f'BN_{uuid.uuid4().hex[:8]}')
            fill_price = float(resp.get('avgPrice', 0) or 0) or price
            stop = fill_price * (1 + stop_pct)     # stop above entry for short
            target = fill_price * (1 - take_profit_pct)  # target below entry
            fee = size_usd * BINANCE_MAKER_FEE_PCT

            self._set_stop_loss(symbol, 'BUY', stop)
            self._set_take_profit(symbol, 'BUY', target)

            self._open_positions[symbol] = {
                'side': 'SHORT', 'qty': qty, 'entry': fill_price,
                'stop': stop, 'target': target,
                'leverage': leverage, 'size_usd': size_usd,
                'order_id': str(order_id),
            }
            log_trade(
                strategy=strategy, broker='binance',
                symbol=symbol, action='SELL', order_type='LIMIT',
                qty=qty, price=fill_price, fee_usd=fee, paper=False,
                order_id=str(order_id),
                notes=f"SHORT lev={leverage}x SL={stop:.4f} TP={target:.4f}"
            )
            alert_trade_opened(strategy, symbol, 'SELL', qty, fill_price, stop, target)
            print(f"[BinanceBroker] SHORT {qty} {symbol} @ {fill_price:.4f} lev={leverage}x "
                  f"| SL={stop:.4f} TP={target:.4f}")
            return resp

        except Exception as e:
            print(f"[BinanceBroker] open_short {symbol} failed: {e}")
            log_event('ERROR', 'BinanceBroker', f"open_short {symbol}: {e}")
            return None

    def close_position(
        self,
        symbol: str,
        strategy: str = 'crypto_perp',
        reason: str = 'Signal',
    ) -> Optional[dict]:
        """Close an open position (long or short) at market. Cancels server-side SL/TP first."""
        pos = self._open_positions.get(symbol)
        if not pos:
            return None

        if PAPER_TRADING or not self._client:
            return self._paper_close(symbol, strategy, reason)

        try:
            # Cancel all open conditional orders for this symbol first
            try:
                self._client.futures_cancel_all_open_orders(symbol=symbol)
            except Exception:
                pass

            close_side = 'SELL' if pos['side'] == 'LONG' else 'BUY'
            resp = self._client.futures_create_order(
                symbol=symbol,
                side=close_side,
                type='MARKET',
                quantity=pos['qty'],
                reduceOnly=True,
            )

            exit_price = self._get_mark_price(symbol) or pos['entry']
            if pos['side'] == 'LONG':
                pnl = (exit_price - pos['entry']) * pos['qty'] * pos['leverage']
            else:
                pnl = (pos['entry'] - exit_price) * pos['qty'] * pos['leverage']

            fee = pos['size_usd'] * BINANCE_TAKER_FEE_PCT
            self._open_positions.pop(symbol, None)

            log_trade(
                strategy=strategy, broker='binance',
                symbol=symbol, action=close_side, order_type='MARKET',
                qty=pos['qty'], price=exit_price, fee_usd=fee, pnl_usd=pnl,
                paper=False, notes=f"reason={reason}"
            )
            alert_trade_closed(strategy, symbol, pos['side'],
                               pos['qty'], pos['entry'], exit_price, pnl, reason)
            print(f"[BinanceBroker] CLOSED {pos['side']} {symbol} @ {exit_price:.4f} "
                  f"| P&L: ${pnl:+.2f} | {reason}")
            return resp

        except Exception as e:
            print(f"[BinanceBroker] close_position {symbol} failed: {e}")
            return None

    # ─── Market data ──────────────────────────────────────────────────────────

    def get_position(self, symbol: str) -> Optional[dict]:
        return self._open_positions.get(symbol)

    def get_all_positions(self) -> dict:
        return dict(self._open_positions)

    def get_mark_price(self, symbol: str) -> float:
        return self._get_mark_price(symbol) or 0.0

    def get_wallet_balance(self) -> float:
        if not self._client:
            return 0.0
        try:
            balances = self._client.futures_account_balance()
            usdt = next((b for b in balances if b['asset'] == 'USDT'), None)
            return float(usdt['balance']) if usdt else 0.0
        except Exception:
            return 0.0

    def get_funding_rate(self, symbol: str) -> float:
        """Fetch current funding rate for a symbol (% per 8h as decimal)."""
        try:
            if BINANCE_AVAILABLE:
                client = self._client
                if not client:
                    client = BinanceClient('', '')  # public endpoint — no auth needed
                # get_funding_rate returns list; take most recent
                rates = client.futures_funding_rate(symbol=symbol, limit=1)
                if rates:
                    return float(rates[0].get('fundingRate', 0))
        except Exception:
            pass
        return 0.0

    def get_open_interest(self, symbol: str) -> float:
        """Open interest in USD — rising OI confirms momentum."""
        try:
            if BINANCE_AVAILABLE:
                client = self._client
                if not client:
                    client = BinanceClient('', '')
                oi = client.futures_open_interest(symbol=symbol)
                return float(oi.get('openInterest', 0))
        except Exception:
            pass
        return 0.0

    def get_klines(self, symbol: str, interval: str = '1m', limit: int = 100):
        """
        Fetch OHLCV klines from Binance Futures (public endpoint — no auth needed).
        Returns a DataFrame with columns: open, high, low, close, volume.
        Falls back to yfinance if Binance unavailable.
        """
        import pandas as pd
        try:
            if BINANCE_AVAILABLE:
                client = self._client
                if not client:
                    client = BinanceClient('', '')  # public endpoint
                klines = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
                if klines:
                    df = pd.DataFrame(klines, columns=[
                        'open_time', 'open', 'high', 'low', 'close', 'volume',
                        'close_time', 'quote_vol', 'trades', 'taker_buy_base',
                        'taker_buy_quote', 'ignore',
                    ])
                    for col in ['open', 'high', 'low', 'close', 'volume']:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                    return df[['open', 'high', 'low', 'close', 'volume']].reset_index(drop=True)
        except Exception as e:
            print(f"[BinanceBroker] get_klines {symbol}: {e}")

        # yfinance fallback
        try:
            import yfinance as yf
            base = _binance_symbol_to_base(symbol)
            period = '5d' if limit > 100 else '2d'
            hist = yf.Ticker(f'{base}-USD').history(period=period, interval='1m')
            if hist is not None and not hist.empty:
                hist.columns = [c.lower() for c in hist.columns]
                return hist[['open', 'high', 'low', 'close', 'volume']].tail(limit).reset_index(drop=True)
        except Exception:
            pass
        return None

    # ─── Internal helpers ─────────────────────────────────────────────────────

    def _get_mark_price(self, symbol: str) -> Optional[float]:
        """Get current mark price. Falls back to yfinance if API unavailable."""
        try:
            if BINANCE_AVAILABLE:
                client = self._client
                if not client:
                    client = BinanceClient('', '')  # public endpoint
                result = client.futures_mark_price(symbol=symbol)
                mark = float(result.get('markPrice', 0))
                if mark > 0:
                    return mark
        except Exception:
            pass

        # yfinance fallback
        try:
            import yfinance as yf
            base = _binance_symbol_to_base(symbol)
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
            self._client.futures_change_leverage(symbol=symbol, leverage=leverage)
        except Exception as e:
            # Binance returns error if leverage is already set to this value — safe to ignore
            if 'No need to change leverage' not in str(e):
                print(f"[BinanceBroker] set_leverage {symbol} {leverage}x: {e}")

    def _set_margin_type(self, symbol: str, margin_type: str = 'ISOLATED') -> None:
        if not self._client:
            return
        try:
            self._client.futures_change_margin_type(symbol=symbol, marginType=margin_type)
        except Exception as e:
            # Binance returns error if margin type is already set — safe to ignore
            if 'No need to change margin type' not in str(e):
                print(f"[BinanceBroker] set_margin_type {symbol}: {e}")

    def _set_stop_loss(self, symbol: str, side: str, stop_price: float) -> None:
        """Place a STOP_MARKET order as server-side stop loss."""
        if not self._client:
            return
        try:
            self._client.futures_create_order(
                symbol=symbol,
                side=side,
                type='STOP_MARKET',
                stopPrice=round(stop_price, 4),
                closePosition=True,
                timeInForce='GTE_GTC',
            )
        except Exception as e:
            print(f"[BinanceBroker] set_stop_loss {symbol}: {e}")

    def _set_take_profit(self, symbol: str, side: str, tp_price: float) -> None:
        """Place a TAKE_PROFIT_MARKET order as server-side take profit."""
        if not self._client:
            return
        try:
            self._client.futures_create_order(
                symbol=symbol,
                side=side,
                type='TAKE_PROFIT_MARKET',
                stopPrice=round(tp_price, 4),
                closePosition=True,
                timeInForce='GTE_GTC',
            )
        except Exception as e:
            print(f"[BinanceBroker] set_take_profit {symbol}: {e}")

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

        fee = size_usd * BINANCE_TAKER_FEE_PCT
        order_id = f'PAPER_{uuid.uuid4().hex[:8]}'

        self._open_positions[symbol] = {
            'side': side, 'qty': qty, 'entry': price,
            'stop': stop, 'target': target,
            'leverage': leverage, 'size_usd': size_usd,
            'order_id': order_id,
        }

        action = 'BUY' if side == 'LONG' else 'SELL'
        log_trade(
            strategy=strategy, broker='binance_paper',
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

        fee = size_usd * BINANCE_TAKER_FEE_PCT
        close_action = 'SELL' if side == 'LONG' else 'BUY'

        log_trade(
            strategy=strategy, broker='binance_paper',
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
_binance_broker: Optional[BinanceBroker] = None


def get_binance_broker() -> BinanceBroker:
    global _binance_broker
    if _binance_broker is None:
        _binance_broker = BinanceBroker()
    return _binance_broker


# Alias so anything that imported get_bybit_broker still works
get_bybit_broker = get_binance_broker
