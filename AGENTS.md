# AGENTS.md — Algo Trading System Knowledge Base
# Auto-loaded by Codex at the start of every session.
# This file is the canonical repo memory.
# When you change runtime truth, update this file and append CHANGELOG.md.

## Canonical Truth

- Repo root: `/Users/joshmacbookair2020/Projects/algo_trading_final`
- Canonical version: `v19.1.6` (`2026-05-29`)
- Canonical active lane: **Dual-Lane**: Coinbase Spot Scalp + Kalshi Weather Expansion
- **Status:** **LEDGERLESS SOVEREIGN**. Autonomous Self-Healing & Broker-First Truth.
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
