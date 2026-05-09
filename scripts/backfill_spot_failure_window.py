"""
Backfill recent live spot closes into the real learning tables.

This repairs the dead learning window identified in the 2026-04-28 spot audit
by reconstructing spot close lineage from:
  - trades
  - scan_candidates
  - tv_signals

Rows written by this script are marked reconstructed so later audits can
distinguish them from native live-ingested rows.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "logs" / "trades.db"
DEFAULT_CUTOFF = "2026-04-22T21:36:39.390822+00:00"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ts_key(ts: str) -> datetime:
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def _clean_symbol(symbol: str) -> str:
    clean = str(symbol or "").upper().replace("/", "-")
    for suffix in ("-USDC", "-USDT", "-USD", "USDC", "USDT", "USD"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)]
            break
    return clean.replace("-", "")


@dataclass
class TradePair:
    buy: sqlite3.Row
    sell: sqlite3.Row


def _load_trade_pairs(conn: sqlite3.Connection, cutoff: str) -> list[TradePair]:
    rows = conn.execute(
        """
        SELECT id, ts, strategy, symbol, action, qty, price, fee_usd, pnl_usd, order_id, notes, paper
        FROM trades
        WHERE strategy LIKE 'spot_%'
          AND paper = 0
          AND ts >= ?
        ORDER BY ts ASC, id ASC
        """,
        (cutoff,),
    ).fetchall()
    open_by_strategy: dict[str, sqlite3.Row] = {}
    pairs: list[TradePair] = []
    for row in rows:
        strategy = str(row["strategy"] or "")
        action = str(row["action"] or "").upper()
        if action == "BUY":
            open_by_strategy[strategy] = row
        elif action == "SELL":
            buy = open_by_strategy.pop(strategy, None)
            if buy is not None:
                pairs.append(TradePair(buy=buy, sell=row))
    return pairs


def _find_candidate(conn: sqlite3.Connection, buy: sqlite3.Row) -> sqlite3.Row | None:
    base_asset = _clean_symbol(str(buy["symbol"] or ""))
    buy_ts = str(buy["ts"] or "")
    return conn.execute(
        """
        SELECT *
        FROM scan_candidates
        WHERE paper = 0
          AND decision = 'entered'
          AND base_asset = ?
          AND ABS(
                strftime('%s', replace(substr(ts, 1, 19), 'T', ' ')) -
                strftime('%s', replace(substr(?, 1, 19), 'T', ' '))
              ) <= 300
        ORDER BY ABS(
                strftime('%s', replace(substr(ts, 1, 19), 'T', ' ')) -
                strftime('%s', replace(substr(?, 1, 19), 'T', ' '))
              ) ASC,
              id DESC
        LIMIT 1
        """,
        (base_asset, buy_ts, buy_ts),
    ).fetchone()


def _find_tv_context(conn: sqlite3.Connection, base_asset: str, buy_ts: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT *
        FROM tv_signals
        WHERE symbol LIKE ?
          AND secret_validated = 1
          AND ts <= ?
        ORDER BY ts DESC, id DESC
        LIMIT 1
        """,
        (f"{base_asset}%", buy_ts),
    ).fetchone()
    if not row:
        return {}
    age_seconds = max(0.0, (_ts_key(buy_ts) - _ts_key(str(row["ts"]))).total_seconds())
    return {
        "tv_profile_name": str(row["profile_name"] or ""),
        "tv_htf_bias": str(row["htf_bias"] or row["direction"] or ""),
        "tv_signal_age_sec": age_seconds,
        "tv_indicator_name": str(row["indicator_name"] or ""),
    }


def _feature_payload(pair: TradePair, candidate: sqlite3.Row | None, tv_context: dict[str, Any]) -> dict[str, Any]:
    base_asset = _clean_symbol(str(pair.buy["symbol"] or ""))
    payload = {
        "symbol": base_asset,
        "base_asset": base_asset,
        "executed_symbol": base_asset,
        "candidate_id": 0,
        "scan_id": "",
        "raw_scanner_symbol": base_asset,
        "route_type": "",
        "regime": "UNKNOWN",
        "spot_regime": "UNKNOWN",
        "setup_family": "",
        "setup_score": 0.0,
        "composite_score": 0.0,
        "conviction_score": 0.0,
        "entry_thesis_score": 0.0,
        "frame_score_5m": 0.0,
        "frame_score_30m": 0.0,
        "volatility_quality": 0.0,
        "path_efficiency": 0.0,
        "momentum_impulse": 0.0,
        "structure_component": 0.0,
        "participation_component": 0.0,
        "reconstructed": 1,
    }
    if candidate is not None:
        payload.update(
            {
                "candidate_id": int(candidate["id"] or 0),
                "scan_id": str(candidate["scan_id"] or ""),
                "raw_scanner_symbol": str(candidate["symbol"] or base_asset),
                "base_asset": str(candidate["base_asset"] or base_asset),
                "route_type": str(candidate["execution_route"] or ""),
                "regime": str(candidate["spot_regime"] or candidate["regime"] or "UNKNOWN"),
                "spot_regime": str(candidate["spot_regime"] or candidate["regime"] or "UNKNOWN"),
                "setup_family": str(candidate["setup_family"] or ""),
                "setup_score": float(candidate["setup_score"] or 0.0),
                "composite_score": float(candidate["composite_score"] or 0.0),
                "conviction_score": float(candidate["final_spot_score"] or 0.0),
                "entry_thesis_score": float(candidate["final_spot_score"] or 0.0),
                "frame_score_5m": float(
                    _extract_numeric_state(candidate["tf_5m_state"])
                ),
                "frame_score_30m": float(
                    _extract_numeric_state(candidate["tf_30m_state"])
                ),
            }
        )
    payload.update(tv_context)
    return payload


