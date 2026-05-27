# Param Set — v4.3 Active

#active #parameter-set

**Status: BELIEVED ACTIVE (as of 2026-03-25)**
**Note: No paper trading confirmation yet — derived from code inspection only**

---

## WHAT CHANGED IN v4.3 (relative to v4.2)

Added 7 new technical indicators to `data/indicators.py` and scoring blocks to `job_runner.py`:

| Indicator | Signal | Pts |
|-----------|--------|-----|
| SuperTrend (ATR 10, mult 3.0) | Bullish direction | +12 |
| WaveTrend Oscillator (LazyBear) | WT1 crosses WT2 from below −53 | +12 |
| Ichimoku Cloud (kumo only) | Close > cloud top | +8 |
| Ehlers Fisher Transform | Fisher crosses up from negative | +8 |
| Laguerre RSI (γ=0.5) | LRSI < 0.15 (deep oversold) | +8 |
| Waddah Attar Explosion | Bullish + exploding | +10 |
| WAE bullish only (no explosion) | | +5 |
| Choppiness Index < 38.2 | Trending regime | +5 |
| Laguerre RSI < 0.25 | Mild oversold | +4 |

Max additional conviction from Tier 2b: **72 pts**

---

## FULL CONVICTION SCORING MAP

### Tier 1 — Legacy
| Signal | Trigger | Pts |
|--------|---------|-----|
| MACD 3-variant consensus | All 3 variants agree | 25 |
| Williams %R | ≤ −80 | 20 |
| Momentum + volume | Both fire | 15 |

### Tier 2a — Advanced Math
| Signal | Trigger | Pts |
|--------|---------|-----|
| BB-Keltner squeeze | Fired ≥ 20 bars, direction > 0 | 20 |
| RV ratio | ≥ 1.3 vol expansion | 15 |
| Kalman deviation | ≤ −1.0% below estimate | 10 |
| AVWAP deviation | ≤ −0.5% below AVWAP | 10 |
| OU half-life | In [3, 60] minutes | 5 |
| Kyle lambda | ≤ 30th percentile | 5 |

### Tier 2b — New Indicators (v4.3)
| Signal | Trigger | Pts |
|--------|---------|-----|
| SuperTrend | Bullish | 12 |
| WaveTrend | Cross from oversold (< −53) | 12 |
| Ichimoku | Price above cloud | 8 |
| Fisher Transform | Cross up from negative | 8 |
| Laguerre RSI | < 0.15 | 8 |
| WAE | Bullish + exploding | 10 |
| WAE | Bullish only | 5 |
| CHOP | < 38.2 (trending) | 5 |
| Laguerre RSI | < 0.25 | 4 |

### Tier 3 — External
| Signal | Trigger | Pts |
|--------|---------|-----|
| TradingView webhook | Buy signal within 5 min | 20 |

**Total theoretical max: ~175 pts**
**Gate thresholds: 30 pts (normal) / 70 pts (dead zone 2-5am ET)**

---

## RISK PARAMETERS

| Param | Value |
|-------|-------|
| MAX_RISK_PER_TRADE_PCT | 1% |
| MAX_DAILY_LOSS_PCT | 4% |
| MAX_POSITIONS_CRYPTO | 5 |
| MAX_POSITIONS_EQUITY | 3 |
| CRYPTO_STOP_LOSS_PCT | 1.5% |
| CRYPTO_TAKE_PROFIT_PCT | 4.5% |
| EQUITY_STOP_LOSS_PCT | 2.5% |
| EQUITY_TAKE_PROFIT_PCT | 7.5% |
| ATR_STOP_MULTIPLIER | 2.0 |
| ATR_TARGET_MULTIPLIER | 4.0 |
| MAX_STRATEGY_LOSS_STREAK | 4 |
| FULL_DEBATE_MIN_AGREEMENT | 0.40 (2 of 5) |
| MAX_DEPLOYED_PCT | 90% |
| MAX_DAILY_FEE_DRAG_PCT | 10% |

---

## AI PARAMETERS

| Param | Value |
|-------|-------|
| GEMINI_MODEL | claude-sonnet-4-6 |
| DEBATE_MAX_TOKENS | 700 |
| EXIT_REVIEW_MAX_TOKENS | 1500 |
| MODERATOR_MAX_TOKENS | 900 |
| QUICK_DEBATE_AGENTS | microstructure, fee_discipline, flow_tape |
| FULL_DEBATE_AGENTS | + regime_volatility, manipulation_risk |

---

## SIGNAL FILTERS (HARDCODED VETOS)

- ATR fee-floor guard: ATR/price < 0.4% → skip debate entirely
- OBI/TFI microstructure veto: OBI < −0.35 AND TFI < −0.20 → skip
- Dead-zone (2:00–5:00 AM ET): conviction floor = 70
- Symbol cooldown: 20 min after losing exit
- Stagnant exit: 45 min with < 15% target progress
- Min hold: 3 min before SELL fires
- Max chase: price moved > 3% since signal → skip

---

## WHAT THIS REPLACED

**v4.2** → Added TradingView webhook + Tier 3 conviction scoring
**v4.1** → OU z-score entry refinement + CI/CD setup
**v4.0** → De-risk overhaul: all sizes cut 50%, RSI removed as entry, Hurst removed

---

## EXPECTED ADVANTAGES

- Tier 2b adds confluence signals from different mathematical families (trend, momentum, oscillator)
- Multiple oversold detectors (LaguerreRSI, WaveTrend, Fisher) provide redundancy
- SuperTrend adds directional trend context that pure MACD misses
- WAE adds volatility expansion confirmation similar to squeeze but MACD-derived

---

## RISKS / FAILURE MODES

- Signal stacking: many new signals may fire simultaneously in strong trends,
  creating false confidence in marginal setups
- Over-optimized pre-filter: if conviction threshold is too easy to hit,
  the agent debate becomes the only real filter
- WAE and squeeze overlap: both measure vol expansion via BB — may double-count same regime
- Fisher Transform on 1-min candles: designed for daily bars, may be noisy intraday

---

## EVIDENCE

None yet. Paper trading not started as of 2026-03-25.

---

## NEXT: Track these after 30 trades

- Which Tier 2b signals correlated with winning trades?
- Which signals fired on losers but not winners?
- Is conviction score > 60 a better predictor of outcomes than > 30?
