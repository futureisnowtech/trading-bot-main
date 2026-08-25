# Strategy Catalog: Alpha Models & Risk Gates

This catalog details the rules, probability models, safety gates, and exit logics implemented in the strategy engines.

---

## 1. Unified Strategy: `weather_physics`

The deployed v19.20 system uses a single strategy named **`weather_physics`**. It merges deterministic numerical forecasts with explicit uncertainty and bounded temperature physics, evaluates odds, and manages exits.

### 1.1 Deterministic Physical-Model Blend
The baseline probability estimate ($q_{\text{hat}}$) uses promoted relative GFS/ECMWF skill weights, model/horizon predictive-error sigma, and mode-aware temperature physics before the CDF. Commercial ensembles and ICON are absent. Unanimous same-tail GFS/ECMWF agreement earns $1.5\text{x}$; a probability gap above 20 points reduces probability and size, and above 70 points vetoes the trade.

### 1.2 NCEP AIGFS consensus (Sigma Scaler)
*   Instead of changing the fair probability directly, the machine-learning NCEP AIGFS forecast serves as a volatility ($\sigma$) scaler.
*   If the actual AIGFS value disagrees with the combined GFS/ECMWF mean, uncertainty ($\sigma$) is increased, causing the Kelly sizing model to allocate fewer contracts.
*   If the AI model agrees closely, uncertainty is decreased, permitting larger allocations.

---

## 2. Multi-Factor SRE Safety Gates

Before placing orders, the bot checks 11 safety rules. If any check fails, the trade is vetoed:

1.  **Lane and city policy**: fresh entries must be enabled in the versioned lane policy and survive the station/city firewall.
2.  **Provider identity and freshness**: GFS is mandatory; stale or commercial-ensemble payloads fail closed.
3.  **Spread checks**: daily contracts are capped at a $0.12 spread and 35% spread-to-price ratio.
4.  **Horizon bounds**: daily entries must be 1 to 120 hours from settlement.
5.  **Forecast uncertainty**: excessive projected sigma or a GFS/ECMWF probability gap above 70 points vetoes.
6.  **Post-fee EV**: chosen-side expected value must meet the canonical 0.12 floor after ceiled modeled fees.
7.  **Position and portfolio risk**: duplicate strikes, quote depth, concurrency, same-family exposure, regional exposure, and covariance budget are all enforced.

The former overround/parity/entropy “economics gate” was dead code and has been removed; it must not be described as an active safety layer.

---

## 3. Exit and Salvage Logic

Open positions are continuously monitored for exit signals:

*   **Take-Profit**: Closures are triggered to lock in $70\%$ of maximum potential profit (e.g., if entry is $\$0.30$, max gain is $\$0.70$, target gain is $\$0.49$, triggering exit at $\$0.79$).
*   **Sovereign Salvage**: If updated weather forecasts indicate the bet's probability has dropped below $15\%$, the bot exits immediately at market price to salvage remaining capital.
*   **Portfolio Swaps**: At the 20-position base limit, the runner may evaluate a stronger candidate for risk-controlled redeployment rather than blindly adding exposure.
