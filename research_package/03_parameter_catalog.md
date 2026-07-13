# Parameter and Configuration Catalog

This document registers all risk boundaries, sizing constants, timing thresholds, weather station mappings, and configuration variables parsed by the bot.

---

## 1. Risk and Capital Limits (Active)

These parameters control capital deployment size and prevent over-exposure:

| Parameter Name | File Source | Value | Description | Status |
| :--- | :--- | :--- | :--- | :--- |
| `KALSHI_MAX_DEPLOYED_PCT` | `config.py` | `0.90` (90%) | Max percentage of total capital base that can be deployed concurrently. | **CONFIRMED** |
| `KALSHI_MAX_CONCURRENT_POSITIONS` | `config.py` | `50` | Maximum number of open contracts allowed in the portfolio. | **CONFIRMED** |
| `KALSHI_MAX_USD_PER_POSITION` | `config.py` | `$40.00` | SRE Hard dollar ceiling for any single position. | **CONFIRMED** |
| `KALSHI_MAX_QTY_PER_POSITION` | `config.py` | `2500` | Hard ceiling on the number of contracts in any single position. | **CONFIRMED** |
| `KALSHI_SAME_EVENT_FAMILY_CAP` | `config.py` | `5` | Maximum number of active contracts allowed per city/event series. | **CONFIRMED** |
| `KALSHI_KELLY_CAP` | `config.py` | `0.10` (10%) | Max fractional Kelly sizing allocation (hard constraint). | **CONFIRMED** |
| `SAME_EVENT_PENALTY` | `strategy_engine.py` | `0.50` (50%) | Sizing multiplier applied to halve sizes if event family shares positions. | **CONFIRMED** |
| `MIN_FREE_DISK_MB` | `config.py` | `2048` | Minimum free disk space required on host system to execute. | **CONFIRMED** |

---

## 2. SRE Strategy and Gate Thresholds (Active)

These parameters gate entry signals based on order book and forecast characteristics:

| Parameter Name | File Source | Value | Control Target | Status |
| :--- | :--- | :--- | :--- | :--- |
| `EV_THRESHOLD` | `strategy_engine.py` | `0.050` | Minimum post-fee expected value per contract. | **CONFIRMED** |
| `MAX_OVERROUND` | `strategy_engine.py` | `0.15` | Maximum house edge overround cap. | **CONFIRMED** |
| `MAX_SPREAD_DOLLARS` | `strategy_engine.py` | `$0.12` | Maximum allowable bid-ask spread in dollars. | **CONFIRMED** |
| `KALSHI_MAX_SPREAD_RATIO` | `config.py` | `0.35` | Maximum spread-to-price ratio. | **CONFIRMED** |
| `MIN_HOURS_TO_RES` | `strategy_engine.py` | `1.0` | Minimum hours remaining to settlement. | **CONFIRMED** |
| `MAX_HOURS_TO_RES` | `strategy_engine.py` | `120.0` (5 days) | Maximum hours remaining to settlement. | **CONFIRMED** |
| `MIN_IMPLIED_PROB_FOR_YES` | `strategy_engine.py` | `0.10` | Refuse to buy YES if blended prob is below this (longshot gate). | **CONFIRMED** |
| `MAX_ENTROPY_FOR_ENTRY` | `strategy_engine.py` | `0.67` | Maximum entropy limit. | **CONFIRMED** |
| `MIN_ENTROPY_FOR_ENTRY` | `strategy_engine.py` | `0.05` | Minimum entropy limit (filters already-resolved markets). | **CONFIRMED** |
| `MAX_SIGMA_T` | `strategy_engine.py` | `0.80` | Volatility cap during rapid repricings. | **CONFIRMED** |
| `MAX_PARITY_GAP_ABS` | `strategy_engine.py` | `0.05` | Parity mismatch gap (|YES + NO - 1.0|). | **CONFIRMED** |
| `KALSHI_MAX_FEE_DRAG_PCT` | `config.py` | `0.30` | Vetoes trade if fees represent > 30% of gross gain. | **CONFIRMED** |
| `KALSHI_DATA_FRESHNESS_MINUTES` | `config.py` | `180` (3 hours) | Maximum allowed age of GFS/ECMWF weather snapshot files. | **CONFIRMED** |

---

## 3. Position Exit & Invalidation Parameters (Active)

| Parameter Name | File Source | Value | Control Target | Status |
| :--- | :--- | :--- | :--- | :--- |
| `Sovereign Salvage Delta` | `forecast/runner.py` | `0.15` (15%) | Purge position if bet probability drops below this. | **CONFIRMED** |
| `Take-Profit Target` | `forecast/runner.py` | `0.70` (70%) | Exit lock-in target gain (70% of max potential profit). | **CONFIRMED** |
| `KALSHI_EXPENSIVE_YES_THRESHOLD` | `config.py` | `0.70` | Threshold above which contracts are marked as expensive. | **CONFIRMED** |
| `KALSHI_EXPENSIVE_YES_MIN_NET_EDGE`| `config.py` | `0.01` | Minimum required edge for expensive YES contracts. | **CONFIRMED** |

---

## 4. Regional SRE Overrides: Hard RBI Floors (Active)

*   **Source File**: [`config/hub_params.json`](file:///Users/joshmacbookair2020/projects/algo_trading_final/config/hub_params.json)
*   **Purpose**: Hubs override the lane-aware defaults for the minimum required forecast probability conviction before entries are approved.

| Regional Hub | Hard RBI Override | Included Cities |
| :--- | :--- | :--- |
| **MIDWEST** | `0.50` | Chicago, Minneapolis, Milwaukee, Omaha, St. Louis, Detroit, Kansas City, Oklahoma City |
| **NORTHEAST** | `0.52` | New York, Boston, Philadelphia, Washington D.C. |
| **SOUTH** | `0.70` | Atlanta, Charlotte, Raleigh-Durham, Nashville, Charleston |
| **FLORIDA** | `0.70` | Miami, Orlando |
| **GULF** | `0.70` | Houston, Austin, Dallas, San Antonio, New Orleans |
| **MOUNTAIN** | `0.70` | Denver, Salt Lake City, Albuquerque |
| **WEST** | `0.54` | Los Angeles, San Francisco, Phoenix, Seattle, Portland, Las Vegas |

*   *SRE Clamp*: Enforced unconditionally via `max(0.50, min(0.95, value))` in `_resolve_hard_rbi_threshold()`.

---

## 5. Location and Timezone Settings

*   **Active City Count**: 32 cities.
*   **Official Timezone**: `America/New_York` (defined as `MARKET_TIMEZONE` in `config.py`).
*   **Station Reference File**: [`data/kalshi_weather_monitor.py`](file:///Users/joshmacbookair2020/projects/algo_trading_final/data/kalshi_weather_monitor.py#L86-L120) containing lat/lon and ICAO mappings for NOAA METAR reporting.
