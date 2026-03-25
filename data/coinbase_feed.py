"""
data/coinbase_feed.py
Real-time crypto data via Coinbase Advanced Trade API.
WebSocket for live ticks, REST for candles.
Official free API — no cost.
"""
import collections
import json
import math
import threading
import time
from datetime import datetime, timedelta, timezone
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
    end_time = datetime.now(timezone.utc)
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


def get_historical_candles(
    product_id: str = 'BTC-USDC',
    granularity: str = 'FIVE_MINUTE',
    days: int = 90,
    use_cache: bool = True,
) -> Optional[pd.DataFrame]:
    """
    Fetch months of OHLCV history from Coinbase with automatic pagination.
    Coinbase caps at 350 candles per request, so we page backwards in chunks.
    Results are cached to logs/cache/ as parquet so repeated backtest runs
    don't re-hit the API.

    Returns a DataFrame sorted ascending by timestamp, columns:
        open, high, low, close, volume
    """
    cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'logs', 'cache')
    os.makedirs(cache_dir, exist_ok=True)

    seconds_per_bar = GRANULARITY_MAP.get(granularity, 300)
    max_bars_per_req = 300  # stay under the 350 hard limit
    total_bars = int(days * 86400 / seconds_per_bar)

    # Cache key: product + granularity + requested days, refreshed daily
    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    cache_file = os.path.join(
        cache_dir,
        f"{product_id.replace('-', '_')}_{granularity}_{days}d_{today_str}.parquet"
    )

    if use_cache and os.path.exists(cache_file):
        try:
            df = pd.read_parquet(cache_file)
            print(f"[coinbase_feed] Loaded {len(df)} candles from cache ({cache_file})")
            return df
        except Exception as e:
            print(f"[coinbase_feed] Cache read failed: {e} — re-fetching")

    client = get_rest_client()
    if client is None:
        print("[coinbase_feed] No Coinbase client — cannot fetch historical data")
        return None

    all_records: list = []
    end_time = datetime.now(timezone.utc)
    bars_remaining = total_bars

    print(f"[coinbase_feed] Fetching {total_bars} {granularity} candles for {product_id} "
          f"({days} days, {max_bars_per_req} per request)…")

    while bars_remaining > 0:
        chunk = min(bars_remaining, max_bars_per_req)
        start_time = end_time - timedelta(seconds=seconds_per_bar * chunk)

        try:
            response = client.get_candles(
                product_id=product_id,
                start=str(int(start_time.timestamp())),
                end=str(int(end_time.timestamp())),
                granularity=granularity,
            )
            candles = response.candles if hasattr(response, 'candles') else []
        except Exception as e:
            print(f"[coinbase_feed] Fetch error at chunk (remaining={bars_remaining}): {e}")
            break

        if not candles:
            break

        for c in candles:
            all_records.append({
                'timestamp': pd.to_datetime(int(c.start), unit='s', utc=True),
                'open':   float(c.open),
                'high':   float(c.high),
                'low':    float(c.low),
                'close':  float(c.close),
                'volume': float(c.volume),
            })

        bars_remaining -= len(candles)
        end_time = start_time
        time.sleep(0.15)   # ~6 req/s, Coinbase rate limit is 10/s

    if not all_records:
        print(f"[coinbase_feed] No historical data returned for {product_id}")
        return None

    df = pd.DataFrame(all_records)
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').set_index('timestamp')

    # Persist to cache
    if use_cache:
        try:
            df.to_parquet(cache_file)
            print(f"[coinbase_feed] Cached {len(df)} candles → {cache_file}")
        except Exception as e:
            print(f"[coinbase_feed] Cache write failed: {e}")

    print(f"[coinbase_feed] Historical fetch complete: {len(df)} candles for {product_id}")
    return df


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


# ─── Microstructure Feed — OBI / TFI / Microprice / Spread ───────────────────

