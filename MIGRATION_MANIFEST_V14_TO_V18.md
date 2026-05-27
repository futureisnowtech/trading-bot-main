# MIGRATION MANIFEST: V14 (Archive) to V18 (Current Spot)
## The "Migration Bible" for Autonomous Spot Scalping (V2 - Smart Execution Edition)

**Purpose**: This document serves as the definitive technical reference for porting high-leverage mathematical logic and governance frameworks from the v14 Perp-First archive to the v18 Spot-Scalp repository.

**Execution Strategy**: **"Smart Bolt-on"**. We utilize **Soft-Vetoes**, **Bayesian Dampening**, and **Parallel Shadow-State Tracking** to extract alpha without the risk of over-filtering or execution latency.

---

## Section 1: Advanced Microstructure & Volatility Math

### 1.1 Kyle's Lambda ($\lambda$) - Market Impact (Adaptive Estimation)
**Logic**: Measures the price impact per unit of volume. High $\lambda$ indicates an illiquid market where even small trades move the price significantly.
**Math**: OLS slope of $\Delta P$ (returns) against $S \cdot V$ (signed volume).
$$ r_t = \lambda \cdot (Sign(r_t) \cdot V_t) + \epsilon $$
**Smart Implementation**:
- **Technique**: Use a **1D Kalman Filter** to track $\lambda$ as a time-varying state rather than a static OLS window.
- **Dampening**: If $\lambda$ spikes $> 2\sigma$ above its 24-hour mean, we classify the market as "Fragile."
- **Reasoning**: Static windows lag. A state-space estimation of $\lambda$ detects "liquidity holes" before they result in massive slippage.

### 1.2 OBI / TFI "Soft-Veto" (Position Sizing Modifier)
**Logic**: Protects against "Quote Stuffing" where the order book looks bullish but actual market orders (Tape) are bearish.
**Math**:
- **Order Book Imbalance (OBI)**: $(BidQty - AskQty) / (BidQty + AskQty)$
- **Trade Flow Imbalance (TFI)**: $(BuyVol - SellVol) / TotalVol$
**Smart Scaling (Not Blocking)**:
- **Condition**: If $OBI > 0.20$ but $TFI < -0.10$ (Bullish Quotes / Bearish Tape).
- **Action**: **Reduce Position Size by 50%**.
- **Hard Veto**: Only skip entirely if $OBI < -0.35$ AND $TFI < -0.20$ (Extreme Selling Aggression).
- **Reasoning**: Binary vetoes cause "Signal Starvation." Position scaling allows us to collect data on "murky" trades while protecting capital.

### 1.3 ATR Fee Floor Guard (The Absolute Gate)
**Logic**: A coin must be volatile enough to "outrun" the high fees on Coinbase (1.2% round-trip).
**Formula**: Skip if $\frac{ATR}{Price} < 0.004$ (0.4%).
**Reasoning**: Fee math is deterministic, not probabilistic. If the coin doesn't move enough to cover the tax, the expected value (EV) is negative regardless of the signal quality. This remains a **Hard Veto**.

---

## Section 2: Adaptive Filtering & State-Space Models

### 2.1 1D Kalman Filter (Shadow State Tracking)
**Logic**: Estimates the "latent" fair price by balancing the predicted state with noisy observations.
**Math**: Recursive Bayesian update.
**Shadow Implementation**:
- **Technique**: Run the Kalman recursion in an **Async Shadow Process**.
- **Latency Optimization**: The Signal Engine reads the "Last Known Good" state from the previous bar (Latency = 0).
- **Threshold**: `kalman_dev <= -1.0%` (Buy when price is 1% below adaptive fair value).
- **Reasoning**: Calculating a 100-bar recursion at the moment of entry introduces "Systemic Jitter." Shadow tracking provides the math without the lag.

### 2.2 Ornstein-Uhlenbeck (OU) & ADF Pre-Flight
**Logic**: Measures how fast price reverts to its mean.
**Smart Gate**:
- **Pre-Flight**: Perform an **Augmented Dickey-Fuller (ADF)** test first.
- **Rule**: If $p$-value $> 0.05$ (Series is Trending), **Disable Mean-Reversion Setups**.
- **Reasoning**: OU math assumes stationarity. If the market is trending, mean-reversion signals are "Falling Knives." We use the ADF test as a safety fuse.

---

## Section 3: ML Confidence Calibration

