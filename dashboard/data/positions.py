"""
dashboard/data/positions.py — Ledgerless Truth Layer (v19.1.ARCH)
Strictly broker-first projection. The 'open_positions' ledger is retired.
"""

from datetime import datetime
import logging
import db as _db

logger = logging.getLogger(__name__)

def _ts_sort_key(row: dict) -> datetime:
    raw = str(row.get("ts_entry") or row.get("timestamp") or "")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return datetime.min

def get_spot_positions_dashboard():
    """
    v19.1: Ledgerless Spot Truth (Broker-Direct).
    """
    try:
        from execution.coinbase_spot_broker import get_spot_broker
        broker = get_spot_broker()
        holdings = broker.get_spot_positions() or []
        
        # Dashboard-specific enrichment is handled by the caller or by server.py snapshotting
        return holdings
    except Exception as e:
        logger.error(f"[dashboard] Failed to fetch ledgerless spot truth: {e}")
        return []

def get_perp_positions():
    """
    v19.1: Ledgerless Perp Truth.
    Coinbase CFM API is canonical. Metadata borrowed from DB via broker sync.
    """
    try:
        from execution.coinbase_broker import get_coinbase_broker
        broker = get_coinbase_broker()
        if not broker.is_connected():
            broker.connect()
        if not broker.is_connected():
            return []
        
        live_positions = broker.sync_live_positions()
        if not live_positions:
            return []
            
        # Enrich with DB metadata for dashboard rendering
        # (This is still broker-first because we iterate over live_positions)
        db_rows = _db._q("SELECT * FROM open_positions WHERE strategy NOT LIKE 'spot_%' AND paper=0")
        db_by_symbol = {str(row.get("symbol") or "").upper(): row for row in db_rows}
        
        merged = []
        for symbol, live in live_positions.items():
            db_row = dict(db_by_symbol.get(symbol, {}))
            merged.append({
                **db_row,
                "symbol": symbol,
                "strategy": db_row.get("strategy") or "v10_perp",
                "paper": 0,
                "direction": live.get("direction") or db_row.get("direction") or "LONG",
                "qty": float(live.get("qty") or 0.0),
                "entry": float(live.get("entry_price") or db_row.get("entry") or 0.0),
                "contracts": float(live.get("contracts") or 0.0),
                "current_price": float(live.get("current_price") or 0.0),
                "unrealized_pnl": float(live.get("unrealized_pnl") or 0.0),
                "venue": "coinbase",
            })
        merged.sort(key=_ts_sort_key, reverse=True)
        return merged
    except Exception as e:
        logger.error(f"[dashboard] Failed to fetch perp truth: {e}")
        return []

def get_open_positions():
    """All open positions (perp + spot)."""
    combined = get_perp_positions() + get_spot_positions_dashboard()
    combined.sort(key=_ts_sort_key, reverse=True)
    return combined

def get_crypto_deployed_snapshot() -> dict:
    """
    Canonical crypto deployment truth for dashboard surfaces.
    Uses the same ledgerless readers.
    """
    perp_positions = get_perp_positions()
    spot_positions = get_spot_positions_dashboard()

    perp_deployed_usd = sum(
        abs(float(p.get("qty") or 0.0)) * float(p.get("current_price") or p.get("entry") or 0.0)
        for p in perp_positions
    )
    spot_deployed_usd = sum(
        float(p.get("current_value") or 0.0) or (abs(float(p.get("qty") or 0.0)) * float(p.get("current_price") or p.get("entry") or 0.0))
        for p in spot_positions
    )

    return {
        "perp_positions": perp_positions,
        "spot_positions": spot_positions,
        "perp_deployed_usd": round(perp_deployed_usd, 2),
        "spot_deployed_usd": round(spot_deployed_usd, 2),
        "deployed_usd": round(perp_deployed_usd + spot_deployed_usd, 2),
        "open_count": len(perp_positions) + len(spot_positions),
    }

def get_live_prices(symbols: list) -> dict:
    """Mock/Fallback price fetcher for dashboard indicators."""
    # Logic remains similar but simplified
    import json
    import urllib.request
    prices = {}
    if not symbols: return prices
    try:
        url = "https://futures.kraken.com/derivatives/api/v3/tickers"
        with urllib.request.urlopen(url, timeout=4) as resp:
            data = json.loads(resp.read())
        for t in data.get("tickers", []):
            sym = t.get("symbol", "")
            price = t.get("markPrice") or t.get("last") or 0
            if sym and price: prices[sym] = float(price)
    except: pass
    return prices
