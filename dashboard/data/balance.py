"""
dashboard/data/balance.py — Ledgerless Balance Layer (v19.1.ARCH)
Strictly broker-first. Paper mode excised.
"""

from __future__ import annotations

import os
import sys
import time
import logging
import db as _db

logger = logging.getLogger(__name__)

# Resolve project root
_DASH_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_DASH_DIR))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

def get_coinbase_balance() -> dict:
    """Return Coinbase account balance info from live API."""
    try:
        from runtime.live_account import get_live_account_size
        base = float(get_live_account_size())
    except Exception:
        base = 5000.0

    try:
        from execution.coinbase_broker import get_coinbase_broker
        broker = get_coinbase_broker()
        if not broker.is_connected():
            broker.connect()
        if broker.is_connected():
            data = broker._request("GET", "/api/v3/brokerage/cfm/balance_summary")
            summary = data.get("balance_summary", {})
            futures_bp = float(summary.get("futures_buying_power", {}).get("value", 0) or 0)
            cbi_cash = float(summary.get("total_usd_balance", {}).get("value", 0) or 0)
            total_balance = futures_bp if futures_bp > 0 else cbi_cash
            return {
                "balance": round(total_balance, 2),
                "base": base,
                "realized_pnl": round(total_balance - base, 2),
                "unrealized_pnl": 0.0,
                "buying_power": round(futures_bp, 2),
                "source": "live_api",
                "paper": False,
                "connected": True,
            }
    except Exception as e:
        logger.warning(f"[balance] Coinbase live API error: {e}")

    return {
        "balance": base, "base": base, "realized_pnl": 0.0, "unrealized_pnl": 0.0,
        "buying_power": 0.0, "source": "fallback", "paper": False, "connected": False,
    }

def get_ibkr_balance() -> dict:
    """Return IBKR account balance info (archived/disabled by default)."""
    return {
        "balance": 0.0, "source": "archived", "connected": False, "paper": False,
        "note": "FUTURES_LANE_ACTIVE=false — MES lane dormant",
    }

def get_spot_balance_summary() -> dict:
    """
    Return Coinbase spot balance summary.
    v19.1: Ledgerless. Pulls from broker directly.
    """
    try:
        from execution.coinbase_spot_broker import get_spot_broker
        broker = get_spot_broker()
        if not broker.is_connected():
            broker.connect()
        
        bal = broker.get_spot_balance()
        usd = float(bal.get("usd_available", 0))
        symbol_balances = bal.get("symbol_balances") or {}
        
        held_usd_by_symbol: dict[str, float] = {}
        for sym, qty in symbol_balances.items():
            qty_f = float(qty or 0.0)
            if qty_f <= 0: continue
            px = broker.get_mark_price(sym)
            held_usd_by_symbol[sym] = round(qty_f * px, 2) if px > 0 else 0.0
            
        spot_equity = round(usd + sum(held_usd_by_symbol.values()), 2)
        return {
            "usd_available": round(usd, 2),
            "held_usd_by_symbol": held_usd_by_symbol,
            "spot_equity": spot_equity,
            "source": "live_api",
        }
    except Exception as e:
        logger.warning(f"[balance] get_spot_balance_summary error: {e}")
        return {"usd_available": 0.0, "held_usd_by_symbol": {}, "spot_equity": 0.0, "source": "live_api_error"}

def get_all_balances(use_cache: bool = True) -> dict:
    cb = get_coinbase_balance()
    ib = get_ibkr_balance()
    return {
        "coinbase": cb,
        "ibkr": ib,
        "total_usd": round(cb["balance"] + ib["balance"], 2),
        "cached_at": time.time(),
    }
