"""
dashboard/data/positions.py — Open positions and live price fetching.
"""

import json
import urllib.request

from db import _q, _runtime_paper_flag


def get_open_positions():
    """All open positions for current mode (perp + spot)."""
    return _q(
        "SELECT * FROM open_positions WHERE paper=? ORDER BY ts_entry DESC",
        (_runtime_paper_flag(),),
    )


def get_perp_positions():
    """Open perp-only positions (excludes spot_ strategy rows). For P&L and margin calcs."""
    return _q(
        "SELECT * FROM open_positions WHERE strategy NOT LIKE 'spot_%' AND paper=? ORDER BY ts_entry DESC",
        (_runtime_paper_flag(),),
    )


def get_spot_positions_dashboard():
    """Open spot-only positions (strategy LIKE 'spot_%'). For spot section display."""
    return _q(
        "SELECT * FROM open_positions WHERE strategy LIKE 'spot_%' AND paper=? ORDER BY ts_entry DESC",
        (_runtime_paper_flag(),),
    )


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
