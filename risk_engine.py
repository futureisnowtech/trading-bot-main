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


_state = RiskState()


def update_balances(
    current_balance: float, deployed_usd: float = 0.0, margin_usd: float = 0.0
):
    """
    Update core balance metrics. Call whenever balance or deployment changes.
    """
    with _lock:
        _state.account_balance = current_balance
        _state.peak_balance = max(_state.peak_balance, current_balance)
        _state.total_deployed_usd = deployed_usd

        if _state.peak_balance > 0:
            _state.drawdown_pct = (
                _state.peak_balance - current_balance
            ) / _state.peak_balance

        if _state.daily_start_balance > 0:
            _state.daily_loss_pct = max(
                0.0,
                (_state.daily_start_balance - current_balance)
                / _state.daily_start_balance,
            )

        if current_balance > 0:
            _state.margin_utilization = margin_usd / current_balance
        _state.ts = time.time()

        # ── Grafana IRM Soft-Halt Integration ────────────────────────────────
        if _state.drawdown_pct >= 0.12:
            try:
                from monitoring.irm_reporter import create_irm_incident
                create_irm_incident(
                    title=f"RISK HALT: Drawdown hit {_state.drawdown_pct:.1%}",
                    severity="high",
                    description=f"Portfolio drawdown ({_state.drawdown_pct:.1%}) has reached the 12% new-entry halt threshold.",
                    labels=["scope:portfolio", "type:drawdown_halt"],
                    extra_details={
                        "drawdown_pct": _state.drawdown_pct,
                        "balance": current_balance,
                        "peak": _state.peak_balance
                    }
                )
            except Exception as e:
                logger.debug(f"[risk_engine] irm report failed: {e}")

        # 📊 Metrics
        try:
            from monitoring.metrics import update_performance
            # Daily PnL estimate: current - daily_start
            pnl = current_balance - _state.daily_start_balance
            update_performance(pnl, current_balance, _state.drawdown_pct)
        except ImportError:
            pass

    # Kill switch check is handled by v10_runner.kill_switch_monitor().
    # peak_balance starts at the hardcoded 10 000 default and has no mode awareness,
    # which caused false live-mode triggers (1966 < 0.75*10000 = 7500).


def reset_daily(balance: float):
    """Call at start of each trading day (midnight UTC)."""
    with _lock:
        _state.daily_start_balance = balance


def compute_var_cvar(
    returns: List[float], confidence: float = 0.95
) -> Tuple[float, float]:
    """
    Historical VaR and CVaR from a list of P&L returns.

    Args:
        returns:    list of daily/trade P&L values in USD
        confidence: e.g. 0.95 for 95% VaR

    Returns:
        (var, cvar) — both positive numbers representing loss magnitude
    """
    if len(returns) < 10:
        return 0.0, 0.0

    arr = np.array(returns)
    loss_threshold = np.percentile(arr, (1 - confidence) * 100)
    var = -loss_threshold

    # CVaR = mean of all returns below VaR threshold
    tail = arr[arr <= loss_threshold]
    cvar = -float(np.mean(tail)) if len(tail) > 0 else var

    return round(float(var), 2), round(float(cvar), 2)


def update_var_from_db():
    """Compute VaR/CVaR from trade history. Call periodically."""
    try:
        from logging_db.trade_logger import get_logger

        db = get_logger()
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

        with _lock:
            _state.var_95 = var95
            _state.cvar_95 = cvar95
            _state.var_99 = var99
            _state.cvar_99 = cvar99

    except Exception as e:
        logger.debug(f"[risk_engine] var update error: {e}")


