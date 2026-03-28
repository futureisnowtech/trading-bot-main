"""
data/whale_tracker.py — Prediction market smart money tracker.

Monitors large/consistent traders on Polymarket CLOB to identify "whale" activity.
Whale positions signal genuine conviction — they have real money at stake.

Strategy: if whales are heavily positioned YES, our probability estimate gets a boost.
If whales are heavily positioned NO, our estimate gets penalized.

Storage: SQLite whale_activity table in logs/trades.db.
Refresh: called at start of each Lane 3 scan (15-min cycle).

Adapted from Fully-Autonomous-Polymarket-AI-Trading-Bot/src/analytics/wallet_scanner.py
(simplified; sync requests; our SQLite; no external deps beyond requests).
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
import time
from datetime import datetime
from typing import Optional

import pytz
import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH, MARKET_TIMEZONE

logger = logging.getLogger(__name__)

CLOB_BASE = "https://clob.polymarket.com"
_REQUEST_TIMEOUT = 15.0
_WHALE_POSITION_THRESHOLD_USD = 500    # trades ≥ $500 qualify as whale activity
_WHALE_SCORE_CLIP = 1.0               # max edge boost/penalty from whale signal

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS whale_activity (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    market_id   TEXT NOT NULL,
    yes_whale_volume  REAL DEFAULT 0.0,
    no_whale_volume   REAL DEFAULT 0.0,
    whale_signal      REAL DEFAULT 0.0,  -- -1 to +1 (positive = whales bullish YES)
    trade_count       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS whale_activity_market ON whale_activity(market_id, ts);
"""


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


_schema_ready = False


def _init_once() -> None:
    global _schema_ready
    if _schema_ready:
        return
    conn = _conn()
    for stmt in _INIT_SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            try:
                conn.execute(s)
            except Exception:
                pass
    conn.commit()
    conn.close()
    _schema_ready = True


def _get_recent_trades(token_id: str, limit: int = 200) -> list[dict]:
    """Fetch recent trades for a CLOB token. Returns [] on error."""
    try:
        resp = requests.get(
            f"{CLOB_BASE}/trades",
            params={"token_id": token_id, "limit": limit},
            timeout=_REQUEST_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("data", [])
    except Exception as e:
        logger.warning(f"[whale_tracker] get_recent_trades({token_id[:16]}…): {e}")
        return []


def scan_whale_activity(market_id: str, token_id: str, outcome: str = "YES") -> float:
    """
    Scan recent trades for a market token and compute a whale signal.

    Returns a float in [-1, +1]:
      +1.0 = whales are heavily buying YES (bullish signal)
       0.0 = neutral or insufficient data
      -1.0 = whales are heavily selling/buying NO (bearish signal)

    The signal is stored to SQLite for audit trail.
    """
    _init_once()
    trades = _get_recent_trades(token_id, limit=200)
    if not trades:
        return 0.0

    yes_whale_vol = 0.0
    no_whale_vol  = 0.0
    whale_count   = 0

    for t in trades:
        price = float(t.get("price", 0))
        size  = float(t.get("size", t.get("amount", 0)))
        side  = str(t.get("side", "")).lower()
        notional = price * size

        if notional < _WHALE_POSITION_THRESHOLD_USD:
            continue

        whale_count += 1
        if side == "buy":
            # Buying YES token = bullish
            if outcome.upper() == "YES":
                yes_whale_vol += notional
            else:
                no_whale_vol += notional
        elif side == "sell":
            # Selling YES token = bearish (or buying NO equivalent)
            if outcome.upper() == "YES":
                no_whale_vol += notional
            else:
                yes_whale_vol += notional

    total = yes_whale_vol + no_whale_vol
    if total < _WHALE_POSITION_THRESHOLD_USD:
        return 0.0

    # Normalise: +1 = all whales buying YES, -1 = all whales selling YES
    signal = (yes_whale_vol - no_whale_vol) / total
    signal = max(-_WHALE_SCORE_CLIP, min(_WHALE_SCORE_CLIP, signal))

    try:
        conn = _conn()
        conn.execute(
            """INSERT INTO whale_activity
               (ts, market_id, yes_whale_volume, no_whale_volume, whale_signal, trade_count)
               VALUES (?,?,?,?,?,?)""",
            (datetime.now(pytz.timezone(MARKET_TIMEZONE)).isoformat(),
             market_id, yes_whale_vol, no_whale_vol, signal, whale_count)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[whale_tracker] SQLite write: {e}")

    logger.debug(
        f"[whale_tracker] {market_id[:20]}… signal={signal:+.3f} "
        f"yes_vol=${yes_whale_vol:,.0f} no_vol=${no_whale_vol:,.0f} trades={whale_count}"
    )
    return signal


def get_cached_whale_signal(market_id: str, max_age_minutes: int = 20) -> Optional[float]:
    """
    Return the most recent cached whale signal for a market.
    Returns None if no recent data.
    """
    _init_once()
    try:
        conn = _conn()
        row = conn.execute(
            """SELECT whale_signal FROM whale_activity
               WHERE market_id=?
               AND ts >= datetime('now', ? || ' minutes')
               ORDER BY ts DESC LIMIT 1""",
            (market_id, f"-{max_age_minutes}")
        ).fetchone()
        conn.close()
        return float(row["whale_signal"]) if row else None
    except Exception as e:
        logger.warning(f"[whale_tracker] get_cached: {e}")
        return None


def get_whale_edge_boost(market_id: str, token_id: str, outcome: str = "YES") -> float:
    """
    Get whale edge boost for a market. Checks cache first, scans if stale.

    Returns edge adjustment in probability points (e.g. +0.05 = +5% boost).
    Max impact is ±0.08 (8 percentage points) on the raw probability.
    """
    # Check cache first (avoid repeated CLOB API calls)
    cached = get_cached_whale_signal(market_id, max_age_minutes=15)
    if cached is not None:
        signal = cached
    else:
        signal = scan_whale_activity(market_id, token_id, outcome)

    # Convert signal (-1 to +1) to probability boost (-0.08 to +0.08)
    return round(signal * 0.08, 4)
