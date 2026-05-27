# MATRIX_DECISION_UNIVERSE.md
# Spot Scalp Lane — Operator Decision Universe
# Version: 2026-05-27
# Evidence base: 140 live trades, 2026-04-22 – 2026-04-28 (Audit pending)

---

## What the lane should trade

| Allowed | Condition |
|---|---|
| impulse_continuation | Any regime, maker_first only, score floor from regime config |
| compression_breakout | TREND or NEUTRAL regime, maker_first only |
| pullback_reclaim | TREND regime ONLY, maker_first only, requires higher setup_score |

## What the lane must NOT trade (quarantined)

| Banned | Evidence | Config flag to re-enable |
|---|---|---|
| pullback_reclaim × NEUTRAL | n=115, 0% WR, avg -$1.29 | `SPOT_PULLBACK_RECLAIM_NEUTRAL_BLOCKED=false` |
| pullback_reclaim × CHOP | n=22, 0% WR, avg -$0.70 | `SPOT_PULLBACK_RECLAIM_CHOP_BLOCKED=false` |
| Any setup × taker_fallback route | n=113, 0% WR, higher fee | `SPOT_TAKER_FALLBACK_ENABLED=true` |

Re-enabling any quarantined cluster requires: ≥30 live closed trades with positive net expectancy after fees.

---

## Symbol Status

| Symbol | Status | Notes |
|---|---|---|
| BTC | ALLOWED | Core; worst loss in failure window but that was pullback_reclaim |
| ETH | ALLOWED | Core; same |
| SOL | ALLOWED | Core |
| XRP | ALLOWED | Core |
| LTC | ALLOWED | Synthetic candidate only; no perp scanner |
| DOGE | ALLOWED | Synthetic candidate; smallest per-trade loss in failure window |
| ADA | ALLOWED | Synthetic candidate |
| LINK | ALLOWED | Synthetic candidate |

No symbol is suppressed at the symbol level — the setup family quarantine is the primary filter.

---

## Route Behavior

| Route | Policy |
|---|---|
| maker_first | ONLY allowed route. Post-only limit order; wait SPOT_MAKER_WAIT_SECONDS; cancel if unfilled. |
| taker_fallback | DISABLED. If maker unfills, cancel and skip the trade. |
| paper_market | Paper mode only; no real execution. |

---

## HTF / TradingView Context

TradingView is a context filter, not an entry trigger:
- Fresh HTF SHORT bias → veto LONG entries
- Fresh HTF CLOSE bias → veto LONG entries (or contribute exit pressure)
- HTF LONG bias → modest boost (config: `TV_SIGNAL_BOOST_CONVICTION=6`)
- No synthetic candidate injection (`TV_PROMOTE_SYNTHETIC_CANDIDATES=false`)
- Signal age max: 900s (`TV_SIGNAL_MAX_AGE_SECONDS=900`)
- Profile: `algobot_htf_v2` on 4H

---

## Stop Behavior

- Stop set at entry time from ATR × symbol_k × tighten multipliers
- NEVER widened after entry (Amygdala Rule #3)
- NEUTRAL regime: 8% tighter (`SPOT_STOP_TIGHTEN_NEUTRAL=0.92`)
- CHOP regime: 12% tighter (`SPOT_STOP_TIGHTEN_CHOP=0.88`)
- pullback_reclaim family: 10% tighter (`SPOT_STOP_TIGHTEN_PULLBACK=0.90`)
- Floor: `stop_floor_pct` from `SPOT_SCALP_SYMBOL_CONFIG` per symbol

---

## Scanner Behavior

- Top-N: 20 candidates per cycle (`SCANNER_TOP_N=20`)
- Workers: 8 parallel (`SCANNER_PARALLEL_WORKERS=8`)
- Core-only: `scanner.scan(..., core_only=True)` for live
- Synthetic injection: LTC/DOGE/ADA/LINK injected for spot-only path (no perp scanner)
- Quarantine gates fire inside `spot_quality_block_reason()` before economics gate

---

## Signals That Are Actually Binding

| Signal | Where bound | Test |
|---|---|---|
| pullback_reclaim NEUTRAL quarantine | `runtime/spot_strategy.py:spot_quality_block_reason()` | SG-01 |
| pullback_reclaim CHOP quarantine | same | SG-02 |
| Taker fallback disable | `spot_engine.py:_maker_first_buy()/_maker_first_sell()` | SG-10, SG-11 |
| Stop tighten multipliers | `spot_engine.py:_compute_stop_pct()` | SG-07, SG-08, SG-09 |
| Loss cluster kill switch | `runtime/spot_kill_switch.py` + `v10_runner._attempt_entry()` | SG-12, SG-13 |
| ML symbol routing | `signal_engine.py → model_store._get_pair_key()` | indirect |
| Attribution lineage | `learning_loop.record_closed_trade()` | SLI-01 |

## Parameters That Are Reporting-Only (not yet bound to runtime gates)

| Parameter | Config key | Binding target |
|---|---|---|
| Thesis decay rate threshold | `SPOT_GOV_MAX_THESIS_DECAY_RATE` | Nightly audit only; not blocking |
| Fast follow-through rate | `SPOT_GOV_MIN_FAST_FOLLOW_RATE` | Nightly audit only; not blocking |
| Profit factor floor | `SPOT_GOV_MIN_PROFIT_FACTOR` | Nightly audit only; not blocking |
| Min expected net PnL | `SPOT_GOV_MIN_EXPECTED_NET_PNL` | Nightly audit only; not blocking |

---

## Conditions Making the Lane NOT READY

- proof suite failures
- kill switch active (check: `from runtime.spot_kill_switch import kill_switch_status`)
- `SPOT_LANE_ACTIVE = false`
- Calibrator active but producing conditions that contradict quarantine
- Lineage fields missing from last 5 live closes

## Conditions for Automated Tiny Live Operation

All are met as of 2026-05-27:
- Proof suite: 535 pass / 0 fail
- Learning truth layer repaired
- Quarantines wired
- Taker disabled
- Kill switch armed (KS10)
- Max 1 concurrent position per symbol, max 3 total
- Floor-aware scaling active: $5.0 minimum order size.
