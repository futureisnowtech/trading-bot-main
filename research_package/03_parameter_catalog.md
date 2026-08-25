# Parameter and Configuration Catalog

This document registers all risk boundaries, sizing constants, timing thresholds, weather station mappings, and configuration variables parsed by the bot.

---

## 1. Risk and Capital Limits (Active)

These parameters control capital deployment size and prevent over-exposure:

| Parameter Name | File Source | Value | Description | Status |
| :--- | :--- | :--- | :--- | :--- |
| `KALSHI_MAX_DEPLOYED_PCT` | `config.py` | `0.90` (90%) | Max percentage of total capital base that can be deployed concurrently. | **CONFIRMED** |
| `KALSHI_MAX_CONCURRENT_POSITIONS` | `config.py` | `20` | Maximum number of open contracts allowed before explicit ultra-NO bonus slots. | **CONFIRMED** |
| `KALSHI_ULTRA_HIGH_PROB_NO_CONCURRENT_BONUS` | `config.py` | `+2` | Additional concurrent slots reserved for ultra-high-conviction `NO` trades only. | **CONFIRMED** |
| `KALSHI_MAX_USD_PER_POSITION` | `config.py` | `$10.00` | Base dollar ceiling before explicit conviction multipliers and stricter additive caps. | **CONFIRMED** |
| `KALSHI_HIGH_PROB_POSITION_CAP_MULTIPLIER` | `config.py` | `1.50x` | Size-up multiplier for held-side probabilities at or above `80%`. | **CONFIRMED** |
| `KALSHI_ULTRA_HIGH_PROB_POSITION_CAP_MULTIPLIER` | `config.py` | `2.00x` | Full size-up multiplier for held-side probabilities at or above `90%`. | **CONFIRMED** |
| `KALSHI_MAX_QTY_PER_POSITION` | `config.py` | `15` | Hard ceiling on the number of contracts in any single position. | **CONFIRMED** |
| `KALSHI_SAME_EVENT_FAMILY_CAP` | `config.py` | `5` | Maximum number of active contracts allowed per city/event series. | **CONFIRMED** |
| `KALSHI_ULTRA_HIGH_PROB_NO_FAMILY_CAP_BONUS` | `config.py` | `+1` | Additional same-city slot reserved for ultra-high-conviction `NO` trades only. | **CONFIRMED** |
| `KALSHI_KELLY_CAP` | `config.py` | `0.12` (12%) | Fee-inclusive order-capital ceiling. | **CONFIRMED** |
| `KALSHI_MAX_RISK_PER_EVENT_PCT` | `config.py` | `0.08` (8%) | Aggregate same-family exposure ceiling. | **CONFIRMED** |
| `SAME_EVENT_PENALTY` | `strategy_engine.py` | `0.50` (50%) | Sizing multiplier applied to halve sizes if event family shares positions. | **CONFIRMED** |
| `MIN_FREE_DISK_MB` | `config.py` | `2048` | Minimum free disk space required on host system to execute. | **CONFIRMED** |

---

## 2. SRE Strategy and Gate Thresholds (Active)

These parameters gate entry signals based on order book and forecast characteristics:

| Parameter Name | File Source | Value | Control Target | Status |
| :--- | :--- | :--- | :--- | :--- |
| `EV_THRESHOLD` | `strategy_engine.py` | `0.120` | Minimum post-fee expected value per contract. | **CONFIRMED** |
| `MAX_SPREAD_DOLLARS` | `strategy_engine.py` | `$0.12` | Maximum allowable bid-ask spread in dollars. | **CONFIRMED** |
| `KALSHI_MAX_SPREAD_RATIO` | `config.py` | `0.35` | Maximum spread-to-price ratio. | **CONFIRMED** |
| `MIN_HOURS_TO_RES` | `strategy_engine.py` | `1.0` | Minimum hours remaining to settlement. | **CONFIRMED** |
| `MAX_HOURS_TO_RES` | `strategy_engine.py` | `120.0` (5 days) | Maximum hours remaining to settlement. | **CONFIRMED** |
| `KALSHI_DATA_FRESHNESS_MINUTES_HOURLY` | `config.py` | `25` (SPEC §4.5) | Maximum allowed age of the contract-aligned weather snapshot for hourly contracts. | **CONFIRMED** |
| `KALSHI_DATA_FRESHNESS_MINUTES_DAILY` | `config.py` | `90` (SPEC §4.5) | Maximum allowed age for daily high/low contracts. Selected per contract by `weather_freshness_limit_minutes()`. | **CONFIRMED** |
| `WEATHER_REFRESH_TARGET_SEC` | `kalshi_weather_monitor.py` | `1200` (20 min) | Deterministic model refresh cadence, derived from the tightest freshness window minus a fetch margin. | **CONFIRMED** |

---

## 3. Position Exit & Invalidation Parameters (Active)

| Parameter Name | File Source | Value | Control Target | Status |
| :--- | :--- | :--- | :--- | :--- |
| `Sovereign Salvage Delta` | `forecast/runner.py` | `0.15 / 0.12 / 0.10` | Purge position if held-side probability falls below `15%` by default, `12%` for `80%+` entries, or `10%` for `90%+` entries. | **CONFIRMED** |
| `Take-Profit Target` | `forecast/runner.py` | `0.70` (70%) | Exit lock-in target gain (70% of max potential profit). | **CONFIRMED** |
| `KALSHI_EXPENSIVE_YES_THRESHOLD` | `config.py` | `0.70` | Threshold above which contracts are marked as expensive. | **CONFIRMED** |
| `KALSHI_EXPENSIVE_YES_MIN_NET_EDGE`| `config.py` | `0.01` | Minimum required edge for expensive YES contracts. | **CONFIRMED** |

---

## 4. Retired Hard-RBI Floor Surface

*   **Source File**: [`config/hub_params.json`](file:///Users/joshmacbookair2020/projects/algo_trading_final/config/hub_params.json)
The former `_resolve_hard_rbi_threshold()` helper and hub-specific probability floors were not called by production and were removed in v19.20. `config/hub_params.json` must not be credited as an active probability gate; regional exposure is controlled by the enforced hub-dollar cap.

| Regional Hub | Hard RBI Override | Included Cities |
| :--- | :--- | :--- |
| **MIDWEST** | `0.50` | Chicago, Minneapolis, Milwaukee, Omaha, St. Louis, Detroit, Kansas City, Oklahoma City |
| **NORTHEAST** | `0.52` | New York, Boston, Philadelphia, Washington D.C. |
| **SOUTH** | `0.70` | Atlanta, Charlotte, Raleigh-Durham, Nashville, Charleston |
| **FLORIDA** | `0.70` | Miami, Orlando |
| **GULF** | `0.70` | Houston, Austin, Dallas, San Antonio, New Orleans |
| **MOUNTAIN** | `0.70` | Denver, Salt Lake City, Albuquerque |
| **WEST** | `0.54` | Los Angeles, San Francisco, Phoenix, Seattle, Portland, Las Vegas |

*   *Status*: Historical reference only; no active entry enforcement.

---

## 5. Location and Timezone Settings

*   **Active City Count**: 32 cities.
*   **Official Timezone**: `America/New_York` (defined as `MARKET_TIMEZONE` in `config.py`).
*   **Station Reference File**: [`data/kalshi_weather_monitor.py`](file:///Users/joshmacbookair2020/projects/algo_trading_final/data/kalshi_weather_monitor.py#L86-L120) containing lat/lon and ICAO mappings for NOAA METAR reporting.
