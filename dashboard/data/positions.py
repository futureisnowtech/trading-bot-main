"""
dashboard/data/positions.py — Open positions and live price fetching.
"""

import json
import urllib.request

from db import _q


def get_open_positions():
    return _q("SELECT * FROM open_positions WHERE paper=1 ORDER BY ts_entry DESC")


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
