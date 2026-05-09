"""
dashboard/data/balance.py — Live account balance reading for all venues.

Reads real balances from each connected account:
  Coinbase  — live API (futures_buying_power)
  IBKR      — TWS NetLiquidation

Results are cached for 60 seconds so the 10s dashboard refresh doesn't hammer the APIs.

Usage:
    from data.balance import get_all_balances, get_coinbase_balance, get_ibkr_balance
"""

from __future__ import annotations

import os
import sys
import time
import sqlite3
import logging

logger = logging.getLogger(__name__)

# Ensure repo root and dashboard dir are importable
_DASH_DIR = os.path.dirname(os.path.abspath(__file__))  # dashboard/data/
_DASHBOARD_DIR = os.path.dirname(_DASH_DIR)  # dashboard/
_ROOT = os.path.dirname(_DASHBOARD_DIR)  # project root
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

# ── Cache ─────────────────────────────────────────────────────────────────────
_CACHE_TTL = 60.0  # seconds
_cache: dict = {}
_cache_ts: float = 0.0


def _cache_valid() -> bool:
    return (time.time() - _cache_ts) < _CACHE_TTL


def _set_cache(data: dict) -> None:
    global _cache, _cache_ts
    _cache = data
    _cache_ts = time.time()


# ── DB path ───────────────────────────────────────────────────────────────────
_DB_PATH = os.path.join(_ROOT, "logs", "trades.db")


def _unrealized_pnl() -> float:
    """Sum unrealized P&L across open positions using last-known prices from DB."""
    try:
        if not os.path.exists(_DB_PATH):
            return 0.0
        with sqlite3.connect(_DB_PATH, timeout=5, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT symbol, direction, qty, entry FROM open_positions "
                "WHERE paper=0 AND strategy NOT LIKE 'spot_%'",
            ).fetchall()
        if not rows:
            return 0.0
        # Try to get live prices from the bot's in-memory state
        try:
            sys.path.insert(0, _ROOT)
            import perps_engine

            pos_map = perps_engine.get_open_positions()
        except Exception:
            pos_map = {}
        total = 0.0
        for r in rows:
            sym = r["symbol"]
            direction = r["direction"] or "LONG"
            qty = float(r["qty"] or 0)
            entry = float(r["entry"] or 0)
            pos = pos_map.get(sym, {})
            current = float(pos.get("last_price", 0) or 0)
            if current <= 0:
                continue
            if direction == "LONG":
                total += (current - entry) * qty
            else:
                total += (entry - current) * qty
        return total
    except Exception as e:
        logger.debug(f"[balance] unrealized_pnl error: {e}")
        return 0.0


# ── Coinbase balance ──────────────────────────────────────────────────────────


def get_coinbase_balance() -> dict:
    """
    Return Coinbase account balance info.

    Returns dict:
        balance     float   — current equity in USD
        source      str     — 'live_api' | 'fallback'
        buying_power float  — futures buying power
        paper       bool    — always False
    """
    try:
        from runtime.live_account import get_live_account_size

        base = float(get_live_account_size())
    except Exception:
        base = 5000.0

    # Live mode — call Coinbase API
    try:
        from execution.coinbase_broker import get_coinbase_broker

        broker = get_coinbase_broker()
        if not broker.is_connected():
            broker.connect()
        if broker.is_connected():
            data = broker._request("GET", "/api/v3/brokerage/cfm/balance_summary")
            summary = data.get("balance_summary", {})
            # futures_buying_power is the correct measure of account capacity for
            # Coinbase US nano futures (CFM). total_usd_balance is only the CBI
            # cash sub-account (~$24) — NOT the total tradeable value (~$1,965).
            futures_bp = float(
                summary.get("futures_buying_power", {}).get("value", 0) or 0
            )
            cbi_cash = float(summary.get("total_usd_balance", {}).get("value", 0) or 0)
            # Use futures_buying_power as canonical balance; fall back to cbi_cash
            # only if buying power is absent.
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

    # Live but API failed — fall back to base
    return {
        "balance": base,
        "base": base,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "buying_power": 0.0,
        "source": "fallback",
        "paper": False,
        "connected": False,
    }


# ── IBKR balance ──────────────────────────────────────────────────────────────


def get_ibkr_balance() -> dict:
    """
    Return IBKR account balance info.

    Returns dict:
        balance     float   — NetLiquidation in USD (or 0.0 if disconnected)
        source      str     — 'live_tws' | 'unavailable' | 'archived'
        connected   bool
        paper       bool    — False
    """
    try:
        from config import FUTURES_LANE_ACTIVE
        from runtime.live_account import get_live_account_size

        futures_enabled = bool(FUTURES_LANE_ACTIVE)
        base = float(get_live_account_size())
    except Exception:
        futures_enabled, base = False, 5000.0

    if not futures_enabled:
        return {
            "balance": 0.0,
            "source": "archived",
            "connected": False,
            "paper": False,
            "note": "FUTURES_LANE_ACTIVE=false — MES lane dormant",
        }

    try:
        from execution.ibkr_broker import get_ibkr_broker

        broker = get_ibkr_broker()
        if broker.is_connected():
            bal = broker.get_account_balance()
            if bal and bal > 0:
                return {
                    "balance": round(bal, 2),
                    "source": "live_tws",
                    "connected": True,
                    "paper": False,
                }
    except Exception as e:
        logger.debug(f"[balance] IBKR balance error: {e}")

    # Not connected — return 0
    return {
        "balance": 0.0,
        "source": "unavailable",
        "connected": False,
        "paper": False,
    }


