# RUNTIME_INVARIANTS.md
# Spot Scalp Lane — Runtime Invariants
# Version: 2026-05-27

---

## Invariant Definitions

| # | Invariant | Why it matters | How checked | Violation → |
|---|---|---|---|---|
| RI-01 | Every live spot close writes `ml_feature_snapshots` within the close call | Without this, calibrator and ML training are blind | `close_spot()` calls `learning_loop.record_closed_trade()` directly; SLI-01 proof test | HALT candidate (KS4) |
| RI-02 | Every live spot close writes `trade_attribution` via `analyze_closed_trade()` | Attribution is the truth layer for Bayesian learning | `close_spot()` → `learning_loop` → `analyze_closed_trade()` | HALT candidate (KS3) |
| RI-03 | `ml_feature_snapshots` row includes canonical lineage fields (candidate_id, route_type, setup_family, spot_regime) | Without lineage, calibrator joins on fuzzy timestamps and produces wrong clusters | Wired in `learning_loop.record_closed_trade()` and `_spot_entry_features()` | Warning + reconstructed=1 flag |
| RI-04 | `trade_attribution.signal_stats` upsert uses 4-key UNIQUE (signal_name, regime, strategy, source) | Old 3-key conflict caused silent attribution drops | Fixed in `signal_performance.py` ON CONFLICT clause | Silent attribution loss |
| RI-05 | `pullback_reclaim` in NEUTRAL regime is quarantined before execution | 115-trade, 0%-WR cluster. Must not be re-admitted without evidence | `spot_quality_block_reason()` checks `SPOT_PULLBACK_RECLAIM_NEUTRAL_BLOCKED`; SG-01 proof test | Systematic losses resume |
| RI-06 | `pullback_reclaim` in CHOP regime is quarantined | 22-trade, 0%-WR cluster | `spot_quality_block_reason()` checks `SPOT_PULLBACK_RECLAIM_CHOP_BLOCKED`; SG-02 proof test | Systematic losses resume |
| RI-07 | Taker fallback is disabled at entry (maker-only policy) | 113 taker trades in failure window, 0% WR, higher fee burn | `_maker_first_buy()` / `_maker_first_sell()` check `SPOT_TAKER_FALLBACK_ENABLED`; SG-10/SG-11 | Higher fee drain resumes |
| RI-08 | Stop is set at entry time, never widened | Core risk rule (Amygdala Removal Rule #3) | `position_manager.py` never calls `set_stop(new > current)` | Capital exposure widens |
| RI-09 | Stop tighten multipliers are applied after ATR calculation | Evidence-derived constraint from failure window MAE analysis | `_compute_stop_pct()` applies SPOT_STOP_TIGHTEN_* before return; SG-07/08/09 | Wider stops than spec |
| RI-10 | Kill switch checks consecutive losses before spot entry attempt | Prevents compounding losses after a cluster fails | `_attempt_entry()` calls `check_spot_kill_switch()` before spot path; SG-12/13 | Loss cluster continues unchecked |
| RI-11 | Spot engine symbol routing is exclusive: strategy prefix `spot_*` never contaminates perp `open_positions` | Mixing spot and perp positions corrupts P&L and sizing | `get_spot_positions()` filters by `strategy LIKE 'spot_%'`; separate table columns | P&L contamination |
| RI-12 | ML inference uses symbol-aware model key (pair-specific model if available, GENERIC fallback) | Generic model gives stale inference for all symbols | `model_store._get_pair_key()` strips suffix and maps to pair; `signal_engine._get_ml_score()` passes symbol hint | Stale generic ML for all symbols |
| RI-13 | Calibrator lineage joins on `ml_feature_snapshots.trade_id` (not fuzzy timestamp) | Timestamp join caused wrong cluster attribution | `spot_edge_calibrator._fetch_closed_spot_trades()` joins via trade_id | Wrong calibration clusters |
| RI-14 | TradingView is context-only (no synthetic candidate injection, no direct conviction override) | TV was a known failure mode — raw +20 scoring is absent | `signal_engine.py` TV contribution is bounded; `TV_PROMOTE_SYNTHETIC_CANDIDATES=false` | TV becomes entry trigger |
| RI-15 | Config controls runtime behavior; no hidden hardcoded constants override config values | Prevents silent config drift | SPOT_* values in `config.py` are the single source (e.g. SPOT_MIN_ORDER_USD=$5.0); spot_engine.py reads config at runtime | Config changes have no effect |

---

## Test Coverage

| Invariant | Proof Test | Status |
|---|---|---|
| RI-01 | `test_sli01_spot_close_persists_learning_and_tv_lineage` | PASS |
| RI-02 | `test_sli01_spot_close_persists_learning_and_tv_lineage` | PASS |
| RI-03 | `test_sli01_spot_close_persists_learning_and_tv_lineage` | PASS |
| RI-04 | implicitly tested via signal_performance migration | PASS |
| RI-05 | `test_sg01_pullback_reclaim_neutral_quarantined` | PASS |
| RI-06 | `test_sg02_pullback_reclaim_chop_quarantined` | PASS |
| RI-07 | `test_sg10_taker_buy_disabled_when_config_false` / `sg11` | PASS |
| RI-08 | Existing position_manager proof tests | PASS |
| RI-09 | `test_sg07_neutral_tighten_applied` / `sg08` / `sg09` | PASS |
| RI-10 | `test_sg12_kill_switch_consecutive_losses_fires` / `sg13` | PASS |
| RI-11 | `test_sp01..08` spot position tests | PASS |
| RI-12 | `model_store._get_pair_key()` symbol mapping (indirect via SLI tests) | PASS |
| RI-13 | calibrator lineage join (indirect via backfill) | PARTIAL |
| RI-14 | TradingView contract tests (existing) | PASS |
| RI-15 | Config precedence (config.py is authoritative) | PARTIAL — no dedicated test |

---

## Violation Response Matrix

| Severity | Response |
|---|---|
| CRITICAL | Spot entry blocked; HALT event written to system_events; operator notification via dashboard |
| HIGH | Warning logged; monitoring surface updated; entry may still proceed |
| MEDIUM | Debug log; no action |
