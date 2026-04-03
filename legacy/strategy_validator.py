"""
backtesting/strategy_validator.py — Strategy validation gate.

Any new strategy or parameter change must pass this gate before paper trading.
Also runs periodically to check if live performance has diverged from backtest baseline.

Validation thresholds (configurable, sensible defaults for a $500 crypto account):
  win_rate    >= 45%
  sharpe      >= 0.5
  max_drawdown <= 20%
  min_trades  >= 20  (statistical significance floor)
  avg_pnl     > 0 after fees

Usage:
  from backtesting.strategy_validator import validate_strategy
  result = validate_strategy('crypto_macd', 'BTC-USDC', params, days=90)
  if result.passed:
      print("Strategy cleared for paper trading")
"""
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from learning.intelligence_bridge import archive_backtest_result, get_best_backtest


@dataclass
class ValidationResult:
    passed: bool
    strategy_name: str
    symbol: str
    stats: dict
    failures: list[str]
    warnings: list[str]
    recommendation: str
    archived_id: Optional[int] = None

    def summary(self) -> str:
        status = "✅ PASSED" if self.passed else "❌ FAILED"
        lines = [
            f"{status} — {self.strategy_name} on {self.symbol}",
            f"  Win rate:     {self.stats.get('win_rate', 0):.1%}",
            f"  Sharpe:       {self.stats.get('sharpe', 0):.2f}",
            f"  Max drawdown: {self.stats.get('max_drawdown', 0):.1%}",
            f"  Total trades: {self.stats.get('total_trades', 0)}",
            f"  Total P&L:    ${self.stats.get('total_pnl', 0):+.2f}",
            f"  Avg P&L/trade: ${self.stats.get('avg_pnl', 0):+.2f}",
        ]
        if self.failures:
            lines.append(f"  FAILURES: {'; '.join(self.failures)}")
        if self.warnings:
            lines.append(f"  WARNINGS: {'; '.join(self.warnings)}")
        lines.append(f"  → {self.recommendation}")
        return '\n'.join(lines)


def _compute_stats(trades: list[dict], fee_pct: float = 0.012) -> dict:
    """Compute validation stats from a list of trade dicts."""
    if not trades:
        return {'total_trades': 0, 'win_rate': 0, 'total_pnl': 0,
                'sharpe': 0, 'max_drawdown': 0, 'avg_pnl': 0, 'profit_factor': 0}

    pnls = []
    for t in trades:
        gross = float(t.get('pnl_usd', 0))
        fee   = float(t.get('fee_usd', 0)) or abs(gross) * fee_pct
        pnls.append(gross - fee)

    import numpy as np
    arr = pd.Series(pnls)
    wins   = arr[arr > 0]
    losses = arr[arr <= 0]
    win_rate = len(wins) / len(arr) if len(arr) > 0 else 0

    # Sharpe (annualised, assuming 1-min bars, 525600 bars/year)
    if arr.std() > 0:
        sharpe = (arr.mean() / arr.std()) * (525600 ** 0.5)
    else:
        sharpe = 0.0

    # Max drawdown
    cumulative = arr.cumsum()
    running_max = cumulative.cummax()
    drawdown = (cumulative - running_max) / (running_max.abs() + 1e-9)
    max_dd = abs(drawdown.min())

    # Profit factor
    gross_win  = wins.sum()
    gross_loss = abs(losses.sum())
    pf = gross_win / gross_loss if gross_loss > 0 else (float('inf') if gross_win > 0 else 0)

    return {
        'total_trades': len(trades),
        'win_rate': win_rate,
        'total_pnl': float(arr.sum()),
        'avg_pnl': float(arr.mean()),
        'sharpe': float(sharpe),
        'max_drawdown': float(max_dd),
        'profit_factor': float(pf),
        'wins': len(wins),
        'losses': len(losses),
    }


