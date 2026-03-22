"""
memory/trade_memory.py
LanceDB vector store — the system's long-term memory.
Every completed trade is embedded and stored.
Before each debate, the 3 most similar historical setups are retrieved
and passed as context: "last time you saw this exact pattern, here's what happened."

LanceDB: serverless, embedded in the app (like SQLite but for vectors).
No Docker. No config. One pip install.
"""
import os
import sys
import uuid
from datetime import datetime
from typing import Optional
import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import LANCEDB_PATH, MARKET_TIMEZONE

try:
    import lancedb
    import numpy as np
    LANCEDB_AVAILABLE = True
except ImportError:
    LANCEDB_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer
    _model = SentenceTransformer('all-MiniLM-L6-v2')   # 80MB, fast, good quality
    EMBEDDINGS_AVAILABLE = True
except ImportError:
    EMBEDDINGS_AVAILABLE = False
    _model = None


def _embed(text: str):
    """Embed a text string into a 384-dim vector."""
    if not EMBEDDINGS_AVAILABLE or _model is None:
        # Fallback: random vector (better than crashing)
        import random
        return [random.gauss(0, 0.1) for _ in range(384)]
    return _model.encode(text).tolist()


def _get_table():
    """Get or create the trade_experiences table."""
    if not LANCEDB_AVAILABLE:
        return None
    os.makedirs(LANCEDB_PATH, exist_ok=True)
    db = lancedb.connect(LANCEDB_PATH)
    table_names = db.table_names()
    if 'trade_experiences' not in table_names:
        # Create with a seed record
        import pyarrow as pa
        schema = pa.schema([
            pa.field('id', pa.string()),
            pa.field('ts', pa.string()),
            pa.field('symbol', pa.string()),
            pa.field('strategy', pa.string()),
            pa.field('entry_reason', pa.string()),
            pa.field('exit_reason', pa.string()),
            pa.field('outcome', pa.float32()),
            pa.field('won', pa.bool_()),
            pa.field('rsi_at_entry', pa.float32()),
            pa.field('macd_hist_at_entry', pa.float32()),
            pa.field('adx_at_entry', pa.float32()),
            pa.field('vol_spike_at_entry', pa.float32()),
            pa.field('regime', pa.string()),
            pa.field('vector', pa.list_(pa.float32(), 384)),
        ])
        db.create_table('trade_experiences', schema=schema)
    return db.open_table('trade_experiences')


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
    regime: str = 'unknown'
) -> bool:
    """
    Store a completed trade in vector memory.
    Called by job_runner after every position close.
    """
    if not LANCEDB_AVAILABLE:
        return False
    try:
        table = _get_table()
        if table is None:
            return False

        # Build embedding text — this is what gets semantically searched
        experience_text = (
            f"Symbol: {symbol}. Strategy: {strategy}. Regime: {regime}. "
            f"Entry: {entry_reason}. Exit: {exit_reason}. "
            f"RSI: {rsi:.1f}. MACD: {macd_hist:.4f}. ADX: {adx:.1f}. "
            f"Volume spike: {vol_spike:.1f}x. "
            f"Outcome: {'WIN' if pnl_usd > 0 else 'LOSS'} ${pnl_usd:+.2f}."
        )

        tz = pytz.timezone(MARKET_TIMEZONE)
        record = {
            'id': str(uuid.uuid4()),
            'ts': datetime.now(tz).isoformat(),
            'symbol': symbol,
            'strategy': strategy,
            'entry_reason': entry_reason,
            'exit_reason': exit_reason,
            'outcome': float(pnl_usd),
            'won': pnl_usd > 0,
            'rsi_at_entry': float(rsi),
            'macd_hist_at_entry': float(macd_hist),
            'adx_at_entry': float(adx),
            'vol_spike_at_entry': float(vol_spike),
            'regime': regime,
            'vector': _embed(experience_text),
        }

        table.add([record])
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
    limit: int = 3
) -> list:
    """
    Find the 3 most similar historical trade setups.
    Returns list of dicts with fields: symbol, outcome, won, entry_reason, exit_reason, regime
    Used to brief the debate agents with "here's what happened last time."
    """
    if not LANCEDB_AVAILABLE:
        return []
    try:
        table = _get_table()
        if table is None:
            return []

        query_text = (
            f"Symbol: {symbol}. Regime: {regime}. Entry: {entry_reason}. "
            f"RSI: {rsi:.1f}. MACD: {macd_hist:.4f}. ADX: {adx:.1f}. "
            f"Volume spike: {vol_spike:.1f}x."
        )
        query_vector = _embed(query_text)

        results = table.search(query_vector).limit(limit).to_list()
        return results

    except Exception as e:
        print(f"[trade_memory] Retrieve error: {e}")
        return []


def format_memory_context(experiences: list) -> str:
    """Format retrieved experiences into a context string for the debate agents."""
    if not experiences:
        return "No similar historical trades found yet — this system is still building its memory."

    lines = ["SIMILAR HISTORICAL SETUPS FROM MEMORY:"]
    for i, exp in enumerate(experiences, 1):
        outcome_str = f"WIN +${exp.get('outcome',0):.2f}" if exp.get('won') else f"LOSS ${exp.get('outcome',0):.2f}"
        lines.append(
            f"  {i}. {exp.get('symbol','?')} ({exp.get('regime','?')} regime) → {outcome_str}\n"
            f"     Entry: {exp.get('entry_reason','?')[:80]}\n"
            f"     Exit: {exp.get('exit_reason','?')[:80]}"
        )
    return '\n'.join(lines)


def get_memory_stats() -> dict:
    """Return stats about the memory store for dashboard display."""
    if not LANCEDB_AVAILABLE:
        return {'total': 0, 'wins': 0, 'losses': 0, 'available': False}
    try:
        table = _get_table()
        if table is None:
            return {'total': 0, 'wins': 0, 'losses': 0, 'available': False}
        data = table.to_pandas()
        total = len(data)
        wins = int(data['won'].sum()) if total > 0 else 0
        return {
            'total': total, 'wins': wins, 'losses': total - wins,
            'win_rate': wins / total if total > 0 else 0,
            'available': True
        }
    except Exception:
        return {'total': 0, 'wins': 0, 'losses': 0, 'available': False}
