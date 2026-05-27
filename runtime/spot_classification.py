"""
runtime/spot_classification.py — Lightweight holding classification for Ledgerless v19.1.

Tracks which symbols are bot-managed vs external/manual. 
Strictly metadata; truth is always sourced from the Broker.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any
from logging_db.trade_logger import _conn as _db_conn

logger = logging.getLogger(__name__)

_CLASS_TABLE = "spot_holding_classifications"

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def ensure_classification_table() -> None:
    """Ensure the classification table exists in the trades DB."""
    with _db_conn() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_CLASS_TABLE} (
                symbol TEXT PRIMARY KEY,
                classification TEXT NOT NULL,
                note TEXT DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()

def get_classifications() -> dict[str, dict[str, Any]]:
    """Return all symbol classifications."""
    ensure_classification_table()
    with _db_conn() as conn:
        rows = conn.execute(
            f"SELECT symbol, classification, note, updated_at FROM {_CLASS_TABLE}"
        ).fetchall()
    return {
        str(r["symbol"]).upper(): {
            "classification": str(r["classification"] or "").strip(),
            "note": str(r["note"] or "").strip(),
            "updated_at": str(r["updated_at"] or ""),
        }
        for r in rows
    }

def set_classification(
    symbol: str,
    classification: str,
    note: str = "",
) -> None:
    """Update classification for a symbol."""
    ensure_classification_table()
    clean = str(symbol).strip().upper()
    with _db_conn() as conn:
        conn.execute(
            f"""
            INSERT INTO {_CLASS_TABLE} (symbol, classification, note, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                classification=excluded.classification,
                note=excluded.note,
                updated_at=excluded.updated_at
            """,
            (clean, str(classification or "").strip(), str(note or "").strip(), _now_iso()),
        )
        conn.commit()
    logger.info(f"[spot_classification] Updated {clean} to {classification}")

def is_external_manual(symbol: str, classifications: dict[str, dict[str, Any]] | None = None) -> bool:
    """Check if a symbol is marked as external/manual."""
    if classifications is None:
        classifications = get_classifications()
    info = classifications.get(str(symbol).upper(), {})
    return str(info.get("classification")).lower() == "external_manual"
