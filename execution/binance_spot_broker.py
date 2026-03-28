"""
execution/binance_spot_broker.py — Binance spot execution.
Replaces Coinbase Advanced Trade for spot crypto.

Fees: 0.10% maker/taker (vs Coinbase 0.40%) = 4x cheaper round-trip.
With BNB fee discount: 0.075%.
Symbol format input: "BTC-USDC" → normalized to "BTCUSDC" for Binance API.

Paper mode: logs to SQLite, uses Binance public REST for real prices.
Live mode: requires BINANCE_API_KEY + BINANCE_API_SECRET with Spot Trading permission.
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
    BINANCE_API_KEY, BINANCE_API_SECRET,
    PAPER_TRADING, MARKET_TIMEZONE,
    BINANCE_SPOT_MAKER_FEE_PCT,
    CRYPTO_POSITION_SIZE_USD,
)
from logging_db.trade_logger import log_trade, log_event
from alerts.telegram_alert import alert_trade_opened, alert_trade_closed

try:
    from binance.client import Client as BinanceClient
    from binance.enums import TIME_IN_FORCE_GTC
    BINANCE_AVAILABLE = True
except ImportError:
    BINANCE_AVAILABLE = False
    BinanceClient = None
    TIME_IN_FORCE_GTC = 'GTC'
    print("[binance_spot_broker] python-binance not installed. Run: pip install python-binance")


def _normalize_symbol(product_id: str) -> str:
    """
    Normalize Coinbase-style symbol to Binance spot symbol.
      BTC-USDC  → BTCUSDC
      ETH-USDC  → ETHUSDC
      SOL-USDC  → SOLUSDC
      BTC-USD   → BTCUSDT   (USD without C → map to USDT)
      ETH-USD   → ETHUSDT
    """
    if '-' not in product_id:
        # Already normalized (e.g., BTCUSDC)
        return product_id.upper()
    base, quote = product_id.upper().split('-', 1)
    if quote == 'USD':
        return base + 'USDT'
    return base + quote


def _get_spot_price_public(symbol: str) -> Optional[float]:
    """
    Fetch current price from Binance public REST (no auth needed).
    GET https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDC
    5s timeout. Returns None on failure.
    """
    try:
        import urllib.request
        import json
        url = f'https://api.binance.com/api/v3/ticker/price?symbol={symbol}'
        req = urllib.request.Request(url, headers={'User-Agent': 'algo-bot/1.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            price = float(data.get('price', 0))
            return price if price > 0 else None
    except Exception as e:
        print(f"[binance_spot] public price fetch failed for {symbol}: {e}")
        return None


# Per-symbol price cache: symbol → (price, timestamp)
_price_cache: dict = {}
_PRICE_CACHE_TTL = 10  # seconds


def _get_spot_price_cached(product_id: str) -> Optional[float]:
    """Cached spot price lookup — refreshes at most once per 10s per symbol."""
    symbol = _normalize_symbol(product_id)
    now = time.time()
    cached = _price_cache.get(symbol)
    if cached and (now - cached[1]) < _PRICE_CACHE_TTL:
        return cached[0]
    price = _get_spot_price_public(symbol)
    if price:
        _price_cache[symbol] = (price, now)
    return price


class BinanceSpotBroker:
    """
    Binance spot execution layer. Drop-in replacement for CoinbaseBroker.

    Identical public API:
      buy_limit(product_id, base_size, limit_price, strategy, stop_loss, take_profit) -> bool
      sell_limit(product_id, base_size, limit_price, strategy, entry_price, reason) -> bool
      get_current_price(product_id) -> float

    Paper mode: uses real Binance public prices, logs to SQLite.
    Live mode: places GTC limit orders via python-binance.
    """

    def __init__(self):
        self._client: Optional['BinanceClient'] = None
        self._connected = False
        self._open_positions: dict = {}  # product_id (original format) → position dict

    def connect(self) -> bool:
        if not BINANCE_AVAILABLE:
            print("[BinanceSpotBroker] python-binance not installed — paper mode only")
            self._connected = True
            return True

        if not BINANCE_API_KEY or not BINANCE_API_SECRET:
            print("[BinanceSpotBroker] API credentials not set — paper mode only")
            self._connected = True
            return True

        try:
            self._client = BinanceClient(
                api_key=BINANCE_API_KEY,
                api_secret=BINANCE_API_SECRET,
            )
            # Verify connection: get account status
            account = self._client.get_account()
            self._connected = True
            can_trade = account.get('canTrade', False)
            print(f"[BinanceSpotBroker] Connected to Binance Spot ✅  canTrade={can_trade}")
            log_event('INFO', 'BinanceSpotBroker', f'Connected, canTrade={can_trade}')
            return True

        except Exception as e:
            print(f"[BinanceSpotBroker] Connection error: {e}")
            print("[BinanceSpotBroker] Falling back to paper mode")
            log_event('WARNING', 'BinanceSpotBroker', f'Connection failed, paper mode: {e}')
            self._connected = True
            return True

    def is_connected(self) -> bool:
        return self._connected

    # ─── Buy orders ───────────────────────────────────────────────────────────

    def buy_limit(
        self,
        product_id: str,
        base_size: float,
        limit_price: float,
        strategy: str = 'crypto_macd_consensus',
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
    ) -> bool:
        """
        Place a spot limit buy order.
        base_size = quantity in base currency (e.g. BTC amount).
        Returns True on success (paper fill or live order accepted), False on failure.
        """
        if base_size <= 0 or limit_price <= 0:
            print(f"[BinanceSpotBroker] Invalid buy params: {product_id} size={base_size} price={limit_price}")
            return False

        if PAPER_TRADING or not self._client:
            return self._paper_buy(product_id, base_size, limit_price,
                                   strategy, stop_loss, take_profit)

        symbol = _normalize_symbol(product_id)
        try:
            order = self._client.order_limit_buy(
                symbol=symbol,
                quantity=round(base_size, 6),
                price=str(round(limit_price, 8)),
                timeInForce=TIME_IN_FORCE_GTC,
            )
            order_id = str(order.get('orderId', f'BN_{uuid.uuid4().hex[:8]}'))
            fee = limit_price * base_size * BINANCE_SPOT_MAKER_FEE_PCT

            self._open_positions[product_id] = {
                'qty': base_size,
                'entry': limit_price,
                'stop': stop_loss,
                'target': take_profit,
                'high_since_entry': limit_price,
                'order_id': order_id,
            }

            log_trade(
                strategy=strategy, broker='binance_spot',
                symbol=product_id, action='BUY', order_type='LIMIT',
                qty=base_size, price=limit_price, fee_usd=fee, paper=False,
                order_id=order_id,
                notes=f"SL={stop_loss:.4f} TP={take_profit:.4f}"
            )
            alert_trade_opened(strategy, product_id, 'BUY', base_size,
                               limit_price, stop_loss, take_profit)
            print(f"[BinanceSpotBroker] BUY LIMIT {base_size:.6f} {product_id} "
                  f"@ ${limit_price:,.4f} | fee~${fee:.3f}")
            return True

        except Exception as e:
            print(f"[BinanceSpotBroker] buy_limit failed {product_id}: {e}")
            log_event('ERROR', 'BinanceSpotBroker', f"buy_limit {product_id}: {e}")
            return False

    def sell_limit(
        self,
        product_id: str,
        base_size: float,
        limit_price: float,
        strategy: str = 'crypto_macd_consensus',
        entry_price: float = 0.0,
        reason: str = 'Signal',
    ) -> bool:
        """
        Close a long position with a spot limit sell.
        Returns True on success, False on failure.
        """
        if base_size <= 0 or limit_price <= 0:
            print(f"[BinanceSpotBroker] Invalid sell params: {product_id} size={base_size} price={limit_price}")
            return False

        pnl = (limit_price - entry_price) * base_size if entry_price > 0 else 0.0

        if PAPER_TRADING or not self._client:
            return self._paper_sell(product_id, base_size, limit_price,
                                    strategy, entry_price, reason)

        symbol = _normalize_symbol(product_id)
        try:
            order = self._client.order_limit_sell(
                symbol=symbol,
                quantity=round(base_size, 6),
                price=str(round(limit_price, 8)),
                timeInForce=TIME_IN_FORCE_GTC,
            )
            order_id = str(order.get('orderId', f'BN_{uuid.uuid4().hex[:8]}'))
            fee = limit_price * base_size * BINANCE_SPOT_MAKER_FEE_PCT

            self._open_positions.pop(product_id, None)

            log_trade(
                strategy=strategy, broker='binance_spot',
                symbol=product_id, action='SELL', order_type='LIMIT',
                qty=base_size, price=limit_price, fee_usd=fee, pnl_usd=pnl,
                paper=False, order_id=order_id,
                notes=f"reason={reason}"
            )
            alert_trade_closed(strategy, product_id, 'SELL', base_size,
                               entry_price, limit_price, pnl, reason)
            print(f"[BinanceSpotBroker] SELL LIMIT {base_size:.6f} {product_id} "
                  f"@ ${limit_price:,.4f} | P&L: ${pnl:+.2f} | {reason}")
            return True

        except Exception as e:
            print(f"[BinanceSpotBroker] sell_limit failed {product_id}: {e}")
            log_event('ERROR', 'BinanceSpotBroker', f"sell_limit {product_id}: {e}")
            return False

    def get_current_price(self, product_id: str) -> float:
        """
        Fetch current spot price. Uses cached Binance public REST.
        Falls back to yfinance if Binance is unavailable.
        Returns 0.0 on total failure.
        """
        price = _get_spot_price_cached(product_id)
        if price:
            return price

        # yfinance fallback
        try:
            import yfinance as yf
            symbol = _normalize_symbol(product_id)
            base = symbol.replace('USDC', '').replace('USDT', '')
            hist = yf.Ticker(f'{base}-USD').history(period='1d', interval='1m')
            if hist is not None and not hist.empty:
                return float(hist['Close'].iloc[-1])
        except Exception:
            pass
        return 0.0

    # ─── Position management ──────────────────────────────────────────────────

    def get_position(self, product_id: str) -> Optional[dict]:
        return self._open_positions.get(product_id)

    def get_all_positions(self) -> dict:
        return dict(self._open_positions)

    def update_position_high(self, product_id: str, current_price: float) -> None:
        if product_id in self._open_positions:
            self._open_positions[product_id]['high_since_entry'] = max(
                self._open_positions[product_id].get('high_since_entry', current_price),
                current_price,
            )

    # ─── Paper trading ────────────────────────────────────────────────────────

    def _paper_buy(self, product_id, base_size, price, strategy,
                   stop, target) -> bool:
        fee = price * base_size * BINANCE_SPOT_MAKER_FEE_PCT
        order_id = f'PAPER_{uuid.uuid4().hex[:8]}'

        self._open_positions[product_id] = {
            'qty': base_size,
            'entry': price,
            'stop': stop,
            'target': target,
            'high_since_entry': price,
            'order_id': order_id,
        }

        log_trade(
            strategy=strategy, broker='binance_spot_paper',
            symbol=product_id, action='BUY', order_type='LIMIT',
            qty=base_size, price=price, fee_usd=fee, paper=True,
            order_id=order_id,
            notes=f"SL={stop:.4f} TP={target:.4f}"
        )

        print(f"[PAPER] BUY {base_size:.6f} {product_id} @ ${price:,.4f} | fee=${fee:.3f}")
        alert_trade_opened(strategy, product_id, 'BUY', base_size, price, stop, target)
        return True

    def _paper_sell(self, product_id, base_size, price, strategy,
                    entry_price, reason) -> bool:
        fee = price * base_size * BINANCE_SPOT_MAKER_FEE_PCT
        pnl = (price - entry_price) * base_size if entry_price > 0 else 0.0

        self._open_positions.pop(product_id, None)

        log_trade(
            strategy=strategy, broker='binance_spot_paper',
            symbol=product_id, action='SELL', order_type='LIMIT',
            qty=base_size, price=price, fee_usd=fee, pnl_usd=pnl,
            paper=True, order_id=f'PAPER_{uuid.uuid4().hex[:8]}',
            notes=f"reason={reason}"
        )

        print(f"[PAPER] SELL {base_size:.6f} {product_id} @ ${price:,.4f} | P&L: ${pnl:+.2f}")
        alert_trade_closed(strategy, product_id, 'SELL', base_size, entry_price, price, pnl, reason)
        return True


# ─── Module-level singleton ───────────────────────────────────────────────────
_binance_spot_broker: Optional[BinanceSpotBroker] = None


def get_binance_spot_broker() -> BinanceSpotBroker:
    global _binance_spot_broker
    if _binance_spot_broker is None:
        _binance_spot_broker = BinanceSpotBroker()
    return _binance_spot_broker
