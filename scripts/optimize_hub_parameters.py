#!/usr/bin/env python3
"""
scripts/optimize_hub_parameters.py — Per-Hub Parameter Optimizer

SRE-grade backtest sweep over the 81,984-row winrate_optimization_dataset.csv
to find the optimal trio of (hard_rbi_threshold, theta_steepness, dynamic_offset)
per Thermodynamic Regional Hub.

Runs TWO independent sweeps:
  1. PROB-DOMAIN — sigmoid input = q_side (probability), per user spec.
  2. EV-DOMAIN   — sigmoid input = ev (post-fee edge), drop-in to live engine.

Both honor:
  - Hub-grouped optimization (7 hubs, 7 trios).
  - sigma_vol > 4.5 chaos pre-filter.
  - MAX_POSITION_SIZE = 100 with floor(scale * 100) integer cast.
  - Discrete fee tiers <=0.10:1c / <=0.20:2c / >0.20:7c.
  - Sigmoid exponent clipped to [-50, 50] (SRE Check 1).
  - PnL evaluated against ask price (SRE Check 2).

Outputs:
  ~/Downloads/hub_optimization_matrix_prob.csv
  ~/Downloads/hub_optimization_matrix_ev.csv
  ~/Downloads/hub_params_optimized_prob_domain.json   (comparison only)
  ~/Downloads/hub_params_optimized_ev_domain.json     (live deploy)
"""

from __future__ import annotations

import json
import os
import sys
from typing import Iterable

import numpy as np
import pandas as pd

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

CSV_IN = os.path.expanduser("~/Downloads/winrate_optimization_dataset.csv")
OUT_DIR = os.path.expanduser("~/Downloads")

HUBS: tuple[str, ...] = (
    "MIDWEST",
    "NORTHEAST",
    "SOUTH",
    "FLORIDA",
    "GULF",
    "MOUNTAIN",
    "WEST",
)

# Chaos veto pre-filter (Task 4 constraint 2).
SIGMA_CHAOS_CUTOFF: float = 4.5

# Absolute sizing translation (Task 4 constraint 3).
MAX_POSITION_SIZE: int = 100

# Argmax guard — refuse degenerate "trade only one row" optima.
MIN_TRADES_PER_HUB: int = 30

# Sigmoid exponent overflow clamp (SRE mandate).
EXP_CLIP_LO: float = -50.0
EXP_CLIP_HI: float = 50.0

# Shared sweep grids — hard_rbi_threshold is always in PROBABILITY domain
# (Gate 11 floors q_side, never ev).
T_GRID: np.ndarray = np.round(np.arange(0.50, 0.71, 0.02), 4)

# Prob-domain sweep (sigmoid input = q_side, range 0–1).
THETA_GRID_PROB: np.ndarray = np.array([5.0, 10.0, 15.0, 20.0, 25.0, 30.0])
DELTA_GRID_PROB: np.ndarray = np.array([0.50, 0.55, 0.60, 0.65, 0.70])

# EV-domain sweep (sigmoid input = ev, range roughly -0.5–+0.5).
THETA_GRID_EV: np.ndarray = np.array([5.0, 10.0, 15.0, 20.0, 25.0, 30.0])
DELTA_GRID_EV: np.ndarray = np.array([0.030, 0.040, 0.050, 0.060, 0.070, 0.080, 0.090, 0.100])


# ──────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ──────────────────────────────────────────────────────────────────────────────

def discrete_fee(price: np.ndarray) -> np.ndarray:
    """Kalshi V2 discrete taker fee tiers — vectorized.

    SRE Check 2: explicit tier logic, no parabolic approximation.
    """
    return np.where(price <= 0.10, 0.01, np.where(price <= 0.20, 0.02, 0.07))


def safe_sigmoid(exponent: np.ndarray) -> np.ndarray:
    """Numpy logistic with the SRE overflow clamp baked in."""
    safe_exp = np.clip(exponent, EXP_CLIP_LO, EXP_CLIP_HI)
    return 1.0 / (1.0 + np.exp(safe_exp))


