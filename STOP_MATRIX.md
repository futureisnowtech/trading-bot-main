# STOP_MATRIX.md
# Spot Scalp Lane — Stop Architecture
# Derived from: 140-trade live failure window (2026-04-22 – 2026-04-28)
# Version: spot_stop_matrix_2026_04_28_v1

---

## Current Stop Behavior (pre-surgery)

`_compute_stop_pct()` in `spot_engine.py`:

```
floor    = symbol_cfg.stop_floor_pct   (default 0.010 = 1.0%)
cap      = symbol_cfg.stop_cap_pct     (default 0.020 = 2.0%)
symbol_k = symbol_cfg.symbol_k         (default 1.1)
base     = max(atr_pct * symbol_k, floor)
penalty  += 0.10 if CHOP
penalty  += min(0.20, (rv_ratio - 1.30) * 0.25) if rv_ratio > 1.30
penalty  += 0.05 if low momentum / acceleration
raw_stop = max(floor, min(base * (1 + penalty), cap))
```

No downstream tightening for regime or setup family.

---

## Evidence from Failure Window

| Cluster | n | Avg PnL | Exit: thesis_decay | Exit: stop |
|---|---|---|---|---|
| pullback_reclaim / NEUTRAL / taker | 88 | -$1.29 | 89% | 5% |
| pullback_reclaim / NEUTRAL / maker | 27 | -$1.27 | 89% | 5% |
| pullback_reclaim / CHOP / taker | 22 | -$0.70 | 89% | 5% |

Key finding: **thesis_decay drives 89% of all exits.** Trades enter and drift; the
score thesis decays before price moves enough to hit a hard stop or target. The ATR-
based stop is never touched in most losses. Tighter hard stops would not have
materially improved win rate — but they would have bounded the loss size per trade,
and the entry quarantine (pullback_reclaim NEUTRAL) is the primary fix.

---

## Proposed Stop Behavior (post-surgery, wired in `_compute_stop_pct`)

Tighten multipliers applied after the raw ATR-based stop is computed.
Multipliers take the **minimum** (most conservative applies).

| Condition | Multiplier | Config Key | Evidence |
|---|---|---|---|
| NEUTRAL regime | 0.92 | `SPOT_STOP_TIGHTEN_NEUTRAL` | 115 NEUTRAL trades at avg -$1.28 |
| CHOP regime | 0.88 | `SPOT_STOP_TIGHTEN_CHOP` | 22 CHOP trades at avg -$0.70 |
| pullback_reclaim family | 0.90 | `SPOT_STOP_TIGHTEN_PULLBACK` | 139/140 trades, all losing |
| taker route | 0.90 | `SPOT_STOP_TIGHTEN_TAKER` | Higher fee burn, same 0% WR |
| low setup score | 0.90 | `SPOT_STOP_TIGHTEN_LOW_SETUP` | — |
| weak HTF alignment | 0.95 | `SPOT_STOP_TIGHTEN_WEAK_HTF` | — |

Floor always enforced: `stop >= stop_floor_pct` from symbol config.

Stop is set at entry time and never moved wider. This is the hard invariant.

---

## Stop Rules by Cluster

| Symbol | Regime | Setup | Route | Stop policy |
|---|---|---|---|---|
| Any | NEUTRAL | pullback_reclaim | Any | QUARANTINED — entry blocked before stop is relevant |
| Any | CHOP | pullback_reclaim | Any | QUARANTINED — entry blocked |
| Any | NEUTRAL | impulse_continuation | maker_first | ATR × 0.92 tighten |
| Any | CHOP | compression_breakout | maker_first | ATR × 0.88 tighten |
| Any | TREND | Any | maker_first | ATR × 1.0 (no tighten — positive sample too small) |

---

## Runtime Binding

- File: `spot_engine.py:_compute_stop_pct()`
- Config: `SPOT_STOP_TIGHTEN_NEUTRAL`, `SPOT_STOP_TIGHTEN_CHOP`, `SPOT_STOP_TIGHTEN_PULLBACK`
- Test: `tests/proof/test_spot_governance.py::test_sg07_neutral_tighten_applied` (SG-07, SG-08, SG-09)

---

## Impact Assessment

Primary loss mechanism is thesis_decay (89% of exits). Tighter stops reduce
max loss per trade for positions that drift adversely before decaying, but the
entry quarantine (pullback_reclaim NEUTRAL blocked) eliminates 82% of all recent
losing volume before stops become relevant. Net expected impact:
- Expected loss per admitted trade: reduced by stop tighten where applicable
- Expected trade volume: ~18% of current (only non-NEUTRAL pullback_reclaim setups admitted)
- Expected net PnL: unknown (no live data on TREND impulse_continuation performance)

---

## Invariants

1. Stop distance is computed once at entry; never widened after entry (hardcoded in position_manager.py).
2. Stop must be >= stop_floor_pct (enforced by `max(floor, raw_stop * tighten)`).
3. SPOT_STOP_MATRIX_VERSION in config identifies which matrix version is live.
