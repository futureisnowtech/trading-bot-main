# Trade Data Summary & Execution Analytics

This document analyzes the historical trade records and schemas recovered from Kalshi.

---

## 1. Trade Volume and Statistics

*   **Total Executions**: **1,470 trades** (reconstructed directly from the exchange).
*   **Date Range**: **2026-05-24** to **2026-06-23**.
*   **Environment**: Live trading on Kalshi API v2.
*   **Markets Traded**: Weather strike families (temps, rain, wind, snow) for major US hubs (Boston, Austin, New York, Los Angeles, Denver, Houston, Atlanta, Chicago).

---

## 2. Ingested Data Fields

*   **Timestamps**: Recorded in local Eastern Time offset (`-04:00`).
*   **Sizing**: Contract quantities, average filled price, total cost, and exchange transaction fees.
*   **Attributes**: Side (`YES`/`NO`), Action (`BUY`/`SELL`), Type (`Limit`/`Market`).

---

## 3. Data Gaps

Internal prediction parameters (GFS/ECMWF model probabilities) were not written to the `trades` database table at execution time and remain `NULL`. The physical contract parameters and market result prices are, however, fully present.

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