def precompute_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add q_side, p_cost, won (the values that don't change per (T, θ, Δ))."""
    out = df.copy()
    is_yes = out["best_side"].values == "YES"
    out["q_side"] = np.where(is_yes, out["fair_blended_prob"].values, 1.0 - out["fair_blended_prob"].values)
    out["q_side"] = np.clip(out["q_side"].values, 0.01, 0.99)
    out["p_cost"] = np.where(is_yes, out["ask_yes"].values, out["ask_no"].values)
    is_yes_outcome = out["outcome"].values == "YES"
    out["won"] = (is_yes & is_yes_outcome) | ((~is_yes) & (~is_yes_outcome))
    out["fee"] = discrete_fee(out["p_cost"].values)
    return out


def simulate(
    df: pd.DataFrame,
    *,
    T: float,
    theta: float,
    delta: float,
    sigmoid_input: str,
) -> tuple[float, int, float]:
    """Apply Gate 11 + log-sigmoid sizing, return (pnl, trade_count, win_rate).

    `sigmoid_input` is either ``"q_side"`` (prob-domain) or ``"ev"`` (ev-domain).
    """
    floored = df[df["q_side"].values >= T]
    if floored.empty:
        return 0.0, 0, 0.0

    if sigmoid_input == "q_side":
        signal = floored["q_side"].values
    elif sigmoid_input == "ev":
        signal = floored["ev"].values
    else:
        raise ValueError(f"unknown sigmoid_input={sigmoid_input!r}")

    exponent = -theta * (signal - delta)
    scale = safe_sigmoid(exponent)
    qty = np.floor(scale * MAX_POSITION_SIZE).astype(int)

    traded_mask = qty > 0
    if not traded_mask.any():
        return 0.0, 0, 0.0

    p_cost = floored["p_cost"].values
    fee = floored["fee"].values
    won = floored["won"].values

    pnl_per_contract = np.where(won, 1.0 - p_cost - fee, -p_cost - fee)
    pnl_row = qty * pnl_per_contract

    cumulative_pnl = float(pnl_row.sum())
    trade_count = int(traded_mask.sum())
    win_rate = float(won[traded_mask].mean()) if trade_count else 0.0
    return cumulative_pnl, trade_count, win_rate


def sweep_hub(
    hub_df: pd.DataFrame,
    *,
    t_grid: Iterable[float],
    theta_grid: Iterable[float],
    delta_grid: Iterable[float],
    sigmoid_input: str,
) -> pd.DataFrame:
    """Run the full T × θ × Δ scan for one hub. Returns long-format matrix."""
    rows: list[dict] = []
    for T in t_grid:
        for theta in theta_grid:
            for delta in delta_grid:
                pnl, trade_count, win_rate = simulate(
                    hub_df,
                    T=float(T),
                    theta=float(theta),
                    delta=float(delta),
                    sigmoid_input=sigmoid_input,
                )
                rows.append({
                    "hard_rbi_threshold": round(float(T), 4),
                    "theta_steepness": round(float(theta), 4),
                    "dynamic_offset": round(float(delta), 4),
                    "trades": trade_count,
                    "win_rate": round(win_rate, 4),
                    "pnl": round(pnl, 2),
                })
    return pd.DataFrame(rows)


def pick_optimum(matrix: pd.DataFrame) -> dict:
    """Pick the (T, θ, Δ) trio with the highest pnl subject to MIN_TRADES_PER_HUB."""
    eligible = matrix[matrix["trades"].values >= MIN_TRADES_PER_HUB]
    if eligible.empty:
        # Fall back to the highest-trade-count row to avoid empty output.
        eligible = matrix.sort_values("trades", ascending=False).head(1)
    best = eligible.sort_values("pnl", ascending=False).iloc[0]
    return {
        "hard_rbi_threshold": float(best["hard_rbi_threshold"]),
        "theta_steepness": float(best["theta_steepness"]),
        "dynamic_offset": float(best["dynamic_offset"]),
        "trades": int(best["trades"]),
        "win_rate": float(best["win_rate"]),
        "pnl": float(best["pnl"]),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main() -> int:
    if not os.path.exists(CSV_IN):
        print(f"[FATAL] Dataset not found: {CSV_IN}", file=sys.stderr)
        return 1

    print(f"[Optimizer] Loading {CSV_IN}...")
    df = pd.read_csv(CSV_IN)

    required = {
        "hub", "lane", "fair_blended_prob", "sigma_vol",
        "ask_yes", "ask_no", "best_side", "ev", "outcome",
    }
    missing = required - set(df.columns)
    if missing:
        print(f"[FATAL] Dataset missing columns: {sorted(missing)}", file=sys.stderr)
        return 1

    rows_initial = len(df)
    df = df[df["sigma_vol"].values <= SIGMA_CHAOS_CUTOFF].copy()
    rows_after_chaos = len(df)
    print(
        f"[Optimizer] Chaos pre-filter (sigma_vol > {SIGMA_CHAOS_CUTOFF}): "
        f"dropped {rows_initial - rows_after_chaos} rows "
        f"({rows_after_chaos}/{rows_initial} retained)."
    )

    df = precompute_columns(df)

    summary_rows: list[dict] = []
    prob_matrix_chunks: list[pd.DataFrame] = []
    ev_matrix_chunks: list[pd.DataFrame] = []
    prob_optima: dict[str, dict] = {}
    ev_optima: dict[str, dict] = {}

    for hub in HUBS:
        hub_df = df[df["hub"].values == hub]
        if hub_df.empty:
            print(f"[Optimizer] WARNING: hub {hub} has 0 rows after filter — skipping.")
            continue

        print(f"[Optimizer] Sweeping hub={hub} (n={len(hub_df)})...")

        prob_matrix = sweep_hub(
            hub_df,
            t_grid=T_GRID,
            theta_grid=THETA_GRID_PROB,
            delta_grid=DELTA_GRID_PROB,
            sigmoid_input="q_side",
        )
        prob_matrix.insert(0, "hub", hub)
        prob_matrix_chunks.append(prob_matrix)
        prob_optima[hub] = pick_optimum(prob_matrix)

        ev_matrix = sweep_hub(
            hub_df,
            t_grid=T_GRID,
            theta_grid=THETA_GRID_EV,
            delta_grid=DELTA_GRID_EV,
            sigmoid_input="ev",
        )
        ev_matrix.insert(0, "hub", hub)
        ev_matrix_chunks.append(ev_matrix)
        ev_optima[hub] = pick_optimum(ev_matrix)

        summary_rows.append({
            "hub": hub,
            "rows": int(len(hub_df)),
            "prob_T": prob_optima[hub]["hard_rbi_threshold"],
            "prob_theta": prob_optima[hub]["theta_steepness"],
            "prob_delta": prob_optima[hub]["dynamic_offset"],
            "prob_trades": prob_optima[hub]["trades"],
            "prob_winrate": prob_optima[hub]["win_rate"],
            "prob_pnl": prob_optima[hub]["pnl"],
            "ev_T": ev_optima[hub]["hard_rbi_threshold"],
            "ev_theta": ev_optima[hub]["theta_steepness"],
            "ev_delta": ev_optima[hub]["dynamic_offset"],
            "ev_trades": ev_optima[hub]["trades"],
            "ev_winrate": ev_optima[hub]["win_rate"],
            "ev_pnl": ev_optima[hub]["pnl"],
        })

    if not prob_matrix_chunks:
        print("[FATAL] No hubs produced output. Check dataset 'hub' column values.", file=sys.stderr)
        return 1

    prob_matrix_full = pd.concat(prob_matrix_chunks, ignore_index=True)
    ev_matrix_full = pd.concat(ev_matrix_chunks, ignore_index=True)

    prob_csv = os.path.join(OUT_DIR, "hub_optimization_matrix_prob.csv")
    ev_csv = os.path.join(OUT_DIR, "hub_optimization_matrix_ev.csv")
    prob_matrix_full.to_csv(prob_csv, index=False)
    ev_matrix_full.to_csv(ev_csv, index=False)

    prob_json_path = os.path.join(OUT_DIR, "hub_params_optimized_prob_domain.json")
    ev_json_path = os.path.join(OUT_DIR, "hub_params_optimized_ev_domain.json")

    def _strip_metrics(d: dict[str, dict]) -> dict[str, dict]:
        return {
            hub: {
                "hard_rbi_threshold": v["hard_rbi_threshold"],
                "theta_steepness": v["theta_steepness"],
                "dynamic_offset": v["dynamic_offset"],
            }
            for hub, v in d.items()
        }

    with open(prob_json_path, "w") as fh:
        json.dump(_strip_metrics(prob_optima), fh, indent=2)
    with open(ev_json_path, "w") as fh:
        json.dump(_strip_metrics(ev_optima), fh, indent=2)

    print()
    print("=" * 110)
    print("HUB OPTIMIZATION SUMMARY")
    print("=" * 110)
    summary_df = pd.DataFrame(summary_rows)
    print(summary_df.to_string(index=False))
    print()
    print(f"[Optimizer] Prob-domain matrix: {prob_csv} ({len(prob_matrix_full)} rows)")
    print(f"[Optimizer] EV-domain matrix:   {ev_csv} ({len(ev_matrix_full)} rows)")
    print(f"[Optimizer] Prob-domain JSON:   {prob_json_path}")
    print(f"[Optimizer] EV-domain JSON:     {ev_json_path}  <-- LIVE DEPLOY FILE")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
