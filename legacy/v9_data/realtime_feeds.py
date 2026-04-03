"""
data/realtime_feeds.py — v10 Real-time WebSocket data layer.

Manages persistent WebSocket connections to Binance Futures:
  - aggTrade stream:   tick-level trade data (buy/sell pressure, CVD)
  - bookTicker stream: best bid/ask in real-time
  - markPrice stream:  mark price + funding rate (every 3s)
  - forceOrder stream: liquidation events

Features:
  - Auto-reconnect with exponential backoff (1s → 30s max)
  - In-memory ring buffers (thread-safe) for each stream type
  - Publishes to internal event bus for consuming modules
  - Latency monitoring: warns if exchange_ts → local_ts > 2s
  - Graceful shutdown via stop()

Usage:
    from data.realtime_feeds import RealtimeFeeds
    feeds = RealtimeFeeds(['BTCUSDT', 'ETHUSDT'])
    feeds.start()
    # Anywhere in codebase:
    book = feeds.get_book('BTCUSDT')   # {'bid': ..., 'ask': ..., 'spread_pct': ...}
    mark = feeds.get_mark('BTCUSDT')   # {'mark_price': ..., 'funding_rate': ..., ...}
    trades = feeds.get_recent_trades('BTCUSDT', seconds=60)
    feeds.stop()
"""

import json
import logging
import threading
import time
from collections import defaultdict, deque
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import websocket
    _WS_OK = True
except ImportError:
    _WS_OK = False
    logger.warning('[realtime_feeds] websocket-client not installed — real-time feeds disabled')

# ── Constants ─────────────────────────────────────────────────────────────────
_WS_BASE = 'wss://fstream.binance.com/stream'
_WS_TESTNET_BASE = 'wss://stream.binancefuture.com/stream'
_RECONNECT_MIN = 1
_RECONNECT_MAX = 30
_LATENCY_WARN_S = 2.0
_TRADE_BUFFER_SIZE = 5000     # trades per symbol
_BOOK_BUFFER_SIZE = 1         # only latest book snapshot needed
_LATENCY_SAMPLE_SIZE = 100    # rolling latency samples


