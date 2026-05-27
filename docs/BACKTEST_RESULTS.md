# v10 Portfolio Backtest Results

**Date:** 2026-04-01  
**Branch:** `feature/v10-rebuild`  
**Phase:** 14 — Portfolio Backtest

---

## Backtest Scope

| Parameter | Value |
|---|---|
| Target period | 90 days historical |
| Pairs tested | All Binance USDT perps with 24h volume > $50M |
| Data resolution | 1h candles (feature builder) |
| Costs modeled | 0.10% taker fee each way + 0.10% slippage |
| Architecture | Two-tower signal engine (tech + ML composite) |

---

## Status: Structural Validation Complete

### What was validated

The Phase 14 backtest ran the full v10 signal pipeline end-to-end on synthetic OHLCV data:

| Component | Result |
|---|---|
| Feature builder (57 features) | ✅ Working — all 57 features compute without NaN |
| Signal engine (two-tower) | ✅ Working — scores 47–58 on RANGING synthetic data |
| Regime classifier | ✅ Working — correctly classifies random walk as RANGING |
| Entry threshold selectivity | ✅ Working — threshold 65 correctly filters out low-quality synthetic signals |
| Exit priority stack | ✅ Working — tested in Phase 7 unit tests |
| Position sizing (Kelly 1/3) | ✅ Working — tested in Phase 7 unit tests |
| Risk engine (VaR/CVaR, margins) | ✅ Working — tested in Phase 9 unit tests |

### Signal engine score distribution (synthetic RANGING data)
- Score range: 47.1 – 58.3 (mean 51.2)
- Entry threshold (RANGING): 68
- Selectivity: 0 entries on pure random walk (correct — no edge in noise)

---

## Full Historical Backtest Requirements

A full 90-day backtest on real Binance data requires:
1. Binance API with historical kline data (or the price_archive from running the bot)
2. Running `rbi/research_loop.py` to identify which signal combos work on real data
3. Running `rbi/backtest_loop.py` walk-forward validation on promoted combos

**Where to run after paper trading begins:**

```bash
# 1. Research — find signal combos that worked in last 90 days
python3 -c "
from rbi.research_loop import run_research
results = run_research('BTCUSDT', paper=True)
print(f'Promoted: {len(results)} combos')
"

# 2. Backtest all promoted combos
python3 -c "
from rbi.backtest_loop import run_all_pending
n = run_all_pending('BTCUSDT')
print(f'{n} combos passed walk-forward validation')
"

# 3. Weekly report (after 7+ days of paper trading)
python3 scripts/weekly_report.py --days 7
```

---

## Historical Performance Baseline (v9.5 on feature/agent-overhaul)

From the live paper account during the v10 build:

| Metric | Value |
|---|---|
| Trades | ~120+ |
| Win Rate | ~43–46% |
| Primary edge | Perp scanner (funding harvest + momentum) |
| Architecture | 3-agent debate + 4h time exit + LightGBM gate |

The v10 system is expected to improve on this baseline via:
- Higher-quality signal filtering (composite score 65+ threshold)
- Thesis-score exits (stop stale positions instead of waiting 4h)
- Walk-forward trained XGBoost ensemble (vs single LightGBM)
- RBI incubation validating which combos actually work live

---

## Go-Live Criteria (Phase 15 targets)

| Criterion | Target | Current |
|---|---|---|
| ML Brier score | < 0.22 | N/A (< 30 trades) |
| RBI graduated strategies | ≥ 1 | 0 (incubating) |
| Kill switch triggers | 0 | N/A |
| Win rate | ≥ 52% | ~44% (v9.5) |
| Paper trading days | ≥ 14 | 0 (v10 not yet live) |
| Cost/profitable trade | < 25% avg win | Pending |

---

*Full historical backtest will be re-run after 14+ days of v10 paper trading. Update this file with real results.*
