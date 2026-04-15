# Strategy Overview

#active #strategy

> ## HISTORICAL SECTION BELOW
> This document was written for the v4.3 architecture (Coinbase Advanced Trade spot,
> Bybit perps, Tradovate MES, MACD-based conviction scoring, 5-agent debate).
> That system no longer exists. The current live system is **v15.2** (2026-04-15).
> See CLAUDE.md and `brain/01_current_system/Current Active Logic.md` for current truth.
> Historical strategies are preserved for research reference only.

---

## v15.2 STRATEGY SUMMARY (2026-04-15)

### Unified Perp Strategy (Crypto Lane — LIVE)
**Entry runner**: `scheduler/v10_runner.py`
**Signal engine**: `signal_engine.py` — two-tower (Technical 0-100 + ML 0-100)
**Execution**: `perps_engine.py` → `execution/coinbase_broker.py`
**Symbols**: BTC/ETH/SOL/XRP (Coinbase CFTC nano perp futures only)
**Scanning**: Kraken Futures + Binance USDM + Hyperliquid (intelligence only)
**Entry**: composite >= regime threshold (TRENDING/RANGING=58, HIGH_VOL=60, LOW_VOL=56)
**Exit**: 7-priority stack (trailing stop, scale-out, thesis score, hard stop, risk forced, kill switch, dead-money)
**Sizing**: Kelly + ATR via `position_manager.compute_position_size()`
**ML**: XGBoost 60% + LightGBM 40%, 57 features, PnL regressor

### ForecastEx Strategy (Forecast Lane — STARTED, enrollment pending)
**Entry runner**: `forecast/runner.py`
**Signal engine**: `forecast/strategy_engine.py` — 3 families (continuation, mean_reversion, late_repricing)
**Execution**: `execution/forecastex_broker.py` (IBKR clientId=3)
**Markets**: Economic events only (CPI/NFP/FOMC/PCE/GDP/PPI/Unemployment)
**Sizing**: Fractional Kelly (cap 0.10), max deployed 35%, per-event 10%

### MES Scalper (DORMANT)
**Status**: ARCHIVED. FUTURES_LANE_ACTIVE=false. Code preserved in `execution/ibkr_broker.py`.

---

**Status as of: 2026-03-25 (HISTORICAL — v4.3 era)**

---

## ACTIVE STRATEGIES

### 1. Crypto MACD (Primary)
**File**: `strategies/crypto_macd.py`
**Status**: CONFIRMED ACTIVE
**Edge**: 3-variant MACD consensus + 15+ conviction signals
**Regime fit**: Trending + momentum
**Entry path**: Full AI debate (5 agents) after conviction gate
**Variants**:
- Workhorse: MACD(3/15/3) — high frequency
- Classic: MACD(4/16/3) — line vs signal crossover
- Sniper: MACD(6/20/5) — strong momentum only (63.7% win rate in backtest)
**Current active variant**: `consensus` (all 3 must agree)

### 2. Crypto Mean Reversion
**File**: `strategies/crypto_mean_reversion.py`
**Status**: CONFIRMED ACTIVE (runs in parallel with MACD path)
**Edge**: Kalman/AVWAP deviation entry; mean reversion in ranging markets
**Regime fit**: Ranging, low ADX (<22)
**Entry logic**: (Kalman dev ≤ −0.8% OR AVWAP dev ≤ −0.5%) + ADX < 22
**Note**: RSI entry gate removed in v4.0; now uses Kalman+AVWAP only

### 3. Crypto Perpetual (Bybit)
**File**: `strategies/crypto_perp_strategy.py`
**Status**: TESTING (PERP_ENABLED=true, no confirmed paper trades)
**Edge**: Long/short perp with funding rate confirmation
**Long entry**: 20-bar breakout + RSI > 55 + funding ≤ 0.03%
**Short entry**: Breakdown + RSI < 45 + funding ≥ 0.01%/8h
**Exit**: 4h flat exit to avoid funding cost drain
**Leverage**: 10× (halved from 20× in v4.0)

### 4. MES Futures Scalper
**File**: `strategies/futures_scalper.py`
**Status**: BELIEVED ACTIVE (paper simulation only — no real Tradovate API)
**Edge**: Opening range breakout on MES (micro E-mini S&P)
**Session**: Market hours only (ET)
**Contract**: MESM6 (June 2026 front month)
**Note**: Paper fills use yfinance ES prices — not real Tradovate fills

---

## INACTIVE STRATEGIES

### Equity Momentum
**File**: `strategies/equity_momentum.py`
**Status**: RETIRED (EQUITY_ENABLED=false)
**Edge**: KST+MACD+VWAP
**Why disabled**: PDT constraints + $500 account too small for meaningful equity trading
**Re-enable when**: Account > $2,500 OR specific opportunity identified

---

## STRATEGY INTERACTION

```
job_runner.py while-True loop:
  ├── Crypto scan (every CRYPTO_SCAN_INTERVAL_SECONDS)
  │   ├── crypto_macd.py → conviction → debate → execute
  │   └── crypto_mean_reversion.py → conviction → execute (separate path)
  ├── Perp scan (every CRYPTO_SCAN_INTERVAL_SECONDS)
  │   └── crypto_perp_strategy.py → debate → execute (Bybit)
  ├── Futures scan (every FUTURES_SCAN_INTERVAL_SECONDS, market hours)
  │   └── futures_scalper.py → debate → execute (Tradovate paper)
  └── Exit review (every candle close, all open positions)
      └── exit_review.py extended thinking → Tudor/Soros/Simons
```

---

## FEE-ADJUSTED EDGE BY STRATEGY

| Strategy | Gross Target | Round-Trip Cost | Net Target | Win Rate Needed |
|---------|-------------|----------------|-----------|----------------|
| Crypto MACD | 4.5% | 1.2% | 3.3% | > 27% |
| Mean Reversion | 4.5% | 1.2% | 3.3% | > 27% |
| MES Futures | Variable | ~$3 per RT (MES) | Depends | > 40% (tight spread) |
| Perp (Bybit) | ~5% target | 0.1% maker + funding | ~4.5% | > 25% |

---

## KEY RESEARCH BACKING

- MACD 3-variant consensus: own backtests; sniper variant 63.7% win rate
- OU half-life [3,60 min]: Ernie Chan mean-reversion framework
- Kalman filter deviation: Adaptive fair value estimate (vs simple MA)
- Kyle lambda: Market impact / liquidity measure (Kyle 1985)
- OBI/TFI microstructure: Stoikov (2017) microprice + aggressor flow theory
- SuperTrend: Olivier Seban's ATR-band trend system
- WaveTrend: LazyBear TradingView adaptation of WT oscillator
- Ichimoku: Goichi Hosoda — using kumo (cloud) only for multi-session framework
- WAE: Waddah Attar — MACD × sensitivity vs BB width
- Fisher Transform: John Ehlers — Gaussian probability of price extremes
- Choppiness Index: E.W. Dreiss — trend vs chop regime identifier
- LaguerreRSI: John Ehlers — adaptive 4-tap filter, reduces lag vs standard RSI

See [[09_research_notes/Deep Research Highlights.md]] for more.