### 3.1 Beta Calibration (Non-Linear Probability Mapping)
**Logic**: Converts raw model scores (0-100) into true statistical probabilities.
**Math**: Fits a 3-parameter beta distribution to the model outputs.
$$ P(y=1 | f) = \frac{1}{1 + \exp(a \cdot \ln(f) + b \cdot \ln(1-f) + c)} $$
**Implementation**:
- **Phase 1**: Use Platt Scaling (Logistic) for $N < 100$ trades.
- **Phase 2**: Transition to Beta Calibration for $N \ge 100$ trades.
- **Reasoning**: Beta calibration handles "skewed" distributions (where the model is over-confident at the extremes) better than Platt Scaling.

---

## Section 4: Bayesian Online Learner

### 4.1 "Surprise-Only" Updates (Bayesian Dampening)
**Logic**: Adapts to "Today's Market" without overfitting to random noise.
**Math**: Stochastic Gradient Descent (SGD) on model weights.
**Smart Update Rule**:
- **Condition**: Only update the learner if the outcome is a **"Statistically Significant Surprise."**
- **Definition**: $|Outcome - PredictedProb| > 0.4$.
- **Reasoning**: If the model predicts a 60% win and it loses, the model was "Correct but Unlucky." Updating on every trade causes the model to "Chase its Tail." We only learn from failures of prediction, not failures of luck.

---

## Section 5: Tactical Indicators (Tier 2b Suite)

### 5.1 WAE + Squeeze Confluence
**Logic**: Momentum × Volatility.
**Operational Synergy**:
- **Default**: Enter only if $WAE_{Exploding}$ AND $Squeeze_{Firing}$.
- **Recovery Mode**: If we have had $> 3$ "Missed Winners" (logged by scanner), loosen to $WAE_{Exploding}$ only if $ADX > 25$.
- **Reasoning**: Squeeze catch trends; WAE filters noise. Combining them maximizes the Sharpe Ratio of the entry.

---

## Section 6: RBIPMS Governance & Recovery

### 6.1 The Strategy Ladder (Automated Probation)
**Logic**: Enforces discipline in scaling.
**Smart Recovery Clause**:
- **Demotion**: If Rolling 14-day Win Rate $< 20\%$.
- **Probation**: The strategy continues to trade in **Shadow Mode** (logging only).
- **Re-Entry**: If the "Shadow Win Rate" recovers to $\ge 40\%$ over the next 10 candidates, the strategy is automatically fast-tracked back to **Incubation**.
- **Reasoning**: A winning strategy can have a "Bad Week" due to regime mismatch. Automated probation ensures we don't permanently discard alpha due to temporary market noise.

---

## Section 7: Integration Blueprint (The "Smart Bolt-On")

### Step 1: The "Soft" Veto Engine
Modify `runtime/spot_strategy.py` to return a `sizing_multiplier` instead of a boolean block.
```python
def calculate_execution_profile(symbol, data):
    multiplier = 1.0
    # Hard Veto (Block)
    if data['atr_floor_failed']: return 0.0, "FEE_FLOOR"
    
    # Soft Veto (Scaling)
    if data['kyle_lambda_spike']: multiplier *= 0.7
    if data['obi_tfi_divergence']: multiplier *= 0.5
    
    return multiplier, "ACTIVE"
```

### Step 2: Surprise-Aware Online Learner
Update the ML feedback loop in `learning/online_learner.py`.
```python
def record_trade_outcome(features, outcome, predicted_prob):
    # Bayesian Dampening Check
    surprise = abs(outcome - predicted_prob)
    if surprise > 0.4:
        learner.partial_fit(features, outcome)
        logger.info(f"Learner updated: Surprise={surprise:.2f}")
    else:
        logger.info("Learner skipped: Outcome within expectation range.")
```

### Step 3: Shadow Kalman Update
Implement in `data/edge_monitor.py`.
```python
async def update_shadow_state():
    while True:
        state = calculate_kalman_recursion(df)
        shared_memory.set('kalman_state', state)
        await asyncio.sleep(60) # Sync with 1-min bars
```

---
## Pre-Implementation Confirmation Protocol
**The Agent MUST confirm the following logic before executing a port:**
1.  **Confirmation**: I understand that `Kyle's Lambda` and `OBI/TFI` must NOT block trades unless they hit "Hard Veto" levels; otherwise, they scale position size.
2.  **Confirmation**: I understand the `Online Learner` must only update on "Surprises" to prevent overfitting.
3.  **Confirmation**: I understand the `Kalman Filter` must be read from shared state to avoid entry latency.
4.  **Confirmation**: I understand the `ADF Test` is the master safety fuse for mean-reversion setups.

---
**End of Manifest V2**
