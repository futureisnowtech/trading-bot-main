# Strategy Catalog

This document indexes all signals, alpha models, gates, sizing formulas, and exit systems implemented in the codebase.

---

## 1. Unified Strategy: `weather_ensemble` (Active)

*   **File Path**: [`forecast/strategy_engine.py`](file:///Users/joshmacbookair2020/projects/algo_trading_final/forecast/strategy_engine.py)
*   **Plain-English Explanation**: Instead of running isolated statistical models, the bot uses a consolidated ensemble predictor. It dynamically merges numerical weather predictions (NWP) from GFS and ECMWF models, scales uncertainty ($\sigma$) using GraphCast AI predictions, checks safety gates, and calculates Kelly contract quantities.
*   **Active Status**: **ACTIVE** (This is the only strategy that evaluates, submits, and exits live trades).

### 1.1 Blending Math & Fair Probability (`q_hat`)
*   **Ensemble Probability Blending**:
    $$p_{\text{ensemble}} = (p_{\text{GFS}} \cdot w_{\text{GFS}}) + (p_{\text{ECMWF}} \cdot w_{\text{ECMWF}})$$
    *   *Weights*: Hardcoded to $w_{\text{GFS}} = 0.60$ and $w_{\text{ECMWF}} = 0.40$ (labeled `STATIC_DISABLED`).
*   **Convergence Scaling**: If GFS and ECMWF both agree (both $>0.75$ or both $<0.25$), a `convergence_multiplier = 1.5` is applied to size.
*   **Divergence Haircut**: If GFS and ECMWF diverge ($|p_{\text{GFS}} - p_{\text{ECMWF}}| > 0.20$), the final blended probability is shrunk towards $0.50$ (cooldown) and a size haircut is applied:
    $$\text{confidence\_scale} = 1.0 - \min(0.45, (\text{divergence\_gap} - 0.20) \cdot 0.90)$$
    $$p_{\text{ensemble}} = 0.5 + ((p_{\text{ensemble}} - 0.5) \cdot \text{confidence\_scale})$$
*   **Catastrophic Divergence Veto**: If GFS and ECMWF differ by more than $70\%$ ($>0.70$), the contract is vetoed immediately.

### 1.2 GraphCast AI / Sigma Scaler
*   **Bayesian Confirmer**: GraphCast predictions are loaded from `aigefs` records. Rather than altering $q_{\text{hat}}$, the AI prediction scales volatility ($\sigma$) which controls Kelly sizing.
*   **Sigma Volatility Adjustment**:
    *   *AI Disagreement*: If AI deterministic temp deviates from the ensemble mean by $>1.5^{\circ}\text{F}$ (or $>0.1\text{ in}$ for precip), `ai_multiplier = 1.3` (inflates uncertainty, shrinking trade sizes).
    *   *AI Consensus*: If deviation is $<0.5^{\circ}\text{F}$, `ai_multiplier = 0.8` (tightens conviction, expanding trade sizes).

---

## 2. Multi-Factor SRE Economics Gates (Entry Blockers)

Every candidate must pass 11 mandatory gate checks in `_economics_gate()` and `_weather_market_gate()` before an order can be structured:

1.  **Capital Partition Gate**: Deployed capital must be $< 90\%$ (`KALSHI_MAX_DEPLOYED_PCT = 0.90`).
2.  **Horizon Pullback Gate**: Rejects trades if the contract is less than $1$ hour or more than $120$ hours (5 days) from settlement (`MIN_HOURS_TO_RES = 1.0`, `MAX_HOURS_TO_RES = 120.0`).
3.  **Concurrency Limit**: Total open positions must be $< 50$.
4.  **Overround Gate (House Edge)**: Rejects trades if book overround $\Omega > 0.15$ (protects against high exchange fees/spreads).
5.  **Spread Dollar Gate**: Maximum spread must be $\le \$0.12$ per contract.
6.  **Spread-to-Price Ratio Gate**: Spread divided by average price must be $\le 35\%$.
7.  **Entropy Gates (Resolution Certainty)**: Rejects trades if implied entropy $H(p) < 0.05$ (contract already resolved) or $H(p) > 0.67$ (contract is in high-entropy chaotic zone). Refuses to trade in highly uncertain states.
8.  **Volatility Gate**: Rejects if price volatility $\sigma_t > 0.80$.
9.  **Parity Gap Gate**: Rejects if $|p_{\text{implied YES}} + p_{\text{implied NO}} - 1.0| > 0.05$.
10. **Positive Net EV Gate**: Chosen side must have post-fee expected value $EV \ge 0.05$.
11. **Longshot Bias Gate**: Refuses to buy YES if GFS/ECMWF blended probability $q_{\text{hat}} < 0.10$.

---

## 3. Position Sizing: Continuous Kelly

*   **Kelly Fraction**: Computes optimal kelly sizing based on post-fee edge and odds, capped at `0.10` of bankroll.
*   **Duplicate Exposure Haircut**: Kelly fraction is halved (`SAME_EVENT_PENALTY = 0.50`) if there is another open position within the same city event family.
*   **Risk Ceilings**:
    *   *Sovereign SRE Clamp*: Hard USD risk limit of `$40.00` per position (`KALSHI_MAX_USD_PER_POSITION = 40.0`).
    *   *Quantity Clamp*: Hard contract quantity limit of `2500` contracts (`KALSHI_MAX_QTY_PER_POSITION = 2500`).
    *   *Liquidity Depth Clamp*: Order size is capped at top-of-book size (`ask_size`) to prevent crossing the spread.

---

## 4. Exit & Salvage Systems (Position Monitor)

The bot executes four explicit exit systems inside `run_strategy_cycle()`:

*   **Sovereign Salvage (Dead-Trade Purge)**: If the updated weather model probability of our bet falls below $15\%$ ($<0.15$), the position is closed immediately at market prices to salvage remaining capital.
*   **Take-Profit (70% Gain Lock-in)**: Exits the position if the market price appreciates by $70\%$ of the maximum possible gain:
    $$\text{Target Price} \ge \text{Entry Price} + (1.0 - \text{Entry Price}) \cdot 0.70$$
*   **Anti-Double-Down & Bracket Guards**: Rejects buying more contracts on the same strike, or hedging opposite contract sides within the same city event.
*   **Concurrency Swaps**: If the bot is at the 50-position limit, it evaluates new candidates. If a candidate has $EV > 0.15$ and the worst open position has updated model conviction $< 0.65$, it flattens the worst position to redeploy capital.

---

## 5. Legacy & Archived Strategy Concepts

*   **Continuation, Mean Reversion, Late Repricing**: Mentioned in docstrings and typing fields of `forecast/strategy_engine.py` as legacy theoretical models. They do not exist as active python execution paths in the repository.