def compute_correlation_matrix(open_positions: Dict) -> Optional[np.ndarray]:
    """
    Compute correlation between open positions using recent price returns.
    Returns NxN correlation matrix or None if insufficient data.
    """
    symbols = list(open_positions.keys())
    if len(symbols) < 2:
        return None

    try:
        from data.historical_data import get_candles

        returns_by_sym = {}

        for sym in symbols:
            df = get_candles(sym, "1h", 48)
            if df is not None and len(df) >= 20:
                closes = df["close"].values.astype(float)
                rets = np.diff(closes) / closes[:-1]
                returns_by_sym[sym] = rets

        if len(returns_by_sym) < 2:
            return None

        # Align lengths
        min_len = min(len(v) for v in returns_by_sym.values())
        matrix = np.array([v[-min_len:] for v in returns_by_sym.values()])
        corr = np.corrcoef(matrix)
        return corr

    except Exception as e:
        logger.debug(f"[risk_engine] correlation error: {e}")
        return None


def check_correlation_breach(
    open_positions: Dict, threshold: float = 0.85
) -> List[Tuple[str, str, float]]:
    """
    Return list of (sym1, sym2, correlation) pairs breaching threshold.
    """
    breaches = []
    corr = compute_correlation_matrix(open_positions)
    if corr is None:
        return breaches

    symbols = list(open_positions.keys())
    n = len(symbols)
    for i in range(n):
        for j in range(i + 1, n):
            val = float(corr[i, j])
            if abs(val) > threshold:
                breaches.append((symbols[i], symbols[j], round(val, 3)))

    with _lock:
        _state.correlation_breach = len(breaches) > 0

    return breaches


def position_size_multiplier() -> float:
    """
    Return size multiplier based on current drawdown.
    0.0 means halt new entries.
    """
    with _lock:
        dd = _state.drawdown_pct

    mult = 1.0
    for threshold, m in _DRAWDOWN_MULT:
        if dd >= threshold:
            mult = m
    return mult


def can_open_new_position() -> Tuple[bool, str]:
    """
    Returns (allowed, reason) for opening a new position.
    Checks: daily loss, margin utilization, drawdown, kill switch.
    """
    # Kill switch
    try:
        import kill_switch

        if kill_switch.is_halted():
            return False, f"Kill switch active: {kill_switch.get_halt_reason()}"
    except Exception:
        pass

    with _lock:
        daily_loss = _state.daily_loss_pct
        margin = _state.margin_utilization
        deployed = _state.total_deployed_usd
        balance = _state.account_balance
        dd = _state.drawdown_pct

    # Daily loss halt: -5%
    if daily_loss >= 0.05:
        return False, f"Daily loss {daily_loss:.1%} >= 5% halt threshold"

    # Drawdown halt
    if dd >= 0.12:
        return False, f"Drawdown {dd:.1%} >= 12% — halting new entries"

    # Margin check
    if margin >= _MARGIN_NO_NEW:
        return False, f"Margin utilization {margin:.0%} >= {_MARGIN_NO_NEW:.0%}"

    # Max deployed — 95%
    if deployed >= balance * 0.95:
        return False, f"Deployed ${deployed:.0f} >= 95% of balance ${balance:.0f}"

    return True, "OK"


def margin_action_required() -> Optional[str]:
    """
    Returns recommended margin action or None.
    'reduce_existing' | 'emergency_reduce' | None
    """
    with _lock:
        margin = _state.margin_utilization

    if margin >= _MARGIN_EMERGENCY:
        return "emergency_reduce"
    elif margin >= _MARGIN_REDUCE:
        return "reduce_existing"
    return None


def get_risk_report() -> Dict:
    """Full risk snapshot for dashboard/notifications."""
    with _lock:
        s = _state.to_dict()

    s["can_trade"], s["block_reason"] = can_open_new_position()
    s["size_multiplier"] = position_size_multiplier()
    s["margin_action"] = margin_action_required()

    # Format key values
    s["drawdown_pct_str"] = f"{s['drawdown_pct']:.1%}"
    s["daily_loss_pct_str"] = f"{s['daily_loss_pct']:.1%}"
    s["margin_pct_str"] = f"{s['margin_utilization']:.0%}"
    s["var_95_str"] = f"${s['var_95']:.0f}"
    s["cvar_95_str"] = f"${s['cvar_95']:.0f}"

    return s
