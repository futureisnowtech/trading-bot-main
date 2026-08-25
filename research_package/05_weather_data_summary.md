# Weather Data Summary & Ingestion Analysis

This document describes the weather data sources, forecast models, and recovered database resolutions.

---

## 1. Meteorological Data Sources

The bot relies on three primary data-feed classes:

1.  **Open-Meteo API**: Keyless deterministic GFS, ECMWF, and NCEP AIGFS forecasts. The commercial ensemble/key route and ICON are excluded from the v19.20 production path.
2.  **NOAA Aviation Weather (METAR)**: Scraped real-time station observations.
3.  **NCEP AIGFS (`aigefs` internal key)**: Used as the actual GFS/ECMWF disagreement-based uncertainty scaling input, not as a direct vote.

---

## 2. Ingested Weather Files & Database Records

*   **`logs/weather_snapshot.json` (Active)**: Holds raw forecast model arrays across 35 active series.
*   **`forecast_quotes` Table (Active)**: Contains `8,195` historical bid/ask order book quotes.
*   **`forecast_bars` Table (Active)**: Contains `3,850` historical price bars.
*   **`forecast_resolutions` Table (Recovered)**: Contains **551 resolved contracts** pulled directly from Kalshi's settlements history. Each row maps a contract ID to its ground truth outcome (`YES` or `NO`) and settlement timestamp.

---

## 3. Station Mappings & Coordinates

The bot tracks 32 cities mapped to official ASOS weather stations (e.g. `KNYC` for Central Park, `KMDW` for Midway, `KLAX` for Los Angeles, `KDEN` for Denver, `KAUS` for Austin).

---

## 4. Ingestion Gaps & Research Alignment

*   **Settlement Resolutions**: Reconstructed and fully populated with 551 records in `forecast_resolutions`. This enables retrospective performance analysis (e.g. Brier Score calculations).
*   **Model Run Alignment**: The 3-hour data freshness rule (`KALSHI_DATA_FRESHNESS_MINUTES = 180`) was active to prevent executing orders on stale forecast snapshots.
