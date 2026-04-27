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
        _paper_int = 1 if _balance_paper_mode() else 0
        with sqlite3.connect(_DB_PATH, timeout=5, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT symbol, direction, qty, entry FROM open_positions "
                "WHERE paper=? AND strategy NOT LIKE 'spot_%'",
                (_paper_int,),
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


def _paper_spot_balance_summary(base: float) -> dict:
    """
    Paper-mode spot balance summary derived from open_positions.

    We keep spot truth isolated from perp balances, but paper spot should still
    surface something useful in the dashboard instead of all-zero placeholders.
    """
    try:
        with sqlite3.connect(_DB_PATH, timeout=5, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT symbol, qty, entry
                FROM open_positions
                WHERE strategy LIKE 'spot_%' AND paper=1
                """
            ).fetchall()
    except Exception as e:
        logger.debug(f"[balance] paper spot summary query error: {e}")
        rows = []

    held_by_symbol: dict[str, float] = {}
    deployed_usd = 0.0
    for row in rows:
        sym = str(row["symbol"] or "").upper()
        qty = float(row["qty"] or 0.0)
        entry = float(row["entry"] or 0.0)
        current_value = qty * entry
        deployed_usd += current_value
        held_by_symbol[sym] = held_by_symbol.get(sym, 0.0) + current_value

    result = {
        "usd_available": round(max(base - deployed_usd, 0.0), 2),
        "btc_held_usd": round(held_by_symbol.get("BTC", 0.0), 2),
        "eth_held_usd": round(held_by_symbol.get("ETH", 0.0), 2),
        "sol_held_usd": round(held_by_symbol.get("SOL", 0.0), 2),
        "xrp_held_usd": round(held_by_symbol.get("XRP", 0.0), 2),
        "held_usd_by_symbol": {k: round(v, 2) for k, v in held_by_symbol.items()},
        "spot_equity": round(max(base - deployed_usd, 0.0) + deployed_usd, 2),
        "source": "paper_db",
    }
    for sym, amt in held_by_symbol.items():
        result[f"{sym.lower()}_held_usd"] = round(amt, 2)
    return result


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
    paper = _balance_paper_mode()
    try:
        from runtime.live_account import get_live_account_size

        base = float(get_live_account_size(paper=paper))
    except Exception:
        base = 5000.0

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
        source          str    — 'live_api' | 'paper' | 'disabled'
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

    paper = _balance_paper_mode()

    if paper:
        try:
            from runtime.live_account import get_live_account_size

            base = float(get_live_account_size(paper=True))
        except Exception:
            base = 5000.0
        return _paper_spot_balance_summary(base)

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
        # Manual Coinbase purchases are excluded — they are irrelevant to this bot.
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
