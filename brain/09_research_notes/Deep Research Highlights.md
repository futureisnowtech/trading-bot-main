# Deep Research Highlights

#research

**Status as of: 2026-03-25**
**Source: `deep-research-report.md` in project root + CHANGELOG entries**

---

## FEE ECONOMICS (CRITICAL FOUNDATION)

The system's signal gate design is fundamentally driven by fee math.
At Coinbase 0.6% taker, round-trip = 1.2%. Key thresholds:

| Position Size | Round-Trip Cost | Min Gross Move to Win |
|--------------|----------------|----------------------|
| $500 | $6.00 | 1.2% |
| $250 | $3.00 | 1.2% |

**ATR Fee-Floor Guard** (added v3.9):
- If ATR/price < 0.4%, expected 4×ATR target < 1.6% → can't clear 2.4% minimum round-trip gross
- Skip debate entirely on these symbols — saves API tokens
- Formula: skip if `ATR / price < ATR_FEE_FLOOR_PCT (0.004)`

**Fee Discipline Agent (Krillin)**:
- p_min formula: `(1 + 0.012/L) / (R+1)` where L = leverage, R = risk:reward ratio
- Enforces: 2.4% minimum expected gross move per trade
- At 3:1 R:R with 1.5% stop: break-even win rate ≈ 27%

---

## OBI / TFI MICROSTRUCTURE THEORY

**Order Book Imbalance (OBI)**:
```
OBI = (bid_qty - ask_qty) / (bid_qty + ask_qty)
```
- OBI > 0 = more buying pressure than selling at top of book
- OBI ≥ 0.20 = actionable buy pressure threshold
- OBI ≥ 0.35 = strong buy pressure threshold
- OBI < −0.35 AND TFI < −0.20 = microstructure veto (spoofing risk)

**Trade Flow Imbalance (TFI)**:
```
TFI = (buy_volume - sell_volume) / total_volume
```
- Measures actual aggressor direction (market orders hitting asks vs bids)
- TFI ≥ 0.10 = aggressor flow favoring buys

**Why OBI/TFI together?**
- OBI without TFI can be spoofed (large bids placed then cancelled)
- TFI shows what actually traded, not what was quoted
- If OBI bullish but TFI neutral/bearish = probable spoofing = veto

---

## KALMAN FILTER ENTRY

The Kalman filter estimates the "fair value" of price using a state-space model.
- `kalman_estimate` = adaptive moving average that adjusts to recent volatility
- `kalman_dev` = (price - estimate) / estimate
- Entry trigger: kalman_dev ≤ −1.0% (price ≥1% below Kalman fair value)
- This is a mean-reversion entry: we buy when price is below adaptive fair value

**Advantage over simple MA**: Kalman adjusts gain based on observation noise.
In volatile markets it reacts faster. In quiet markets it smooths more.

---

## ORNSTEIN-UHLENBECK PROCESS

The OU model treats price as mean-reverting: `dX = θ(μ - X)dt + σdW`
- `θ` = speed of mean reversion
- `half-life = ln(2)/θ` — how long it takes price to revert halfway to mean
- Entry window: half-life in [3, 60] minutes
  - < 3 min = noise, not mean reversion
  - > 60 min = too slow for 1-min bars (won't complete during hold period)

---

## KYLE LAMBDA (MARKET IMPACT / LIQUIDITY)

Kyle (1985) price impact coefficient:
```
λ = ΔP / ΔV
```
- How much price moves per unit of volume traded
- High λ = illiquid market (large impact from small orders)
- Low λ = liquid market (small impact, better fills)
- Entry: Kyle λ ≤ 30th percentile → liquid enough for clean entry/exit

---

## HURST EXPONENT (RETIRED)

Previously used as regime classifier:
- H < 0.5 = mean-reverting
- H = 0.5 = random walk
- H > 0.5 = trending

**Removed in v4.0** because:
- On 1-min bars, Hurst requires min_periods=96 (1.6 hours of data)
- Noisy and slow to update for intraday signals
- Better alternatives: CHOP index for regime, AVWAP/Kalman for mean-reversion entry

---

## AR(1) AUTOCORRELATION

Measures first-order autocorrelation in returns: `r_t = a * r_{t-1} + ε`
- `a < 0` = mean-reverting (negative autocorrelation)
- `a > 0` = momentum/trending (positive autocorrelation)
- `a ≈ 0` = random walk
Used as confidence boost for mean-reversion setups when a < 0.

---

## SUPERTREND DESIGN NOTES

SuperTrend is an ATR-based trailing band:
- `upper_band = (H + L)/2 + multiplier × ATR`
- `lower_band = (H + L)/2 − multiplier × ATR`
- Direction flips when close crosses a band
- Parameters: ATR period 10, multiplier 3.0

**Key property**: Direction is non-trivial to vectorise — final band values depend on prior direction.
This is why `_supertrend_manual()` exists in indicators.py as a fallback.

**On 1-min bars**: SuperTrend at (10, 3.0) will flip direction relatively frequently.
Worth monitoring whether it's too noisy intraday or provides useful trend context.

---

## WADDAH ATTAR EXPLOSION (WAE) DESIGN

```
MACD_fast = EMA(close, 20) - EMA(close, 40)
MACD_slow = MACD_fast[1]
Explosion_line = BB_upper - BB_lower  # BB(20, 2.0)
Trend_force = (MACD_fast - MACD_slow) × sensitivity  # sensitivity = 150
Dead_zone = ATR(20) × 0.5

WAE bullish = Trend_force > 0 AND Trend_force > Explosion_line
WAE exploding = Trend_force > Explosion_line (force exceeds band width)
```

**Conceptually**: WAE measures whether momentum is stronger than current volatility band width.
A "true" breakout has both direction (MACD) and force exceeding the BB range.

---

## WAVETREND OSCILLATOR (LazyBear version)

```
HLC3 = (H + L + C) / 3
ESA = EMA(HLC3, 10)
D = EMA(|HLC3 - ESA|, 10)
CI = (HLC3 - ESA) / (0.015 × D)
WT1 = EMA(CI, 21)
WT2 = SMA(WT1, 4)

Signal: WT1 crosses above WT2 from below −53 (deep oversold zone)
```

**Why −53?** At −53, price has been deeply compressed relative to recent EMA distance.
Cross-up signals the oversold exhaustion.

---

## LAGUERRE RSI DESIGN (John Ehlers)

4-element Laguerre filter with damping γ:
```
L0 = (1 - γ) × close + γ × L0[1]
L1 = -γ × L0 + L0[1] + γ × L1[1]
L2 = -γ × L1 + L1[1] + γ × L2[1]
L3 = -γ × L2 + L2[1] + γ × L3[1]

cu = Σ max(Li - Li+1, 0) for i in 0..2
cd = Σ max(Li+1 - Li, 0) for i in 0..2
LRSI = cu / (cu + cd)
```

**Advantage over standard RSI**: Much less lag. γ=0.5 provides good balance.
LRSI < 0.15 = deeply oversold (equivalent to RSI ~20 on 14-period).
LRSI > 0.85 = deeply overbought.
