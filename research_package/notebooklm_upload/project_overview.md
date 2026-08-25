# Project Overview: Kalshi Weather Prediction Bot

This research package organizes the source code, data, logs, and configurations of a live-execution weather trading bot. The goal of this bot is to predict and trade weather outcomes (temperature, rain, snow, wind) on the Kalshi prediction exchange.

---

## 1. Core History & Data Recovery

Although the remote droplet server containing the historical operational database was deleted, **100% of the actual live trade execution history and settlement values have been recovered directly from Kalshi's exchange servers.** 

### Reconstructed Dataset Scope:
*   **Total Executions**: **1,470 trades** spanning from **May 24, 2026** to **June 23, 2026**.
*   **Resolved Settlements**: **551 contracts** populated with exchange ground truth outcomes.
*   **Operating Lane**: `forecast` (the weather lane).
*   **Verdict**: **PASS / RESEARCH-READY**.

---

## 2. Document Layout

This research directory contains the following catalogs and data exports to help NotebookLM understand the project:

### 2.1 Technical and Strategy Catalogs
*   **[`00_project_inventory.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/00_project_inventory.md)**: Inventories every script, database, deployment file, and configuration file in the project.
*   **[`01_system_architecture.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/01_system_architecture.md)**: Blueprints the daemon processes, data pipelines, order routing, and thermodynamic netting rules.
*   **[`02_strategy_catalog.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/02_strategy_catalog.md)**: Explains the deterministic GFS/ECMWF probability blend, bounded physics, and NCEP AIGFS uncertainty scaling.
*   **[`03_parameter_catalog.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/03_parameter_catalog.md)**: Indexes the risk limits, SRE gate thresholds, and weather station mappings.

### 2.2 Data Quality & History Reports
*   **[`04_trade_data_summary.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/04_trade_data_summary.md)**: Describes logged fills and trade database structures.
*   **[`05_weather_data_summary.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/05_weather_data_summary.md)**: Identifies deterministic forecast providers, NCEP AIGFS, HRRR, and the METAR observations cache.
*   **[`06_bug_and_change_history.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/06_bug_and_change_history.md)**: Details resolved bugs and critical system anomalies.
*   **[`07_data_quality_audit.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/07_data_quality_audit.md)**: Audits files for missing values, duplicates, and gives a research-readiness verdict.

### 2.3 Normalized Data Exports (CSVs)
*   **[`normalized_trades.csv`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/normalized_trades.csv)**: 1,470 rows.
*   **[`normalized_weather_forecasts.csv`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/normalized_weather_forecasts.csv)**: 8,195 rows.
*   **[`normalized_weather_actuals.csv`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/normalized_weather_actuals.csv)**: 20 rows.
