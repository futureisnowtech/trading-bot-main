"""
data/historical_data.py — v10 Historical OHLCV data layer.

Fetches and caches OHLCV candles for all timeframes needed by the indicator engine.
Stores in SQLite (price_archive.db) with incremental updates — only fetches missing bars.

Supported timeframes: 1m, 5m, 15m, 1h, 4h, 1d

Source routing (in priority order):
  PF_/PI_ prefix  → Kraken Futures REST (always)
  Short coin name → Hyperliquid FIRST (HEMI, LIT, SOL, HYPE, etc.)
                    Avoids yfinance returning stock/ETF prices for same ticker
  USDT/USDC suffix→ Binance futures/spot → Hyperliquid fallback → yfinance last

Coverage tracking:
  - Before hitting API, checks SQLite cache for >= 80% coverage
  - If yes: zero API calls, serve from cache
  - If no: fetch from appropriate source → store → return merged

Usage:
    from data.historical_data import get_candles
    df = get_candles('BTCUSDT', '5m', limit=200)   # Binance format
    df = get_candles('SOL', '1h', limit=100)        # Hyperliquid coin name
    df = get_candles('PF_SOLUSD', '1h', limit=100) # Kraken perp
"""

import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

try:
    import yfinance as yf
    _YF_OK = True
except ImportError:
    _YF_OK = False

# ── Config ────────────────────────────────────────────────────────────────────
_DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'logs', 'price_archive.db')
_BINANCE_FUTURES_BASE = 'https://fapi.binance.com'
_BINANCE_SPOT_BASE = 'https://api.binance.com'
_KRAKEN_CHARTS_BASE = 'https://futures.kraken.com/api/charts/v1/trade'
_COVERAGE_THRESHOLD = 0.80
_MAX_CANDLES_PER_REQUEST = 1500

# Timeframe → milliseconds per bar
_TF_MS = {
    '1m':  60_000,
    '5m':  300_000,
    '15m': 900_000,
    '1h':  3_600_000,
    '4h':  14_400_000,
    '1d':  86_400_000,
}

# yfinance interval mapping
_YF_INTERVAL = {
    '1m': '1m', '5m': '5m', '15m': '15m',
    '1h': '1h', '4h': '1h', '1d': '1d',
}

_lock = threading.RLock()

# Suffixes that indicate a Binance-format trading pair (quote currency appended).
# Symbols WITHOUT these suffixes are treated as Hyperliquid coin names and routed
# to Hyperliquid BEFORE yfinance — prevents yfinance returning stock/ETF prices
# for tickers like LIT (Lithium ETF), HEMI, REZ, ALT, etc.
_BINANCE_SUFFIXES = ('USDT', 'USDC', 'BUSD', 'BTC', 'ETH', 'BNB')

# Kraken interval names
_KRAKEN_INTERVAL = {
    '1m': '1m', '5m': '5m', '15m': '15m',
    '1h': '1h', '4h': '4h', '1d': '1d',
}


# ── Database setup ─────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    db_path = os.path.abspath(_DB_PATH)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS ohlcv (
            symbol      TEXT NOT NULL,
            timeframe   TEXT NOT NULL,
            open_time   INTEGER NOT NULL,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      REAL,
            PRIMARY KEY (symbol, timeframe, open_time)
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ohlcv_lookup ON ohlcv(symbol, timeframe, open_time)')
    conn.commit()
    return conn


# ── API fetching ──────────────────────────────────────────────────────────────

def _fetch_binance(symbol: str, interval: str, start_ms: int, limit: int) -> Optional[list]:
    """Fetch klines from Binance futures, fallback to spot."""
    if not _REQUESTS_OK:
        return None

    for base, endpoint in [
        (_BINANCE_FUTURES_BASE, '/fapi/v1/klines'),
        (_BINANCE_SPOT_BASE, '/api/v3/klines'),
    ]:
        try:
            params = {
                'symbol': symbol,
                'interval': interval,
                'startTime': start_ms,
                'limit': min(limit, _MAX_CANDLES_PER_REQUEST),
            }
            r = requests.get(base + endpoint, params=params, timeout=10)
            if r.status_code == 200:
                return r.json()
        except Exception:
            continue
    return None


