"""
learning/live_backtest_validator.py — Background rolling backtest validator.

Runs every 4 hours in a background thread. Backtests the top 3 crypto
pairs on the last 30 days of archived price data (zero API calls —
uses price_archive.db flywheel built from live candles).

Results are stored in the backtest_results table and injected into
every debate as a "ROLLING BACKTEST" context block.

This closes the loop between strategy validation and live trading:
agents can see "strategy passed 30d backtest with 58% win rate" vs
"strategy failing recently — only 38% win rate last 30 days" and
adjust their conviction accordingly.

Fails gracefully: if backtest fails, get_recent_backtest_context()
returns "" and debate proceeds without backtest context.
"""
import os
import sys
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH, CRYPTO_PAIRS

_BACKTEST_INTERVAL_SEC = 4 * 3600   # run every 4 hours
_last_run_ts: float = 0
_result_cache: dict[str, dict] = {}  # {symbol: result_dict}
_cache_ts: float = 0
_CACHE_TTL = 3600                    # 1-hour result cache


# ── DB helper ──────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


# ── Backtest runner ────────────────────────────────────────────────────────

def _run_single(symbol: str, days: int = 30) -> Optional[dict]:
    """
    Run a 30-day backtest for one symbol using the price archive.
    Returns a compact result dict or None on failure.
    """
    try:
        from backtesting.backtest_engine import run_with_intelligence
        result = run_with_intelligence(
            symbol=symbol,
            strategy='crypto',
            period='1mo',
            interval='5m',
            variant='workhorse',
            archive_to_db=False,    # don't double-write backtest_results
            validate=True,
        )
        if 'error' in result:
            return None
        return result
    except Exception as e:
        print(f"[live_backtest] {symbol}: {e}")
        return None


def _write_result(symbol: str, result: dict):
    """Persist a validation result to backtest_results table."""
    now     = datetime.now(timezone.utc).isoformat()
    period_start = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    try:
        with _conn() as c:
            c.execute("""
                INSERT INTO backtest_results
                    (strategy_name, variant, symbol, timeframe,
                     period_start, period_end,
                     total_trades, win_rate, total_pnl, sharpe,
                     max_drawdown, avg_pnl, profit_factor, passed,
                     archived_at, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                'crypto', 'workhorse', symbol, '5min',
                period_start, now,
                result.get('total_trades', 0),
                result.get('win_rate'),
                result.get('total_pnl'),
                result.get('sharpe'),
                result.get('max_drawdown'),
                result.get('avg_pnl'),
                result.get('profit_factor'),
                int(bool(result.get('passed', False))),
                now,
                'live_rolling_30d',
            ))
    except Exception as e:
        print(f"[live_backtest] DB write {symbol}: {e}")


def _run_all():
    """Background thread: backtest top 3 crypto pairs, store results."""
    global _last_run_ts, _cache_ts
    _last_run_ts = time.time()

    symbols = CRYPTO_PAIRS[:3]
    print(f"[live_backtest] Starting 30-day rolling validation → {symbols}")

    for sym in symbols:
        result = _run_single(sym)
        if result:
            _write_result(sym, result)
            wr_str = f"{result.get('win_rate', 0)*100:.0f}%" if result.get('win_rate') is not None else "?"
            sh_str = f"{result.get('sharpe', 0):.2f}" if result.get('sharpe') is not None else "?"
            ok     = "✅ PASS" if result.get('passed') else "❌ FAIL"
            print(f"[live_backtest] {sym} 30d: wr={wr_str} sharpe={sh_str} "
                  f"trades={result.get('total_trades', 0)} {ok}")

    _cache_ts = 0   # force cache refresh on next read


# ── Public API ─────────────────────────────────────────────────────────────

def trigger_background_backtest():
    """
    Call once per scan cycle. Starts a background backtest thread if
    4+ hours have elapsed since the last run. Non-blocking.
    """
    if (time.time() - _last_run_ts) < _BACKTEST_INTERVAL_SEC:
        return
    t = threading.Thread(target=_run_all, daemon=True, name='live_backtest')
    t.start()


def get_recent_backtest_context(symbol: str) -> str:
    """
    Returns a one-line context string for debate injection.
    E.g.: "ROLLING BACKTEST (30d): win_rate=55% sharpe=0.92 ✅ PASS (41 trades)"

    Returns "" if no recent result is available.
    """
    global _result_cache, _cache_ts

    if (time.time() - _cache_ts) > _CACHE_TTL:
        # Refresh from DB — keep only results from the last 10 hours
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
        try:
            with _conn() as c:
                rows = c.execute("""
                    SELECT symbol, win_rate, sharpe, total_trades,
                           max_drawdown, passed, archived_at
                    FROM backtest_results
                    WHERE strategy_name = 'crypto'
                      AND notes LIKE '%live_rolling%'
                      AND archived_at > ?
                    ORDER BY archived_at DESC
                """, (cutoff,)).fetchall()

            seen: set[str] = set()
            _result_cache = {}
            for r in rows:
                if r['symbol'] not in seen:
                    seen.add(r['symbol'])
                    _result_cache[r['symbol']] = dict(r)
            _cache_ts = time.time()
        except Exception:
            pass

    r = _result_cache.get(symbol)
    if not r:
        return ""

    wr_str = f"{r['win_rate']*100:.0f}%" if r.get('win_rate') is not None else "N/A"
    sh_str = f"{r['sharpe']:.2f}"        if r.get('sharpe')   is not None else "N/A"
    dd_str = f"{r['max_drawdown']*100:.0f}%" if r.get('max_drawdown') is not None else "N/A"
    status = "✅ PASS" if r.get('passed') else "❌ FAIL"
    n      = r.get('total_trades', 0)

    return (
        f"ROLLING BACKTEST (30d, {n} trades): "
        f"win_rate={wr_str} sharpe={sh_str} max_dd={dd_str} {status}"
    )
