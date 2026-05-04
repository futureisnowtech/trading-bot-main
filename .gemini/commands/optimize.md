---
name: optimize
description: Spot-lane parameter optimization using replay/backtest evidence, not indicator expansion
argument-hint: "[--symbol=BTC] [--setup=impulse_continuation] [--regime=TREND|NEUTRAL]"
allowed-tools:
  - Read
  - Write
  - Bash
  - Glob
---

Optimize only within the current spot truth-lane contract.

## Read First

1. `AGENTS.md`
2. `runtime/spot_strategy.py`
3. `SCANNER_PRECISION_REPORT.md`
4. `STOP_MATRIX.md`
5. `PROFIT_GOVERNANCE.md`

## Rules

- Do not add new indicators.
- Do not widen the live lane casually.
- Do not optimize around `pullback_reclaim` unless it is explicitly being re-evaluated from quarantine.
- Prefer tighter governance and better suppression over higher trade count.

## Process

1. Define the specific symbol / setup / regime cluster.
2. Establish current baseline from reports + DB evidence.
3. Use replay / backtest paths already in the repo.
4. Compare:
   - expectancy after fees
   - fast follow-through
   - thesis-decay rate
   - stop efficiency
5. Recommend only changes that improve net edge without breaking the truth-lane contract.

## Output

- baseline
- evidence used
- candidate parameter changes
- expected tradeoff
- whether the change is suitable for live, replay-only, or research-only

