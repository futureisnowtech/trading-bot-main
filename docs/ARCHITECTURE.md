# HISTORICAL REFERENCE — v10.0 Architecture Design Doc

> **This document captures the v10.0 initial design (2026-04-01).**
> The current live system is **v15.2** (2026-04-15). See CLAUDE.md for current truth.
>
> Key things that changed since this doc was written:
> - Live execution venue: was Binance USDM → now **Coinbase US CFTC nano perp futures** (`coinbase_broker.py`)
> - 47 ML features → **57 features** (11 groups)
> - 6-priority exit stack → **7-priority** (dead-money exit added v13.8)
> - 5-tab dashboard → **6 tabs** (FORECAST TRADING added v15.0; ARCHIVED FUTURES (MES) renamed)
> - ForecastEx lane added (v15.0): IBKR ForecastEx event contracts
> - Runtime truth layer added (v15.2): `system_runtime_state`, `lane_runtime_state`, incidents
> - MES/IBKR futures: DORMANT (`FUTURES_LANE_ACTIVE=false`)
> - Notifications: SQLite only (no Telegram — this was already the design)
>
> Do not use the exchange decision, risk limits, or feature counts in this doc as current values.

---

# ARCHITECTURE.md — v10 System Design
**Version:** v10.0 (build started 2026-04-01)
**Branch:** feature/v10-rebuild
**Owner:** Josh James | futureisnowtech@gmail.com

---

## Exchange Decision: Binance USDM Futures

**Decision:** Binance USDM Perpetual Futures via `python-binance` library.

**Rationale:**
- `python-binance` has mature futures support (`futures_*` methods, WebSocket streams, server-side SL/TP)
- `binance-connector` is lower-level and requires more boilerplate for futures
- Binance USDM has the deepest liquidity in crypto perps ($50B+ daily volume)
- Testnet available at `https://testnet.binancefuture.com` — all paper trading goes here
- Geo-block workaround: CoinGecko fallback for market scanning (already proven in v9.5)

**Margin mode:** ISOLATED on every position. Non-negotiable. One bad position cannot liquidate others.

**Pair format:** `BTCUSDT` (not `BTC-USDC`). All USDT-margined perpetuals.

**Paper trading:** `BINANCE_TESTNET=true` in `.env`. All paper trades go through testnet API.

---

## System Architecture: 15 Subsystems

