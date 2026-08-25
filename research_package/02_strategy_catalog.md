# Strategy Catalog

This document indexes all signals, alpha models, gates, sizing formulas, and exit systems implemented in the codebase.

---

## 1. Unified Strategy: `weather_physics` (Candidate; not yet deployed)

*   **File Path**: [`forecast/strategy_engine.py`](file:///Users/joshmacbookair2020/projects/algo_trading_final/forecast/strategy_engine.py)
*   **Plain-English Explanation**: The bot converts deterministic GFS and ECMWF forecasts plus explicit forecast-error sigma into contract probabilities, applies bounded daily-high/low physics before the CDF, uses actual NCEP AIGFS disagreement to scale uncertainty, optionally splices HRRR into near-term daily-HIGH pricing, checks safety gates, and calculates Kelly contract quantities.
*   **Active Status**: **ACTIVE** (This is the only strategy that evaluates, submits, and exits live trades).

### 1.1 Blending Math & Fair Probability (`q_hat`)
*   **Probability Blending**: Promoted RBI weights set the relative deterministic GFS/ECMWF allocation. Commercial ensembles and ICON are absent.
*   **Convergence Scaling**: If every available physical model agrees (all $>0.75$ or all $<0.25$), a `convergence_multiplier = 1.5` is applied to size.
*   **Divergence Haircut**: If the maximum pairwise physical-model probability gap exceeds $0.20$, the final blended probability is shrunk towards $0.50$ and a size haircut is applied:
    $$\text{confidence\_scale} = 1.0 - \min(0.45, (\text{divergence\_gap} - 0.20) \cdot 0.90)$$
    $$p_{\text{ensemble}} = 0.5 + ((p_{\text{ensemble}} - 0.5) \cdot \text{confidence\_scale})$$
*   **Catastrophic Divergence Veto**: If the maximum physical-model gap exceeds $70\%$ ($>0.70$), the contract is vetoed immediately.

### 1.2 NCEP AIGFS / Sigma Scaler
*   **Bayesian Confirmer**: The contract-aligned AIGFS value is loaded from the legacy internal `aigefs` key and compared with the combined physical members. It does not receive ensemble voting weight; it controls kernel width and Kelly uncertainty through a bounded lambda.
*   **Sigma Volatility Adjustment**: Close consensus yields lambda below 1.0; increasing standardized disagreement raises lambda toward 2.25, smoothing probabilities and shrinking size.

---

## 2. Canonical Entry Gates

The retired `_economics_gate()` helper and its decorative overround/entropy/parity controls were removed because production never called it. The active path uses `_strategy_weather_details()`, `_weather_market_gate()`, and portfolio checks:

1.  **Lane and city policy**: fresh entries must be enabled in `config/lane_policy.json` and not blocked by the versioned city firewall.
2.  **Freshness and identity**: paired quotes and contract-projected weather must be fresh; missing GFS or a commercial-ensemble payload fails closed.
3.  **Probability safety**: excessive forecast sigma or more than 70 points of GFS/ECMWF probability disagreement vetoes.
4.  **Post-fee EV**: the chosen side must clear the canonical `EV_THRESHOLD` after ceiled round-trip Kalshi fees.
5.  **Price/liquidity**: the hard entry floor, daily value bracket, dollar spread, spread ratio, and available top-of-book size are enforced.
6.  **Portfolio risk**: deployed capital, concurrency, same-family count/exposure, city/hub exposure, strike consistency, and covariance budget are enforced before submission.

---

## 3. Position Sizing: Continuous Kelly

*   **Kelly Fraction**: Computes quarter-Kelly sizing from post-fee edge and binary odds, with a fee-inclusive `0.12` bankroll cap.
*   **Duplicate Exposure Haircut**: Kelly fraction is halved (`SAME_EVENT_PENALTY = 0.50`) if there is another open position within the same city event family.
*   **Risk Ceilings**:
    *   *Position Clamp*: `$10.00` base position rail plus the fee-inclusive Kelly cap.
    *   *Event Clamp*: Aggregate same-family exposure cannot exceed `8%` of bankroll.
    *   *Quantity Clamp*: Hard limit of `15` contracts per position.
    *   *Liquidity Depth Clamp*: Order size is capped at top-of-book size (`ask_size`) to prevent crossing the spread.

---

## 4. Exit & Salvage Systems (Position Monitor)

The bot executes four explicit exit systems inside `run_strategy_cycle()`:

*   **Sovereign Salvage (Dead-Trade Purge)**: If the updated held-side model probability falls below the conviction-tier salvage floor, the position is closed immediately at market prices to salvage remaining capital. The live floor is `15%` by default, `12%` for `80%+` entries, and `10%` for `90%+` entries.
*   **Take-Profit (70% Gain Lock-in)**: Exits the position if the market price appreciates by $70\%$ of the maximum possible gain:
    $$\text{Target Price} \ge \text{Entry Price} + (1.0 - \text{Entry Price}) \cdot 0.70$$
*   **Anti-Double-Down & Bracket Guards**: Rejects buying more contracts on the same strike, or hedging opposite contract sides within the same city event.
*   **Concurrency Swaps**: At the 20-position base limit, the runner may evaluate a stronger candidate for risk-controlled redeployment rather than blindly adding exposure.

---

## 5. Legacy & Archived Strategy Concepts

*   **Continuation, Mean Reversion, Late Repricing**: Archived concepts with no active execution path.
