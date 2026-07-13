# Trade Data Summary & Metadata Analysis

This document describes the structure, locations, fields, and metrics of all historical trades recovered directly from Kalshi.

---

## 1. Trade Storage Locations

*   **Primary Database Table**: `trades` table inside [`logs/trades.db`](file:///Users/joshmacbookair2020/projects/algo_trading_final/logs/trades.db).
*   **Reconstruction Script**: [`scripts/pull_and_reconstruct_kalshi_data.py`](file:///Users/joshmacbookair2020/projects/algo_trading_final/scripts/pull_and_reconstruct_kalshi_data.py) was executed to download data from Kalshi.
*   **Normalized Research Export**: [`/research_package/normalized_trades.csv`](file:///Users/joshmacbookair2020/projects/algo_trading_final/research_package/normalized_trades.csv) containing 1,470 rows.

---

## 2. Recovery & Dataset Statistics

*   **Total Executions**: **1,470 trades** (deduplicated).
*   **Date Range**: From **2026-05-24T15:05:22-04:00** to **2026-06-23T10:45:12-04:00**.
*   **Environments**: Live executions on the `live_v10` pipeline.
*   **Traded Underliers**:
    *   `KXTEMP` and `KXTEMPNYCH` (Hourly temperatures).
    *   `KXHIGH` (Daily high temperature contracts for BOS, DEN, ATL, LV, SFO, LAX, HOU, DAL, NYC).
    *   `KXLOW` (Daily low temperature contracts).
    *   `KXRAIN` (Daily precipitation contracts).

---

## 3. Data Gaps and Analysis (Updated)

*   **Historical Model Probabilities**: Since GFS/ECMWF probabilities at execution time are calculated in-memory and were not logged to the database during live runs, the columns `model_prob_gfs`, `model_prob_ecmwf`, and `forecast_yes_prob` remain `NULL`.
*   **Orphan Positions Resolution**: All active positions (such as the June 9th `0.35` USD entry price discrepancies) have been reconciled and closed out as part of the database recovery loop.

---

## 4. Calculated Performance Metrics (Pivot Tables)

Below are the exact portfolio distributions calculated directly from the database of 1,470 trades:

### 4.1 Performance by Regional Hub
| Regional Hub | Trades | Win Rate | Net PnL (USD) | Total Fees |
| :--- | :--- | :--- | :--- | :--- |
| WEST | 275 | 33.8% | $459.05 | $24.02 |
| GULF | 227 | 33.5% | $319.98 | $16.41 |
| UNKNOWN (Legacy/Misc) | 138 | 37.7% | $132.72 | $17.20 |
| MIDWEST | 146 | 35.6% | $67.59 | $12.95 |
| MOUNTAIN | 55 | 21.8% | $56.08 | $3.24 |
| FLORIDA | 30 | 20.0% | -$23.61 | $6.38 |
| SOUTH | 38 | 7.9% | -$76.32 | $4.71 |
| NORTHEAST | 561 | 23.2% | -$737.35 | $43.62 |

### 4.2 Performance by Asset Type
| Asset Type | Trades | Win Rate | Net PnL (USD) | Total Fees |
| :--- | :--- | :--- | :--- | :--- |
| Daily Low Temp | 613 | 40.9% | $1,236.52 | $55.22 |
| Daily High Temp | 510 | 26.9% | $515.11 | $51.83 |
| Legacy NBA | 2 | 50.0% | $12.30 | $1.68 |
| UNKNOWN | 15 | 26.7% | -$15.94 | $2.72 |
| Hourly Temp | 140 | 7.1% | -$157.05 | $9.36 |
| Rain (Precipitation) | 190 | 11.1% | -$1,392.82 | $7.72 |

### 4.3 Performance by Price Bracket
| Price Bracket | Trades | Win Rate | Net PnL (USD) | Total Fees |
| :--- | :--- | :--- | :--- | :--- |
| > $0.70 (Expensive Favorites) | 514 | 77.2% | $2,595.37 | $27.79 |
| $0.30 - $0.70 (Mid Range) | 168 | 10.1% | -$188.25 | $27.53 |
| $0.10 - $0.30 (Longshots) | 489 | 1.8% | -$417.30 | $51.01 |
| < $0.10 (Penny Contracts) | 299 | 0.3% | -$1,791.69 | $22.20 |

### 4.4 Portfolio Summary
*   **Total Executed Trades**: 1,470
*   **Total Wins**: 424 (28.8%)
*   **Total Net PnL**: **+$198.13**
*   **Total Transaction Fees Paid**: $128.53
*   **Fee-to-PnL Ratio**: 0.65x (transaction fees ate 65% of net profits)

