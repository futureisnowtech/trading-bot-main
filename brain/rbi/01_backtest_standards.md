# Backtest Standards — Walk-Forward OOS Specification
## Version 1.0 | Created: 2026-03-26

---

## Why This Document Exists

The current backtest pipeline runs a single contiguous period. That passes in-sample. It does NOT tell you whether the strategy generalizes to new data. A single-period backtest that shows 60% WR might be showing you the regime it was tuned to, not the strategy's actual edge.

Walk-forward testing is the minimum standard for a strategy that will trade real money.

---

## Walk-Forward Protocol

### Standard Walk-Forward (required for all new strategies)

**Setup:**
- Total data window: 90 days of 5-min candles (available via price_archive.db)
- Training window: 60 days (in-sample)
- Test window: 30 days (out-of-sample)
- Step size: 15 days (overlap by 15 days each step)

**Resulting folds:**

```
Fold 1: Train [Day 1-60]   → Test [Day 61-90]
Fold 2: Train [Day 16-75]  → Test [Day 76-90+15]
Fold 3: Train [Day 31-90]  → Test [Day 91-120]  ← requires 120 days of data
```

For 90-day data, run at minimum 2 folds. For 180-day data, run 4 folds.

**Pass criteria:** OOS performance must hold across ≥ 75% of folds.
- Acceptable: 2/2 or 3/4 folds pass
- Borderline: 1/2 folds pass → extend to more folds before deciding
- Fail: 0/2 folds pass → reject strategy regardless of in-sample results

### Accelerated Walk-Forward (for variant testing only)

When testing a variant of an already-validated strategy (e.g., new threshold values, not new signal logic):
- 1 OOS fold is acceptable
- Must use a period NOT used in original validation

---

## Pass/Fail Gates Per Fold

Every OOS test fold must independently pass:

| Metric | Minimum | Notes |
|--------|---------|-------|
| Win rate | ≥ 30% | Adjusted for 3:1 R:R; break-even is 25% |
| Profit factor | ≥ 1.2 | (gross_wins / gross_losses) |
| Sharpe ratio | ≥ 0.5 | Annualized, assuming 252 trading days |
| Max drawdown | ≤ 20% | As % of starting capital in the fold |
| OOS trade count | ≥ 15 | Fewer than 15 trades = insufficient signal |

**Note on trade count:** If a fold produces < 15 OOS trades, do NOT fail the strategy — instead extend the test window. Crypto strategies can have natural dry spells. 15 trades minimum is a floor for statistical validity.

---

## Look-Ahead Bias Checklist

Before archiving any backtest result, verify:

**Data hygiene:**
- [ ] Indicators computed on bars that were complete at the time of the simulated signal (no `iloc[-1]` on incomplete candles)
- [ ] No forward-filling of NaN values that uses future data
- [ ] price_archive.db contains only data available at bar close time (it does — WAL writes happen after bar close)

**Signal hygiene:**
- [ ] All signal thresholds in `market_data_to_signals()` were fixed before the backtest period started (not tuned to fit the test period)
- [ ] No "I saw the chart and tuned the parameters" optimization on the OOS data
- [ ] regime is set at bar N, NOT using regime computed on bar N+1 or later

**Execution hygiene:**
- [ ] Fill price = close of the signal bar + slippage estimate (default 0.1%)
- [ ] Fees deducted on both entry and exit
- [ ] No partial fills assumed (assume full fill at close price)

**Common look-ahead mistakes in this codebase to watch for:**
- `rolling()` windows that accidentally include future bars — check `min_periods` settings
- AVWAP computed using intraday data that includes the signal bar's final price
- Regime detector using ADX/ATR computed over a window that spans the test/OOS boundary

---

## Fee Model Specification

All backtests must use these fee assumptions:

| Parameter | Value | Source |
|-----------|-------|--------|
| Taker fee | 0.60% | Coinbase Advanced Trade tier |
| Maker fee | 0.40% | Not used (assume all market orders) |
| Slippage | 0.10% per side | Conservative estimate for $250 position |
| Round-trip cost | 1.40% | (0.60 + 0.10) × 2 |

