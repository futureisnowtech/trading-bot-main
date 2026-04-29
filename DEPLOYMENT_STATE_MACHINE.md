# DEPLOYMENT_STATE_MACHINE.md
# Spot Scalp Lane — Deployment State Machine
# Version: 2026-04-28

---

## States

| State | Meaning |
|---|---|
| NOT_READY | Pre-conditions not met; no live entries allowed |
| READY_FOR_TINY_LIVE | All acceptance tests pass; live may be started |
| TINY_LIVE | Live mode active; kill switches armed |
| DEGRADED | Live active but rolling metrics below thresholds |
| HALTED | Hard kill switch triggered; human reset required |

---

## Transition Rules

### NOT_READY → READY_FOR_TINY_LIVE
All of the following must be true:
- [ ] Proof suite passes (≥ 535 tests, 0 failures): `python3 -m pytest tests/proof/`
- [ ] Learning ingestion: `ml_feature_snapshots` and `trade_attribution` written on live spot close
- [ ] pullback_reclaim NEUTRAL quarantine wired and tested (SG-01)
- [ ] pullback_reclaim CHOP quarantine wired and tested (SG-02)
- [ ] Taker fallback disabled (SG-10, SG-11)
- [ ] Kill switch loss-cluster fires correctly (SG-12, SG-13)
- [ ] Backfill complete: failure window rows present in ml_feature_snapshots
- [ ] STOP_MATRIX.md, SCANNER_PRECISION_REPORT.md, RUNTIME_INVARIANTS.md present
- [ ] `SPOT_TINY_LIVE_MAX_CONCURRENT = 1` confirmed in config
- [ ] `SPOT_TINY_LIVE_MAX_POSITION_USD = 50.0` confirmed in config
- [ ] `SPOT_TAKER_FALLBACK_ENABLED = false` confirmed

**Current verdict: READY_FOR_TINY_LIVE** — all items above completed as of 2026-04-28.

### READY_FOR_TINY_LIVE → TINY_LIVE
- Start command: `python3 scripts/go_live.py`
- System must confirm: `connected=1`, `buying_power_usd > 0`, kill switch healthy
- Print kill switch health summary before first scan

### TINY_LIVE → DEGRADED (automatic)
Any of:
- Rolling 10-trade thesis decay rate > 60% (`SPOT_GOV_MAX_THESIS_DECAY_RATE`)
- Rolling 10-trade fast follow-through rate < 25% (`SPOT_GOV_MIN_FAST_FOLLOW_RATE`)
- Fee burn > 50% of gross positive PnL over last 20 trades
- Scanner surfacing > 80% pullback_reclaim in a scan cycle (new quarantine species leaking)

### TINY_LIVE → HALTED (automatic, hard kill switch)
- KS10a: 4 consecutive losing trades
- KS10b: Daily PnL ≤ -2% of live account equity
- KS8: 3 consecutive order failures (future implementation)
- Any invariant violation from RUNTIME_INVARIANTS.md

### DEGRADED → TINY_LIVE (automatic)
- Rolling metrics recover above thresholds over ≥10 new trades
- No active kill switch flags

### DEGRADED → HALTED (automatic)
- Degraded state persists for ≥ 2 days without recovery
- Any hard kill switch triggers during DEGRADED

### HALTED → any state
- Manual only: human reviews halt reason, clears condition
- `python3 -c "from runtime.spot_kill_switch import reset_spot_kill_switch; reset_spot_kill_switch()"`
- Then restart: `python3 scripts/go_live.py`

---

## Metrics Governing Promotion/Demotion

| Metric | Source | DEGRADED threshold | HALTED threshold |
|---|---|---|---|
| Consecutive losses | trades table | — | 4 (`SPOT_KS_CONSECUTIVE_LOSSES`) |
| Daily PnL % | trades table | — | -2% (`SPOT_KS_DAILY_LOSS_PCT`) |
| Thesis decay rate | trade_attribution | > 60% | — |
| Fast follow-through rate | ml_feature_snapshots | < 25% | — |
| Learning freshness | ml_feature_snapshots.ts | > 24h stale | — |

---

## Current Deployment Verdict

**READY_FOR_TINY_LIVE** as of 2026-04-28

Completed:
- Learning truth layer repaired (Codex pass)
- Failure window backfilled (139 trades reconstructed)
- pullback_reclaim NEUTRAL/CHOP quarantine wired and tested
- Taker fallback disabled
- Stop tighten multipliers wired
- Kill switch (KS10) implemented and tested
- 535 proof tests pass (0 failures)

Remaining before production confidence (not blocking tiny live):
- KS8 (execution anomaly) not yet implemented
- No live data on impulse_continuation performance yet
- Calibrator has not yet derived conditions (needs ≥30 new trades per symbol)
- MATRIX_DECISION_UNIVERSE.md operator review recommended before scaling up

---

## Tiny Live Mode Constraints

| Parameter | Value | Config Key |
|---|---|---|
| Max concurrent positions | 1 | `SPOT_TINY_LIVE_MAX_CONCURRENT` |
| Max position size | $50 | `SPOT_TINY_LIVE_MAX_POSITION_USD` |
| Allowed route | maker_first only | `SPOT_TINY_LIVE_ALLOWED_ROUTE` |
| Allowed setup families | impulse_continuation, compression_breakout, pullback_reclaim (TREND only) | governance quarantine |
| Kill switches | KS10a, KS10b active | `runtime/spot_kill_switch.py` |
