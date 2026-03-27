"""
memory/trade_memory.py
NumPy + SQLite vector memory — no LanceDB, no sentence-transformers.

Stores completed trades as 8-dim feature vectors in a local SQLite DB.
Retrieves similar setups via cosine similarity.

Vector layout (8 dims):
  [rsi/100, tanh(macd*10), adx/100, min(vol/5,1),
   regime_trending, regime_ranging, regime_volatile, regime_unknown]

Same public API as the old LanceDB version — callers don't change.
"""
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime
from typing import Optional

import numpy as np
import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import LANCEDB_PATH, MARKET_TIMEZONE

_DB_PATH = os.path.join(LANCEDB_PATH, 'trade_memory.db')
_REGIMES = ['trending', 'ranging', 'volatile', 'unknown']
_VECTOR_DIM = 4 + len(_REGIMES)  # 8

# Kept for dashboard compatibility (was conditional on lancedb import)
LANCEDB_AVAILABLE = True


# ─── Embedding ────────────────────────────────────────────────────────────────

def _embed(rsi: float, macd_hist: float, adx: float,
           vol_spike: float, regime: str) -> np.ndarray:
    """Build an 8-dim feature vector from numeric trade signals."""
    v_rsi   = float(rsi) / 100.0
    v_macd  = float(np.tanh(float(macd_hist) * 10))   # squash to (-1, 1)
    v_adx   = float(adx) / 100.0
    v_vol   = min(float(vol_spike) / 5.0, 1.0)
    regime_vec = [1.0 if regime == r else 0.0 for r in _REGIMES]
    return np.array([v_rsi, v_macd, v_adx, v_vol] + regime_vec, dtype=np.float32)


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# ─── Storage ──────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_experiences (
            id                  TEXT PRIMARY KEY,
            ts                  TEXT,
            symbol              TEXT,
            strategy            TEXT,
            entry_reason        TEXT,
            exit_reason         TEXT,
            outcome             REAL,
            won                 INTEGER,
            rsi_at_entry        REAL,
            macd_hist_at_entry  REAL,
            adx_at_entry        REAL,
            vol_spike_at_entry  REAL,
            regime              TEXT,
            vector              TEXT
        )
    """)
    conn.commit()
    return conn


# ─── Public API ───────────────────────────────────────────────────────────────

def store_trade_experience(
    symbol: str,
    strategy: str,
    entry_reason: str,
    exit_reason: str,
    pnl_usd: float,
    rsi: float = 50.0,
    macd_hist: float = 0.0,
    adx: float = 25.0,
    vol_spike: float = 1.0,
    regime: str = 'unknown',
) -> bool:
    """
    Store a completed trade in vector memory.
    Called by job_runner / exit_monitor after every position close.
    """
    try:
        vec = _embed(rsi, macd_hist, adx, vol_spike, regime)
        tz  = pytz.timezone(MARKET_TIMEZONE)
        conn = _get_conn()
        conn.execute(
            """INSERT INTO trade_experiences VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                datetime.now(tz).isoformat(),
                symbol, strategy,
                entry_reason, exit_reason,
                float(pnl_usd),
                int(pnl_usd > 0),
                float(rsi), float(macd_hist), float(adx), float(vol_spike),
                regime,
                json.dumps(vec.tolist()),
            ),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[trade_memory] Store error: {e}")
        return False


def retrieve_similar_experiences(
    symbol: str,
    entry_reason: str,
    regime: str,
    rsi: float = 50.0,
    macd_hist: float = 0.0,
    adx: float = 25.0,
    vol_spike: float = 1.0,
    limit: int = 3,
) -> list:
    """
    Find the N most similar historical trade setups via cosine similarity.
    Returns list of dicts with fields: symbol, outcome, won, entry_reason,
    exit_reason, regime.
    """
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT id, symbol, entry_reason, exit_reason, outcome, won, "
            "rsi_at_entry, macd_hist_at_entry, adx_at_entry, vol_spike_at_entry, "
            "regime, vector FROM trade_experiences"
        ).fetchall()
        conn.close()

        if not rows:
            return []

        query_vec = _embed(rsi, macd_hist, adx, vol_spike, regime)
        scored = []
        for row in rows:
            try:
                stored_vec = np.array(json.loads(row[11]), dtype=np.float32)
                sim = _cosine_sim(query_vec, stored_vec)
                scored.append((sim, row))
            except Exception:
                continue

        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for _, row in scored[:limit]:
            results.append({
                'id':           row[0],
                'symbol':       row[1],
                'entry_reason': row[2],
                'exit_reason':  row[3],
                'outcome':      row[4],
                'won':          bool(row[5]),
                'rsi_at_entry': row[6],
                'adx_at_entry': row[8],
                'regime':       row[10],
            })
        return results

    except Exception as e:
        print(f"[trade_memory] Retrieve error: {e}")
        return []


def format_memory_context(experiences: list) -> str:
    """Format retrieved experiences into a context string for debate agents."""
    if not experiences:
        return "No similar historical trades found yet — this system is still building its memory."

    lines = ["SIMILAR HISTORICAL SETUPS FROM MEMORY:"]
    for i, exp in enumerate(experiences, 1):
        outcome_str = (
            f"WIN +${exp.get('outcome', 0):.2f}" if exp.get('won')
            else f"LOSS ${exp.get('outcome', 0):.2f}"
        )
        lines.append(
            f"  {i}. {exp.get('symbol','?')} ({exp.get('regime','?')} regime) → {outcome_str}\n"
            f"     Entry: {exp.get('entry_reason','?')[:80]}\n"
            f"     Exit:  {exp.get('exit_reason','?')[:80]}"
        )
    return '\n'.join(lines)


def get_memory_stats() -> dict:
    """Return stats about the memory store for dashboard display."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT outcome, won FROM trade_experiences"
        ).fetchall()
        conn.close()
        total = len(rows)
        wins  = sum(1 for r in rows if r[1])
        return {
            'total':    total,
            'wins':     wins,
            'losses':   total - wins,
            'win_rate': wins / total if total > 0 else 0,
            'available': True,
        }
    except Exception:
        return {'total': 0, 'wins': 0, 'losses': 0, 'available': False}
