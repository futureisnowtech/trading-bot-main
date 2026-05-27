"""
forecast — ForecastEx event-contract trading lane (v15.0)

Architecture:
  db.py            — 5-table SQLite schema (markets/contracts/quotes/bars/resolutions)
  primitives.py    — log-odds math primitives (x_t, v_t, a_t, σ_t, H_t, Ω_t, G_t, z_t)
  discovery.py     — ForecastEx market/contract discovery (economic markets only)
  quote_harvester.py — real-time quote polling + bar aggregation (5m/30m/1h/4h/1d)
  strategy_engine.py — 3 strategy families + economics gate + fractional Kelly sizing
  runner.py        — scheduler loop (discovery/harvest/eval/monitor)

Execution: execution/forecastex_broker.py (IBKR OPT/FORECASTX, Right=C/P for YES/NO)

Non-negotiable v1 constraints:
  - ForecastEx only (FORECASTX exchange, zero commission)
  - Economic markets only: Fed/rates, CPI, employment, payrolls
  - Bankroll ~$100; max deployed 35%; max per-event 10%; max concurrent 2
  - Fractional Kelly cap 0.10
  - Pricing substrate: bid/ask/midpoint — never last/trade prints
  - Cannot short; flatten YES by buying NO and vice versa
"""
