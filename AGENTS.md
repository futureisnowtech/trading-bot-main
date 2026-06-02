# AGENTS.md — Algo Trading System Knowledge Base
# Auto-loaded by Codex at the start of every session.
# This file is the canonical repo memory.
# When you change runtime truth, update this file and append CHANGELOG.md.

## Canonical Truth

- Repo root: `/Users/joshmacbookair2020/Projects/algo_trading_final`
- Canonical version: `v19.7.0` (`2026-06-02`)
- Canonical active lane: **Dual-Lane**: Coinbase Spot Scalp + Kalshi Weather Sovereign Precision
- **Status:** **SOVEREIGN PRECISION**. Market Truth Veto & Forced Salvage.
- **Critical Changes (v19.7.0):**
  - **Horizon Pullback**: Enforced a strict 48-hour resolution window for all trades (rolling back the 7-day experiment). This eliminates long-range atmospheric chaos and focuses capital on imminent "Sure Thing" events.
  - **Market Truth Veto**: Implemented a 30% cap on Model-Market Divergence. If our ensemble disagrees with the market price by >30%, the bot assumes model error/staleness and vetos the trade.
  - **High-Alpha Floor**: Increased the minimum edge floor from 8% to 20% (Net of fees). The bot now only swings at "Grand Slam" opportunities with a massive margin of safety.
  - **Hardened Salvage**: Refactored the broker to use Market Orders for all "Sovereign Salvage" exits, bypassing limit-order deadlocks and ensuring immediate capital rotation out of toxic positions.
- **Critical Changes (v19.6.0):**
  - **Salvage Unblocking**: Refactored `forecast/runner.py` to move Sovereign Salvage and Take-Profit triggers BEFORE the capital guard. This prevents the bot from "freezing" when fully deployed, allowing it to free up capital by purging toxic trades.
  - **Buffer Tuning**: Relaxed the Oxygen Buffer from 20% to 10% (MAX_DEPLOYED_PCT = 0.90) per user request, increasing trading capacity for smaller balances.
- **Critical Changes (v19.5.1):**
  - **The Junk Veto ($0.15 Floor)**: Strictly forbidden to buy weather contracts priced below $0.15, eliminating "Penny Longshot" gambling.
  - **Sovereign Chaos Veto (Sigma > 3.0)**: Automatically rejects trades if model disagreement (Sigma) exceeds 3.0F, ensuring entries are grounded in stable atmospheric regimes.
  - **Fee-Aware Intelligence**: Implemented "Net EV" logic that subtracts the $0.07 Kalshi fee before calculating edge. Vetoes trades where fees consume >30% of projected gain.
  - **Boundary Controls**: Implemented a 200-contract global quantity cap and a 20% spread-to-price ratio gate to prevent oversized entries and illiquidity traps.
  - **Oxygen Buffer**: Reduced max capital deployment to 80% of balance, ensuring a 20% mandatory cash reserve for high-alpha opportunities.
- **Critical Changes (v19.4.0):**
  - **Dynamic Hub Scaling**: Replaced dollar-based hub caps with a balance-aware logic (Cap = 20% of Equity). This addresses the "Concentration Paradox" as the system scales to 31 cities.
  - **Logical Exclusivity (One-Strike Rule)**: Hardened `check_strike_consistency` to strictly allow only one active strike per city per side (YES/NO), preventing redundant "Bracket Overlap" risk.
  - **Forensic DB Pruning**: Implemented a 6-hour automated maintenance task in `forecast/runner.py`. Tightened retention to 7 days for quotes and 30 days for bars to preserve HUD dashboard performance.
- **Critical Changes (v19.3.0):**
  - **Sovereign Expansion**: Doubled the weather trading universe to 31 US cities, including Charleston, SC (KCHS), providing a massive increase in alpha surface area.
  - **Regional Hub Rebalancing**: Rearchitected city-level gating into 7 "Sovereign Regional Hubs" (MIDWEST, NORTHEAST, SOUTH, FLORIDA, GULF, MOUNTAIN, WEST) with a strictly enforced $60 USD cap per hub to prevent correlation washouts.
- **Critical Changes (v19.2.0):**
  - **Grand Ensemble v19.2**: Integrated AI-GraphCast models via Open-Meteo, moving from 82 to 113+ members. Implemented a 40/30/30 weighted blend (GFS/ECMWF/AI) with catastrophic divergence vetos.
  - **Strike Consistency**: Deployed a logical consistency layer that prevents contradictory weather bets (e.g. betting YES on >85 and YES on <80 for the same event).
  - **Sovereign Salvage & Take-Profit**: Implemented aggressive capital rotation. The bot now purges "toxic" positions (<15% win prob) and locks in 70% of max potential gain to free up slots for higher-alpha plays.
