# Glossary: Weather Trading & SRE Terms

This glossary defines the meteorological, quantitative trading, and Site Reliability Engineering (SRE) terms used throughout this project.

---

## 1. Meteorological Concepts

*   **GFS (Global Forecast System)**: A numerical weather prediction model run by the US National Oceanic and Atmospheric Administration (NOAA). Updated 4 times daily.
*   **ECMWF (European Centre for Medium-Range Weather Forecasts)**: A European numerical weather prediction model, widely regarded as the most accurate global weather model.
*   **METAR (Meteorological Aerodrome Report)**: A standardized format for reporting weather observations, typically issued hourly by airport weather stations. Used by Kalshi for contract settlement.
*   **ASOS (Automated Surface Observing System)**: A joint government network of automated sensors at airports, which generates the official METAR observation reports used to settle Kalshi weather contracts.
*   **NCEP AIGFS (`aigefs` internal key)**: NOAA/NCEP's machine-learning weather forecast used by this bot as a volatility ($\sigma$) consensus filter. The old GraphCast label and `gfs_graphcast025` provider identifier are retired.
*   **Shortwave Radiation (SSRD)**: A metric measuring solar energy reaching the surface. Used by the bot's cloud veto logic to verify if cloud cover will affect temperature spikes.

---

## 2. Quantitative SRE Concepts

*   **Overround**: The sum of YES and NO contract implied probabilities minus $1.0$. Represents the market friction or house edge ($YES_{\text{price}} + NO_{\text{price}} - 1.0$).
*   **Parity Gap**: The absolute difference between YES and NO implied prices and $1.00$. If YES + NO deviates significantly from $1.00$, the book is inconsistent.
*   **Thermodynamic Netting**: A regional risk management rule. It assigns positive or negative signs to positions based on weather direction (Cool/Wet vs Warm/Dry) and nets them before evaluating exposure caps.
*   **Regional Hub**: A cluster of meteorologically correlated stations (e.g., Northeast, Midwest, Florida) used to evaluate covariance netting.
*   **Kelly Criterion**: A mathematical formula used to determine optimal trade size based on expected edge and odds, maximizing logarithmic bankroll growth.
*   **Sovereign SRE Ceilings**: Enforced limits including the `$10.00` base position rail, `15`-contract maximum, `12%` fee-inclusive Kelly cap, and `8%` aggregate event-family cap.
*   **Brier Score**: A statistical score used to measure the accuracy of probability predictions. It is the mean squared error of predicted probabilities versus actual binary outcomes (0 to 1, where 0 is perfect).
*   **Orphan Position**: An active open position recorded in `forecast_positions` that has no matching execution log in the `trades` database table.
*   **Sovereign Salvage**: A risk exit system that closes a position immediately if the updated weather models indicate the bet's probability has dropped below $15\%$.
