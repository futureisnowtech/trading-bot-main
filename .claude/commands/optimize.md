---
name: optimize
description: Run parameter optimization for a strategy using walk-forward validation
argument-hint: "<strategy_key> [--symbol=BTC-USDC] [--period=3mo]"
allowed-tools:
  - Read
  - Write
  - Bash
  - Glob
---

Optimize strategy parameters using the walk-forward backtest engine.

## Process

### 1. Parse Arguments

Extract `strategy_key`, `symbol` (default: BTC-USDC), and `period` (default: 3mo) from `$ARGUMENTS`.

### 2. Baseline Run

Run a baseline backtest with current params:
```bash
python3 run_backtest.py --strategy {strategy_key} --symbol {symbol} --period {period}
```

Record: win_rate, sharpe, max_drawdown, total_trades.

### 3. Identify Tunable Parameters

Read `config.py` and identify which params are relevant to this strategy:
- Stop-loss / take-profit percentages
- ADX minimum threshold
- Indicator periods (MACD fast/slow/signal)
- Position size

### 4. Walk-Forward Grid Search

For each parameter combination, run via the MCP tool:
```
run_backtest(symbol=symbol, strategy=strategy_key, period=period)
```

Search grid (conservative — 9 combinations max to avoid overfitting):
- stop_pct: [current × 0.8, current, current × 1.2]
- tp_pct: derived from stop to maintain 3:1 R/R

### 5. Rank Results

Build a comparison table:
| Params | Win Rate | Sharpe | Max DD | Trades | Score |

Score = sharpe × win_rate / max_drawdown (higher is better).

### 6. Validation Gate

Before recommending any change:
- Must pass: WR ≥ 30%, Sharpe ≥ 0.5, DD ≤ 20%, Trades ≥ 15
- OOS improvement must be > 10% over baseline on at least 2 metrics
- Flag if best params are at the edge of the search grid (suggests extrapolation)

### 7. Recommendation

Output the recommended params with:
- Comparison vs baseline (delta on each metric)
- Devil's advocate note: what could cause this to fail in live?
- Exact lines to change in `config.py` (show diff, don't auto-apply)

## Anti-Overfitting Rules

- Never optimize more than 3 params simultaneously
- Always hold out the most recent 20% of the period as true OOS
- If win_rate improves but Sharpe drops — the new params are more volatile, not better
- If best result is on the search grid boundary — expand grid before concluding

## Success Criteria

- [ ] Baseline documented
- [ ] At least 5 combinations tested
- [ ] Winner passes all validation gates
- [ ] Recommendation includes specific config.py diff
