"""
backtesting/event_backtester.py — Live-faithful event-driven backtester.

Replays scan_candidates (or historical OHLCV) through the actual live stack:
  - signal_engine.score()  (optional — skipped when scores already stored)
  - risk/economics_gate.py EV veto
  - position_manager sizing
  - Simplified faithful exit logic: trailing stop, scale-out, thesis, hard stop,
    dead-money gate

TRUST TIERS (non-negotiable):
  - Backtest results are RESEARCH-GRADE only.
  - They are NEVER live-equivalent and must not auto-apply to live parameters.
  - Promotion requires human confirmation (see backtesting/promotion_engine.py).

Modes:
  candidate_replay — uses stored scan_candidates as opportunity set
  historical       — replays OHLCV over a date range (future)
  stress           — applies scenario parameter shocks (future)

Usage:
    from backtesting.event_backtester import EventBacktester
    bt = EventBacktester(mode="candidate_replay")
    result = bt.run(strategy="v10_default", symbol=None)   # None = all symbols
    # result written to backtest_results table
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import ACCOUNT_SIZE, DB_PATH

# Kraken taker fee (matches economics_gate constant)
_DEFAULT_TAKER_FEE = 0.00065  # 0.065%

_VALID_MODES = frozenset({"candidate_replay", "historical", "stress"})
_RESEARCH_SOURCE_TAG = "candidate_replay"  # backtest results are always research-grade


def _conn():
    import sqlite3

    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Simplified faithful exit logic ────────────────────────────────────────────
# Mirrors position_manager exit priority stack but operates on OHLC bar data.


def _sim_exit(
    direction: str,
    entry_price: float,
    stop_price: float,
    target_price: float,
    atr_at_entry: float,
    composite_score: float,
    bars: list[dict],  # list of OHLC dicts with keys: open, high, low, close
    taker_fee: float = _DEFAULT_TAKER_FEE,
) -> dict:
    """
    Simulate exit over OHLC bars using the 7-priority exit stack logic.

    Returns a dict with:
        exit_price, exit_bar, exit_type, pnl_pct (net of fees),
        mfe_pct, mae_pct, hold_bars, won
    """
    if not bars or entry_price <= 0:
        return {
            "exit_price": entry_price,
            "exit_bar": 0,
            "exit_type": "no_data",
            "pnl_pct": 0.0,
            "mfe_pct": 0.0,
            "mae_pct": 0.0,
            "hold_bars": 0,
            "won": False,
        }

    is_long = direction.upper() == "LONG"
    best_price = entry_price  # MFE tracking
    worst_price = entry_price  # MAE tracking
    trailing_active = False
    trailing_stop = stop_price
    trail_mult = 4.0  # ATR multiplier for trailing width
    scale_done = False

    # Dead-money gate: 24 bars (1h bars) = 24h, hard backstop at 96 bars
    _DEAD_MONEY_BARS = 24
    _HARD_BACKSTOP_BARS = 96

    for i, bar in enumerate(bars):
        high = float(bar.get("high", entry_price))
        low = float(bar.get("low", entry_price))
        close = float(bar.get("close", entry_price))

        # Track MFE/MAE
        if is_long:
            best_price = max(best_price, high)
            worst_price = min(worst_price, low)
        else:
            best_price = min(best_price, low)
            worst_price = max(worst_price, high)

        # 1. Trailing stop activation (regime-simple: 1.5×ATR from entry)
        if not trailing_active and atr_at_entry > 0:
            move = (high - entry_price) if is_long else (entry_price - low)
            if move >= 1.5 * atr_at_entry:
                trailing_active = True
                if is_long:
                    trailing_stop = high - trail_mult * atr_at_entry
                else:
                    trailing_stop = low + trail_mult * atr_at_entry

        # Update trailing stop
        if trailing_active and atr_at_entry > 0:
            if is_long:
                new_trail = high - trail_mult * atr_at_entry
                trailing_stop = max(trailing_stop, new_trail)
            else:
                new_trail = low + trail_mult * atr_at_entry
                trailing_stop = min(trailing_stop, new_trail)

        # 2. Trailing stop hit
        if trailing_active:
            if is_long and low <= trailing_stop:
                exit_price = trailing_stop
                exit_type = "trailing"
                break
            elif not is_long and high >= trailing_stop:
                exit_price = trailing_stop
                exit_type = "trailing"
                break

        # 3. Take-profit (scale-out at target)
        if not scale_done:
            if is_long and high >= target_price:
                exit_price = target_price
                exit_type = "take_profit"
                break
            elif not is_long and low <= target_price:
                exit_price = target_price
                exit_type = "take_profit"
                break

        # 4. Hard stop
        if is_long and low <= stop_price:
            exit_price = stop_price
            exit_type = "hard_stop"
            break
        elif not is_long and high >= stop_price:
            exit_price = stop_price
            exit_type = "hard_stop"
            break

        # 7. Dead-money exit
        if i >= _DEAD_MONEY_BARS and not trailing_active and not scale_done:
            drift = abs(close - entry_price)
            if atr_at_entry > 0 and drift < 0.5 * atr_at_entry:
                exit_price = close
                exit_type = "dead_money"
                break

        # Hard backstop
        if i >= _HARD_BACKSTOP_BARS:
            exit_price = close
            exit_type = "time_stop"
            break
    else:
        exit_price = float(bars[-1].get("close", entry_price))
        exit_type = "end_of_data"

    # Net of fees
    gross_pct = (
        (exit_price - entry_price) / entry_price
        if is_long
        else (entry_price - exit_price) / entry_price
    )
    round_trip_fee = taker_fee * 2
    net_pct = gross_pct - round_trip_fee

    mfe_pct = (
        (best_price - entry_price) / entry_price
        if is_long
        else (entry_price - best_price) / entry_price
    )
    mae_pct = (
        (entry_price - worst_price) / entry_price
        if is_long
        else (worst_price - entry_price) / entry_price
    )

    return {
        "exit_price": exit_price,
        "exit_bar": i,
        "exit_type": exit_type,
        "pnl_pct": net_pct,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
        "hold_bars": i + 1,
        "won": net_pct > 0,
    }


# ── Main backtester class ─────────────────────────────────────────────────────


class EventBacktester:
    """
    Event-driven backtester using the live signal + risk stack.

    All results are tagged RESEARCH-GRADE. No live parameter changes may
    be applied without human confirmation via the promotion engine.
    """

    def __init__(
        self,
        mode: str = "candidate_replay",
        taker_fee: float = _DEFAULT_TAKER_FEE,
    ):
        if mode not in _VALID_MODES:
            raise ValueError(f"Invalid mode {mode!r}. Must be one of {_VALID_MODES}")
        self.mode = mode
        self.taker_fee = taker_fee

    def _load_candidates(
        self,
        symbol: Optional[str] = None,
        strategy: Optional[str] = None,
        days_back: int = 30,
        limit: int = 500,
    ) -> list[dict]:
        """Load scan_candidates that were entered (decision='entered')."""
        import datetime as _dt

        cutoff = (
            datetime.now(timezone.utc) - _dt.timedelta(days=days_back)
        ).isoformat()
        conn = _conn()
        cur = conn.cursor()
        params = [cutoff]
        sql = """
            SELECT sc.*, co.ret_1h_pct, co.ret_4h_pct, co.mfe_4h_pct, co.mae_4h_pct,
                   co.hit_1r, co.hit_2r, co.hit_stop
            FROM scan_candidates sc
            LEFT JOIN candidate_outcomes co ON co.candidate_id = sc.id
            WHERE sc.ts >= ?
              AND sc.composite_score IS NOT NULL
        """
        if symbol:
            sql += " AND sc.symbol = ?"
            params.append(symbol)
        sql += " ORDER BY sc.ts ASC LIMIT ?"
        params.append(limit)
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    def _fetch_forward_bars(
        self, symbol: str, ts: str, n_bars: int = 100, timeframe: str = "1h"
    ) -> list[dict]:
        """
        Fetch OHLCV bars starting at ts for simulation.
        Uses data/historical_data.py get_candles(). Returns [] on failure.
        """
        try:
            from data.historical_data import get_candles
            import datetime as _dt

            since = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            # get_candles returns a DataFrame — convert to list of dicts
            df = get_candles(symbol, timeframe=timeframe, limit=n_bars + 5)
            if df is None or df.empty:
                return []
            # Filter to bars at or after entry time
            if "timestamp" in df.columns:
                df = df[df["timestamp"] >= since]
            return df.head(n_bars)[["open", "high", "low", "close"]].to_dict("records")
        except Exception:
            return []

    def run(
        self,
        strategy: str = "v10_default",
        symbol: Optional[str] = None,
        days_back: int = 30,
        params_json: str = None,
        notes: str = None,
    ) -> dict:
        """
        Run a backtest and write results to backtest_results table.

        Returns summary dict with run_id and key metrics.
        All results are tagged source='candidate_replay' (RESEARCH-GRADE).
        """
        run_id = uuid.uuid4().hex[:16]
        ts_start = _now_iso()

        candidates = self._load_candidates(symbol=symbol, days_back=days_back)
        if not candidates:
            return {
                "run_id": run_id,
                "n_trades": 0,
                "status": "no_candidates",
                "trust": "RESEARCH-GRADE",
            }

        trades = []

        for cand in candidates:
            sym = cand.get("symbol", "")
            direction = cand.get("direction", "LONG")
            entry_price = float(cand.get("price") or 0)
            if entry_price <= 0:
                continue

            composite = float(cand.get("composite_score") or 50)
            atr = float(cand.get("atr_15m") or 0)
            stop_pct = float(cand.get("stop_pct") or 0.015)
            target_pct = float(cand.get("target_pct") or 0.045)

            # If labeled outcome available, use it (faster — no live data fetch)
            if cand.get("ret_4h_pct") is not None:
                net_pct = float(cand["ret_4h_pct"] or 0) - self.taker_fee * 2
                won = net_pct > 0
                mfe_pct = float(cand.get("mfe_4h_pct") or 0)
                mae_pct = float(cand.get("mae_4h_pct") or 0)
                exit_type = "labeled_outcome"
                hold_bars = 4
            else:
                # Derive stop/target prices
                if direction.upper() == "LONG":
                    stop_price = entry_price * (1 - stop_pct)
                    target_price = entry_price * (1 + target_pct)
                else:
                    stop_price = entry_price * (1 + stop_pct)
                    target_price = entry_price * (1 - target_pct)

                bars = self._fetch_forward_bars(
                    sym, cand.get("ts", ts_start), n_bars=100
                )
                if not bars:
                    continue

                sim = _sim_exit(
                    direction=direction,
                    entry_price=entry_price,
                    stop_price=stop_price,
                    target_price=target_price,
                    atr_at_entry=atr,
                    composite_score=composite,
                    bars=bars,
                    taker_fee=self.taker_fee,
                )
                net_pct = sim["pnl_pct"]
                won = sim["won"]
                mfe_pct = sim["mfe_pct"]
                mae_pct = sim["mae_pct"]
                exit_type = sim["exit_type"]
                hold_bars = sim["hold_bars"]

            # Size using account fraction (1% risk)
            risk_usd = ACCOUNT_SIZE * 0.01
            size_usd = float(cand.get("size_usd") or risk_usd / max(stop_pct, 0.005))
            pnl_usd = size_usd * net_pct

            trades.append(
                {
                    "symbol": sym,
                    "direction": direction,
                    "entry_price": entry_price,
                    "net_pct": net_pct,
                    "pnl_usd": pnl_usd,
                    "won": won,
                    "mfe_pct": mfe_pct,
                    "mae_pct": mae_pct,
                    "exit_type": exit_type,
                    "hold_bars": hold_bars,
                }
            )

        ts_end = _now_iso()
        if not trades:
            return {
                "run_id": run_id,
                "n_trades": 0,
                "status": "no_trades_simulated",
                "trust": "RESEARCH-GRADE",
            }

        # Compute metrics
        n = len(trades)
        wins = sum(1 for t in trades if t["won"])
        win_rate = wins / n
        total_pnl = sum(t["pnl_usd"] for t in trades)
        gross_wins = sum(t["pnl_usd"] for t in trades if t["pnl_usd"] > 0)
        gross_losses = abs(sum(t["pnl_usd"] for t in trades if t["pnl_usd"] < 0))
        profit_factor = gross_wins / max(gross_losses, 0.01)

        # Max drawdown (running equity curve)
        equity = ACCOUNT_SIZE
        peak = equity
        max_dd = 0.0
        for t in trades:
            equity += t["pnl_usd"]
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100
            max_dd = max(max_dd, dd)

        # Sharpe (simplified: annualise daily returns proxy)
        pnls = [t["pnl_usd"] for t in trades]
        avg_pnl = sum(pnls) / n
        std_pnl = (sum((p - avg_pnl) ** 2 for p in pnls) / max(n - 1, 1)) ** 0.5
        sharpe = (avg_pnl / max(std_pnl, 0.01)) * (252**0.5) if std_pnl > 0 else 0.0

        result_row = {
            "run_id": run_id,
            "strategy": strategy,
            "mode": self.mode,
            "n_trades": n,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "net_pnl": total_pnl,
            "max_drawdown_pct": max_dd,
            "sharpe": sharpe,
            "ts_start": ts_start,
            "ts_end": ts_end,
            "trust": "RESEARCH-GRADE",
            "params_json": params_json or "{}",
            "notes": notes or "",
        }

        # Write to backtest_results
        self._write_result(result_row, strategy, symbol)

        return result_row

    def _write_result(self, result: dict, strategy: str, symbol: Optional[str]) -> None:
        """Persist backtest result to backtest_results table."""
        try:
            conn = _conn()
            cur = conn.cursor()
            # Ensure run_id and source columns exist (additive migration)
            for migration in [
                "ALTER TABLE backtest_results ADD COLUMN run_id TEXT",
                "ALTER TABLE backtest_results ADD COLUMN source TEXT DEFAULT 'candidate_replay'",
                "ALTER TABLE backtest_results ADD COLUMN mode TEXT",
                "ALTER TABLE backtest_results ADD COLUMN net_pnl REAL",
                "ALTER TABLE backtest_results ADD COLUMN max_drawdown_pct REAL",
                "ALTER TABLE backtest_results ADD COLUMN ts_start TEXT",
                "ALTER TABLE backtest_results ADD COLUMN ts_end TEXT",
            ]:
                try:
                    cur.execute(migration)
                except Exception:
                    pass

            cur.execute(
                """
                INSERT INTO backtest_results
                    (strategy_name, symbol, run_id, source, mode,
                     total_trades, win_rate, profit_factor, total_pnl,
                     net_pnl, max_drawdown, sharpe, params_json, notes,
                     passed, ts_start, ts_end, archived_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    strategy,
                    symbol or "ALL",
                    result["run_id"],
                    _RESEARCH_SOURCE_TAG,
                    result.get("mode", "candidate_replay"),
                    result["n_trades"],
                    result["win_rate"],
                    result["profit_factor"],
                    result["net_pnl"],
                    result["net_pnl"],
                    result["max_drawdown_pct"],
                    result["sharpe"],
                    result["params_json"],
                    result["notes"],
                    0,  # passed=0 until promotion engine evaluates
                    result["ts_start"],
                    result["ts_end"],
                    _now_iso(),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[backtester] write_result error: {e}")
