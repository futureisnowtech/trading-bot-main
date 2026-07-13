# Weather Data Ingestion & Alignment

This document details the meteorological data inputs and recovered settlements.

---

## 1. Weather Data Feeds

1.  **Open-Meteo GFS and ECMWF Ensembles**: Cached in `logs/weather_snapshot.json`.
2.  **NOAA ASOS / METAR Observations**: Real-time hourly reports cached in `logs/weather_watermarks.json` (partially corrupted locally by unit tests).
3.  **GraphCast AI (AIGEFS)**: Volatility adjustment model.

---

## 2. Recovered Settlements

*   **Database Resolutions**: **551 resolved contracts** are now fully logged in the `forecast_resolutions` table.
*   **Attributes**: Maps target contract symbols to the actual exchange-certified resolution outcome (`YES` or `NO`) and settlement timestamp, allowing for precise model performance audits.
