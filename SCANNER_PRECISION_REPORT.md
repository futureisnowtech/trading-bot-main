# SCANNER_PRECISION_REPORT.md
# Spot Scalp Lane — Scanner Precision
# Derived from: 140-trade live failure window (2026-04-22 – 2026-04-28)
# Version: 2026-04-28

---

## Candidate Funnel (failure window)

| Stage | Count | Notes |
|---|---|---|
| scan_candidates (spot, live) | ~150+ | Includes econ-vetoed rows |
| admitted / entered | 140 | Execution success |
| closed | 140 | All closed within hold period |
| profitable after fees | 0 | **0.0% net win rate** |

The scanner admitted 140 trades with 0% net profitability. This is not a
scanner ranking failure — it is a setup family / regime composition failure.
The scanner correctly identified the candidates; the admission gate was too
permissive.

---

## Precision by Setup Family

| Setup Family | n | Net PnL | Avg | WR |
|---|---|---|---|---|
| pullback_reclaim | 139 | -$164.48 | -$1.18 | 0% |
| compression_breakout | 1 | -$0.77 | -$0.77 | 0% |
| impulse_continuation | 0 | — | — | — |

**Finding:** The scanner exclusively surfaced pullback_reclaim setups in this
window. No impulse_continuation or other families entered execution. The market
regime during this window produced only pullback structures.

---

## Precision by Regime

| Regime | n | Net PnL | Avg |
|---|---|---|---|
| NEUTRAL | 115 | -$148.02 | -$1.29 |
| CHOP | 23 | -$16.11 | -$0.70 |
| TREND | 2 | -$1.11 | -$0.56 |

**Finding:** NEUTRAL dominates (82%) and has the worst per-trade performance.
CHOP is less bad but still 0% WR. TREND has n=2 (insufficient).

---

## Precision by Symbol

| Symbol | n | Net PnL | Avg |
|---|---|---|---|
| ETH | 19 | -$30.49 | -$1.60 |
| BTC | 21 | -$31.90 | -$1.52 |
| SOL | 33 | -$47.53 | -$1.44 |
| LINK | 6 | -$6.44 | -$1.07 |
| LTC | 5 | -$5.45 | -$1.09 |
| ADA | 11 | -$10.03 | -$0.91 |
| XRP | 28 | -$23.72 | -$0.85 |
| DOGE | 17 | -$9.68 | -$0.57 |

All symbols negative. ETH/BTC/SOL have worst per-trade loss.
DOGE/XRP have smallest per-trade loss (lower price volatility = smaller ATR stop = less room to drift).

---

## Precision by Route

| Route | n | Net PnL | Fee | WR |
|---|---|---|---|---|
| taker_fallback | 113 | -$130.95 | $56.43 | 0% |
| maker_first | 27 | -$34.30 | $19.30 | 0% |

**Finding:** taker_fallback was 81% of executions. Taker route incurs 0.03%
higher per-side fee. On $50 average order: ~$0.03/trade additional fee.
Cumulative: $56 fee vs $19 for maker. Both routes 0% WR — but maker loses
less per trade ($-1.27 vs $-1.16 avg), consistent with lower fee burden.

---

## Setup Score Buckets

| Score Range | n | Net PnL | Avg PnL |
|---|---|---|---|
| 0.00 – 0.20 | 19 | -$16.47 | -$0.87 |
| 0.20 – 0.35 | 101 | -$118.30 | -$1.17 |
| 0.35 – 0.50 | 11 | -$18.95 | -$1.72 |
| 0.50 – 0.65 | 9 | -$11.54 | -$1.28 |
| 0.65+ | 0 | — | — |

**Finding:** Higher setup_score does NOT correlate with better performance in
this window. The [0.35-0.50) bucket is worse than the [0.20-0.35) bucket.
This indicates the setup_score signal is not predictive for the pullback_reclaim
family in the current regime. Score floors cannot fix a fundamentally
non-profitable setup species.

---

## False-Positive Clusters to Suppress

| Priority | Cluster | n | Action |
|---|---|---|---|
| CRITICAL | pullback_reclaim × NEUTRAL | 115 | QUARANTINED (wired) |
| HIGH | pullback_reclaim × CHOP | 22 | QUARANTINED (wired) |
| LOW | pullback_reclaim × TREND | 2 | Allowed — insufficient data |
| UNKNOWN | impulse_continuation | 0 | Allowed — never traded in window |
| UNKNOWN | compression_breakout | 1 | Allowed — n=1 insufficient |

---

## Scanner Precision Rules (wired)

1. `SCANNER_TOP_N = 20` — admits top 20 candidates per cycle
2. `SCANNER_PARALLEL_WORKERS = 8` — parallel fetch workers
3. Quarantine gates in `spot_quality_block_reason()` block NEUTRAL/CHOP pullback_reclaim
   before any sizing or execution logic runs.
4. Taker fallback disabled — reduces the effective admission count from scanner
   to execution (skipped_taker_score / taker_fallback_disabled gates)

---

## Strongest Pathways to Preserve

- impulse_continuation (any regime, maker_first): no live data yet, keep open
- compression_breakout (TREND/NEUTRAL, maker_first): n=1 in CHOP, open gate elsewhere
- pullback_reclaim (TREND only, maker_first): n=2, insufficient — allow with higher score floor
