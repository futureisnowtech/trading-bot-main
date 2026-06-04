"""
forecast — Kalshi weather market trading lane.

Architecture:
  db.py              — SQLite schema for markets/contracts/quotes/bars/resolutions
  primitives.py      — log-odds math primitives (x_t, v_t, a_t, σ_t, H_t, Ω_t, G_t, z_t)
  discovery.py       — Kalshi market discovery and market-stub persistence
  quote_harvester.py — quote polling + bar aggregation (5m/30m/1h/4h/1d)
  strategy_engine.py — weather strategy logic + economics gate + sizing
  runner.py          — cycle helpers used by the lean execution runtime

Execution: execution/kalshi_broker.py (Kalshi YES/NO contracts via REST).
"""
