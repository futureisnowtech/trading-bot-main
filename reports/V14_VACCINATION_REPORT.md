# V14 Archival Vaccination Report

## Executive Summary
This report analyzes 253 historical "Workhorse" failures from the V14 era to determine how the current V18 system would have handled them and to identify potential safety improvements.

## Phase 1: Archival Profiling
Total failures analyzed: **253**
- **Regime:** Mostly categorized as 'unknown' or 'ranging' in the archival DB.
- **ADX Observations:** Failures frequently occurred in low-trend environments (Avg Normalized ADX: **0.0377**).
- **WAE Observations:** High frequency of 'wae_exploding' signals (Avg: **0.4862**) suggests momentum traps in low-volatility regimes were a primary failure mode.

## Phase 2: Generational Benchmarking (V18 Performance)
We fed the historical feature data into the current V18 `signal_engine.score()` logic.
- **Blocked by V18 (Score < 50):** 0
- **Passing V18 (Score >= 50):** 253
- **Verdict:** The current V18 scoring logic, while more robust than V14, still assigns a high composite score to these specific historical failure patterns. These trades represent "leakage" that the standard technical/ML towers are not yet filtering out.

## Phase 3: Stationary Guard Discovery (ADF Calibration)
We isolated the price history for 232 of these failures and ran them through the V18 Augmented Dickey-Fuller (ADF) module.
- **Current ADF Threshold:** -2.86 (MacKinnon 5% critical value).
- **Failures Passing as Stationary:** 29 (out of 232 analyzed).
- **Simulated Tightening:** We searched for a threshold that would have vetoed these specific stationary-leakage cases.
- **Recommendation:** Tighten the ADF Critical Value from **-2.86** to **-3.10**. 
- **Impact:** This would have successfully vetoed 90% of the historical failures that currently pass the V18 stationary guard.

## Phase 4: Verification & Governance
- **Invariant Check:** `runtime/spot_position_truth.py` was **NOT** modified during this analysis.
- **Clean Compilation:** System health confirmed via `python3 scripts/go_paper.py` (dry run).

## Recommended Action Plan
1. **Tighten Stationary Guard:** Manually update `data/edge_monitor.py` to change the ADF critical value from `-2.86` to `-3.10`.
2. **ADX Gate:** Consider adding a hard floor for `regime_adx_normalized > 0.10` in `runtime/spot_strategy.py` for ranging setups.
3. **WAE/Chop Filter:** Review the interaction between `wae_exploding` and `chop_ranging` to ensure momentum is not chased when the market is clearly boxing.

Report generated on: Monday, May 4, 2026
Version: v18.17 (Discovery Mode)
