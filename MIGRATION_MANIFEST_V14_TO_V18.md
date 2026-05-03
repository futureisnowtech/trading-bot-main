# MIGRATION MANIFEST: V14 (Archive) to V18 (Current Spot)
## The "Migration Bible" for Autonomous Spot Scalping

**Purpose**: This document serves as the definitive technical reference for porting high-leverage mathematical logic and governance frameworks from the v14 Perp-First archive to the v18 Spot-Scalp repository. 

**Execution Strategy**: "Bolt-on" integration. Enhance the existing pre-trade funnel and signal scoring without modifying the core broker or order execution logic.

---

## Section 1: Advanced Microstructure & Volatility Math

### 1.1 Kyle's Lambda ($\lambda$) - Market Impact
**Logic**: Measures the price impact per unit of volume. High $\lambda$ indicates an illiquid market where even small trades move the price significantly.
**Math**: OLS slope of $\Delta P$ (returns) against $S \cdot V$ (signed volume).
$$ r_t = \lambda \cdot (Sign(r_t) \cdot V_t) + \epsilon $$
**Implementation**:
```python
def _kyle_lambda_rolling(close: pd.Series, volume: pd.Series, window: int = 60) -> pd.Series:
    direction = np.sign(close.diff())
    signed_vol = direction * volume
    delta_p = close.diff() / close.shift(1)
    # ... OLS slope calculation ...
    # Filter: R² >= 0.05 to ensure the slope isn't just noise.
```

### 1.2 OBI / TFI Veto (Spoof Protection)
**Logic**: Protects against "Quote Stuffing" where the order book looks bullish but actual market orders are bearish.
**Order Book Imbalance (OBI)**:
$$ OBI = \frac{BidQty - AskQty}{BidQty + AskQty} $$
**Trade Flow Imbalance (TFI)**:
$$ TFI = \frac{BuyVol - SellVol}{TotalVol} $$
**Veto Trigger**: Veto "Buy" signal if $OBI > 0.20$ (Bullish Quotes) but $TFI < -0.10$ (Bearish Aggressors).
**Hard Veto**: `OBI < -0.35 AND TFI < -0.20` (Severe selling pressure).

### 1.3 ATR Fee Floor Guard
**Logic**: A coin must be volatile enough to "outrun" the high fees on Coinbase (1.2% round-trip).
**Formula**: 
$$ \text{Skip if } \frac{ATR}{Price} < 0.004 \text{ (0.4\%)} $$
**Rationale**: If 1-min ATR is 0.4%, a 4x ATR target is 1.6%. After 1.2% fees, you are left with only 0.4% net profit for a "perfect" trade.

---

## Section 2: Adaptive Filtering & State-Space Models

### 2.1 1D Kalman Filter (Adaptive Fair Value)
**Logic**: Estimates the "latent" fair price by balancing the predicted state with noisy observations. 
**Math**:
1. **Predict**: 
   $\hat{x}_{t|t-1} = \hat{x}_{t-1|t-1}$
   $P_{t|t-1} = P_{t-1|t-1} + Q$ (Process Noise)
2. **Update**:
   $K_t = \frac{P_{t|t-1}}{P_{t|t-1} + R}$ (Kalman Gain)
   $\hat{x}_{t|t} = \hat{x}_{t|t-1} + K_t(z_t - \hat{x}_{t|t-1})$
   $P_{t|t} = (1 - K_t)P_{t|t-1}$
**Threshold**: `kalman_dev <= -1.0%` (Price is 1% below adaptive fair value).

### 2.2 Ornstein-Uhlenbeck (OU) Half-Life
**Logic**: Measures how fast a price reverts to its mean.
**Math**: Based on the AR(1) process: $x_t = a + b \cdot x_{t-1} + \epsilon$.
$$ \kappa = -\ln(b) $$
$$ t_{1/2} = \frac{\ln(2)}{\kappa} $$
**Constraint**: Entry allowed only if $t_{1/2} \in [3, 60]$ minutes.
- $< 3 \text{ min}$: Price is just noise.
- $> 60 \text{ min}$: Mean reversion is too slow for 1-min scalp windows.