def _fetch_kraken(symbol: str, interval: str, limit: int) -> Optional[pd.DataFrame]:
    """Fetch OHLCV from Kraken Futures charts API for PF_ symbols."""
    import urllib.request, json as _json
    kraken_interval = _KRAKEN_INTERVAL.get(interval, '1h')
    bar_seconds = _TF_MS.get(interval, 3_600_000) // 1000
    from_ts = int(time.time()) - (limit + 5) * bar_seconds
    url = f'{_KRAKEN_CHARTS_BASE}/{symbol}/{kraken_interval}?from={from_ts}'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'AlgoBot/1.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
        candles = data.get('candles', [])
        if not candles:
            return None
        rows = []
        for c in candles:
            # Kraken candle: {time (ms), open, high, low, close, volume} — all strings
            rows.append([
                int(c['time']),
                float(c['open']),
                float(c['high']),
                float(c['low']),
                float(c['close']),
                float(c.get('volume', 0)),
            ])
        _save_to_db(symbol, interval, rows)
        df = pd.DataFrame(rows, columns=['open_time', 'open', 'high', 'low', 'close', 'volume'])
        df['open_time'] = pd.to_datetime(df['open_time'].astype(int), unit='ms', utc=True)
        df = df.set_index('open_time')
        return df.tail(limit)
    except Exception as e:
        logger.debug(f'[historical_data] Kraken fetch failed for {symbol} {interval}: {e}')
        return None


def _fetch_yfinance(symbol: str, interval: str, limit: int) -> Optional[pd.DataFrame]:
    """yfinance fallback for symbols not on Binance futures."""
    if not _YF_OK:
        return None
    try:
        yf_interval = _YF_INTERVAL.get(interval, '5m')
        # Map BTCUSDT → BTC-USD for yfinance
        yf_sym = symbol.replace('USDT', '-USD').replace('USDC', '-USD')
        period_map = {'1m': '7d', '5m': '60d', '15m': '60d', '1h': '730d', '4h': '730d', '1d': 'max'}
        period = period_map.get(interval, '60d')
        ticker = yf.Ticker(yf_sym)
        df = ticker.history(period=period, interval=yf_interval, auto_adjust=True)
        if df.empty:
            return None
        df = df.rename(columns={'Open': 'open', 'High': 'high', 'Low': 'low',
                                 'Close': 'close', 'Volume': 'volume'})
        return df[['open', 'high', 'low', 'close', 'volume']].tail(limit)
    except Exception as e:
        logger.debug(f'[historical_data] yfinance fallback failed for {symbol}: {e}')
        return None


# ── Cache read/write ──────────────────────────────────────────────────────────

