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
    Solely relies on live Coinbase Spot Broker payload.
    """
    try:
        from execution.coinbase_spot_broker import get_spot_broker
        broker = get_spot_broker()
        holdings = broker.get_spot_positions() or []
        
        merged = []
        for h in holdings:
            sym = str(h.get("symbol") or "").upper()
            qty = float(h.get("qty") or 0.0)
            avg_entry = float(h.get("avg_entry") or 0.0)
            current_value = float(h.get("current_value") or 0.0)
            current_price = float(current_value / qty) if qty > 0 else avg_entry
            
            merged.append({
                "symbol": sym,
                "strategy": f"spot_{sym.lower()}",
                "paper": 0,
                "direction": "LONG",
                "qty": qty,
                "entry": avg_entry,
                "current_price": current_price,
                "current_value": current_value,
                "unrealized_pnl": current_value - (qty * avg_entry),
                "venue": "coinbase_spot",
                "is_bot_managed": True
            })
        return merged
    except Exception as e:
        logger.error(f"[dashboard] Failed to fetch ledgerless spot truth: {e}")
        return []

def get_perp_positions():
    """
    v19.1: Ledgerless Perp Truth.
    Coinbase CFM API is canonical. Metadata projected directly from broker state.
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
            
        merged = []
        for symbol, live in live_positions.items():
            merged.append({
                "symbol": symbol,
                "strategy": "v10_perp",
                "paper": 0,
                "direction": live.get("direction") or "LONG",
                "qty": float(live.get("qty") or 0.0),
                "entry": float(live.get("entry_price") or 0.0),
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