def validate_strategy(
    strategy_name: str,
    symbol: str,
    params: dict,
    trades: Optional[list[dict]] = None,
    stats: Optional[dict] = None,
    # Thresholds
    min_win_rate: float = 0.45,
    min_sharpe: float = 0.5,
    max_drawdown: float = 0.20,
    min_trades: int = 20,
    min_avg_pnl: float = 0.0,
    # Metadata
    variant: str = '',
    timeframe: str = 'ONE_MINUTE',
    period_start: str = '',
    period_end: str = '',
    notes: str = '',
) -> ValidationResult:
    """
    Validate a strategy against quality thresholds.

    Pass either:
      - trades: raw list of trade dicts (stats computed here)
      - stats:  pre-computed stats dict (from backtest_engine output)

    Archives result to backtest_results table either way.
    """
    # Compute stats if not provided
    if stats is None:
        if trades:
            stats = _compute_stats(trades)
        else:
            stats = {'total_trades': 0, 'win_rate': 0, 'total_pnl': 0,
                     'sharpe': 0, 'max_drawdown': 0, 'avg_pnl': 0, 'profit_factor': 0}

    failures = []
    warnings = []

    # Gate checks
    wr   = stats.get('win_rate', 0) or 0
    sh   = stats.get('sharpe', 0) or 0
    dd   = stats.get('max_drawdown', 0) or 0
    n    = stats.get('total_trades', 0) or 0
    ap   = stats.get('avg_pnl', 0) or 0

    if n < min_trades:
        failures.append(f"Only {n} trades (need ≥ {min_trades} for significance)")
    if wr < min_win_rate:
        failures.append(f"Win rate {wr:.1%} < {min_win_rate:.1%} minimum")
    if sh < min_sharpe:
        failures.append(f"Sharpe {sh:.2f} < {min_sharpe:.1f} minimum")
    if dd > max_drawdown:
        failures.append(f"Max drawdown {dd:.1%} > {max_drawdown:.1%} limit")
    if ap < min_avg_pnl:
        failures.append(f"Avg P&L ${ap:.3f} < ${min_avg_pnl:.3f} minimum")

    # Warnings (non-blocking)
    if wr < 0.52:
        warnings.append(f"Win rate {wr:.1%} is marginal (target ≥ 52%)")
    if sh < 1.0:
        warnings.append(f"Sharpe {sh:.2f} is acceptable but not strong (target ≥ 1.0)")
    pf = stats.get('profit_factor', 0) or 0
    if 0 < pf < 1.5:
        warnings.append(f"Profit factor {pf:.2f} is weak (target ≥ 1.5)")

    passed = len(failures) == 0

    # Recommendation
    if passed and not warnings:
        rec = "Promote to paper trading immediately."
    elif passed:
        rec = f"Promote to paper trading with caution. Monitor: {'; '.join(warnings)}"
    else:
        rec = f"Do NOT deploy. Fix: {'; '.join(failures[:2])}"

    # Archive to DB
    archived_id = archive_backtest_result(
        strategy_name=strategy_name, symbol=symbol, params=params,
        stats=stats, passed=passed, variant=variant,
        timeframe=timeframe, period_start=period_start,
        period_end=period_end,
        notes=notes + (f" | PASS" if passed else f" | FAIL: {'; '.join(failures)}"),
    )

    return ValidationResult(
        passed=passed,
        strategy_name=strategy_name,
        symbol=symbol,
        stats=stats,
        failures=failures,
        warnings=warnings,
        recommendation=rec,
        archived_id=archived_id,
    )


def check_live_vs_backtest_drift(
    strategy_name: str,
    symbol: str,
    live_win_rate: float,
    live_trades: int,
    drift_threshold: float = 0.10,
) -> dict:
    """
    Check if live performance has drifted significantly from backtest baseline.
    Returns a dict with: drifted (bool), severity, message.
    """
    best = get_best_backtest(strategy_name, symbol)
    if not best or not live_trades or live_trades < 10:
        return {'drifted': False, 'severity': 'none', 'message': 'Insufficient data'}

    bt_wr = best.get('win_rate') or 0.5
    delta = bt_wr - live_win_rate

    if delta > drift_threshold * 2:
        severity = 'critical'
        msg = (f"{strategy_name}/{symbol}: Live win rate {live_win_rate:.1%} is "
               f"{delta:.1%} below backtest {bt_wr:.1%} — REVIEW IMMEDIATELY")
    elif delta > drift_threshold:
        severity = 'warning'
        msg = (f"{strategy_name}/{symbol}: Live win rate {live_win_rate:.1%} is "
               f"{delta:.1%} below backtest {bt_wr:.1%} — monitor closely")
    else:
        severity = 'ok'
        msg = f"{strategy_name}/{symbol}: Within {delta:.1%} of backtest baseline — OK"

    return {
        'drifted': severity != 'ok',
        'severity': severity,
        'message': msg,
        'bt_win_rate': bt_wr,
        'live_win_rate': live_win_rate,
        'delta': delta,
    }