- **Critical Changes (v19.1.12):**
  - **Sovereign Sizing (v2)**: Hard-tightened Kalshi weather exposure to a 10% bankroll cap per position. Decoupled conviction multipliers (Convergence/Sigma) from probability space, applying them as linear scalers to final USD size to prevent "suicide sizing."
  - **Opportunistic Swaps**: Replaced the hard 15-position concurrency gate with a dynamic "Swap Alpha" layer. The bot now evaluates new trades continuously and will automatically flatten its weakest existing bet if a new candidate offers >10% EV improvement (Swap Alpha > 0.10).
  - **Sovereign Mobile HUD**: Rearchitected the Telegram bot into a stateful, interactive terminal. Added the `/hud` command for real-time mobile insights into Kalshi portfolio state, regional hub exposure, and "Sovereign Philosophy" deep-dives.
  - **Strict Hub Gating**: Hardened regional weather hub enforcement with a strictly enforced $40.0 USD cap per hub, preventing over-concentration in specific city clusters.
- **Critical Changes (v19.1.11):**
  - **Institutional Alpha Levers**: Finalized the Sovereign Weather Engine with advanced quant-desk tactics:
    - **The Sigma Lever**: Implemented dynamic position sizing based on ensemble standard deviation (Sigma). Stable models (low Sigma) trigger larger bets, while chaotic spreads trigger size reduction.
    - **Fee-Alpha Floor**: Added an automated veto for contracts priced below $0.15 unless the mathematical edge exceeds 40%, preventing fee erosion.
    - **Midnight Spike Guard**: Hardened the exit engine with an 8 PM local trigger that dumps 90c+ "locked" contracts if high-res HRRR models predict a late-night spoiler event.
  - **Enhanced Sovereign Intelligence**: Exposed Sigma and Volatility multipliers in the HUD dashboard for full transparency into the bot's sizing decisions.
- **Critical Changes (v19.1.10):**
  - **Sovereign Weather Alpha Blueprint**: Transformed the weather lane from a "Hammer" to a "Scalpel" by integrating a 3-phase execution engine:
    - **ECMWF Consensus (Phase 1)**: Integrated 51-member ECMWF ensembles alongside GFS (60/40 weighted blend) for high-conviction entries.
    - **HRRR Intraday Pivot (Phase 2)**: Leveraged 3km High-Resolution Rapid Refresh (HRRR) models for intraday risk assessment and "Salvage Exits" when global models diverge.
    - **METAR Precision Ground Truth (Phase 3)**: Implemented real-time METAR airport observation polling with 0.1°C T-group parsing for "Bust Exits" and "Precision Locks."
  - **Sovereign Instrumentation**: Added dedicated Prometheus gauges for ensemble probability, METAR discrepancies, and HRRR trends to ensure 100% observability of the weather alpha.
  - **HUD Dashboard Enrichment**: Upgraded the Forecast dashboard with a "Sovereign Intelligence" section, exposing the raw model data for all open positions.
  - **Sovereign Recon (v19.1.10)**: Implemented self-healing broker reconciliation in the position monitor, allowing the bot to "adopt" manual trades or clean up DB drift automatically.
- **Critical Changes (v19.1.6):**
  - **High-Velocity Weather Expansion**: Horizontally expanded the weather lane to 15+ US cities (Austin, Phoenix, Seattle, etc.) covering both HIGH and LOW temperature series.
  - **Derivative Alpha Integration**: Introduced a new strategy for Precipitation (`KXRAIN...`) markets utilizing GFS ensemble probabilities.
  - **The Expiration Guillotine**: Enforced a ruthless 72-hour maximum resolution window (`MAX_DAYS_TO_RESOLUTION = 3.0`) for all Forecast markets to eliminate slow-moving political/macro noise and prioritize daily cash flow.
  - **Parallelized Harvester**: Optimized the QuoteHarvester with a throttled ThreadPoolExecutor (4 workers) to ensure 100+ contracts are polled within the 120s freshness window without CPU starvation.
  - **Aggressive Weather Caching**: Implemented a 6-hour coordinate-based cache for Open-Meteo ensemble data to eliminate 429 rate limit death spirals.
- **Critical Changes (v19.1.4):**
- **Critical Changes (v19.1):**
  - **Ledgerless Architecture**: Retired the `open_positions` table as an authoritative ledger. The system now projects truth directly from broker holdings via `execution/coinbase_spot_broker.py`.
  - **Unified Entry Point**: Consolidated all launch paths into a single `main.py`. Legacy scripts (`go_live.py`, `check_readiness.py`, etc.) have been purged.
  - **Hardened Data Layer**: Rewrote the `dashboard/data/` layer to be strictly broker-first, eliminating staleness and reconciliation drift.
  - **Paper Excision**: Fully removed all paper-trading logic and stubs. The system is strictly live-only.
- **Critical Changes (v18.35):**
  - **Strategic Optimization**: Reduced `SPOT_MIN_ORDER_USD` to $5.0. Lowered regime floors to 40.0 (entry) and 35.0 (exit) to unlock volume on small accounts.
  - **Floor-Aware Scaling**: Implemented logic in `v10_runner.py` to "bump" high-quality setups to the $5.0 minimum instead of skipping them.
  - **Enriched Alerts**: Updated `log_alerter.py` with an **Insight Layer** that translates terse Python errors (like `limit_order_rejected`) into strategic human-readable explanations in Telegram.
  - **Weather Pivot**: Replaced generic macro forecasting with a dedicated Weather Prediction Engine utilizing 31-member GFS ensembles (via Open-Meteo).
