"""Revision-aware market outcomes with finalized Kalshi truth as training authority."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from config import DB_PATH
from intelligence.schema import connect, init_intelligence_db


def _hash_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def record_outcome(
    *,
    ticker: str,
    market_result: str,
    market_status: str,
    official: bool,
    source: str,
    source_payload: dict[str, Any] | None = None,
    observed_value: float | None = None,
    determined_at: str = "",
    settled_at: str = "",
    db_path: str = DB_PATH,
) -> dict[str, Any]:
    init_intelligence_db(db_path)
    result = str(market_result or "").upper()
    if result not in {"YES", "NO"}:
        raise ValueError(f"Unsupported market result: {market_result}")
    now = datetime.now(timezone.utc).isoformat()
    payload_hash = _hash_payload(source_payload or {})
    with connect(db_path) as conn:
        current = conn.execute(
            "SELECT * FROM intelligence_outcomes WHERE ticker=? AND current=1",
            (ticker,),
        ).fetchone()
        if current and (
            str(current["market_result"]) == result
            and str(current["market_status"]) == str(market_status)
            and int(current["official"] or 0) == int(bool(official))
            and str(current["source_payload_hash"] or "") == payload_hash
        ):
            return {"inserted": False, "revision": int(current["revision"]), "ticker": ticker}

        revision = int(current["revision"] if current else 0) + 1
        if current:
            conn.execute("UPDATE intelligence_outcomes SET current=0 WHERE id=?", (current["id"],))
        conn.execute(
            """INSERT INTO intelligence_outcomes
               (ticker, revision, market_result, market_status, official,
                observed_value, source, source_payload_hash, determined_at,
                settled_at, recorded_at, current)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                ticker,
                revision,
                result,
                market_status,
                int(bool(official)),
                observed_value,
                source,
                payload_hash,
                determined_at,
                settled_at,
                now,
            ),
        )
        conn.commit()
    return {"inserted": True, "revision": revision, "ticker": ticker}


def _update_legacy_resolution(
    ticker: str,
    result: str,
    *,
    settled_at: str,
    source: str,
    db_path: str,
) -> int:
    """Keep legacy cockpit consumers aligned without using them as RBI 2.0 truth."""
    with connect(db_path) as conn:
        contract_rows = conn.execute(
            "SELECT id FROM forecast_contracts WHERE local_symbol=?",
            (ticker,),
        ).fetchall()
        updated = 0
        for row in contract_rows:
            conn.execute(
                """INSERT INTO forecast_resolutions
                   (contract_id, resolved_side, resolved_at, payout_at, notes,
                    source, basis_quality)
                   VALUES (?, ?, ?, ?, ?, ?, 'CONFIRMED')
                   ON CONFLICT(contract_id) DO UPDATE SET
                     resolved_side=excluded.resolved_side,
                     resolved_at=excluded.resolved_at,
                     payout_at=excluded.payout_at,
                     notes=excluded.notes,
                     source=excluded.source,
                     basis_quality='CONFIRMED'""",
                (
                    int(row["id"]),
                    result,
                    settled_at,
                    settled_at,
                    f"Official finalized Kalshi result for {ticker}",
                    source,
                ),
            )
            updated += 1
        conn.commit()
    return updated


def sync_official_market_outcomes(
    broker,
    *,
    db_path: str = DB_PATH,
    max_markets: int = 40,
) -> dict[str, Any]:
    """Reconcile due evidence against finalized market results using bounded calls."""
    init_intelligence_db(db_path)
    summary = {"checked": 0, "finalized": 0, "inserted": 0, "legacy_rows": 0, "errors": []}
    now = datetime.now(timezone.utc).isoformat()
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT DISTINCT p.ticker
               FROM intelligence_predictions p
               LEFT JOIN intelligence_outcomes o
                 ON o.ticker=p.ticker AND o.current=1 AND o.official=1
               WHERE o.id IS NULL
                 AND COALESCE(p.market_close_at, '') <= ?
               ORDER BY p.market_close_at ASC
               LIMIT ?""",
            (now, max(1, int(max_markets))),
        ).fetchall()

    for row in rows:
        ticker = str(row["ticker"] or "")
        if not ticker:
            continue
        summary["checked"] += 1
        try:
            payload = broker._request("GET", f"/trade-api/v2/markets/{ticker}")
            if payload.get("error"):
                summary["errors"].append(f"{ticker}:{payload.get('error')}")
                continue
            market = payload.get("market") if isinstance(payload.get("market"), dict) else payload
            status = str(market.get("status") or "").lower()
            result = str(market.get("result") or "").upper()
            if status != "finalized" or result not in {"YES", "NO"}:
                continue
            summary["finalized"] += 1
            settled_at = str(market.get("settlement_ts") or market.get("updated_time") or now)
            recorded = record_outcome(
                ticker=ticker,
                market_result=result,
                market_status=status,
                official=True,
                source="kalshi_market_finalized",
                source_payload=market,
                determined_at=str(market.get("determined_time") or ""),
                settled_at=settled_at,
                db_path=db_path,
            )
            summary["inserted"] += int(bool(recorded.get("inserted")))
            summary["legacy_rows"] += _update_legacy_resolution(
                ticker,
                result,
                settled_at=settled_at,
                source="kalshi_market_finalized",
                db_path=db_path,
            )
        except Exception as exc:
            summary["errors"].append(f"{ticker}:{exc}")
    return summary


def outcome_health(db_path: str = DB_PATH) -> dict[str, Any]:
    init_intelligence_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            """SELECT COUNT(*) total,
                      SUM(CASE WHEN official=1 THEN 1 ELSE 0 END) official,
                      SUM(CASE WHEN official=0 THEN 1 ELSE 0 END) provisional
               FROM intelligence_outcomes WHERE current=1"""
        ).fetchone()
    return {
        "total": int(row["total"] or 0),
        "official": int(row["official"] or 0),
        "provisional": int(row["provisional"] or 0),
    }
