# Parameter and Configuration Catalog

This catalog details the risk limits, SRE gate thresholds, exit targets, regional conviction floors, and active station scopes used by the bot.

---

## 1. Capital & Sizing Limits

These values control position size and total portfolio leverage:

*   **`KALSHI_MAX_DEPLOYED_PCT` (0.90)**: The bot will not trade if outstanding deployed capital exceeds $90\%$ of total bankroll.
*   **`KALSHI_MAX_CONCURRENT_POSITIONS` (50)**: A maximum of 50 active contracts can be held.
*   **`KALSHI_MAX_USD_PER_POSITION` ($40.00)**: Hard SRE ceiling on USD allocation for any single position.
*   **`KALSHI_MAX_QTY_PER_POSITION` (2500)**: Maximum number of contracts per position.
*   **`KALSHI_KELLY_CAP` (0.10)**: Fractional Kelly sizing limit.
*   **`SAME_EVENT_PENALTY` (0.50)**: Halves Kelly allocation if another contract in the same city series is open.

---

## 2. Strategy and Gate Thresholds

These values filter incoming contracts to reject low-edge or high-risk setups:

*   **`EV_THRESHOLD` (0.05)**: Expected net value of chosen side (post-fees) must be $\ge \$0.05$ per contract.
*   **`MAX_OVERROUND` (0.15)**: Rejects trades if book overround (house edge) is $> 15\%$.
*   **`MAX_SPREAD_DOLLARS` ($0.12)**: Maximum bid-ask spread.
*   **`KALSHI_MAX_SPREAD_RATIO` (0.35)**: Rejects if spread / average price is $> 35\%$.
*   **`MIN_HOURS_TO_RES` (1.0)** & **`MAX_HOURS_TO_RES` (120.0)**: Horizon limits (trades are restricted to a 1-hour to 5-day window from resolution).
*   **`MIN_IMPLIED_PROB_FOR_YES` (0.10)**: Prevents buying YES contracts with implied probabilities under $10\%$ (longshot bias).
*   **`MAX_ENTROPY_FOR_ENTRY` (0.67)** & **`MIN_ENTROPY_FOR_ENTRY` (0.05)**: Filters out already-settled contracts and highly uncertain setups.
*   **`MAX_SIGMA_T` (0.80)**: Volatility cap during rapid repricings.

---

## 3. Exit and Invalidation Constants

*   **Sovereign Salvage Delta (0.15)**: Exits position if contract probability drops below $15\%$.
*   **Take-Profit Target (0.70)**: Closes contract to secure $70\%$ of the maximum possible gain.
*   **`KALSHI_DATA_FRESHNESS_MINUTES` (180)**: GFS/ECMWF weather snapshot files must be less than 3 hours old to prevent stale forecast executions.

---

## 4. Regional Hard Conviction Floors (HUB Overrides)

The minimum required prediction conviction before a trade is approved is adjusted regionally based on local weather complexity:

*   **Midwest (`0.50`)**: Lowest floor (Chicago, Minneapolis, Detroit, St. Louis, Omaha, Kansas City).
*   **Northeast (`0.52`)**: (New York, Boston, Philadelphia, Washington D.C.).
*   **West (`0.54`)**: (Los Angeles, San Francisco, Phoenix, Seattle, Portland, Las Vegas).
*   **South, Florida, Gulf, Mountain (`0.70`)**: Strictest conviction floors due to higher summer storm volatility (Atlanta, Miami, Houston, Austin, Denver, Salt Lake City).
