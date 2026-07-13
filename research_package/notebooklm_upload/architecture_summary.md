# Architecture Summary: How the Bot Worked

This document explains the software design, execution daemons, and mathematical netting constraints of the weather trading bot.

---

## 1. Core Operating Daemons

The system runs two long-lived python processes on a remote server:

1.  **Trading Execution Daemon (`execution_daemon.py`)**:
    *   Runs the trading loop every 5 minutes.
    *   Checks system disk space (stops if free space is below 2048 MB).
    *   Orchestrates contract discovery, GFS/ECMWF model updates, order book quote scanning, strategy evaluations, and trade submission.
    *   Hosts an embedded thread for the Telegram interface.
2.  **Telegram Operator Daemon (`telegram_daemon.py` / `notifications/telegram_bot.py`)**:
    *   Polls for incoming operator messages.
    *   Executes whitelisted status commands (e.g. `/status` to check balance, positions, and logs).

---

## 2. The Data Ingestion Flow

The bot requires three datasets to evaluate trades:

*   **Ensemble Weather Forecasts**: The bot pulls predictions from Open-Meteo GFS and ECMWF models. These arrays are cached locally in `logs/weather_snapshot.json`.
*   **Order Book Quotes**: The quote harvester fetches paired bid/ask levels from Kalshi and caches them to `forecast_quotes` in `logs/trades.db`.
*   **Intraday Observations (METAR)**: Scraping NOAA METAR reports updates real-time temperature boundaries in `logs/weather_watermarks.json` to manage exits.

---

## 3. Decision and Sizing Pipeline

The execution runner passes candidates through a sequential filter:

```
[Discovery: Active Tickers] -> [Quote Ingestion: Spreads] -> [SRE Gate Checks]
                                                                  |
[Order Fill: Limit Orders]  <- [Sizing: Kelly Cap & NET Hubs] <- [Passed Candidates]
```

### 3.1 Thermodynamic Sizing Netting
To manage geographic risk correlation (e.g., if it rains in New York, it is likely to rain in Boston), the bot net-hedges positions inside regional hubs.
*   *Cool/Wet outcomes* (low temp, rain, snow, wind) are signed **negative (-1.0)**.
*   *Warm/Dry outcomes* (high temp) are signed **positive (+1.0)**.
*   The sum of these signed exposures is checked against a dynamically calculated regional cap to prevent over-exposure.

### 3.2 Order Submissions
Orders are submitted as marketable limit orders with hard budget constraints (`KALSHI_MAX_USD_PER_POSITION = 40.0`). Transactions are logged in `logs/trades.db` and daily CSV files.
