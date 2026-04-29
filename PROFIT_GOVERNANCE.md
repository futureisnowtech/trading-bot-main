# PROFIT_GOVERNANCE.md
# Spot Scalp Lane — Profit Governance
# Derived from: 140-trade live failure window (2026-04-22 – 2026-04-28)
# Version: 2026-04-28

---

## Cluster Status Table

| Cluster | n | Net PnL | Avg | WR | Status | Condition to promote |
|---|---|---|---|---|---|---|
| pullback_reclaim × NEUTRAL × any route | 115 | -$148 | -$1.29 | 0% | **QUARANTINED** | 30+ live trades with net positive expectancy AND positive profit factor |
| pullback_reclaim × CHOP × any route | 22 | -$16 | -$0.70 | 0% | **QUARANTINED** | 20+ live trades with net positive expectancy |
| pullback_reclaim × TREND × maker_first | 2 | -$1.11 | -$0.56 | 0% | PROBATION | 10+ live trades; any halt suspends to QUARANTINE |
| compression_breakout × CHOP × maker_first | 1 | -$0.77 | -$0.77 | 0% | PROBATION | 10+ live trades |
| impulse_continuation × any × maker_first | 0 | — | — | — | ALLOWED | First 10 trades evaluated |
| compression_breakout × TREND/NEUTRAL × maker_first | 0 | — | — | — | ALLOWED | First 10 trades evaluated |

---

## Rolling Suppression Rules

Automatic probation trigger (any cluster, rolling 30d window):

| Metric | Threshold | Action |
|---|---|---|
| Net expectancy after fees | < 0 over ≥ 20 trades | → PROBATION |
| Profit factor | < 1.0 over ≥ 20 trades | → PROBATION |
| Thesis decay rate | > 60% of exits | → PROBATION |
| Fast follow-through rate | < 25% | → PROBATION |
| Net expectancy | < 0 over ≥ 50 trades | → QUARANTINE |

Config parameters: `SPOT_GOV_MAX_THESIS_DECAY_RATE=0.60`, `SPOT_GOV_MIN_FAST_FOLLOW_RATE=0.25`,
`SPOT_GOV_MIN_PROFIT_FACTOR=1.00`, `SPOT_GOV_MIN_EXPECTED_NET_PNL=0.0`

---

## Route-Specific Policy

| Route | Status | Rationale |
|---|---|---|
| maker_first | ALLOWED | Only admitted route; lower fee burden |
| taker_fallback | DISABLED | `SPOT_TAKER_FALLBACK_ENABLED=false`; 113 trades, 0% WR, higher fees |

To re-enable taker_fallback: require ≥20 maker-only trades with positive expectancy
AND evidence that taker route provides incremental positive value over maker-skip.

---

## Symbol-Level Status

All 8 symbols were negative in the failure window. Per-trade loss varies:
- Worst: ETH (-$1.60 avg), BTC (-$1.52), SOL (-$1.44)
- Best: DOGE (-$0.57), XRP (-$0.85)

Symbol suppression is NOT implemented at the symbol level because the setup family
quarantine eliminates the primary failure mode across all symbols. Symbol-level
suppression would require per-symbol positive expectancy evidence (≥20 trades).

---

## Auto-Suppression Thresholds

Governed by `SPOT_GOV_*` config values. The governance nightly audit
(`monitoring/nightly_audit.py`) evaluates these thresholds. Currently:
- Minimum cluster trades for suppression decision: 5 (`SPOT_GOV_MIN_CLUSTER_TRADES`)
- Minimum confident sample: 20 (`SPOT_GOV_CONFIDENT_TRADES`)
- High confidence: 50 (`SPOT_GOV_HIGH_CONF_TRADES`)

---

## Promotion/Demotion Ladder

```
QUARANTINED ← hard quarantine based on evidence ← needs 30+ positive-expectancy trades to exit
    ↓ (20+ positive-expectancy trades)
PROBATION ← rolling window evaluation, first 10 trades special monitoring
    ↓ (10+ positive-expectancy trades with no halt)
ALLOWED ← active trading
    ↓ (rolling window fails thresholds above)
PROBATION
    ↓ (persists through probation window or hard KS fires)
QUARANTINED
```

Promotion from QUARANTINED requires:
1. ≥ 30 live closed trades in the cluster
2. Net expectancy > 0 after fees over those 30 trades
3. Profit factor ≥ 1.2 (conservative buffer)
4. Thesis decay rate < 60%
5. Explicit review and config change (`SPOT_PULLBACK_RECLAIM_NEUTRAL_BLOCKED=false`)

---

## Fee Governance

Target: fee burden < 30% of gross positive PnL (currently 100%+ — all trades losing).
At maker-first 0.03% per side (0.06% round-trip) on $50 average:
- Fee per trade: ~$0.03
- Required minimum gross profit to break even: $0.03

The economics gate (`risk/spot_economics_gate.py`) enforces this at admission time.
