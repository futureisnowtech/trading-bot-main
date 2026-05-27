"""
config/alpha_specs.py — Strategy-level thresholds and signal parameters.

Single source of truth for all threshold values used across:
  - signal_engine.py (entry composite thresholds, regime fractions)
  - risk/economics_gate.py (EV tier thresholds)
  - position_manager.py (sizing limits, Kelly bounds)
  - scheduler/v10_runner.py (tier floors, conviction weights)

These values are read-only at runtime. Changing them requires:
  1. A backtest proving the new value improves edge
  2. Human confirmation
  3. Update here + CHANGELOG.md

DO NOT reach into this file from scanner.py, signal_engine.py, or data/indicators.py —
those are protected files. v10_runner.py and position_manager.py may import from here.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── Entry thresholds ──────────────────────────────────────────────────────────

COMPOSITE_TIER1_FLOOR: float = 50.0  # Tier 1 minimum composite score
COMPOSITE_TIER2_THRESHOLD: float = 58.0  # Tier 2 minimum composite score

REGIME_ENTRY_THRESHOLDS: dict[str, float] = {
    "TRENDING_UP": 58.0,
    "TRENDING_DOWN": 58.0,
    "RANGING": 58.0,
    "HIGH_VOL": 60.0,
    "LOW_VOL": 56.0,
    "UNKNOWN": 58.0,
}

# ── Exit thresholds ───────────────────────────────────────────────────────────

THESIS_REGIME_FRACTIONS: dict[str, float] = {
    "TRENDING": 0.30,  # thesis must stay above 30% of entry score
    "RANGING": 0.15,  # faster exit in ranging (fragile setups)
    "HIGH_VOL": 0.35,  # more patience in high vol (noisy signal)
    "UNKNOWN": 0.25,
}

# ── Economics gate EV tiers ───────────────────────────────────────────────────
# These must match economics_gate.py constants.
# Kept here as the single named reference — economics_gate.py imports these.

ECONOMICS_EV_TIERS: dict[str, float] = {
    "A+": 0.016,  # 1.6% expected value — highest conviction
    "A": 0.008,  # 0.8%
    "B": 0.003,  # 0.3% — minimum acceptable
}

# ── Risk limits ───────────────────────────────────────────────────────────────
# These match config.py constants. Kept here as named references for modules
# that import alpha_specs instead of config (avoids circular import paths).

MAX_ACCOUNT_RISK_PCT: float = 0.01  # 1% max risk per trade
MAX_DAILY_LOSS_PCT: float = 0.04  # 4% daily halt (live); paper uses 20%
MAX_DEPLOYED_CAPITAL_PCT: float = 0.90  # 90% max deployed
KILL_SWITCH_THRESHOLD_PCT: float = 0.75  # halt if balance < 75% of ACCOUNT_SIZE

# ── ML training ───────────────────────────────────────────────────────────────

ML_MIN_TRADES_TO_TRAIN: int = 50  # minimum clean trades before first ML train
ML_RETRAIN_INTERVAL_TRADES: int = 25  # retrain every N new clean trades
ML_MIN_FIRES_TO_LEARN: int = 10  # minimum signal fires before Bayesian weight shifts

# ── Bayesian learning ─────────────────────────────────────────────────────────

BAYESIAN_PRIOR_N: int = 20  # phantom trade count for prior confidence
BAYESIAN_MAX_WEIGHT_MULT: float = 2.5  # cap Bayesian weight at 2.5x prior

# ── Promotion criteria (mirrors promotion_engine defaults) ────────────────────

PROMOTION_MIN_TRADES: int = 30
PROMOTION_MIN_WIN_RATE: float = 0.50
PROMOTION_MIN_PROFIT_FACTOR: float = 1.2
PROMOTION_MAX_DRAWDOWN_PCT: float = 15.0
DEMOTION_WIN_RATE_DROP: float = 0.10  # 10 percentage points
DEMOTION_PROFIT_FACTOR_FLOOR: float = 1.0