def _load_from_db(symbol: str, timeframe: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Load cached OHLCV rows from SQLite for the given time range."""
    with _lock:
        conn = _get_db()
        try:
            df = pd.read_sql_query(
                '''SELECT open_time, open, high, low, close, volume
                   FROM ohlcv
                   WHERE symbol=? AND timeframe=? AND open_time>=? AND open_time<?
                   ORDER BY open_time''',
                conn,
                params=(symbol, timeframe, start_ms, end_ms),
            )
        finally:
            conn.close()

    if df.empty:
        return df
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
    df = df.set_index('open_time')
    return df


def _save_to_db(symbol: str, timeframe: str, rows: list):
    """
    Upsert OHLCV rows to SQLite.
    rows: list of [open_time_ms, open, high, low, close, volume]
    """
    if not rows:
        return
    with _lock:
        conn = _get_db()
        try:
            conn.executemany(
                '''INSERT OR REPLACE INTO ohlcv
                   (symbol, timeframe, open_time, open, high, low, close, volume)
                   VALUES (?,?,?,?,?,?,?,?)''',
                [(symbol, timeframe, int(r[0]), float(r[1]), float(r[2]),
                  float(r[3]), float(r[4]), float(r[5])) for r in rows]
            )
            conn.commit()
        finally:
            conn.close()


def _fetch_hyperliquid(coin: str, timeframe: str, limit: int) -> Optional[pd.DataFrame]:
    """Fetch OHLCV from Hyperliquid candleSnapshot API. Final fallback for HL perp symbols."""
    import urllib.request as _hl_ur
    import json as _hl_js
    tf_secs = {
        '1m': 60, '5m': 300, '15m': 900, '30m': 1800,
        '1h': 3600, '4h': 14400, '1d': 86400,
    }
    bar_secs = tf_secs.get(timeframe, 3600)
    end_ms   = int(time.time() * 1000)
    start_ms = end_ms - (limit + 10) * bar_secs * 1000
    try:
        body = _hl_js.dumps({
            'type': 'candleSnapshot',
            'req':  {'coin': coin, 'interval': timeframe,
                     'startTime': start_ms, 'endTime': end_ms},
        }).encode('utf-8')
        req = _hl_ur.Request(
            'https://api.hyperliquid.xyz/info', data=body,
            headers={'Content-Type': 'application/json', 'User-Agent': 'AlgoBot/1.0'},
        )
        with _hl_ur.urlopen(req, timeout=8) as resp:
            data = _hl_js.loads(resp.read().decode('utf-8'))
        if not data or not isinstance(data, list):
            return None
        rows = []
        for k in data:
            try:
                ts_ms   = int(k.get('T', 0))
                close   = float(k.get('c', 0))
                vol_usd = float(k.get('v', 0)) * close   # coin vol × close price
                rows.append({'timestamp': ts_ms,
                             'open':   float(k.get('o', 0)),
                             'high':   float(k.get('h', 0)),
                             'low':    float(k.get('l', 0)),
                             'close':  close,
                             'volume': vol_usd})
            except Exception:
                continue
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df = df[['open', 'high', 'low', 'close', 'volume']].sort_index()
        df = df.tail(limit) if len(df) >= 5 else None
        if df is not None:
            # Cache HL data under the coin name so subsequent calls hit SQLite
            cache_rows = []
            for ts, row in df.iterrows():
                ts_ms = int(ts.timestamp() * 1000)
                cache_rows.append([ts_ms, row['open'], row['high'],
                                   row['low'], row['close'], row['volume']])
            _save_to_db(coin, timeframe, cache_rows)
        return df
    except Exception as e:
        logger.debug(f'[historical_data] Hyperliquid fetch failed for {coin}: {e}')
        return None


# ── Main public API ──────────────────────────────────────────────────────────

def get_candles(symbol: str, timeframe: str = '5m', limit: int = 200) -> pd.DataFrame:
    """
    Return a DataFrame of OHLCV candles for symbol/timeframe.

    Routing:
      PF_/PI_  → Kraken Futures only
      No quote suffix (SOL, HEMI, HYPE, LIT …) → cache → Hyperliquid → Binance → yfinance
      USDT/USDC/… suffix (SOLUSDT …)            → cache → Binance → Hyperliquid → yfinance

    Returns DataFrame with DatetimeIndex (UTC) and columns: open, high, low, close, volume.
    Returns empty DataFrame on failure (never raises).
    """
    if timeframe not in _TF_MS:
        logger.error(f'[historical_data] Unknown timeframe: {timeframe}')
        return pd.DataFrame()

    # ── 1. Kraken Futures (PF_/PI_) ──────────────────────────────────────────
    if symbol.startswith('PF_') or symbol.startswith('PI_'):
        df = _fetch_kraken(symbol, timeframe, limit)
        if df is not None and not df.empty:
            return df
        logger.warning(f'[historical_data] Failed to fetch {symbol} {timeframe}')
        return pd.DataFrame()

    bar_ms   = _TF_MS[timeframe]
    now_ms   = int(time.time() * 1000)
    start_ms = now_ms - (limit * bar_ms)

    # ── 2. SQLite cache (shared by all sources) ───────────────────────────────
    cached   = _load_from_db(symbol, timeframe, start_ms, now_ms)
    coverage = len(cached) / limit if limit > 0 else 0
    if coverage >= _COVERAGE_THRESHOLD:
        return cached.tail(limit)

    # ── 3. Route by symbol format ─────────────────────────────────────────────
    is_binance_fmt = symbol.upper().endswith(_BINANCE_SUFFIXES)

    if not is_binance_fmt:
        # Short coin name (HEMI, LIT, SOL, HYPE, FARTCOIN …)
        # Hyperliquid FIRST — prevents yfinance returning stock/ETF prices
        hl_df = _fetch_hyperliquid(symbol, timeframe, limit)
        if hl_df is not None and not hl_df.empty:
            logger.debug(f'[historical_data] HL OK {symbol} {timeframe} ({len(hl_df)} bars)')
            return hl_df

        # HL failed — try Binance (unlikely to work for short names, but cheap attempt)
        df = _fetch_and_store(symbol, timeframe, start_ms, limit, bar_ms, now_ms)
        if df is not None and not df.empty:
            return df.tail(limit)
    else:
        # Binance-format pair (SOLUSDT, BTCUSDT …)
        df = _fetch_and_store(symbol, timeframe, start_ms, limit, bar_ms, now_ms)
        if df is not None and not df.empty:
            return df.tail(limit)

        # Binance failed (geo-block) — try Hyperliquid with base coin name
        base = symbol.rstrip('0123456789')
        for suffix in _BINANCE_SUFFIXES:
            if base.endswith(suffix):
                base = base[:-len(suffix)]
                break
        if base and base != symbol:
            hl_df = _fetch_hyperliquid(base, timeframe, limit)
            if hl_df is not None and not hl_df.empty:
                logger.debug(f'[historical_data] HL fallback OK {symbol}→{base} {timeframe}')
                return hl_df

    # ── 4. Return partial cache if anything exists ────────────────────────────
    if not cached.empty:
        logger.debug(f'[historical_data] Partial cache ({len(cached)}/{limit}) for {symbol}')
        return cached.tail(limit)

    # ── 5. yfinance — truly last resort (stock/ETF conflicts possible) ────────
    yf_df = _fetch_yfinance(symbol, timeframe, limit)
    if yf_df is not None and not yf_df.empty:
        logger.debug(f'[historical_data] yfinance last-resort OK for {symbol}')
        return yf_df

    logger.warning(f'[historical_data] All sources failed for {symbol} {timeframe}')
    return pd.DataFrame()


def _fetch_and_store(symbol: str, timeframe: str, start_ms: int,
                     limit: int, bar_ms: int, now_ms: int) -> Optional[pd.DataFrame]:
    """Fetch from Binance (or yfinance), store to DB, return DataFrame."""
    raw = _fetch_binance(symbol, timeframe, start_ms, limit)

    if raw:
        # Binance kline format: [open_time, open, high, low, close, volume, ...]
        rows = [[r[0], r[1], r[2], r[3], r[4], r[5]] for r in raw]
        _save_to_db(symbol, timeframe, rows)

        df = pd.DataFrame(rows, columns=['open_time', 'open', 'high', 'low', 'close', 'volume'])
        df['open_time'] = pd.to_datetime(df['open_time'].astype(int), unit='ms', utc=True)
        df = df.set_index('open_time')
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        return df

    # yfinance fallback
    df = _fetch_yfinance(symbol, timeframe, limit)
    if df is not None and not df.empty:
        # Store yfinance data to DB too
        rows_yf = []
        for ts, row in df.iterrows():
            ts_ms = int(ts.timestamp() * 1000) if hasattr(ts, 'timestamp') else int(ts) * 1000
            rows_yf.append([ts_ms, row['open'], row['high'], row['low'], row['close'], row['volume']])
        _save_to_db(symbol, timeframe, rows_yf)
        return df

    logger.warning(f'[historical_data] Failed to fetch {symbol} {timeframe}')
    return None


def prefetch_symbols(symbols: list, timeframes: list = None):
    """
    Pre-warm the cache for a list of symbols and timeframes.
    Call at startup to avoid cold-cache latency on first scan.
    """
    if timeframes is None:
        timeframes = ['5m', '15m', '1h']

    logger.info(f'[historical_data] Prefetching {len(symbols)} symbols × {len(timeframes)} timeframes')
    for sym in symbols:
        for tf in timeframes:
            try:
                get_candles(sym, tf, limit=200)
            except Exception as e:
                logger.debug(f'[historical_data] Prefetch failed {sym} {tf}: {e}')
            time.sleep(0.05)  # avoid rate limit
    logger.info('[historical_data] Prefetch complete')
