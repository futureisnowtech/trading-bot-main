# Strategy Catalog: Alpha Models & Risk Gates

This catalog details the rules, probability models, safety gates, and exit logics implemented in the strategy engines.

---

## 1. Unified Strategy: `weather_ensemble`

The system uses a single active trading strategy named **`weather_ensemble`**. It merges numerical forecasts, evaluates odds, and manages exits.

### 1.1 GFS and ECMWF Blending
The baseline probability estimate ($q_{\text{hat}}$) is built by combining:
*   **GFS Ensemble members** (weighted at 60%).
*   **ECMWF Ensemble members** (weighted at 40%).
*   *Agreement Boost*: If both models predict high certainty (both $>75\%$ or both $<25\%$), size is increased by $1.5\text{x}$.
*   *Divergence Haircut*: If models disagree ($>20\%$ gap), size is reduced and the probability is shrunk towards 50-50. If disagreement exceeds $70\%$, the trade is vetoed immediately.

### 1.2 GraphCast AI consensus (Sigma Scaler)
*   Instead of changing the fair probability directly, the machine learning GraphCast AI model serves as a volatility ($\sigma$) scaler.
*   If the AI model disagrees with GFS/ECMWF mean values, uncertainty ($\sigma$) is increased, causing the Kelly sizing model to allocate fewer contracts.
*   If the AI model agrees closely, uncertainty is decreased, permitting larger allocations.

---

## 2. Multi-Factor SRE Safety Gates

Before placing orders, the bot checks 11 safety rules. If any check fails, the trade is vetoed:

1.  **Overround Cap**: Rejects if exchange bid-ask spreads create a house edge $> 15\%$.
2.  **Spread Cap**: Rejects if bid-ask spread is $>\$0.12$.
3.  **Spread-to-Price Ratio**: Rejects if spread divided by average price is $> 35\%$.
4.  **Horizon Bounds**: Rejects if contract is $<1$ hour or $>120$ hours (5 days) from settlement.
5.  **Volatility Cap**: Rejects if log-odds price noise $\sigma > 0.80$.
6.  **Parity Gap**: Rejects if implied YES + implied NO prices deviate from $\$1.00$ by $>\$0.05$.
7.  **Positive EV Floor**: Rejects if expected value after fees is $< \$0.05$ per contract.
8.  **Longshot Gate**: Rejects buying YES if blended probability is $< 10\%$.
9.  **Entropy Limits**: Rejects trades if probability is too close to certainty ($<5\%$) or in chaotic zones ($>67\%$).
10. **Duplicate Strike Guard**: Rejects buying if another position is open on the exact same contract.
11. **Regional Exposure Cap**: Rejects if trade exceeds regional risk allocations.

---

## 3. Exit and Salvage Logic

Open positions are continuously monitored for exit signals:

*   **Take-Profit**: Closures are triggered to lock in $70\%$ of maximum potential profit (e.g., if entry is $\$0.30$, max gain is $\$0.70$, target gain is $\$0.49$, triggering exit at $\$0.79$).
*   **Sovereign Salvage**: If updated weather forecasts indicate the bet's probability has dropped below $15\%$, the bot exits immediately at market price to salvage remaining capital.
*   **Portfolio Swaps**: If the 50-position limit is reached and a new, high-EV ($>0.15$) trade is found, the bot flattens its lowest-probability position ($<0.65$) to release capital.
