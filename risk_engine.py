"""
risk_engine.py — Portfolio-level risk: VaR/CVaR, correlation matrix, margin utilization.

Hard limits (account-size aware):
  Kill switch threshold : delegated to kill_switch / live-account truth
  Daily loss halt       : -5%
  Max single position   : account-aware via downstream sizing
  Max deployed          : 95% of current account balance
  Margin above 60%      : No new positions
  Margin above 75%      : Reduce existing
  Margin above 85%      : Emergency reduce
  5% drawdown           : New sizes -25%
  8% drawdown           : New sizes -50%
  12% drawdown          : Halt new entries
  15% drawdown          : Close all, halt
  Correlation > 0.85    : Force size reduction
"""

import logging
import time
import threading
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_lock = threading.RLock()


def _default_account_size() -> float:
    try:
        from runtime.live_account import get_live_account_size

        return float(get_live_account_size())
    except Exception:
        return 5000.0

# Drawdown-based position size multipliers
_DRAWDOWN_MULT = [
    (0.05, 0.75),  # 5% DD → 75% of normal size
    (0.08, 0.50),  # 8% DD → 50%
    (0.12, 0.0),  # 12% DD → halt new entries
    (0.15, 0.0),  # 15% DD → close all + halt
]

# Margin utilization thresholds
_MARGIN_NO_NEW = 0.60
_MARGIN_REDUCE = 0.75
_MARGIN_EMERGENCY = 0.85


class RiskState:
    """Live snapshot of portfolio risk metrics."""

    __slots__ = [
        "account_balance",
        "peak_balance",
        "daily_start_balance",
        "total_deployed_usd",
        "margin_utilization",
        "drawdown_pct",
        "daily_loss_pct",
        "var_95",
        "cvar_95",
        "var_99",
        "cvar_99",
        "correlation_breach",
        "ts",
    ]

    def __init__(self):
        _base = _default_account_size()
        self.account_balance = _base
        self.peak_balance = _base
        self.daily_start_balance = _base
        self.total_deployed_usd = 0.0
        self.margin_utilization = 0.0
        self.drawdown_pct = 0.0
        self.daily_loss_pct = 0.0
        self.var_95 = 0.0
        self.cvar_95 = 0.0
        self.var_99 = 0.0
        self.cvar_99 = 0.0
        self.correlation_breach = False
        self.ts = time.time()

    def to_dict(self) -> Dict:
        return {k: getattr(self, k) for k in self.__slots__}