class CoinbaseMicrostructureFeed:
    """
    Real-time microstructure signals via Coinbase WebSocket.

    Subscribes to:
      - level2    → maintains top-of-book (best bid/ask qty) per product
      - market_trades → rolling 60-second trade buffer for TFI

    Computes on demand (get_microstructure):
      OBI   = (bid_qty - ask_qty) / (bid_qty + ask_qty)
      TFI   = (buy_vol - sell_vol) / (buy_vol + sell_vol) over last 60 s
              Note: Coinbase side field = MAKER side, so side='SELL' = taker BUY
      microprice = (ask_price×bid_qty + bid_price×ask_qty) / (bid_qty + ask_qty)
      microprice_premium_bps = (microprice - midprice) / midprice × 10000
      spread_bps = (ask - bid) / midprice × 10000
    """
    _TRADE_WINDOW_SEC = 60  # rolling window for TFI

    _STALE_THRESHOLD_SEC = 90  # return None if no update in 90 seconds

    def __init__(self):
        # Per-symbol top-of-book snapshot
        # {'BTC-USDC': {'bid': price, 'bid_qty': qty, 'ask': price, 'ask_qty': qty}}
        self._book: dict = {}
        # Per-symbol trade deque: [(ts_float, side_str, size_float), ...]
        self._trades: dict = collections.defaultdict(collections.deque)
        # Per-symbol last-update timestamp — staleness guard
        self._last_update: dict = {}
        self._lock = threading.Lock()
        self._ws: Optional['WSClient'] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._product_ids: list = []

    def start(self, product_ids: Optional[list] = None) -> None:
        if not COINBASE_AVAILABLE:
            print("[MicrostructureFeed] coinbase-advanced-py not installed")
            return
        if not COINBASE_API_KEY:
            print("[MicrostructureFeed] No API credentials — skipping")
            return
        self._product_ids = product_ids or CRYPTO_PAIRS
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"[MicrostructureFeed] Started for {self._product_ids}")

    def stop(self) -> None:
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def get_microstructure(self, product_id: str) -> dict:
        """Return OBI, TFI, microprice_premium_bps, spread_bps or None if no/stale data."""
        _null = {'obi': None, 'tfi': None, 'microprice_premium_bps': None, 'spread_bps': None}
        with self._lock:
            # Staleness check — if WebSocket died silently, don't serve stale numbers
            last_ts = self._last_update.get(product_id, 0)
            if time.time() - last_ts > self._STALE_THRESHOLD_SEC:
                return _null
            book = self._book.get(product_id)
            trades = list(self._trades.get(product_id, []))

        if not book:
            return _null

        bid_p = book.get('bid', 0)
        ask_p = book.get('ask', 0)
        bid_q = book.get('bid_qty', 0)
        ask_q = book.get('ask_qty', 0)

        result = {'obi': None, 'tfi': None, 'microprice_premium_bps': None, 'spread_bps': None}

        # OBI + microprice (require both sides)
        total_q = bid_q + ask_q
        if bid_p > 0 and ask_p > 0 and total_q > 0:
            result['obi'] = (bid_q - ask_q) / total_q
            microprice = (ask_p * bid_q + bid_p * ask_q) / total_q
            midprice = (bid_p + ask_p) / 2.0
            result['microprice_premium_bps'] = (microprice - midprice) / midprice * 10_000
            result['spread_bps'] = (ask_p - bid_p) / midprice * 10_000

        # TFI from rolling 60-second trade window
        cutoff = time.time() - self._TRADE_WINDOW_SEC
        buy_vol = sell_vol = 0.0
        for ts, side, size in trades:
            if ts >= cutoff:
                # Coinbase side = MAKER side. side='SELL' means maker sold = taker BUY
                if side == 'SELL':
                    buy_vol += size
                else:
                    sell_vol += size
        total_vol = buy_vol + sell_vol
        if total_vol > 0:
            result['tfi'] = (buy_vol - sell_vol) / total_vol

        return result

    def _on_message(self, msg: str) -> None:
        try:
            data = json.loads(msg) if isinstance(msg, str) else msg
            channel = data.get('channel', '')
            events = data.get('events', [])

            if channel == 'l2_data':
                for event in events:
                    pid = event.get('product_id', '')
                    etype = event.get('type', '')
                    updates = event.get('updates', [])
                    with self._lock:
                        if pid not in self._book:
                            self._book[pid] = {}
                        self._last_update[pid] = time.time()  # mark fresh
                        book = self._book[pid]

                        if etype == 'snapshot':
                            # Reset bids/asks from snapshot
                            best_bid_p = best_bid_q = best_ask_p = best_ask_q = 0.0
                            for u in updates:
                                side = u.get('side', '')
                                p = float(u.get('price_level', 0) or 0)
                                q = float(u.get('new_quantity', 0) or 0)
                                if q > 0:
                                    if side == 'bid':
                                        if p > best_bid_p:
                                            best_bid_p, best_bid_q = p, q
                                    elif side == 'offer':
                                        if best_ask_p == 0 or p < best_ask_p:
                                            best_ask_p, best_ask_q = p, q
                            if best_bid_p > 0:
                                book['bid'] = best_bid_p
                                book['bid_qty'] = best_bid_q
                            if best_ask_p > 0:
                                book['ask'] = best_ask_p
                                book['ask_qty'] = best_ask_q

                        elif etype == 'update':
                            # Apply incremental updates
                            for u in updates:
                                side = u.get('side', '')
                                p = float(u.get('price_level', 0) or 0)
                                q = float(u.get('new_quantity', 0) or 0)
                                if side == 'bid':
                                    cur_bid = book.get('bid', 0)
                                    cur_bid_q = book.get('bid_qty', 0)
                                    if q == 0 and p == cur_bid:
                                        # Top bid removed — we lost best bid, can't recover without full book
                                        book.pop('bid', None)
                                        book.pop('bid_qty', None)
                                    elif q > 0 and (p > cur_bid or cur_bid == 0):
                                        book['bid'] = p
                                        book['bid_qty'] = q
                                    elif q > 0 and p == cur_bid:
                                        book['bid_qty'] = q
                                elif side == 'offer':
                                    cur_ask = book.get('ask', 0)
                                    cur_ask_q = book.get('ask_qty', 0)
                                    if q == 0 and p == cur_ask:
                                        book.pop('ask', None)
                                        book.pop('ask_qty', None)
                                    elif q > 0 and (cur_ask == 0 or p < cur_ask):
                                        book['ask'] = p
                                        book['ask_qty'] = q
                                    elif q > 0 and p == cur_ask:
                                        book['ask_qty'] = q

            elif channel == 'market_trades':
                for event in events:
                    for trade in event.get('trades', []):
                        pid = trade.get('product_id', '')
                        side = trade.get('side', '')
                        size = float(trade.get('size', 0) or 0)
                        if pid and size > 0:
                            with self._lock:
                                dq = self._trades[pid]
                                now = time.time()
                                dq.append((now, side, size))
                                self._last_update[pid] = now  # mark fresh
                                # Trim stale entries (older than 5 minutes)
                                cutoff = time.time() - 300
                                while dq and dq[0][0] < cutoff:
                                    dq.popleft()

        except Exception as e:
            print(f"[MicrostructureFeed] Message parse error: {e}")

    def _run(self) -> None:
        while self._running:
            try:
                self._ws = WSClient(
                    api_key=COINBASE_API_KEY,
                    api_secret=COINBASE_API_SECRET,
                    on_message=self._on_message
                )
                self._ws.open()
                self._ws.subscribe(
                    product_ids=self._product_ids,
                    channels=['level2', 'market_trades']
                )
                _watchdog_ticks = 0
                while self._running:
                    time.sleep(1)
                    _watchdog_ticks += 1
                    # Every 30s, check if the connection is actually alive.
                    # If ALL subscribed products have been stale for >3× threshold,
                    # the WebSocket died silently (no exception) — force reconnect.
                    if _watchdog_ticks >= 30 and self._product_ids:
                        _watchdog_ticks = 0
                        now = time.time()
                        all_stale = all(
                            now - self._last_update.get(pid, 0) > self._STALE_THRESHOLD_SEC * 2
                            for pid in self._product_ids
                        )
                        if all_stale:
                            print("[MicrostructureFeed] Watchdog: all products stale — silent disconnect, reconnecting")
                            break  # break inner loop → outer loop reconnects
            except Exception as e:
                print(f"[MicrostructureFeed] WebSocket error: {e} — reconnecting in 10s")
                time.sleep(10)


# ─── Module-level microstructure feed singleton ───────────────────────────────
_global_microstructure: Optional[CoinbaseMicrostructureFeed] = None


def get_microstructure_feed(product_ids: Optional[list] = None) -> CoinbaseMicrostructureFeed:
    global _global_microstructure
    if _global_microstructure is None:
        _global_microstructure = CoinbaseMicrostructureFeed()
        _global_microstructure.start(product_ids)
    return _global_microstructure
