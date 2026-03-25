"""
data/price_archive.py — Local price data flywheel.

Every live candle fetch automatically writes to this archive.
Backtests check here first; API only called for missing ranges.
Grows organically — zero maintenance required.

Storage: logs/price_archive.db (separate from trades.db to keep it lean)
Schema:  candles(symbol, timeframe, ts_unix INTEGER, o, h, l, c, vol)
         unique index on (symbol, timeframe, ts_unix)
"""
import os
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
import pandas as pd

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE_DB = os.path.join(_HERE, 'logs', 'price_archive.db')


# ── DB bootstrap ──────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(ARCHIVE_DB), exist_ok=True)
    c = sqlite3.connect(ARCHIVE_DB, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


def _init():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS candles (
                symbol    TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                ts_unix   INTEGER NOT NULL,
                open      REAL NOT NULL,
                high      REAL NOT NULL,
                low       REAL NOT NULL,
                close     REAL NOT NULL,
                volume    REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (symbol, timeframe, ts_unix)
            )
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_candles_lookup
            ON candles (symbol, timeframe, ts_unix)
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS archive_meta (
                symbol    TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                first_ts  INTEGER,
                last_ts   INTEGER,
                row_count INTEGER DEFAULT 0,
                updated   TEXT,
                PRIMARY KEY (symbol, timeframe)
            )
        """)


_init()


# ── Write ─────────────────────────────────────────────────────────────────────

def upsert_candles(df: pd.DataFrame, symbol: str, timeframe: str) -> int:
    """
    Write a DataFrame of candles to the archive.
    Expects columns: open, high, low, close, volume (and DatetimeIndex or 'timestamp' col).
    Returns number of rows upserted.
    Returns 0 silently if df is empty or malformed.
    """
    if df is None or df.empty:
        return 0
    try:
        work = df.copy()
        # Normalise index to unix timestamps
        if isinstance(work.index, pd.DatetimeIndex):
            work['_ts'] = (work.index.astype('int64') // 10**9).astype(int)
        elif 'timestamp' in work.columns:
            work['_ts'] = pd.to_datetime(work['timestamp']).astype('int64') // 10**9
        else:
            return 0

        col_map = {c.lower(): c for c in work.columns}
        rows = []
        for _, row in work.iterrows():
            try:
                rows.append((
                    symbol, timeframe, int(row['_ts']),
                    float(row.get('open',   row.get('Open',   0))),
                    float(row.get('high',   row.get('High',   0))),
                    float(row.get('low',    row.get('Low',    0))),
                    float(row.get('close',  row.get('Close',  0))),
                    float(row.get('volume', row.get('Volume', 0))),
                ))
            except Exception:
                continue

        if not rows:
            return 0

        with _conn() as c:
            c.executemany("""
                INSERT OR REPLACE INTO candles
                    (symbol, timeframe, ts_unix, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            # Update meta
            ts_vals = [r[2] for r in rows]
            c.execute("""
                INSERT INTO archive_meta (symbol, timeframe, first_ts, last_ts, row_count, updated)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe) DO UPDATE SET
                    first_ts  = MIN(first_ts,  excluded.first_ts),
                    last_ts   = MAX(last_ts,   excluded.last_ts),
                    row_count = (SELECT COUNT(*) FROM candles
                                 WHERE symbol=excluded.symbol AND timeframe=excluded.timeframe),
                    updated   = excluded.updated
            """, (symbol, timeframe, min(ts_vals), max(ts_vals), len(rows),
                  datetime.now(timezone.utc).isoformat()))
        return len(rows)
    except Exception as e:
        print(f"[price_archive] upsert error {symbol}/{timeframe}: {e}")
        return 0


# ── Read ──────────────────────────────────────────────────────────────────────

def get_candles(
    symbol: str,
    timeframe: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> Optional[pd.DataFrame]:
    """
    Retrieve candles from archive.
    Returns DataFrame with DatetimeIndex (UTC) and columns: open, high, low, close, volume.
    Returns None if no data found.
    """
    try:
        query = "SELECT ts_unix, open, high, low, close, volume FROM candles WHERE symbol=? AND timeframe=?"
        params: list = [symbol, timeframe]
        if start:
            query += " AND ts_unix >= ?"
            params.append(int(start.timestamp()))
        if end:
            query += " AND ts_unix <= ?"
            params.append(int(end.timestamp()))
        query += " ORDER BY ts_unix"
        if limit:
            query += f" LIMIT {int(limit)}"

        with _conn() as c:
            rows = c.execute(query, params).fetchall()

        if not rows:
            return None

        df = pd.DataFrame(rows, columns=['ts_unix', 'open', 'high', 'low', 'close', 'volume'])
        df.index = pd.to_datetime(df['ts_unix'], unit='s', utc=True)
        df.index.name = 'timestamp'
        return df.drop(columns=['ts_unix'])

    except Exception as e:
        print(f"[price_archive] read error {symbol}/{timeframe}: {e}")
        return None


def get_candles_tail(symbol: str, timeframe: str, n_bars: int) -> Optional[pd.DataFrame]:
    """Get the most recent n_bars candles from archive."""
    try:
        with _conn() as c:
            rows = c.execute("""
                SELECT ts_unix, open, high, low, close, volume
                FROM candles WHERE symbol=? AND timeframe=?
                ORDER BY ts_unix DESC LIMIT ?
            """, (symbol, timeframe, n_bars)).fetchall()
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=['ts_unix', 'open', 'high', 'low', 'close', 'volume'])
        df = df.sort_values('ts_unix')
        df.index = pd.to_datetime(df['ts_unix'], unit='s', utc=True)
        df.index.name = 'timestamp'
        return df.drop(columns=['ts_unix'])
    except Exception as e:
        print(f"[price_archive] tail error {symbol}/{timeframe}: {e}")
        return None


def has_data(symbol: str, timeframe: str,
             start: datetime, end: datetime,
             min_coverage: float = 0.70) -> bool:
    """
    True if the archive has ≥ min_coverage of expected bars in [start, end].
    Used by backtest engine to decide: use archive or fetch from API.
    """
    try:
        seconds_map = {'ONE_MINUTE': 60, 'FIVE_MINUTE': 300, 'ONE_HOUR': 3600, 'ONE_DAY': 86400}
        secs = seconds_map.get(timeframe, 60)
        expected = max(1, int((end - start).total_seconds() / secs))

        with _conn() as c:
            row = c.execute("""
                SELECT COUNT(*) FROM candles
                WHERE symbol=? AND timeframe=? AND ts_unix BETWEEN ? AND ?
            """, (symbol, timeframe, int(start.timestamp()), int(end.timestamp()))).fetchone()

        actual = row[0] if row else 0
        return (actual / expected) >= min_coverage
    except Exception:
        return False


def get_summary() -> list[dict]:
    """Return per-symbol/timeframe archive stats for dashboard display."""
    try:
        with _conn() as c:
            rows = c.execute("""
                SELECT symbol, timeframe, first_ts, last_ts, row_count, updated
                FROM archive_meta ORDER BY symbol, timeframe
            """).fetchall()
        result = []
        for r in rows:
            first = datetime.fromtimestamp(r['first_ts'], tz=timezone.utc).strftime('%Y-%m-%d') if r['first_ts'] else '?'
            last  = datetime.fromtimestamp(r['last_ts'],  tz=timezone.utc).strftime('%Y-%m-%d') if r['last_ts']  else '?'
            result.append({
                'symbol': r['symbol'], 'timeframe': r['timeframe'],
                'first': first, 'last': last, 'rows': r['row_count'],
                'updated': r['updated'],
            })
        return result
    except Exception:
        return []
