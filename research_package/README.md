# Kalshi Weather Trading Bot - Research Package

This directory contains a clean, cataloged archive of the Kalshi Weather prediction bot's codebase, data logs, version history, and configurations. It is designed to be easily analyzed by research tools like NotebookLM.

> Historical evidence bundle: numeric inventories describe the 2026-07-07
> capture. Current runtime truth lives in `AGENTS.md` and
> `docs/production_probability_path.md` unless a section below is explicitly
> marked as refreshed.

---

## 1. Directory Structure

This research package contains the following files:

### 1.1 Inventories & Catalogs
*   **[`00_project_inventory.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/00_project_inventory.md)**: A complete inventory of every script, database, deployment file, and configuration file in the project.
*   **[`01_system_architecture.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/01_system_architecture.md)**: A blueprint of daemons, database schemas, order routing, and thermodynamic netting rules.
*   **[`02_strategy_catalog.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/02_strategy_catalog.md)**: Breakdown of the full physical-model blend, NCEP AIGFS uncertainty input, convergence guardrail, SRE safety gates, and exit logic.
*   **[`03_parameter_catalog.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/03_parameter_catalog.md)**: Summary of configurations, risk limits, overrides, and station coordinates.

### 1.2 Flat-File Normalized Data Exports (1,470 Trades, 551 Settlements)
*   **[`normalized_trades.csv`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/normalized_trades.csv)**: 1,470 execution rows merged from database and CSV backups.
*   **[`normalized_weather_forecasts.csv`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/normalized_weather_forecasts.csv)**: 8,195 rows of historical bid/ask order book quotes for active contracts.
*   **[`normalized_weather_actuals.csv`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/normalized_weather_actuals.csv)**: 20 rows of observed high/low temperature watermarks.

### 1.3 Audit Logs & Diagnostics
*   **[`04_trade_data_summary.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/04_trade_data_summary.md)**: Telemetry details and trade metadata.
*   **[`05_weather_data_summary.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/05_weather_data_summary.md)**: Analysis of forecast models, caches, and coordinate systems.
*   **[`06_bug_and_change_history.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/06_bug_and_change_history.md)**: Historical resolved bug logs and active SRE system risks.
*   **[`07_data_quality_audit.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/07_data_quality_audit.md)**: Data quality verification checklist and final readiness verdict.

### 1.4 NotebookLM Upload Folder
*   **[`notebooklm_upload/`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/notebooklm_upload)**: Clean, non-technical markdown files explaining the project conceptually for NotebookLM.
    *   [`project_overview.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/notebooklm_upload/project_overview.md)
    *   [`architecture_summary.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/notebooklm_upload/architecture_summary.md)
    *   [`strategy_catalog.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/notebooklm_upload/strategy_catalog.md)
    *   [`parameter_catalog.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/notebooklm_upload/parameter_catalog.md)
    *   [`trade_data_summary.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/notebooklm_upload/trade_data_summary.md)
    *   [`weather_data_summary.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/notebooklm_upload/weather_data_summary.md)
    *   [`bug_and_change_history.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/notebooklm_upload/bug_and_change_history.md)
    *   [`data_quality_audit.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/notebooklm_upload/data_quality_audit.md)
    *   [`research_questions_for_notebooklm.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/notebooklm_upload/research_questions_for_notebooklm.md)
    *   [`glossary.md`](file:///Users/joshmacbookair2020/Projects/algo_trading_final/research_package/notebooklm_upload/glossary.md)

---

## 2. Ingestion & Recovery Details

*   **Trade Database Recovery**: Overwrote local database with 1,470 executions downloaded directly from Kalshi API.
*   **Resolution Database Recovery**: Overwrote local resolutions with 551 resolved settlements retrieved directly from Kalshi API.
*   **Verdict**: **PASS / RESEARCH-READY**.