- **Critical Changes (v18.34):**
  - **Forensic Milestone**: Restored Telegram responsiveness and implemented high-fidelity token telemetry.
  - **Sovereign Separation**: Physically isolated Crypto and Forecast lanes into independent processes/containers.
  - **Truth Harmonization**: Formally promoted Kalshi Forecast to Authoritative Live status.
- **Canonical launch path:** `python3 main.py`
- **Canonical guarded deploy path:** local `./deploy.sh`
- **Canonical memory order:**
  1. `AGENTS.md`
  2. repo code and proof tests
  3. `GEMINI.md` as a concise companion, not the primary source of truth

## What This System Is Now

This repository still contains multiple strategy lanes and historical infrastructure, but operationally it is governed as:

- **Authoritative live lanes:** 
  - **Coinbase spot scalp** (Two-Tower Technical + Local ML)
  - **Kalshi Weather Engine** (31-member GFS Ensembles)
- **Active AI:** **Gemini Studio** (CLI intelligence/DB queries) and **Sovereign Mobile Gemini** (Telegram agent).
- **Current live decision standard:** ledgerless, broker-first, fee-aware, route-aware, evidence-gated.
- **Current launch target:** live-only
- **Current dashboard / readiness authority:** the spot truth-lane contract (HUD Dashboard)
- **Incident Response:** Grafana IRM (pushed via `monitoring/irm_reporter.py`) with OnCall escalation.

## Sovereign Mobile Operator
The Telegram bot acts as a mobile terminal for the Gemini agent. It is authorized to:
- Read and Edit codebase files (`read_file`, `replace`).
- Query live exposure and trade history via SQL (`execute_sql`).
- Execute safe diagnostic commands (`ls`, `git status`, `py_compile`).
- View real-time logs and system vitals.

**Operational Mandates for AI Agent:**
- **Dashboard Truth:** Strictly refer to the operator dashboard as the **HUD dash**.
- **Monitoring Truth:** The primary metrics and alerting surface is **Grafana** (Grafana IRM for incidents).
- **Lane Awareness:** You are a Dual-Lane agent (Coinbase Spot + Kalshi Weather).

## Purged Systems (v19.1.4)
The following systems have been **purged** from the codebase:
- Legacy scripts: `go_live.py`, `check_readiness.py`, `nightly_recon.py`, `diagnose_drift.py`, `coinbase_launch_validator.py`, `ironclad_acceptance_test.py`, `acceptance_test_spot_pipeline.py`, `promote_perp_live.py`, `migrate_v10.py`, `migrate_clean_start.py`, `funding_carry_audit.py`, `purge_phantom_trades.py`.
- Obsolte docs: `brain/` directory, `brain_constitution.md`, `brain_execution_os.md`, `DEPLOYMENT_STATE_MACHINE.md`, `SOP.html`, `refresh_sop.py`.
- Paper trading logic and `--mode` flags.
- IBKR / MES archived futures (dormant).
- ForecastEx archived lane (dormant).
- Legacy Streamlit dashboard (`dashboard/app.py`).

## Spot Truth-Lane Contract

v19.1.ARCH excised `runtime/spot_position_truth.py`. Truth is now projected **directly from broker holdings** via `execution/coinbase_spot_broker.py`.

Broker truth decides:
- whether a spot holding exists
- current quantity
- current deployed notional
- current broker cash

Database truth enriches:
- lineage
- setup family
- setup score
- route
- stop / target profile

## Hard Safety Principles

- no brain/ or docs/ reliance (AGENTS.md is the only repo memory)
- no broad rewrite
- no new signal bloat
- no fake readiness claims
- no paper mode logic
- no live launch outside `main.py`
- no automatic resume after `HALTED`
- no hiding live holdings because the DB is confused

## Key Files For The Active Lane

| File | Role |
|---|---|
| `execution/coinbase_spot_broker.py` | canonical broker-first spot truth |
| `spot_engine.py` | spot execution lifecycle, stop/target persistence |
| `runtime/spot_strategy.py` | setup / regime / score / route governance |
| `scheduler/v10_runner.py` | scan loop, admission path, runtime lane state |
| `main.py` | unified system entry point |
| `dashboard/api/server.py` | authoritative **HUD dash** API (Ledgerless v19.1) |
| `monitoring/log_alerter.py` | Telegram alert watchdog with Insight Layer |
| `execution/kalshi_broker.py` | weather lane execution |

## Operator Commands

```bash
python3 main.py
python3 -m pytest
```

## Change Discipline

When behavior changes:
- update `AGENTS.md`
- update `GEMINI.md` if Gemini workflow guidance changed
- append `CHANGELOG.md`
- prefer targeted proof tests in `tests/proof/`