# ── Spot balance (v16.11) ─────────────────────────────────────────────────────


def get_spot_balance_summary() -> dict:
    """
    Return Coinbase spot balance summary for the configured Coinbase spot universe.
    Completely isolated from perp futures_buying_power.

    Returns dict:
        usd_available   float  — USD available for spot purchases
        btc_held_usd    float  — BTC held converted to USD at current price
        eth_held_usd    float  — ETH held converted to USD at current price
        sol_held_usd    float  — SOL held converted to USD at current price
        xrp_held_usd    float  — XRP held converted to USD at current price
        held_usd_by_symbol dict — symbol → held USD
        spot_equity     float  — USD cash + held USD across the spot book
        source          str    — 'live_api' | 'disabled' | 'live_api_error'
    """
    try:
        from config import SPOT_LANE_ACTIVE
    except Exception:
        SPOT_LANE_ACTIVE = False

    if not SPOT_LANE_ACTIVE:
        return {
            "usd_available": 0.0,
            "btc_held_usd": 0.0,
            "eth_held_usd": 0.0,
            "sol_held_usd": 0.0,
            "xrp_held_usd": 0.0,
            "held_usd_by_symbol": {},
            "spot_equity": 0.0,
            "source": "disabled",
        }

    # Live mode — call spot broker
    try:
        from execution.coinbase_spot_broker import get_spot_broker

        broker = get_spot_broker()
        if not broker.is_connected():
            broker.connect()
        bal = broker.get_spot_balance()
        usd = float(bal.get("usd_available", 0))
        symbol_balances = bal.get("symbol_balances") or {}

        # Only surface bot-managed positions (open_positions WHERE strategy LIKE 'spot_%')
        try:
            with sqlite3.connect(_DB_PATH, timeout=3, check_same_thread=False) as _c:
                _rows = _c.execute(
                    "SELECT DISTINCT symbol FROM open_positions "
                    "WHERE strategy LIKE 'spot_%' AND paper=0"
                ).fetchall()
            _bot_symbols = {str(r[0]).upper() for r in _rows}
        except Exception:
            _bot_symbols = set(symbol_balances.keys())

        held_usd_by_symbol: dict[str, float] = {}
        for sym, qty in symbol_balances.items():
            if sym.upper() not in _bot_symbols:
                continue
            qty_f = float(qty or 0.0)
            if qty_f <= 0:
                continue
            px = broker.get_mark_price(sym)
            held_usd_by_symbol[sym] = round(qty_f * px, 2) if px > 0 else 0.0
        spot_equity = round(usd + sum(held_usd_by_symbol.values()), 2)
        result = {
            "usd_available": round(usd, 2),
            "btc_held_usd": round(held_usd_by_symbol.get("BTC", 0.0), 2),
            "eth_held_usd": round(held_usd_by_symbol.get("ETH", 0.0), 2),
            "sol_held_usd": round(held_usd_by_symbol.get("SOL", 0.0), 2),
            "xrp_held_usd": round(held_usd_by_symbol.get("XRP", 0.0), 2),
            "held_usd_by_symbol": held_usd_by_symbol,
            "spot_equity": spot_equity,
            "source": "live_api",
        }
        for sym, amt in held_usd_by_symbol.items():
            result[f"{sym.lower()}_held_usd"] = round(amt, 2)
        return result
    except Exception as e:
        logger.warning(f"[balance] get_spot_balance_summary error: {e}")
        return {
            "usd_available": 0.0,
            "btc_held_usd": 0.0,
            "eth_held_usd": 0.0,
            "sol_held_usd": 0.0,
            "xrp_held_usd": 0.0,
            "held_usd_by_symbol": {},
            "spot_equity": 0.0,
            "source": "live_api_error",
        }


# ── Combined ──────────────────────────────────────────────────────────────────


def get_all_balances(use_cache: bool = True) -> dict:
    """
    Return balances for all accounts.  Cached for 60 seconds.

    Returns dict:
        coinbase    dict    — from get_coinbase_balance()
        ibkr        dict    — from get_ibkr_balance()
        total_usd   float   — sum of both account balances
        cached_at   float   — unix timestamp of last fetch
    """
    global _cache, _cache_ts

    if use_cache and _cache_valid() and _cache:
        return _cache

    cb = get_coinbase_balance()
    ib = get_ibkr_balance()

    # Only include IBKR in total when actually connected (0.0 when unavailable/disabled)
    result = {
        "coinbase": cb,
        "ibkr": ib,
        "total_usd": round(cb["balance"] + ib["balance"], 2),
        "cached_at": time.time(),
    }
    _set_cache(result)
    return result