def _extract_numeric_state(raw: Any) -> float:
    text = str(raw or "").strip().lower()
    if "score=" in text:
        try:
            return float(text.split("score=", 1)[1].split()[0].split(",")[0])
        except Exception:
            return 0.0
    return 0.0


def _already_backfilled(conn: sqlite3.Connection, sell_trade_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM ml_feature_snapshots WHERE trade_id=? LIMIT 1",
        (sell_trade_id,),
    ).fetchone()
    return bool(row)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from learning.post_trade_analyzer import analyze_closed_trade
    from learning_loop import record_closed_trade

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    pairs = _load_trade_pairs(conn, args.cutoff)
    summary = defaultdict(int)

    for pair in pairs:
        sell_trade_id = int(pair.sell["id"])
        if _already_backfilled(conn, sell_trade_id):
            summary["already_present"] += 1
            continue

        candidate = _find_candidate(conn, pair.buy)
        tv_context = _find_tv_context(conn, _clean_symbol(str(pair.buy["symbol"])), str(pair.buy["ts"]))
        features = _feature_payload(pair, candidate, tv_context)
        pnl_usd = float(pair.sell["pnl_usd"] or 0.0)
        entry_price = float(pair.buy["price"] or 0.0)
        exit_price = float(pair.sell["price"] or 0.0)
        route_type = str(features.get("route_type") or "")
        trade_ref = f"spot_backfill:{int(pair.buy['id'])}:{sell_trade_id}"
        exit_reason = _extract_exit_reason(pair.sell["notes"])

        if args.dry_run:
            summary["would_backfill"] += 1
            continue

        record_closed_trade(
            trade_id=sell_trade_id,
            symbol=_clean_symbol(str(pair.sell["symbol"])),
            direction="LONG",
            won=pnl_usd > 0,
            pnl_usd=pnl_usd,
            entry_price=entry_price,
            exit_price=exit_price,
            entry_score=float(features.get("entry_thesis_score") or 0.0),
            exit_score=0.0,
            regime=str(features.get("spot_regime") or "UNKNOWN"),
            features=features,
            exit_reason=exit_reason,
            trade_ref=trade_ref,
            reconstructed=True,
        )
        analyze_closed_trade(
            symbol=_clean_symbol(str(pair.sell["symbol"])),
            strategy=str(pair.sell["strategy"] or ""),
            entry_price=entry_price,
            exit_price=exit_price,
            qty=float(pair.sell["qty"] or 0.0),
            fee_usd=float(pair.buy["fee_usd"] or 0.0) + float(pair.sell["fee_usd"] or 0.0),
            entry_ts=str(pair.buy["ts"] or ""),
            exit_ts=str(pair.sell["ts"] or ""),
            exit_reason=exit_reason,
            market_data_at_entry=features,
            source="reconstructed_live_spot",
            trade_ref=trade_ref,
            exit_type=exit_reason,
            composite_score=float(features.get("composite_score") or 0.0),
            close_order_id=str(pair.sell["order_id"] or ""),
            entry_order_id=str(pair.buy["order_id"] or ""),
            feature_snapshot_id=None,
        )
        summary["backfilled"] += 1
        if candidate is not None:
            summary["matched_candidate"] += 1
        if tv_context:
            summary["matched_tv"] += 1
        if route_type:
            summary[f"route_{route_type}"] += 1

    conn.close()
    print(json.dumps(dict(summary), indent=2, sort_keys=True))


def _extract_exit_reason(notes: Any) -> str:
    text = str(notes or "")
    marker = "exit_reason="
    if marker not in text:
        return "backfill_unknown"
    return text.split(marker, 1)[1].split()[0].strip()


if __name__ == "__main__":
    main()
