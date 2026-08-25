# Parameter and Configuration Catalog

This catalog details the risk limits, SRE gate thresholds, exit targets, regional conviction floors, and active station scopes used by the bot.

---

## 1. Capital & Sizing Limits

These values control position size and total portfolio leverage:

*   **`KALSHI_MAX_DEPLOYED_PCT` (0.90)**: The bot will not trade if outstanding deployed capital exceeds $90\%$ of total bankroll.
*   **`KALSHI_MAX_CONCURRENT_POSITIONS` (20)**: Base maximum before explicit ultra-NO bonus slots.
*   **`KALSHI_MAX_USD_PER_POSITION` ($10.00)**: Base position rail before explicit conviction multipliers and stricter additive caps.
*   **`KALSHI_MAX_QTY_PER_POSITION` (15)**: Maximum number of contracts per position.
*   **`KALSHI_KELLY_CAP` (0.12)**: Fee-inclusive order-capital limit.
*   **`KALSHI_MAX_RISK_PER_EVENT_PCT` (0.08)**: Aggregate same-family exposure limit.
*   **`SAME_EVENT_PENALTY` (0.50)**: Halves Kelly allocation if another contract in the same city series is open.

---

## 2. Strategy and Gate Thresholds

These values filter incoming contracts to reject low-edge or high-risk setups:

*   **`EV_THRESHOLD` (0.12)**: Expected net value of chosen side after the modeled round-trip fee must be at least $0.12 per contract.
*   **`MAX_SPREAD_DOLLARS` ($0.12)**: Maximum bid-ask spread.
*   **`KALSHI_MAX_SPREAD_RATIO` (0.35)**: Rejects if spread / average price is $> 35\%$.
*   **`MIN_HOURS_TO_RES` (1.0)** & **`MAX_HOURS_TO_RES` (120.0)**: Horizon limits (trades are restricted to a 1-hour to 5-day window from resolution).
*   **`MAX_SIGMA_T` (0.80)**: Volatility cap during rapid repricings.

---

## 3. Exit and Invalidation Constants

*   **Sovereign Salvage Delta (0.15)**: Exits position if contract probability drops below $15\%$.
*   **Take-Profit Target (0.70)**: Closes contract to secure $70\%$ of the maximum possible gain.
*   **`KALSHI_DATA_FRESHNESS_MINUTES_HOURLY` (25) / `_DAILY` (90)**: Contract-aligned weather snapshot data must be younger than the window for that contract type — 25 minutes for hourly contracts, 90 for daily highs and lows — to prevent stale forecast executions. The deterministic weather-model loop refreshes on the configured cadence; hourly entries are currently disabled by the versioned lane policy.

---

## 4. Regional Hard Conviction Floors (HUB Overrides)

The minimum required prediction conviction before a trade is approved is adjusted regionally based on local weather complexity:

*   **Midwest (`0.50`)**: Lowest floor (Chicago, Minneapolis, Detroit, St. Louis, Omaha, Kansas City).
*   **Northeast (`0.52`)**: (New York, Boston, Philadelphia, Washington D.C.).
*   **West (`0.54`)**: (Los Angeles, San Francisco, Phoenix, Seattle, Portland, Las Vegas).
*   **South, Florida, Gulf, Mountain (`0.70`)**: Strictest conviction floors due to higher summer storm volatility (Atlanta, Miami, Houston, Austin, Denver, Salt Lake City).