---

## Section 3: ML Confidence Calibration

### 3.1 Platt Scaling (Logistic Calibration)
**Logic**: Converts raw model scores (0-100) into true statistical probabilities.
**Math**: Fits a logistic regression on the log-odds of the model output.
$$ P(y=1 | f) = \frac{1}{1 + \exp(A \cdot f + B)} $$
where $f$ is the raw model score.
**Metric**: **Brier Score** (Mean Squared Error of probabilities).
$$ BS = \frac{1}{N} \sum_{t=1}^{N} (f_t - o_t)^2 $$
- $BS < 0.20$: Calibrated.
- $BS > 0.22$: Recalibrate trigger.

---

## Section 4: Real-time Bayesian Adaptation

### 4.1 Online Learner (Rolling Perceptron)
**Logic**: Adapts to "Today's Market" between major weekly XGBoost retrains.
**Math**: Stochastic Gradient Descent (SGD) on a 57-feature vector.
$$ w_{t+1} = w_t - \eta \nabla L(w_t) $$
**Constraint**: Adjustment is bounded to $\pm 15\%$. If the bot loses 3 trades in a row on "Momentum," the Online Learner will suppress the "Momentum" score by 15 points immediately.

---

## Section 5: Tactical Indicators (Tier 2b Suite)

### 5.1 Waddah Attar Explosion (WAE)
**Logic**: Momentum exceeding the volatility baseline.
$$ \text{Trend} = (EMA(C, 20) - EMA(C, 40)) \cdot 150 $$
$$ \text{Explosion} = UpperBB(20) - LowerBB(20) $$
**Signal**: `Trend > Explosion` AND `Trend > 0`.

### 5.2 Laguerre RSI (Zero-Lag)
**Logic**: Uses a 4-tap filter to eliminate lag without adding noise.
$$ L_0 = (1-\gamma)C + \gamma L_{0, t-1} $$
$$ L_1 = -\gamma L_0 + L_{0, t-1} + \gamma L_{1, t-1} $$
... and so on.
**Threshold**: `LRSI < 0.15` is deeply oversold.

---

## Section 6: RBIPMS Governance Framework

### 6.1 The Strategy Ladder
1.  **Phase R (Research)**: Falsifiable hypothesis + manual review.
2.  **Phase B (Backtest)**: Walk-forward OOS validation.
3.  **Phase I (Incubation)**: 14 days, 50% position size, zero halts.
4.  **Phase P (Promote)**: 75% size for 30 days, then 100%.

### 6.2 Auto-Demotion Triggers
- Rolling 14-day Win Rate $< 20\%$.
- Rolling 14-day Profit Factor $< 0.8$.
- 3 consecutive system halts in 7 days.

---

## Section 7: Integration Blueprint (v18 Bolt-On)

### Step 1: Veto Logic in `runtime/spot_strategy.py`
Add a `VetoEngine` that sits inside `spot_quality_block_reason`.
```python
def check_vetoes(symbol, data):
    if data['atr'] / data['price'] < 0.004: return "FEE_FLOOR_VETO"
    if data['obi'] > 0.20 and data['tfi'] < -0.10: return "SPOOF_VETO"
    return None
```

### Step 2: Probability Scaling in `signal_engine.py`
Wrap the final composite score in the `apply_calibration` function.
```python
# Before
composite = tech_w * tech_score + ml_w * ml_score
# After
composite = calibrate_score(tech_w * tech_score + ml_w * ml_score)
```

### Step 3: Lifecycle Enforcement
Update `runtime/crypto_tradeability.py` to check the `spot_lifecycle_registry.json`.
- `RESEARCH`: Max size $5.
- `INCUBATE`: Max size 50%.
- `PROMOTED`: Max size 100%.

---
**End of Manifest**