class RiskEngine:
    """Portfolio-level risk management instance."""

    def __init__(self, lane_id: str = "default"):
        self.lane_id = lane_id
        self.lock = threading.RLock()
        self.state = RiskState()
        logger.info(f"[risk_engine] Initialized lane '{lane_id}'")

    def update_balances(
        self, current_balance: float, deployed_usd: float = 0.0, margin_usd: float = 0.0
    ):
        """Update core balance metrics for this lane."""
        with self.lock:
            self.state.account_balance = current_balance
            self.state.peak_balance = max(self.state.peak_balance, current_balance)
            self.state.total_deployed_usd = deployed_usd

            if self.state.daily_start_balance > 0:
                self.state.drawdown_pct = max(
                    0.0,
                    (self.state.daily_start_balance - current_balance)
                    / self.state.daily_start_balance,
                )
                self.state.daily_loss_pct = self.state.drawdown_pct

            if current_balance > 0:
                self.state.margin_utilization = margin_usd / current_balance
            self.state.ts = time.time()

            # ── Grafana IRM Soft-Halt Integration ────────────────────────────────
            if self.state.drawdown_pct >= 0.12:
                try:
                    from monitoring.irm_reporter import create_irm_incident
                    create_irm_incident(
                        title=f"RISK HALT [{self.lane_id.upper()}]: Drawdown hit {self.state.drawdown_pct:.1%}",
                        severity="high",
                        description=f"Lane '{self.lane_id}' drawdown ({self.state.drawdown_pct:.1%}) hit 12% halt threshold.",
                        labels=[f"lane:{self.lane_id}", "type:drawdown_halt"],
                        extra_details={
                            "lane": self.lane_id,
                            "drawdown_pct": self.state.drawdown_pct,
                            "balance": current_balance,
                        }
                    )
                except Exception as e:
                    logger.debug(f"[{self.lane_id}] irm report failed: {e}")

            # 📊 Metrics
            try:
                from monitoring.metrics import update_performance
                pnl = current_balance - self.state.daily_start_balance
                update_performance(pnl, current_balance, self.state.drawdown_pct)
            except ImportError:
                pass

    def reset_daily(self, balance: float):
        """Call at start of each trading day (midnight UTC)."""
        with self.lock:
            self.state.daily_start_balance = balance

    def update_var_from_db(self):
        """Compute VaR/CVaR from trade history."""
        try:
            from logging_db.trade_logger import get_logger
            db = get_logger()
            # Note: v18.33 - Filtering by strategy or lane here would be better
            rows = db.conn.execute(
                """
                SELECT pnl_usd FROM trades
                WHERE paper=0 AND action='SELL'
                ORDER BY ts DESC LIMIT 200
            """
            ).fetchall()

            if len(rows) < 10:
                return

            pnls = [float(r[0]) for r in rows]
            var95, cvar95 = compute_var_cvar(pnls, 0.95)
            var99, cvar99 = compute_var_cvar(pnls, 0.99)

            with self.lock:
                self.state.var_95 = var95
                self.state.cvar_95 = cvar95
                self.state.var_99 = var99
                self.state.cvar_99 = cvar99

        except Exception as e:
            logger.debug(f"[{self.lane_id}] var update error: {e}")

    def position_size_multiplier(self) -> float:
        """Return size multiplier based on current drawdown."""
        with self.lock:
            dd = self.state.drawdown_pct

        mult = 1.0
        for threshold, m in _DRAWDOWN_MULT:
            if dd >= threshold:
                mult = m
        return mult

    def can_open_new_position(self) -> Tuple[bool, str]:
        """Returns (allowed, reason) for opening a new position."""
        # Kill switch
        try:
            # v18.33: Check lane-specific kill switch if refactored,
            # for now keep checking the global module which will be refactored next.
            import kill_switch
            if kill_switch.is_halted():
                return False, f"Kill switch active: {kill_switch.get_halt_reason()}"
        except Exception:
            pass

        with self.lock:
            daily_loss = self.state.daily_loss_pct
            margin = self.state.margin_utilization
            deployed = self.state.total_deployed_usd
            balance = self.state.account_balance
            dd = self.state.drawdown_pct

        if daily_loss >= 0.05:
            return False, f"Daily loss {daily_loss:.1%} >= 5% halt threshold"
        if dd >= 0.12:
            return False, f"Drawdown {dd:.1%} >= 12% — halting new entries"
        if margin >= _MARGIN_NO_NEW:
            return False, f"Margin utilization {margin:.0%} >= {_MARGIN_NO_NEW:.0%}"
        if deployed >= balance * 0.95:
            return False, f"Deployed ${deployed:.0f} >= 95% of balance ${balance:.0f}"

        return True, "OK"

    def margin_action_required(self) -> Optional[str]:
        """Returns recommended margin action or None."""
        with self.lock:
            margin = self.state.margin_utilization
        if margin >= _MARGIN_EMERGENCY:
            return "emergency_reduce"
        elif margin >= _MARGIN_REDUCE:
            return "reduce_existing"
        return None

    def get_risk_report(self) -> Dict:
        """Full risk snapshot for dashboard/notifications."""
        with self.lock:
            s = self.state.to_dict()
        s["can_trade"], s["block_reason"] = self.can_open_new_position()
        s["size_multiplier"] = self.position_size_multiplier()
        s["margin_action"] = self.margin_action_required()
        s["drawdown_pct_str"] = f"{s['drawdown_pct']:.1%}"
        s["daily_loss_pct_str"] = f"{s['daily_loss_pct']:.1%}"
        s["margin_pct_str"] = f"{s['margin_utilization']:.0%}"
        s["var_95_str"] = f"${s['var_95']:.0f}"
        s["cvar_95_str"] = f"${s['cvar_95']:.0f}"
        return s

# ── Multi-Instance Manager ───────────────────────────────────────────────────

_engines: Dict[str, RiskEngine] = {}

def get_engine(lane_id: str = "default") -> RiskEngine:
    """Singleton getter for lane-specific risk engines."""
    if lane_id not in _engines:
        _engines[lane_id] = RiskEngine(lane_id)
    return _engines[lane_id]

# ── Module-level Proxy Functions (Backward Compatibility) ─────────────────────

def update_balances(current_balance: float, deployed_usd: float = 0.0, margin_usd: float = 0.0):
    get_engine().update_balances(current_balance, deployed_usd, margin_usd)

def reset_daily(balance: float):
    get_engine().reset_daily(balance)

def update_var_from_db():
    get_engine().update_var_from_db()

def position_size_multiplier() -> float:
    return get_engine().position_size_multiplier()

def can_open_new_position() -> Tuple[bool, str]:
    return get_engine().can_open_new_position()

def margin_action_required() -> Optional[str]:
    return get_engine().margin_action_required()

def get_risk_report() -> Dict:
    return get_engine().get_risk_report()
