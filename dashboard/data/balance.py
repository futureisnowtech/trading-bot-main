"""
dashboard/data/balance.py — Live account balance reading for all venues.

Reads real balances from each connected account:
  Coinbase  — live API (futures_buying_power) or paper-computed (ACCOUNT_SIZE + DB P&L)
  IBKR      — TWS NetLiquidation or config ACCOUNT_SIZE when disconnected

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


def _balance_paper_mode() -> bool:
    """
    Single canonical paper/live flag for balance computations.
    Delegates to dashboard/db.py _runtime_paper_flag() — the one authoritative
    helper that reads system_runtime_state.process_mode first, then config fallback.
    Returns True = paper, False = live.
    """
    try:
        from db import _runtime_paper_flag

        return bool(_runtime_paper_flag())
    except Exception:
        pass
    # Hard fallback if db module is not importable (e.g. standalone CLI context)
    try:
        with sqlite3.connect(_DB_PATH, check_same_thread=False, timeout=3) as c:
            row = c.execute(
                "SELECT process_mode FROM system_runtime_state ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row and row[0] == "live":
                return False
    except Exception:
        pass
    try:
        from config import PAPER_TRADING

        return bool(PAPER_TRADING)
    except Exception:
        return True


def _paper_equity(base: float) -> float:
    """Compute paper account equity from trades DB: base + net realized P&L."""
    try:
        if not os.path.exists(_DB_PATH):
            return base
        with sqlite3.connect(_DB_PATH, timeout=5, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT COALESCE(SUM(pnl_usd) - SUM(COALESCE(fee_usd,0)), 0) AS net
                   FROM trades
                   WHERE paper=1
                     AND (source IS NULL OR source NOT IN
                          ('backtest','pre_v10_contaminated','bybit_paper','paper_v10'))"""
            ).fetchone()
            net = float(row["net"]) if row and row["net"] is not None else 0.0
        return base + net
    except Exception as e:
        logger.debug(f"[balance] paper_equity error: {e}")
        return base


def _unrealized_pnl() -> float:
    """Sum unrealized P&L across open positions using last-known prices from DB."""
    try:
        if not os.path.exists(_DB_PATH):
            return 0.0
        with sqlite3.connect(_DB_PATH, timeout=5, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT symbol, direction, qty, entry FROM open_positions WHERE paper=1"
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
        source      str     — 'live_api' | 'paper_computed' | 'fallback'
        buying_power float  — futures buying power (live only, else same as balance)
        paper       bool
    """
    try:
        from config import ACCOUNT_SIZE

        base = float(ACCOUNT_SIZE)
    except Exception:
        base = 5000.0
    paper = _balance_paper_mode()

    if paper:
        realized = _paper_equity(base)
        unrealized = _unrealized_pnl()
        total = realized + unrealized
        return {
            "balance": round(total, 2),
            "base": base,
            "realized_pnl": round(realized - base, 2),
            "unrealized_pnl": round(unrealized, 2),
            "buying_power": round(total, 2),
            "source": "paper_computed",
            "paper": True,
            "connected": True,
        }

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
        balance     float   — NetLiquidation in USD (or ACCOUNT_SIZE if disconnected)
        source      str     — 'live_tws' | 'fallback'
        connected   bool
        paper       bool    — always True for paper TWS account
    """
    try:
        from config import FUTURES_LANE_ACTIVE, ACCOUNT_SIZE

        futures_enabled = bool(FUTURES_LANE_ACTIVE)
        base = float(ACCOUNT_SIZE)
    except Exception:
        futures_enabled, base = False, 5000.0

    if not futures_enabled:
        return {
            "balance": 0.0,
            "source": "archived",
            "connected": False,
            "paper": True,
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
                    "paper": True,  # paper TWS account DUP590699
                }
    except Exception as e:
        logger.debug(f"[balance] IBKR balance error: {e}")

    # Not connected — return 0, not ACCOUNT_SIZE (that belongs to the crypto account)
    return {
        "balance": 0.0,
        "source": "unavailable",
        "connected": False,
        "paper": True,
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
