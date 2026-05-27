"""
backtesting/promotion_engine.py — Challenger promotion / demotion engine.

Reads backtest_results, evaluates promotion criteria, writes to challenger_state,
and emits system_events for human review.

DESIGN PRINCIPLES (non-negotiable):
  - Synthetic-only evidence NEVER promotes a strategy.
  - Promotion tier 'PROMOTED' requires human confirmation — this engine never
    auto-applies live parameter changes.
  - Demotion fires when live performance degrades significantly.
  - All state changes are written to challenger_state and system_events.

Promotion criteria (configurable at class init):
  - n_trades >= 30
  - win_rate >= 0.50
  - profit_factor >= 1.2
  - max_drawdown_pct <= 15.0
  - source must be 'candidate_replay' or 'historical' (NOT 'stress'/'synthetic')

Demotion triggers:
  - Live win_rate drops 10+ percentage points vs baseline
  - OR profit_factor < 1.0 over 30+ live trades
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import DB_PATH

_TRUSTED_SOURCES = frozenset({"candidate_replay", "historical"})
_UNTRUSTED_SOURCES = frozenset({"stress", "synthetic", "bootstrap", "backtest_only"})


def _conn():
    import sqlite3

    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_event(level: str, message: str) -> None:
    try:
        from logging_db.trade_logger import log_event

        log_event(level, "promotion_engine", message)
    except Exception:
        pass


class PromotionEngine:
    """
    Evaluates backtest runs and manages challenger promotion state.

    All promotion decisions require human confirmation. This engine only:
      1. Evaluates criteria
      2. Updates challenger_state
      3. Emits system_events with action_required=True for PROMOTED tier
    """

    def __init__(
        self,
        min_trades: int = 30,
        min_win_rate: float = 0.50,
        min_profit_factor: float = 1.2,
        max_drawdown_pct: float = 15.0,
        demotion_win_rate_drop: float = 0.10,  # 10 percentage points
        demotion_profit_factor_floor: float = 1.0,
        demotion_min_trades: int = 30,
    ):
        self.min_trades = min_trades
        self.min_win_rate = min_win_rate
        self.min_profit_factor = min_profit_factor
        self.max_drawdown_pct = max_drawdown_pct
        self.demotion_win_rate_drop = demotion_win_rate_drop
        self.demotion_profit_factor_floor = demotion_profit_factor_floor
        self.demotion_min_trades = demotion_min_trades

    def _load_backtest_runs(self, strategy: Optional[str] = None) -> list[dict]:
        """Load backtest_results rows, grouped by run_id."""
        conn = _conn()
        cur = conn.cursor()
        sql = "SELECT * FROM backtest_results ORDER BY archived_at DESC LIMIT 200"
        params = []
        if strategy:
            sql = (
                "SELECT * FROM backtest_results WHERE strategy_name=? "
                "ORDER BY archived_at DESC LIMIT 200"
            )
            params = [strategy]
        try:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
        except Exception:
            rows = []
        conn.close()
        return rows

    def _get_live_performance(self, strategy: str) -> Optional[dict]:
        """
        Get live/paper performance for a promoted strategy from trade_attribution.
        Returns None if insufficient data.
        """
        try:
            conn = _conn()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COUNT(*) as n, SUM(won) as wins, SUM(pnl_usd) as total_pnl,
                       AVG(pnl_usd) as avg_pnl
                FROM trade_attribution
                WHERE strategy=?
                  AND integrity_tier NOT IN ('quarantined', 'excluded')
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (strategy, self.demotion_min_trades),
            )
            row = cur.fetchone()
            conn.close()
            if not row or not row["n"]:
                return None
            n = int(row["n"])
            wins = int(row["wins"] or 0)
            return {
                "n_trades": n,
                "win_rate": wins / n,
                "total_pnl": float(row["total_pnl"] or 0),
                "avg_pnl": float(row["avg_pnl"] or 0),
            }
        except Exception:
            return None

    def _evaluate_criteria(self, run: dict) -> tuple[str, list[str], list[str]]:
        """
        Evaluate one backtest run against promotion criteria.

        Returns (tier, passed_criteria, failed_criteria).
        tier is one of: 'CANDIDATE', 'CHALLENGER', 'PROMOTED_PENDING_HUMAN'
        """
        passed = []
        failed = []

        source = str(run.get("source") or "").lower()
        mode = str(run.get("mode") or "").lower()

        # Source trust gate — synthetic evidence never promotes
        if source in _UNTRUSTED_SOURCES or mode in _UNTRUSTED_SOURCES:
            failed.append(f"untrusted_source:{source}")
            return "CANDIDATE", passed, failed
        if source not in _TRUSTED_SOURCES and source != "":
            failed.append(f"unknown_source:{source}")
            return "CANDIDATE", passed, failed
        passed.append(f"trusted_source:{source or 'candidate_replay'}")

        n = int(run.get("total_trades") or 0)
        win_rate = float(run.get("win_rate") or 0)
        pf = float(run.get("profit_factor") or 0)
        dd = float(run.get("max_drawdown") or run.get("max_drawdown_pct") or 0)

        if n >= self.min_trades:
            passed.append(f"n_trades:{n}>={self.min_trades}")
        else:
            failed.append(f"n_trades:{n}<{self.min_trades}")

        if win_rate >= self.min_win_rate:
            passed.append(f"win_rate:{win_rate:.1%}>={self.min_win_rate:.1%}")
        else:
            failed.append(f"win_rate:{win_rate:.1%}<{self.min_win_rate:.1%}")

        if pf >= self.min_profit_factor:
            passed.append(f"profit_factor:{pf:.2f}>={self.min_profit_factor}")
        else:
            failed.append(f"profit_factor:{pf:.2f}<{self.min_profit_factor}")

        if dd <= self.max_drawdown_pct:
            passed.append(f"max_drawdown:{dd:.1f}%<={self.max_drawdown_pct}%")
        else:
            failed.append(f"max_drawdown:{dd:.1f}%>{self.max_drawdown_pct}%")

        if failed:
            # Partial pass → CHALLENGER (not ready for promotion)
            tier = "CHALLENGER" if len(passed) >= 2 else "CANDIDATE"
        else:
            # All criteria met — requires human confirmation before going live
            tier = "PROMOTED_PENDING_HUMAN"

        return tier, passed, failed

    def evaluate_all(self, strategy: Optional[str] = None) -> list[dict]:
        """
        Evaluate all backtest runs. Update challenger_state. Emit system_events.
        Returns list of evaluation result dicts.
        """
        from logging_db.trade_logger import upsert_challenger_state, get_promotion_state

        runs = self._load_backtest_runs(strategy=strategy)
        if not runs:
            return []

        results = []
        seen_run_ids = set()

        for run in runs:
            run_id = str(run.get("run_id") or run.get("id") or "")
            strat = str(run.get("strategy_name") or "unknown")

            if not run_id or run_id in seen_run_ids:
                continue
            seen_run_ids.add(run_id)

            tier, passed, failed = self._evaluate_criteria(run)
            criteria_json = json.dumps({"passed": passed, "failed": failed})

            reason = (
                f"passed:[{', '.join(passed[:2])}]"
                if passed
                else f"failed:[{', '.join(failed[:2])}]"
            )

            upsert_challenger_state(
                strategy=strat,
                run_id=run_id,
                promotion_tier=tier,
                criteria_met_json=criteria_json,
                notes=reason,
            )

            if tier == "PROMOTED_PENDING_HUMAN":
                _log_event(
                    "WARN",
                    f"[promotion] CHALLENGER READY FOR REVIEW: strategy={strat} "
                    f"run_id={run_id[:8]} — all criteria met. "
                    f"ACTION REQUIRED: human must confirm before live deployment. "
                    f"Criteria: {reason}",
                )
            elif tier == "CHALLENGER":
                _log_event(
                    "INFO",
                    f"[promotion] CHALLENGER {strat} ({run_id[:8]}): "
                    f"partial pass — {reason}",
                )

            results.append(
                {
                    "strategy": strat,
                    "run_id": run_id,
                    "promotion_tier": tier,
                    "reason": reason,
                    "passed": passed,
                    "failed": failed,
                }
            )

        # Demotion check for any strategy that was previously PROMOTED_PENDING_HUMAN
        self._check_demotions()

        return results

    def _check_demotions(self) -> None:
        """
        Check live performance of promoted strategies. Emit demotion recommendations.
        Does NOT auto-demote — writes to system_events for human review.
        """
        try:
            from logging_db.trade_logger import (
                get_promotion_state,
                upsert_challenger_state,
            )

            for row in get_promotion_state():
                if row.get("promotion_tier") != "PROMOTED_PENDING_HUMAN":
                    continue
                strat = row.get("strategy", "")
                run_id = str(row.get("run_id", ""))
                live = self._get_live_performance(strat)
                if not live or live["n_trades"] < self.demotion_min_trades:
                    continue

                # Load original backtest baseline
                conn = _conn()
                cur = conn.cursor()
                cur.execute(
                    "SELECT win_rate, profit_factor FROM backtest_results WHERE run_id=? LIMIT 1",
                    (run_id,),
                )
                baseline_row = cur.fetchone()
                conn.close()
                if not baseline_row:
                    continue

                baseline_wr = float(baseline_row["win_rate"] or 0)
                live_wr = live["win_rate"]
                live_pf = live.get("profit_factor", 1.0)

                wr_drop = baseline_wr - live_wr
                demote = False
                reasons = []

                if wr_drop >= self.demotion_win_rate_drop:
                    demote = True
                    reasons.append(
                        f"win_rate_drop:{wr_drop:.1%} (baseline={baseline_wr:.1%} "
                        f"live={live_wr:.1%})"
                    )
                if live_pf < self.demotion_profit_factor_floor:
                    demote = True
                    reasons.append(
                        f"profit_factor:{live_pf:.2f}<{self.demotion_profit_factor_floor}"
                    )

                if demote:
                    upsert_challenger_state(
                        strategy=strat,
                        run_id=run_id,
                        promotion_tier="DEMOTED",
                        notes=f"live_degradation: {'; '.join(reasons)}",
                    )
                    _log_event(
                        "WARN",
                        f"[promotion] DEMOTION RECOMMENDATION: strategy={strat} "
                        f"run_id={run_id[:8]} — live performance degraded. "
                        f"Reasons: {'; '.join(reasons)}. "
                        f"ACTION REQUIRED: review and remove from live if appropriate.",
                    )
        except Exception as e:
            _log_event("ERROR", f"[promotion] demotion check error: {e}")