class RealtimeFeeds:
    """
    Manages all Binance Futures WebSocket streams for a given set of symbols.
    Thread-safe. One WebSocket connection per combined stream URL (up to 200 streams).
    """

    def __init__(self, symbols: List[str], testnet: bool = False):
        """
        Args:
            symbols: List of Binance futures symbols e.g. ['BTCUSDT', 'ETHUSDT']
            testnet: Use testnet WebSocket endpoint
        """
        self._symbols = [s.upper() for s in symbols]
        self._base = _WS_TESTNET_BASE if testnet else _WS_BASE
        self._running = False

        # Per-symbol ring buffers (thread-safe via lock)
        self._lock = threading.RLock()
        self._trades: Dict[str, deque] = defaultdict(lambda: deque(maxlen=_TRADE_BUFFER_SIZE))
        self._book: Dict[str, dict] = {}
        self._mark: Dict[str, dict] = {}
        self._liquidations: deque = deque(maxlen=500)

        # Latency monitoring
        self._latency_samples: deque = deque(maxlen=_LATENCY_SAMPLE_SIZE)
        self._latency_warn_count = 0

        # Event bus: consumers subscribe to stream types
        self._listeners: Dict[str, List[Callable]] = defaultdict(list)

        # WebSocket threads
        self._ws_threads: List[threading.Thread] = []
        self._ws_apps: List = []

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self):
        """Start all WebSocket streams in background threads."""
        if not _WS_OK:
            logger.error('[realtime_feeds] Cannot start: websocket-client not installed')
            return
        self._running = True
        self._start_combined_stream()
        logger.info(f'[realtime_feeds] Started for {len(self._symbols)} symbols')

    def stop(self):
        """Gracefully close all WebSocket connections."""
        self._running = False
        for app in self._ws_apps:
            try:
                app.close()
            except Exception:
                pass
        logger.info('[realtime_feeds] Stopped')

    def get_book(self, symbol: str) -> dict:
        """Return latest best bid/ask for symbol. Returns empty dict if not yet received."""
        with self._lock:
            return dict(self._book.get(symbol.upper(), {}))

    def get_mark(self, symbol: str) -> dict:
        """Return latest mark price + funding rate for symbol."""
        with self._lock:
            return dict(self._mark.get(symbol.upper(), {}))

    def get_recent_trades(self, symbol: str, seconds: int = 60) -> List[dict]:
        """Return trades for symbol from the last N seconds."""
        cutoff = time.time() - seconds
        with self._lock:
            buf = self._trades.get(symbol.upper(), deque())
            return [t for t in buf if t['local_ts'] >= cutoff]

    def get_recent_liquidations(self, seconds: int = 300) -> List[dict]:
        """Return liquidation events from the last N seconds."""
        cutoff = time.time() - seconds
        with self._lock:
            return [l for l in self._liquidations if l['local_ts'] >= cutoff]

    def get_latency_stats(self) -> dict:
        """Return p50/p95/p99 latency in ms and warn count."""
        with self._lock:
            samples = sorted(self._latency_samples)
        if not samples:
            return {'p50': None, 'p95': None, 'p99': None, 'warn_count': self._latency_warn_count}
        n = len(samples)
        return {
            'p50': samples[n // 2],
            'p95': samples[int(n * 0.95)],
            'p99': samples[int(n * 0.99)],
            'warn_count': self._latency_warn_count,
        }

    def subscribe(self, stream_type: str, callback: Callable):
        """
        Subscribe to a stream type for event-driven processing.
        stream_type: 'trade' | 'book' | 'mark' | 'liquidation'
        callback signature: callback(symbol: str, data: dict)
        """
        self._listeners[stream_type].append(callback)

    # ── WebSocket internals ────────────────────────────────────────────────────

    def _build_stream_names(self) -> List[str]:
        streams = []
        for sym in self._symbols:
            s = sym.lower()
            streams.append(f'{s}@aggTrade')
            streams.append(f'{s}@bookTicker')
            streams.append(f'{s}@markPrice@1s')
        # Global liquidation stream
        streams.append('!forceOrder@arr')
        return streams

    def _start_combined_stream(self):
        """
        Binance supports up to 200 streams in one combined WebSocket.
        If we have more symbols, chunk into multiple connections.
        """
        streams = self._build_stream_names()
        # Each symbol = 3 streams, plus 1 global = max ~66 symbols per connection
        chunk_size = 195
        for i in range(0, len(streams), chunk_size):
            chunk = streams[i:i + chunk_size]
            t = threading.Thread(
                target=self._run_ws,
                args=(chunk,),
                daemon=True,
                name=f'ws-feed-{i // chunk_size}'
            )
            self._ws_threads.append(t)
            t.start()

    def _run_ws(self, stream_names: List[str]):
        """Run a combined WebSocket with exponential backoff reconnect."""
        backoff = _RECONNECT_MIN
        url = f"{self._base}?streams={'/'.join(stream_names)}"

        while self._running:
            try:
                app = websocket.WebSocketApp(
                    url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._ws_apps.append(app)
                app.run_forever(ping_interval=20, ping_timeout=10)

                if not self._running:
                    break

                logger.warning(f'[realtime_feeds] WS disconnected, reconnecting in {backoff}s')
                time.sleep(backoff)
                backoff = min(backoff * 2, _RECONNECT_MAX)

            except Exception as e:
                logger.error(f'[realtime_feeds] WS error: {e}, reconnecting in {backoff}s')
                time.sleep(backoff)
                backoff = min(backoff * 2, _RECONNECT_MAX)

    def _on_message(self, ws, raw: str):
        try:
            msg = json.loads(raw)
            # Combined stream wraps payload: {"stream": "btcusdt@aggTrade", "data": {...}}
            stream = msg.get('stream', '')
            data = msg.get('data', msg)

            local_ts = time.time()

            if '@aggTrade' in stream:
                self._handle_trade(data, local_ts)
            elif '@bookTicker' in stream:
                self._handle_book(data, local_ts)
            elif '@markPrice' in stream:
                self._handle_mark(data, local_ts)
            elif 'forceOrder' in stream:
                self._handle_liquidation(data, local_ts)

        except Exception as e:
            logger.debug(f'[realtime_feeds] Message parse error: {e}')

    def _on_error(self, ws, error):
        logger.warning(f'[realtime_feeds] WS error: {error}')

    def _on_close(self, ws, code, msg):
        logger.debug(f'[realtime_feeds] WS closed: {code} {msg}')

    def _track_latency(self, exchange_ts_ms: float, local_ts: float):
        latency_ms = (local_ts * 1000) - exchange_ts_ms
        with self._lock:
            self._latency_samples.append(latency_ms)
        if latency_ms > _LATENCY_WARN_S * 1000:
            self._latency_warn_count += 1
            logger.warning(f'[realtime_feeds] High latency: {latency_ms:.0f}ms')

    def _handle_trade(self, data: dict, local_ts: float):
        symbol = data.get('s', '').upper()
        if not symbol:
            return

        trade = {
            'symbol':    symbol,
            'price':     float(data.get('p', 0)),
            'qty':       float(data.get('q', 0)),
            'is_sell':   bool(data.get('m', False)),  # m=True → maker buy = taker sell
            'trade_ts':  data.get('T', 0) / 1000,
            'local_ts':  local_ts,
        }

        if data.get('T'):
            self._track_latency(data['T'], local_ts)

        with self._lock:
            self._trades[symbol].append(trade)

        for cb in self._listeners.get('trade', []):
            try:
                cb(symbol, trade)
            except Exception:
                pass

    def _handle_book(self, data: dict, local_ts: float):
        symbol = data.get('s', '').upper()
        if not symbol:
            return

        bid = float(data.get('b', 0))
        ask = float(data.get('a', 0))
        spread_pct = ((ask - bid) / bid * 100) if bid > 0 else 0.0

        book = {
            'symbol':     symbol,
            'bid':        bid,
            'ask':        ask,
            'spread_pct': round(spread_pct, 4),
            'mid':        (bid + ask) / 2,
            'local_ts':   local_ts,
        }

        with self._lock:
            self._book[symbol] = book

        for cb in self._listeners.get('book', []):
            try:
                cb(symbol, book)
            except Exception:
                pass

    def _handle_mark(self, data: dict, local_ts: float):
        symbol = data.get('s', '').upper()
        if not symbol:
            return

        mark = {
            'symbol':       symbol,
            'mark_price':   float(data.get('p', 0)),
            'index_price':  float(data.get('i', 0)),
            'funding_rate': float(data.get('r', 0)),
            'next_funding_ts': data.get('T', 0) / 1000,
            'local_ts':     local_ts,
        }

        with self._lock:
            self._mark[symbol] = mark

        for cb in self._listeners.get('mark', []):
            try:
                cb(symbol, mark)
            except Exception:
                pass

    def _handle_liquidation(self, data: dict, local_ts: float):
        order = data.get('o', data)
        liq = {
            'symbol':    order.get('s', '').upper(),
            'side':      order.get('S', ''),     # BUY or SELL
            'price':     float(order.get('p', 0)),
            'qty':       float(order.get('q', 0)),
            'value_usd': float(order.get('p', 0)) * float(order.get('q', 0)),
            'local_ts':  local_ts,
        }

        with self._lock:
            self._liquidations.append(liq)

        for cb in self._listeners.get('liquidation', []):
            try:
                cb(liq['symbol'], liq)
            except Exception:
                pass


# ── Module-level singleton ─────────────────────────────────────────────────────
_instance: Optional[RealtimeFeeds] = None
_instance_lock = threading.Lock()


def get_feeds(symbols: Optional[List[str]] = None, testnet: bool = False) -> RealtimeFeeds:
    """
    Returns the module-level singleton RealtimeFeeds instance.
    Creates and starts it on first call with the given symbols.
    Subsequent calls ignore symbols/testnet (use stop()+new instance to change).
    """
    global _instance
    with _instance_lock:
        if _instance is None:
            if symbols is None:
                symbols = []
            _instance = RealtimeFeeds(symbols, testnet=testnet)
            if symbols:
                _instance.start()
    return _instance