```
┌─────────────────────────────────────────────────────────────────┐
│                    DATA LAYER (always running)                   │
│  realtime_feeds.py   historical_data.py   sentiment_data.py     │
│  cumulative_delta → indicators/cvd.py                           │
│  deribit_feed → sentiment_data.py                               │
│  onchain_feed → sentiment_data.py                               │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│                  INDICATOR ENGINE                                │
│  indicators/: cvd, orderflow, open_interest, funding_rate,      │
│  liquidation_levels, vwap_mtf, macd_advanced, rsi_advanced,     │
│  williams_r, orderbook, atr_regime, microstructure              │
└────────────────────────┬────────────────────────────────────────┘
                         │
            ┌────────────┴────────────┐
            │                         │
┌───────────▼──────────┐   ┌──────────▼──────────────────────────┐
│  DYNAMIC SCANNER      │   │  ML PIPELINE                        │
│  scanner.py           │   │  ml/feature_builder.py (47 features)│
│  pair_intelligence.py │   │  ml/models/ (XGBoost + LightGBM)    │
│  7-step filter        │   │  ml/walk_forward_trainer.py         │
│  Top 15 candidates    │   │  ml/calibration.py (Platt scaling)  │
└───────────┬──────────┘   │  ml/regime_classifier.py            │
            │               │  ml/online_learner.py               │
            └────────┬──────┘
                     │
┌────────────────────▼────────────────────────────────────────────┐
│                 SIGNAL COMPOSITION ENGINE                        │
│  signal_engine.py                                               │
│  Tower 1: Technical Score (0-100)                               │
│  Tower 2: ML Score (0-100, regime-adjusted)                     │
│  Composite: weighted blend (shifts 80/20 → 30/70 with data)     │
│  Entry thresholds: regime-adjusted (58-72)                      │
└────────────────────┬────────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────────┐
│               POSITION MANAGER                                   │
│  position_manager.py                                            │
│  Kelly sizing (1/3 → 1/2 path), ATR-based stops                │
│  6-Priority Exit Stack:                                         │
│    P1: Trailing stop (1x ATR activate, 1.5x ATR trail)         │
│    P2: Scale-out (2R→33%, 3.5R→33%, trail remainder)           │
│    P3: Thesis score (< entry × 0.45 → exit all)                │
│    P4: Hard stop (exchange-side stop-market, never widened)     │
│    P5: Risk forced exit (margin/drawdown/correlation)           │
│    P6: Kill switch (overrides all)                              │
└────────────────────┬────────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────────┐
│              EXECUTION LAYER                                     │
│  perps_engine.py — longs/shorts/leverage/ISOLATED margin        │
│  hedge_engine.py — delta-neutral (BTC 1x hedge, rebal 5min)     │
│  execution/binance_broker.py — base (already working)           │
└────────────────────┬────────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────────┐
│                  RISK ENGINE                                     │
│  risk_engine.py — VaR/CVaR, correlation matrix, margin util     │
│  kill_switch.py — balance<75% peak, API errors, latency         │
│  Drawdown circuit breakers: 5%/8%/12%/15%                      │
└────────────────────────────────────────────────────────────────┘

Parallel loops (background threads):
┌──────────────────────────────────────────────────────────────────┐
│  RBI LOOP                 LEARNING LOOP        NOTIFICATIONS     │
│  rbi/research_loop.py     learning_loop.py     notification_     │
│  rbi/backtest_loop.py     weekly_report.py     engine.py         │
│  rbi/incubation_mgr.py                                           │
└──────────────────────────────────────────────────────────────────┘

Dashboard:
┌──────────────────────────────────────────────────────────────────┐
│  dashboard/app.py — THE KING (Lakers gold/navy)                  │
│  SAIYAN MODE toggle — DBZ aesthetic                              │
│  Notification feed (right side, live, color-coded)              │
└──────────────────────────────────────────────────────────────────┘
```

---

## Data Flow: Trade Lifecycle

```
Scanner (every 5min)
  → 7-step filter (volume, momentum, liquidity, EV, correlation, regime, top 15)
  → pair_intelligence.py (pair-specific context)
  → indicator engine (all 12 indicators computed)
  → feature_builder.py (47 features extracted)
  → signal_engine.py (technical score + ML score → composite)
  → if composite >= threshold:
      → position_manager.py (size = Kelly × vol × ML × FG × correlation)
      → perps_engine.py (ISOLATED margin, set hard stop on exchange)
      → risk_engine.py (pre-check: VaR, margin, correlation)
      → notification_engine.py (TRADE_OPEN with WHY)
      → learning_loop.py (log 47 features at entry)

Every candle (open position):
  → signal_engine.py re-scores current state
  → position_manager.py checks 6-priority exit stack
  → risk_engine.py updates portfolio metrics
  → if any exit triggers:
      → perps_engine.py close (partial or full)
      → notification_engine.py (TRADE_CLOSE with WHY)
      → learning_loop.py (log outcome, update ML queue, Bayesian attrs)
      → if ML queue >= 50 new trades: trigger retrain (async)

Nightly (2am ET):
  → rbi/research_loop.py (575 combinations × 90d data)

Weekly (Monday 3am ET):
  → walk_forward_trainer.py (retrain XGBoost + LightGBM)
  → scripts/weekly_report.py (performance + signal analysis + recommendations)
```

---

## Signal Engine: Two-Tower Scoring

### Technical Score Components

**LONG (normalized 0-100 from raw -115 to +150):**

