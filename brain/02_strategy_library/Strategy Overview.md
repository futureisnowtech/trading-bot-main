# Strategy Overview

#active #strategy

**Status as of: 2026-04-30**  
**Scope: active operator truth plus archived lane summary**

## Active Live Strategy

### Coinbase Spot Scalp

- **Entry runner**: `scheduler/v10_runner.py`
- **Execution**: `spot_engine.py` → `execution/coinbase_spot_broker.py`
- **Truth layer**: `runtime/spot_position_truth.py`
- **Setup policy**: `runtime/spot_strategy.py`
- **Regimes**: `TREND`, `NEUTRAL`, `CHOP`
- **Current live stance**:
  - `CHOP` blocked
  - `pullback_reclaim` quarantined
  - `maker_first` only
  - `taker_fallback` disabled
  - `TradingView` monitor-only

### Binding setup families

- `impulse_continuation`
- `pullback_reclaim`
- `compression_breakout`
- `trend_resume_after_shakeout`
- `compression_expansion_retest`

For tiny live, `pullback_reclaim` remains quarantined until explicit evidence justifies promotion.

### Current objective

The spot lane is optimized for:

- truthful live holdings visibility
- fewer entries
- tighter stop / stagnation control
- route-aware and fee-aware net expectancy
- suppression of weak clusters rather than activity for its own sake

## Dormant / Reference Strategies

These remain in the repo, but they are not authoritative for active live spot decisions:

- Coinbase nano perp futures
- ForecastEx
- MES archived futures
- stocks lane
- older multi-agent debate and conviction-stack systems

They may be researched, tested, or reactivated later, but they do not define the current live operator truth.

## Historical Note

Older strategy documents describing:

- Coinbase Advanced Trade spot
- Bybit perps
- Tradovate MES
- 5-agent debate
- MACD-only conviction stacks

are historical only. Use git history or archived notes if that context is needed.

