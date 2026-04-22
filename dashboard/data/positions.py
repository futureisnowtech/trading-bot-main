"""
dashboard/data/positions.py — Open positions and live price fetching.
"""

from datetime import datetime
import json
import urllib.request

from db import _q, _q1, _runtime_paper_flag


def _db_open_positions():
    return _q(
        "SELECT * FROM open_positions WHERE paper=? ORDER BY ts_entry DESC",
        (_runtime_paper_flag(),),
    )


def _db_perp_positions():
    return _q(
        "SELECT * FROM open_positions WHERE strategy NOT LIKE 'spot_%' AND paper=? ORDER BY ts_entry DESC",
        (_runtime_paper_flag(),),
    )


def get_spot_positions_dashboard():
    """Open spot-only positions (strategy LIKE 'spot_%'). For spot section display."""
    db_rows = _q(
        "SELECT * FROM open_positions WHERE strategy LIKE 'spot_%' AND paper=? ORDER BY ts_entry DESC",
        (_runtime_paper_flag(),),
    )
    live_positions = _get_live_coinbase_spot_positions()
    if live_positions is not None:
        return _merge_live_spot_rows(live_positions, db_rows)
    return db_rows


def _ts_sort_key(row: dict) -> datetime:
    raw = str(row.get("ts_entry") or "")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return datetime.min


def _get_live_coinbase_perp_positions() -> dict | None:
    if _runtime_paper_flag():
        return None
    if not _crypto_live_snapshot_enabled():
        return None
    try:
        from execution.coinbase_broker import get_coinbase_broker

        broker = get_coinbase_broker()
        if bool(getattr(broker, "_paper", False)):
            return None
        if not broker.is_connected():
            broker.connect()
        if not broker.is_connected():
            return None
        return broker.sync_live_positions()
    except Exception:
        return None


def _get_live_coinbase_spot_positions() -> list[dict] | None:
    if _runtime_paper_flag():
        return None
    if not _crypto_live_snapshot_enabled():
        return None
    try:
        from execution.coinbase_spot_broker import get_spot_broker

        broker = get_spot_broker()
        if bool(getattr(broker, "_paper", False)):
            return None
        if not broker.is_connected():
            broker.connect()
        if not broker.is_connected():
            return None
        return broker.sync_live_holdings()
    except Exception:
        return None


def _crypto_live_snapshot_enabled() -> bool:
    row = _q1(
        "SELECT connected, mode FROM lane_runtime_state "
        "WHERE lane_id='crypto' ORDER BY id DESC LIMIT 1"
    )
    return bool(row.get("connected")) and str(row.get("mode") or "").lower() == "live"


def _merge_live_perp_rows(live_positions: dict, db_rows: list[dict]) -> list[dict]:
    db_by_symbol = {str(row.get("symbol") or "").upper(): row for row in db_rows}
    merged: list[dict] = []
    for symbol, live in live_positions.items():
        db_row = dict(db_by_symbol.get(symbol, {}))
        merged.append(
            {
                **db_row,
                "symbol": symbol,
                "strategy": db_row.get("strategy") or "v10_perp",
                "paper": 0,
                "direction": live.get("direction") or db_row.get("direction") or "LONG",
                "qty": float(live.get("qty") or db_row.get("qty") or 0.0),
                "entry": float(
                    live.get("entry_price") or db_row.get("entry") or 0.0
                ),
                "contracts": float(live.get("contracts") or 0.0),
                "current_price": float(live.get("current_price") or 0.0),
                "unrealized_pnl": float(live.get("unrealized_pnl") or 0.0),
                "venue": live.get("venue") or "coinbase",
            }
        )
    merged.sort(key=_ts_sort_key, reverse=True)
    return merged


def _merge_live_spot_rows(live_positions: list[dict], db_rows: list[dict]) -> list[dict]:
    db_by_symbol = {str(row.get("symbol") or "").upper(): row for row in db_rows}
    merged: list[dict] = []
    for live in live_positions:
        symbol = str(live.get("symbol") or "").upper()
        db_row = dict(db_by_symbol.get(symbol, {}))
        merged.append(
            {
                **db_row,
                "symbol": symbol,
                "strategy": db_row.get("strategy") or f"spot_{symbol.lower()}",
                "paper": 0,
                "direction": "LONG",
                "qty": float(live.get("qty") or db_row.get("qty") or 0.0),
                "entry": float(
                    live.get("avg_entry") or db_row.get("entry") or 0.0
                ),
                "current_value": float(live.get("current_value") or 0.0),
                "venue": "coinbase_spot",
            }
        )
    merged.sort(key=_ts_sort_key, reverse=True)
    return merged


def get_perp_positions():
    """
    Open perp-only positions.

    Live mode uses Coinbase CFM `/cfm/positions` as the canonical source of truth
    and only borrows DB metadata (stop/target/ts_entry/notes) when a matching row
    exists. Paper mode and live API fallback continue to use open_positions.
    """
    db_rows = _db_perp_positions()
    live_positions = _get_live_coinbase_perp_positions()
    if live_positions is not None:
        return _merge_live_perp_rows(live_positions, db_rows)
    return db_rows


def get_open_positions():
    """All open positions for current mode (perp + spot)."""
    if _runtime_paper_flag():
        return _db_open_positions()
    combined = get_perp_positions() + get_spot_positions_dashboard()
    combined.sort(key=_ts_sort_key, reverse=True)
    return combined


def get_live_prices(symbols: list) -> dict:
    prices = {}
    if not symbols:
        return prices
    try:
        url = "https://futures.kraken.com/derivatives/api/v3/tickers"
        with urllib.request.urlopen(url, timeout=4) as resp:
            data = json.loads(resp.read())
        for t in data.get("tickers", []):
            sym = t.get("symbol", "")
            price = t.get("markPrice") or t.get("last") or 0
            if sym and price:
                prices[sym] = float(price)
    except Exception:
        pass
    missing = [s for s in symbols if s not in prices]
    if missing:
        try:
            req_data = json.dumps({"type": "allMids"}).encode()
            req = urllib.request.Request(
                "https://api.hyperliquid.xyz/info",
                data=req_data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                mids = json.loads(resp.read())
            for sym in missing:
                if sym in mids:
                    prices[sym] = float(mids[sym])
        except Exception:
            pass
    return prices