| Signal | Raw Points | Source |
|---|---|---|
| CVD bullish divergence | +25 | indicators/cvd.py |
| MACD all 3 variants aligned long | +20 | indicators/macd_advanced.py |
| RSI bullish divergence | +15 | indicators/rsi_advanced.py |
| Funding rate squeeze setup | +15 | indicators/funding_rate.py |
| VWAP reclaim on volume | +15 | indicators/vwap_mtf.py |
| OB imbalance bullish (L5) | +10 | indicators/orderbook.py |
| Williams %R oversold exit | +10 | indicators/williams_r.py |
| Liquidation cascade completed | +15 | indicators/liquidation_levels.py |
| Whale accumulation detected | +10 | indicators/microstructure.py |
| Options skew bullish (25d) | +10 | data/deribit_feed.py |
| Volume spike confirmation | +5 | indicators/orderflow.py |
| Price at VWAP +2σ | -20 | indicators/vwap_mtf.py |
| Funding rate extreme positive | -25 | indicators/funding_rate.py |
| OI falling, price rising | -15 | indicators/open_interest.py |
| RSI bearish divergence | -25 | indicators/rsi_advanced.py |
| OB imbalance bearish | -15 | indicators/orderbook.py |
| Whale distribution detected | -15 | indicators/microstructure.py |
| Cascade risk high | -20 | indicators/liquidation_levels.py |

SHORT: exact mirror (CVD bearish divergence +25, extreme positive funding +25, etc.)

### ML Score
```python
ml_score = calibrated_xgb_output * 100 * regime_multiplier

REGIME MULTIPLIERS:
  TRENDING_UP:    long × 1.15,  short × 0.85
  TRENDING_DOWN:  long × 0.85,  short × 1.15
  RANGING:        both × 0.90
  HIGH_VOL:       both × 0.80
  ACCUMULATION:   long × 1.10
```

### Composite
```python
# Weights shift as data accumulates:
if trade_days < 30:
    composite = technical * 0.80 + ml * 0.20
elif trade_days < 100:
    composite = technical * 0.50 + ml * 0.50
else:
    composite = technical * 0.30 + ml * 0.70

# Entry thresholds (regime-adjusted):
TRENDING:  entry if composite >= 62
RANGING:   entry if composite >= 68
HIGH_VOL:  entry if composite >= 72
LOW_VOL:   entry if composite >= 58
```

---

## ML Pipeline Architecture

### Feature Set: 47 Features

**Price (8):** returns_1c, returns_3c, returns_5c, returns_15c, price_vs_24h_high_pct, price_vs_24h_low_pct, price_vs_session_vwap_pct, price_vs_weekly_vwap_pct

**Volume (6):** volume_spike_5c, volume_spike_20c, buy_volume_ratio, dollar_volume_normalized, volume_trend_slope, volume_at_price_level_pct

**CVD (5):** cvd_value_normalized, cvd_slope_5c, cvd_slope_20c, cvd_divergence_strength, cvd_vs_price_correlation_20c

**Momentum (7):** rsi_14, rsi_slope_3c, macd_hist_3_15_3, macd_hist_slope, macd_acceleration, williams_r_14, williams_r_momentum

**VWAP (4):** session_vwap_distance_pct, weekly_vwap_distance_pct, vwap_band_position, vwap_reclaim_signal

**Orderbook (5):** ob_imbalance_l1, ob_imbalance_l5, ob_imbalance_l20, wall_above_distance_pct, wall_below_distance_pct

**Derivatives (6):** funding_rate_current, funding_rate_8h_change, carry_annual, oi_change_pct_4h, ls_ratio, ls_ratio_change_1h

**Liquidation (3):** cascade_risk_score, nearest_long_liq_distance_pct, nearest_short_liq_distance_pct

**Regime/Sentiment (5):** fg_current, fg_momentum_7d, atr_regime, volatility_ratio, options_skew_25delta

**Time (4):** hour_of_day_sin, hour_of_day_cos, day_of_week_sin, day_of_week_cos

**On-chain (4):** exchange_netflow_24h_normalized, whale_buy_count_4h, whale_sell_count_4h, stablecoin_exchange_inflow_24h_normalized

