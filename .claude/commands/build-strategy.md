---
name: build-strategy
description: Scaffold a new trading strategy from spec to working code
argument-hint: "<strategy_name> [--lane=crypto|equity|futures|perp]"
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
  - Grep
---

Build a new trading strategy from scratch, following the existing architecture.

## Process

### 1. Parse Arguments

Extract strategy name and lane from `$ARGUMENTS`.
Default lane: crypto.

### 2. Review Existing Patterns

Read the most similar existing strategy:
- Crypto: `strategies/crypto_macd.py`
- Equity: `strategies/equity_momentum.py`
- Mean-reversion: `strategies/crypto_mean_reversion.py`
- Futures: `strategies/futures_scalper.py`

Read `strategies/base_strategy.py` for the Signal dataclass and abstract base.

### 3. Design the Strategy

Before writing code, output a design summary:
- **Entry signals**: Which indicators trigger entry? (use `data/indicators.py` output fields)
- **Exit logic**: Stop %, take-profit %, trailing conditions
- **Regime filter**: Which market regimes (trending/ranging/volatile) this strategy targets
- **Debate integration**: Does this go through the 3-agent debate or use direct signal?
- **Risk params**: Default position size, stop %, take-profit %

### 4. Implement

Create `strategies/{strategy_name}.py` with:
- Class inheriting from `BaseStrategy`
- `generate_signal(market_data: dict) -> Signal` method
- All params read from `config.py` (never hardcoded)
- Inline comments for any non-obvious math

### 5. Wire Config

Add any new params to `config.py` with sensible defaults.
Add corresponding placeholders to `.env.example`.

### 6. Register in Job Runner

Show the user exactly which lines in `scheduler/job_runner.py` need to be updated to run this strategy.
Do NOT edit job_runner.py automatically — show the diff and ask for confirmation.

### 7. Backtest

Run a quick backtest to verify the strategy fires at least some trades:
```bash
python3 run_backtest.py --strategy {strategy_name} --period 1mo
```

Output the results and flag if win_rate < 30% or total_trades < 5.

### 8. Update CLAUDE.md

Add a one-line entry to the Project Structure section for the new file.

## Success Criteria

- [ ] Strategy file created and importable (`python3 -c "from strategies.{name} import ..."`)
- [ ] At least one signal fires in backtest
- [ ] All params in config.py (no hardcoded values)
- [ ] CLAUDE.md updated
