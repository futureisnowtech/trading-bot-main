# Recommended Research Questions for NotebookLM

This document lists high-value analytical questions designed to help you probe this research package using NotebookLM.

---

## 1. System Reliability & Bug Impacts
1.  **How did the lack of unit test filesystem isolation affect the bot's live operating data?**
    *   *Context*: Check how `tests/proof/test_weather_intraday_watermarks.py` overwrote `logs/weather_watermarks.json` with a mock value of `74.0`.
2.  **What caused the entry price discrepancy where trades show fills at $0.01/$0.07, but the positions table shows $0.35?**
    *   *Context*: Investigate the race condition in `reconcile_forecast_positions` falling back to the order book `mid` price.
3.  **How did the ISO timestamp parsing bug in `_hours_to_resolution()` block valid trades before it was resolved on June 4, 2026?**
4.  **Are there active positions in the database that have no corresponding record in the trades execution log?**
    *   *Context*: Query the repository's orphan position `KXRAINAUSM-26JUN-1`.

---

## 2. Strategy & Sizing Logic
5.  **How are GFS and ECMWF weather model predictions blended in the active `weather_ensemble` strategy, and what are their respective weights?**
    *   *Context*: Trace why weights are locked to a static 60/40 blend (`STATIC_DISABLED`) despite the active RBI calibration loop.
6.  **How does the bot adjust position sizing using the GraphCast AI (AIGEFS) model?**
    *   *Context*: Detail the "Sigma Volatility Scaler" mechanism.
7.  **What is "Thermodynamic Netting" and how does the bot net weather covariance across regional hubs?**
    *   *Context*: Review the signed sums mapping for cool/wet vs warm/dry outcomes in `forecast/runner.py`.
8.  **How do the regional SRE overrides (conviction floors) in `config/hub_params.json` affect the bot's risk appetite?**

---

## 3. Data Auditing & Missing Telemetry
9.  **What critical prediction metrics are missing from the `trades` database table that prevent a thorough backtest audit?**
    *   *Context*: Note that columns like GFS/ECMWF probabilities are entirely `NULL`.
10. **Why are there zero rows in the `forecast_resolutions` table, and what is the impact on post-resolution calibration?**
11. **Which files and directories should a quantitative researcher inspect first to verify the bot's risk boundaries?**
12. **What parts of the system architecture are labeled as "UNKNOWN" or "INFERRED" rather than "CONFIRMED"?**
13. **Is the data in this package ready for quantitative backtesting or machine learning optimization? Summarize the verdict.**