### Model Architecture
- **Primary:** XGBoost (60% ensemble weight), per-pair models (BTC/ETH/SOL/GENERIC × long/short = 8 models)
- **Secondary:** LightGBM (40% ensemble weight), same feature set
- **Hyperparameter tuning:** Optuna, optimize for Sharpe ratio on validation set
- **Walk-forward:** 60d train → 10d validate → 7d step (always forward)
- **Calibration:** Platt scaling, Brier score target < 0.20
- **Online learner:** Rolling perceptron, updates every trade, ±0.15 modulation on XGBoost output

---

## Position Sizing

```python
# Kelly fraction path
kelly_raw = (win_rate * avg_win - loss_rate * avg_loss) / avg_win
kelly_fraction = kelly_raw * 0.33   # start at 1/3 Kelly
# After 50 trades with positive edge: 0.40
# After 100 trades with Sharpe > 1.0: 0.50 (ceiling)

# Dollar risk per trade
dollar_risk = account_balance * 0.02  # 2% risk

# Units based on stop distance
position_units = dollar_risk / (atr_7 * stop_multiplier)
position_usd = position_units * current_price

# Adjustment chain
vol_adj = position_usd * vol_regime_multiplier
ml_adj = vol_adj * (ml_score / 60)
fg_adj = ml_adj * fg_size_multiplier
corr_adj = fg_adj * (1 - max_open_correlation)

# Hard caps
FINAL = min(corr_adj, account_balance * 0.30)  # 30% max single position
# Total deployed cap: 80% of account
# Minimum notional: $100

# Leverage schedule
leverage = 3  # default
if vol_regime == NORMAL and ml_score > 65: leverage = 4
if vol_regime == LOW and ml_score > 75: leverage = 5
if ml_score > 85 and cascade_risk < 20 and vol_regime == LOW and edge_score > 0.70:
    leverage = 10  # absolute ceiling
```

---

## Exit Priority Stack

| Priority | Type | Trigger | Action |
|---|---|---|---|
| 1 (lowest) | Trailing stop | After 1× ATR move in favor; trails at 1.5× ATR from peak | Close on breach |
| 2 | Scale-out | At 2R: close 33%, move stop to BE. At 3.5R: close 33%, trail at 1.5R | Partial close |
| 3 | Thesis score | `current_score < entry_score × 0.45` | Close ALL immediately |
| 4 | Hard stop | Stop-market placed at entry on exchange. Never moved wider. | Full close |
| 5 | Risk forced | Margin breach / drawdown circuit / correlation > 0.85 breach | Full close |
| 6 (highest) | Kill switch | Balance < $7,500 / 5 API errors in 10min / latency > 5s consecutive | Close ALL, halt all trading |

**No time-based exits.** Thesis score (Priority 3) handles stale positions.

---

## Risk Limits ($10,000 Architecture)

| Metric | Limit | Action |
|---|---|---|
| Single position | 30% ($3,000) | Hard cap |
| Total deployed | 80% ($8,000) | No new entries above this |
| Daily loss | 5% ($500) | Halt all trading |
| Kill switch | Balance < $7,500 (75% of $10K peak) | Emergency close all + halt |
| Drawdown 5% | — | New position sizes -25% |
| Drawdown 8% | — | New position sizes -50% |
| Drawdown 12% | — | Halt all new entries |
| Drawdown 15% | — | Close all + halt |
| Margin utilization 60% | — | No new positions |
| Margin utilization 75% | — | Reduce existing positions |
| Margin utilization 85% | — | Emergency size reduction |
| Correlation > 0.85 | — | Force size reduction on correlated pair |

---

## RBI Loop

**Research (nightly 2am ET):**
- Tests 575 signal combinations (15 singles + 105 pairs + 455 triples)
- 90 days of data, Fisher's exact test (p < 0.05 required)
- Promotion criteria: WR > 56%, PF > 1.4, Sharpe > 0.8, DD < 20%, trades > 30
- Output: `research/signal_rankings.json`, `research/backtest_queue.json`

