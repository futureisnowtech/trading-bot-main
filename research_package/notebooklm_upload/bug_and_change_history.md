# Bug and Change History: Quantitative SRE Log

This document catalogues software versions, historical bug fixes, and active SRE risks discovered in the repository.

---

## 1. Version History and Scope Evolution

*   **Active Software Version**: `v19.9.8` (released June 8, 2026).
*   **Historical Lane Merging (2026-06-04)**: The active repository was cleaned of legacy assets. Spot, crypto, stocks, futures, and research models were archived. The bot was narrowed to a live-only Kalshi weather execution model.
*   **Visual Cockpit Dashboard (2026-06-06 to 2026-06-08)**: Streamlined the cockpit UI to map five execution pipelines side-by-side (Hourly Temp, Rain, Snow, Wind, Daily Temp).

---

## 2. Chronology of Major Resolved Bugs

### 2.1 Hours-to-Resolution ISO Parsing Bug (2026-06-04)
*   *Bug*: Valid contracts were blocked at entry due to `RESOLUTION_HORIZON_TOO_SHORT`.
*   *Cause*: ISO Kalshi expiry timestamps (e.g. `2026-06-09T00:33:00.795-04:00`) were incorrectly parsed as `0.0` hours remaining.
*   *Fix*: Corrected string split and regex patterns to capture timezone offsets.

### 2.2 Cockpit P&L Rolling Truncation (2026-06-08)
*   *Bug*: Realized P&L box displayed incorrect totals.
*   *Cause*: Dashboard calculated sums from the last 25 trades only.
*   *Fix*: Replaced calculations with a database query summing the true total session win/loss results.

### 2.3 Flat Fee Assumption Inaccuracy (2026-06-06)
*   *Bug*: Expected value (EV) and contract Kelly sizing formulas were distorted.
*   *Cause*: The bot used a flat `$0.07` per contract fee.
*   *Fix*: Integrated Kalshi's live tiered fee model (evaluating maker vs taker rates) across all calculations.

---

## 3. Critical Active SRE Risks & Vulnerabilities

### 3.1 Test Pollution of Weather Observation Cache
*   *Risk*: Corrupted intraday exit evaluations.
*   *Description*: Running unit tests writes mock temperatures (`74.0`) directly to `logs/weather_watermarks.json`.
*   *Impact*: If the bot executes real-time exit decisions, it will query these polluted values, leading to incorrect take-profit or stop-loss executions.

### 3.2 Position Sync Race Condition
*   *Risk*: Incorrect position cost basis.
*   *Description*: The position reconciler runs immediately after trade placement. Due to database commit delays, the matching execution record is not found, and the reconciler falls back to the order book's `mid` price ($0.35) instead of the actual fill price ($0.01/$0.07).

### 3.3 Offline Learning weights
*   *Risk*: Adaptive weights are inactive.
*   *Description*: The RBI learner writes model skill weights to the database, but the blender is hardcoded to use static weights (60% GFS / 40% ECMWF).
