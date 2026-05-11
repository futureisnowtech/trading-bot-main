"""
runtime/spot_position_truth.py — canonical broker-first truth for the live spot lane.

The live Coinbase account decides whether a spot holding exists.
The database enriches broker truth with bot lineage and strategy metadata.
Live mode only. Paper mode excised v18.17.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

try:
    from config import DB_PATH
except Exception:  # pragma: no cover - fail-soft for scripts/tests
    DB_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "trades.db"
    )

_CLASS_TABLE = "spot_holding_classifications"
_DEFAULT_EXTERNAL_MANUAL = {
    "STETH",
    "ETH",
}
_BLOCKING_STATUSES = {
    "qty_mismatch",
    "metadata_missing",
    "db_only_stale",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_db_path(db_path: str | None = None) -> str:
    if db_path:
        return str(db_path)
    try:
        current = str(globals().get("DB_PATH") or "").strip()
        if current:
            return current
    except Exception:
        pass
    return str(DB_PATH)


def _conn(db_path: str | None = None) -> sqlite3.Connection:
    db_path = _resolve_db_path(db_path)
    c = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _clean_symbol(symbol: str) -> str:
    clean = str(symbol or "").upper().replace("/", "-")
    for suffix in ("-USDC", "-USDT", "-USD", "USDC", "USDT", "USD"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)]
            break
    return clean.replace("-", "")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except Exception:
        return default


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def ensure_spot_truth_tables(db_path: str | None = None) -> None:
    with _conn(db_path) as conn:
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


def seed_default_external_manual_holdings(
    symbols: set[str] | None = None, db_path: str | None = None
) -> None:
    ensure_spot_truth_tables(db_path=db_path)
    if symbols is None:
        try:
            from config import SPOT_EXTERNAL_MANUAL_HOLDINGS

            symbols = set(SPOT_EXTERNAL_MANUAL_HOLDINGS or _DEFAULT_EXTERNAL_MANUAL)
        except Exception:
            symbols = _DEFAULT_EXTERNAL_MANUAL
    seeds = {_clean_symbol(s) for s in symbols}
    now = _now_iso()
    with _conn(db_path) as conn:
        for symbol in sorted(seeds):
            if not symbol:
                continue
            conn.execute(
                f"""
                INSERT INTO {_CLASS_TABLE} (symbol, classification, note, updated_at)
                VALUES (?, 'external_manual', ?, ?)
                ON CONFLICT(symbol) DO NOTHING
                """,
                (symbol, "seeded_manual_holding", now),
            )
        conn.commit()


def set_holding_classification(
    symbol: str,
    classification: str,
    note: str = "",
    db_path: str | None = None,
) -> None:
    ensure_spot_truth_tables(db_path=db_path)
    clean = _clean_symbol(symbol)
    with _conn(db_path) as conn:
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


def get_holding_classifications(
    db_path: str | None = None,
) -> dict[str, dict[str, Any]]:
    ensure_spot_truth_tables(db_path=db_path)
    with _conn(db_path) as conn:
        rows = conn.execute(
            f"SELECT symbol, classification, note, updated_at FROM {_CLASS_TABLE}"
        ).fetchall()
    return {
        _clean_symbol(r["symbol"]): {
            "classification": str(r["classification"] or "").strip(),
            "note": str(r["note"] or "").strip(),
            "updated_at": str(r["updated_at"] or ""),
        }
        for r in rows
    }


def _load_db_spot_positions(db_path: str | None = None) -> list[dict]:
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM open_positions WHERE strategy LIKE 'spot_%' AND paper=0 ORDER BY ts_entry DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def _get_live_broker_snapshot() -> tuple[list[dict] | None, float | None]:
    try:
        from execution.coinbase_spot_broker import get_spot_broker

        broker = get_spot_broker()
        if not broker.is_connected():
            broker.connect()
        if not broker.is_connected():
            return None, None
        holdings = broker.sync_live_holdings()
        balances = broker.get_spot_balance() or {}
        return holdings, _to_float(balances.get("usd_available"), 0.0)
    except Exception as exc:
        logger.debug("[spot_truth] broker snapshot error: %s", exc)
        return None, None


def _metadata_missing(row: dict) -> bool:
    required = (
        ("entry_trade_id", int(row.get("entry_trade_id") or 0) > 0),
        (
            "entry_feature_snapshot_id",
            int(row.get("entry_feature_snapshot_id") or 0) > 0,
        ),
        ("base_asset", bool(str(row.get("base_asset") or "").strip())),
        ("setup_family", bool(str(row.get("setup_family") or "").strip())),
        ("execution_route", bool(str(row.get("execution_route") or "").strip())),
    )
    return any(not ok for _, ok in required)


def _qty_mismatch(db_row: dict, live_row: dict) -> bool:
    db_qty = _to_float(db_row.get("qty"))
    live_qty = _to_float(live_row.get("qty"))
    tolerance = max(1e-8, abs(live_qty) * 0.02)
    return abs(db_qty - live_qty) > tolerance


def _merged_live_row(
    symbol: str,
    live_row: dict | None,
    db_row: dict | None,
    classification_row: dict[str, Any] | None,
) -> dict:
    live_row = live_row or {}
    db_row = db_row or {}
    classification_row = classification_row or {}
    classification = str(classification_row.get("classification") or "").strip()

    # v18.17: Manual bypass — if a coin is marked as manual, it is NEVER a truth blocker
    if classification == "external_manual":
        status = "external_manual"
    elif live_row and db_row:
        if _metadata_missing(db_row):
            # v18.17: Allow metadata_missing as matched_bot_position if quantities match within 1%
            db_qty = _to_float(db_row.get("qty"))
            live_qty = _to_float(live_row.get("qty"))
            tolerance = max(1e-8, abs(live_qty) * 0.01)
            if abs(db_qty - live_qty) <= tolerance:
                status = "matched_bot_position"
                logger.warning(
                    f"[spot_truth] Allowing metadata_missing for {symbol} as matched_bot_position due to quantity match."
                )
            else:
                status = "metadata_missing"
        elif _qty_mismatch(db_row, live_row):
            status = "qty_mismatch"
        else:
            status = "matched_bot_position"
    elif live_row:
        status = classification or "unclassified"
    else:
        status = "db_only_stale"

    current_price = _to_float(
        live_row.get("current_price") or live_row.get("mark_price") or db_row.get("entry")
    )
    current_value = _to_float(live_row.get("current_value"))
    qty = _to_float(live_row.get("qty") or db_row.get("qty"))
    if current_value <= 0 and qty > 0 and current_price > 0:
        current_value = round(qty * current_price, 2)

    row = {
        **db_row,
        "symbol": symbol,
        "strategy": db_row.get("strategy") or f"spot_{symbol.lower()}",
        "paper": 0,
        "direction": "LONG",
        "qty": qty,
        "entry": _to_float(live_row.get("avg_entry") or db_row.get("entry")),
        "current_price": current_price,
        "current_value": current_value,
        "venue": "coinbase_spot",
        "position_truth_status": status,
        "classification_note": str(classification_row.get("note") or "").strip(),
        "classification_updated_at": str(
            classification_row.get("updated_at") or ""
        ).strip(),
        "is_bot_managed": status == "matched_bot_position",
        "is_external_manual": status == "external_manual",
        "truth_blocking": status in _BLOCKING_STATUSES,
        "auto_purge": status == "db_only_stale",
        "auto_purge": status == "db_only_stale",
    }
    if live_row:
        row["live_avg_entry"] = _to_float(live_row.get("avg_entry"))
    return row


def get_spot_position_truth(
    *,
    broker_holdings: list[dict] | None = None,
    db_path: str | None = None,
) -> dict[str, Any]:
    """
    Return broker-canonical truth for the spot lane.

    Keys:
      snapshot_ok
      broker_cash_usd
      all_live_holdings
      bot_managed_positions
      issues
      blocking_issues
      positions_open
      deployment_notional
    """
    ensure_spot_truth_tables(db_path=db_path)
    seed_default_external_manual_holdings(db_path=db_path)
    db_rows = _load_db_spot_positions(db_path=db_path)
    db_by_symbol = {_clean_symbol(r.get("symbol")): r for r in db_rows}
    classifications = get_holding_classifications(db_path=db_path)

    broker_cash_usd = 0.0
    if broker_holdings is None:
        broker_holdings, broker_cash_usd = _get_live_broker_snapshot()
    snapshot_ok = broker_holdings is not None

    live_by_symbol = {
        _clean_symbol(row.get("symbol")): dict(row)
        for row in (broker_holdings or [])
        if _clean_symbol(row.get("symbol"))
    }
    live_symbols = sorted(set(live_by_symbol))
    merged_live = [
        _merged_live_row(
            symbol,
            live_by_symbol.get(symbol),
            db_by_symbol.get(symbol),
            classifications.get(symbol),
        )
        for symbol in live_symbols
    ]
    stale_db_rows = [
        _merged_live_row(
            symbol,
            None,
            db_by_symbol.get(symbol),
            classifications.get(symbol),
        )
        for symbol in sorted(set(db_by_symbol) - set(live_by_symbol))
    ]
    issues = [
        row
        for row in (merged_live + stale_db_rows)
        if row.get("position_truth_status") != "matched_bot_position"
    ]

    if not snapshot_ok:
        issues.insert(
            0,
            {
                "symbol": "",
                "position_truth_status": "broker_snapshot_unavailable",
                "truth_blocking": True,
            },
        )

    # v18.17: Auto-purge stale DB-only positions that are blocking the lane
    auto_purge_rows = [row for row in issues if row.get("auto_purge")]
    if auto_purge_rows:
        try:
            from logging_db.trade_logger import delete_position

            for row in auto_purge_rows:
                sym = str(row.get("symbol") or "")
                strat = str(row.get("strategy") or f"spot_{sym.lower()}")
                delete_position(sym, strategy=strat)
                logger.info(f"[spot_truth] Auto-purged stale DB position for {sym}")
            # Re-filter issues after purging
            issues = [row for row in issues if not row.get("auto_purge")]
        except Exception as e:
            logger.warning(f"[spot_truth] Auto-purge failed: {e}")

    # v18.17: Auto-reconcile qty_mismatch and metadata_missing
    # Curing quantity mismatch often resolves metadata_missing on the next cycle
    healing_rows = [
        row
        for row in issues
        if row.get("position_truth_status") in ("qty_mismatch", "metadata_missing")
    ]
    if healing_rows:
        conn = None
        try:
            import sqlite3

            conn = _conn(db_path)
            for row in healing_rows:
                sym = _clean_symbol(row.get("symbol"))
                live_qty = _to_float(row.get("qty"))
                if live_qty > 0:
                    conn.execute(
                        "UPDATE open_positions SET qty=? WHERE symbol=? AND paper=0",
                        (live_qty, sym),
                    )
                    logger.info(
                        f"[spot_truth] Auto-reconciled DB qty for {sym} to {live_qty}"
                    )
            conn.commit()

            # Mark as fixed in the current truth object
            for row in healing_rows:
                if _to_float(row.get("qty")) > 0:
                    row["position_truth_status"] = "matched_bot_position"
                    row["truth_blocking"] = False

            # Refresh issues list
            issues = [
                row
                for row in (merged_live + stale_db_rows)
                if row.get("position_truth_status") != "matched_bot_position"
                and not row.get("auto_purge")
            ]
        except Exception as e:
            logger.warning(f"[spot_truth] Auto-reconcile failed: {e}")
        finally:
            if conn:
                conn.close()

    blockers = [row for row in issues if row.get("truth_blocking")]
    bot_positions = [row for row in merged_live if row.get("is_bot_managed")]
    deployment = round(sum(_to_float(r.get("current_value")) for r in merged_live), 2)
    return {
        "snapshot_ok": snapshot_ok,
        "broker_cash_usd": round(_to_float(broker_cash_usd), 2),
        "all_live_holdings": merged_live,
        "bot_managed_positions": bot_positions,
        "issues": issues,
        "blocking_issues": blockers,
        "positions_open": len(merged_live),
        "deployment_notional": deployment,
    }


def get_spot_symbol_truth(
    symbol: str, db_path: str | None = None
) -> dict[str, Any] | None:
    clean = _clean_symbol(symbol)
    truth = get_spot_position_truth(db_path=db_path)
    for row in truth.get("all_live_holdings") or []:
        if _clean_symbol(row.get("symbol")) == clean:
            return row
    for row in truth.get("issues") or []:
        if _clean_symbol(row.get("symbol")) == clean:
            return row
    return None
