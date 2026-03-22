"""
data/coinbase_feed.py
Real-time crypto data via Coinbase Advanced Trade API.
WebSocket for live ticks, REST for candles.
Official free API — no cost.
"""
import json
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Optional
import pandas as pd

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    COINBASE_API_KEY, COINBASE_API_SECRET,
    CRYPTO_PAIRS, CRYPTO_CANDLE_GRANULARITY
)

try:
    from coinbase.rest import RESTClient
    from coinbase.websocket import WSClient
    COINBASE_AVAILABLE = True
except ImportError:
    COINBASE_AVAILABLE = False
    print("[coinbase_feed] coinbase-advanced-py not installed. Run: pip install coinbase-advanced-py")


# ─── REST client singleton ────────────────────────────────────────────────────

_rest_client: Optional['RESTClient'] = None

def get_rest_client() -> Optional['RESTClient']:
    global _rest_client
    if not COINBASE_AVAILABLE:
        return None
    if _rest_client is None:
        if not COINBASE_API_KEY or not COINBASE_API_SECRET:
            print("[coinbase_feed] API credentials not set in .env")
            return None
        try:
            _rest_client = RESTClient(
                api_key=COINBASE_API_KEY,
                api_secret=COINBASE_API_SECRET
            )
        except Exception as e:
            print(f"[coinbase_feed] Failed to create REST client: {e}")
            return None
    return _rest_client


# ─── Candles ─────────────────────────────────────────────────────────────────

GRANULARITY_MAP = {
    'ONE_MINUTE': 60,
    'FIVE_MINUTE': 300,
    'FIFTEEN_MINUTE': 900,
    'ONE_HOUR': 3600,
    'SIX_HOUR': 21600,
    'ONE_DAY': 86400,
}


def get_candles(
    product_id: str = 'BTC-USDC',
    granularity: str = 'FIVE_MINUTE',
    lookback_bars: int = 100
) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV candles from Coinbase.
    Returns a pandas DataFrame with lowercase column names.
    """
    client = get_rest_client()
    if client is None:
        return None

    seconds_per_bar = GRANULARITY_MAP.get(granularity, 300)
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(seconds=seconds_per_bar * lookback_bars)

    try:
        response = client.get_candles(
            product_id=product_id,
            start=str(int(start_time.timestamp())),
            end=str(int(end_time.timestamp())),
            granularity=granularity
        )

        candles = response.candles if hasattr(response, 'candles') else []
        if not candles:
            return None

        records = []
        for c in candles:
            records.append({
                'timestamp': pd.to_datetime(int(c.start), unit='s', utc=True),
                'open': float(c.open),
                'high': float(c.high),
                'low': float(c.low),
                'close': float(c.close),
                'volume': float(c.volume),
            })

        df = pd.DataFrame(records)
        df = df.sort_values('timestamp').set_index('timestamp')
        return df

    except Exception as e:
        print(f"[coinbase_feed] Error fetching candles for {product_id}: {e}")
        return None


def get_current_price(product_id: str) -> Optional[float]:
    """Get last trade price for a product."""
    client = get_rest_client()
    if client is None:
        return None
    try:
        ticker = client.get_best_bid_ask(product_ids=[product_id])
        if hasattr(ticker, 'pricebooks') and ticker.pricebooks:
            pb = ticker.pricebooks[0]
            if pb.asks:
                return float(pb.asks[0].price)
        return None
    except Exception as e:
        print(f"[coinbase_feed] Error getting price for {product_id}: {e}")
        return None


def get_account_balance(currency: str = 'USDC') -> float:
    """Get available balance for a currency in Coinbase."""
    client = get_rest_client()
    if client is None:
        return 0.0
    try:
        accounts = client.get_accounts()
        for acct in accounts.accounts:
            if acct.currency == currency:
                bal = acct.available_balance
                val = bal['value'] if isinstance(bal, dict) else bal.value
                return float(val)
        return 0.0
    except Exception as e:
        print(f"[coinbase_feed] Error getting balance for {currency}: {e}")
        return 0.0


# ─── WebSocket feed ───────────────────────────────────────────────────────────

class CoinbaseTickerFeed:
    """
    Real-time ticker feed via Coinbase WebSocket.
    Calls on_tick(product_id, price, volume) on every price update.
    """

    def __init__(self, on_tick: Optional[Callable] = None):
        self.on_tick = on_tick
        self._ws: Optional['WSClient'] = None
        self._prices: dict = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self, product_ids: Optional[list] = None) -> None:
        if not COINBASE_AVAILABLE:
            print("[CoinbaseTickerFeed] coinbase-advanced-py not installed")
            return
        if not COINBASE_API_KEY:
            print("[CoinbaseTickerFeed] No API credentials — can't start WebSocket")
            return

        product_ids = product_ids or CRYPTO_PAIRS
        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            args=(product_ids,),
            daemon=True
        )
        self._thread.start()
        print(f"[CoinbaseTickerFeed] Started for {product_ids}")

    def stop(self) -> None:
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def get_price(self, product_id: str) -> Optional[float]:
        return self._prices.get(product_id)

    def _on_message(self, msg: str) -> None:
        try:
            data = json.loads(msg) if isinstance(msg, str) else msg
            channel = data.get('channel', '')
            events = data.get('events', [])

            if channel == 'ticker':
                for event in events:
                    for tick in event.get('tickers', []):
                        pid = tick.get('product_id', '')
                        price = float(tick.get('price', 0))
                        if price > 0:
                            self._prices[pid] = price
                            if self.on_tick:
                                self.on_tick(
                                    product_id=pid,
                                    price=price,
                                    volume=float(tick.get('volume_24_h', 0))
                                )
        except Exception as e:
            print(f"[CoinbaseTickerFeed] Message parse error: {e}")

    def _run(self, product_ids: list) -> None:
        while self._running:
            try:
                self._ws = WSClient(
                    api_key=COINBASE_API_KEY,
                    api_secret=COINBASE_API_SECRET,
                    on_message=self._on_message
                )
                self._ws.open()
                self._ws.subscribe(
                    product_ids=product_ids,
                    channels=['ticker']
                )
                # Run until disconnected
                while self._running:
                    time.sleep(1)

            except Exception as e:
                print(f"[CoinbaseTickerFeed] WebSocket error: {e} — reconnecting in 5s")
                time.sleep(5)


# ─── Module-level feed instance (shared across strategies) ───────────────────
_global_feed: Optional[CoinbaseTickerFeed] = None


def get_global_feed(on_tick: Optional[Callable] = None) -> CoinbaseTickerFeed:
    global _global_feed
    if _global_feed is None:
        _global_feed = CoinbaseTickerFeed(on_tick=on_tick)
    return _global_feed