**Backtest (continuous when queue non-empty):**
- Walk-forward: 63d train / 27d test / 10 windows
- Pass criteria: mean WR ≥ 54%, worst window WR ≥ 48%, worst DD ≤ 18%
- Output: Kelly fraction, optimal size, expected time to 2× account

**Incubation (live):**
- 25% position size, runs alongside production strategies
- 20 live trades required before evaluation
- Graduate: WR ≥ 50% AND PF ≥ 1.20 AND drawdown ≤ 1.5× backtest DD
- Recycle: WR < 50% OR PF < 1.20 → back to Research with failure notes
- Kill: drawdown > 2× backtest DD → archived permanently

**Production review:** Every 30 days. WR drop > 8% → demote to Incubation.

---

## Notification System

Replaces Telegram entirely. Dashboard-only.

**Categories:** TRADE_OPEN | TRADE_CLOSE | SIGNAL | RISK_ALERT | RBI_UPDATE | SYSTEM | ML_UPDATE | KILL_SWITCH

**Every notification includes WHY:**
- TRADE_OPEN: top 3 signals, ML score + top features, stop/target distances, expected profit, regime
- TRADE_CLOSE: P&L + %, hold duration, specific exit type, what changed (thesis exit), ATR distance (stop exit)
- SIGNAL REJECTED: shown too — what scored, what blocked it

**Storage:** SQLite `notifications` table (same `trades.db`), keep last 500.

---

## Scanner: 7-Step Filter

All Binance USDT perps, every 5 minutes, 24/7 (no time restrictions):

1. **Universe:** All USDT perp pairs, filter 24h volume > $50M
2. **Momentum:** `vol_spike ≥ 1.2` AND `price_move_4h ≥ 0.8%` AND `adx_15m ≥ 22`
3. **Liquidity:** Orderbook top 5 each side > $50K, spread < 0.1%
4. **Expected value:** `expected_profit ≥ $1.50`
5. **Correlation:** Reduce size if correlation > 0.85 with open position
6. **Regime:** Match signal type to current regime
7. **Sort & limit:** Sort by vol_spike descending, take top 15

---

## File Map: What Goes Where

```
Phase 1:  docs/ARCHITECTURE.md (this file)
          scripts/migrate_v10.py
          [remove: telegram, agent debate, session analyst files]

Phase 2:  data/realtime_feeds.py
          data/historical_data.py
          data/sentiment_data.py

Phase 3:  indicators/cvd.py, orderflow.py, open_interest.py,
          funding_rate.py, liquidation_levels.py, vwap_mtf.py,
          macd_advanced.py, rsi_advanced.py, williams_r.py,
          orderbook.py, atr_regime.py, microstructure.py

Phase 4:  scanner.py
          pair_intelligence.py

Phase 5:  ml/feature_builder.py

Phase 6:  signal_engine.py

Phase 7:  position_manager.py

Phase 8:  perps_engine.py
          hedge_engine.py
          kill_switch.py

Phase 9:  risk_engine.py

Phase 10: ml/models/
          ml/walk_forward_trainer.py
          ml/calibration.py
          ml/regime_classifier.py
          ml/online_learner.py

Phase 11: rbi/research_loop.py
          rbi/backtest_loop.py
          rbi/incubation_manager.py

Phase 12: learning_loop.py
          scripts/weekly_report.py

Phase 13: notifications/notification_engine.py
          dashboard/app.py (overhaul)

Phase 14: docs/BACKTEST_RESULTS.md

Phase 15: Paper trading — 14 days minimum
```

---

## Go-Live Criteria (v10)

- [ ] ML model Brier score < 0.22
- [ ] At least 1 strategy graduated from RBI incubation to production
- [ ] Zero kill switch triggers during paper period
- [ ] Cost per profitable trade < 25% of average win
- [ ] 14+ days successful paper trading
- [ ] Portfolio backtest: Sharpe > 0.8, profitable after all costs
- [ ] Account funded to $10,000

---

*Last updated: 2026-04-01 | Phase 1 in progress*