**Break-even calculation:**
- Round-trip cost: 1.40%
- With 3:1 R:R (1.5% stop, 4.5% target): a 1.40% round-trip on a $250 position = $3.50
- At $250 position with $3.50 fee drag, the gross target move needs to be (4.5% + 1.4%) = 5.9% gross for 4.5% net
- Break-even WR = (1.5 + 1.4) / (4.5 + 1.5) = 2.9 / 6.0 = **48%** INCLUDING fees

This means the 30% WR minimum in the gate above is the NET target. In-sample results should show ≥ 35% to provide fee headroom.

---

## Regime-Bucketed Reporting

All backtest results must be reported by regime:

| Regime | Definition | Expected behavior |
|--------|-----------|-------------------|
| trending | ADX > 25, Hurst H > 0.55 | Momentum signals should work; mean-reversion signals should fail |
| ranging | ADX < 20, Hurst H < 0.45 | Mean-reversion signals should work; momentum signals should fail |
| volatile | ATR spike > 1.5× 14-bar average | Both may fail; squeeze-based signals most relevant |

**A strategy that only works in "trending" is a trending strategy.** Label it as such and disable it when regime = ranging/volatile.

**If a strategy has no regime preference**, test whether adding regime filtering improves OOS performance. Usually it does.

---

## Backtest-to-Live Gap Management

**The critical known issue:** The backtest engine runs math signals only. The live system runs math signals + 8-agent AI debate + prescreener + session analyst + macro gate. These are different pipelines.

**What this means:**
1. Backtest WR ≠ Live WR (live will be different — usually higher due to more filtering, but unknown direction)
2. The rolling backtest validator (`live_backtest_validator.py`) validates the math-only path
3. There is no backtest of the full live pipeline

**How to manage this gap:**
- Track live WR separately from backtest WR
- After 30 real trades, compare: `live_WR - backtest_OOS_WR`
- If divergence > 15pp in either direction, run root-cause review
- If live WR consistently ABOVE backtest WR: AI debate is adding alpha (good)
- If live WR consistently BELOW backtest WR: AI debate is adding noise (investigate)

**Long-term fix (Phase B of roadmap):** Add an AI-inclusive backtest mode that replays historical signals through a lightweight debate stub to approximate the live pipeline. This would require ~10 API calls per backtest trade — expensive for 90-day backtests but feasible for 30-day OOS folds.

---

## Archiving Requirements

Every completed backtest (pass or fail) must be archived to the `backtest_results` table:

```sql
INSERT INTO backtest_results (
    strategy_name, variant, symbol, timeframe,
    period_start, period_end,
    total_trades, win_rate, profit_factor, total_pnl,
    sharpe, max_drawdown, avg_pnl, passed,
    archived_at, notes
)
```

The `notes` field must contain:
- `oos_fold_N` for OOS fold results
- `in_sample` for in-sample training run
- `live_rolling_30d` for live_backtest_validator results (already implemented)
- `walk_forward_summary` for aggregated walk-forward results

**Never delete backtest_results rows.** Failed backtests are valuable evidence that the strategy does NOT work in certain periods. Keep the record.

---

## Quick Reference: Run Walk-Forward

```bash
# Current: single period
python3 run_backtest.py --strategy crypto --symbol BTC-USD --period 6mo

# Target: walk-forward (when implemented)
python3 run_backtest.py --strategy crypto --symbol BTC-USD \
    --walk-forward --folds 3 --train-days 60 --test-days 30
```

Until walk-forward is implemented in `run_backtest.py`, run two manual backtests:
1. `--period 6mo` (in-sample, days 1-180)
2. `--period 3mo` (OOS, days 181-270 — uses different recent data)

Compare: if OOS WR is within 10pp of in-sample WR, the strategy is likely generalizing.

---
*Standards version 1.0 — update when backtest engine is modified*
