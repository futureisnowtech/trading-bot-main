# AGENTS.md — Kalshi Weather Engine Repo Memory

This repository is now the active Kalshi-only execution tree.

## Canonical Truth

- Repo root: `/Users/joshmacbookair2020/Projects/algo_trading_final`
- Canonical lane: `forecast`
- Runtime model: lean dual process
- Trading mode: live-only Kalshi weather execution
- Fresh-entry scope: strict true hourly weather contracts only
- Exposure truth: broker-first, ledgerless, fee-aware
- Settlement truth: `forecast_resolutions`
- Learning truth: Weather RBI calibrates only on resolved labels, never inferred PnL

## MANDATE: REAL KALSHI RECORDS INVARIANT
1. **LIVE KALSHI REST API IS CANONICAL**: When auditing real Kalshi performance, always query the live REST API (`/trade-api/v2/portfolio/settlements` and `/trade-api/v2/portfolio/positions`) directly via `KalshiBroker`. Never rely on partial local DB snapshots or static arrays. Settlements — not fills — carry realized PnL; `KalshiBroker` has no fills accessor.
2. **DATE BOUNDARY**: Scope live execution metrics to `POST_PAPER_START_DATE = "2026-07-23"`.
3. **PAPER LANES AUDIT**: Paper lanes (`forecast_positions_paper`, `forecast_positions_paper_lane_b`) run on the droplet. Do not report local empty fallback tables as non-performance without verifying droplet runtime state.
4. **ONE PnL FORMULA**: Realized PnL comes only from `runtime/kalshi_settlement_truth.settlement_pnl_usd`
   (`winning_contracts − yes_total_cost − no_total_cost − fee`, rounded to cents). Never re-derive it
   inline. Never treat `max(yes_cost, no_cost) − min(yes_cost, no_cost)` as exit proceeds: both fields
   are costs, so that form is non-negative by construction and scores a win on every two-sided row —
   it once reported +$346.11 / 88.3% on an account whose true result was −$6.60 / 61.3%. Never trust
   `revenue` either; the API returns 0 for it on the large majority of live rows.

## MANDATE: SYSTEM UPDATE & COCKPIT SYNC PROTOCOL (J.A.R.V.I.S. OPTIMIZATION)
1. **MANDATORY TELEMETRY SYNC**: Whenever system updates, strategy refactors, risk gate changes, or accounting formula fixes occur, the AI assistant/agent MUST immediately update and verify all Cockpit data layers (`dashboard/cockpit_data.py`, `dashboard/streamlit_app.py`, `runtime/kalshi_settlement_truth.py`), audit tools (`scripts/audit_real_kalshi_records.py`), and WebApp presentation models (`src/lib/resultsData.ts`, `ResultsDashboard.tsx`).
2. **ZERO STALE SURFACES**: No operator cockpit surface or AI audit path may rely on deprecated formulas, legacy mock data, or single-endpoint partial cashflow calculations.
3. **AUTOMATED AUDIT RUNTIME**: After any system modification, run `python3 scripts/audit_real_kalshi_records.py` and verify clean compilation and API sync before declaring completion. To refresh the public ledger, add `--emit-webapp-ts <webapp>/src/lib/resultsData.ts`; it refuses to write unless the headline equals the sum of the emitted rows.



## Active Architecture & Strategy (v19.16.0 Release)

- **Tri-Model Ensemble Ingest**: Blends 122 physical simulation paths (50% US GFS / 35% European ECMWF / 15% German DWD ICON).
- **5-Minute Station Calculus**: Evaluates 1st-order numerical thermal derivatives (dT/dt <= -0.20F/hr) to lock out YES bets post afternoon peak heating.
- **Value Price Bracket Gate ($0.30 - $0.70)**: Restricts entries strictly to high-EV price zone, deleting penny longshots and low-upside bets.
- **$2.0°F Safety Buffer**: Enforces minimum model projection headroom to prevent boundary miss wipeouts.
- **Kalshi Cheat Code Mispricing Arbitrage Scanner**: Scans for contracts where model win probability q_tri >= 78% and model-market delta edge >= 22%.
- **Asymmetric Kelly Conviction Sizing**: Dynamically scales position allocations from $5.00 up to $35.00 per high-conviction trade.
- **Tiered Goldmine City Priority Scanner**: Prioritizes Tier 1 goldmine cities (DC, PHL, ATL, DAL, DFW, LV, LAS, OKC, CHI) first before secondary or noisy micro-climate hubs.
- **Autonomous Leak Forensics**: Auto-applies 48h city firewall lockouts and elevates station conviction floors on any trade loss.

## Active Runtime

- `execution_daemon.py` is the production long-lived execution process.
- `sniper_cron.py` runs one Kalshi execution pass and exits.
- `telegram_daemon.py` runs the Telegram operator/oracle process.
- `forecast/runner.py` exposes `run_execution_cycle()` as the canonical single-pass entrypoint.
- `execution/kalshi_broker.py` is the only active broker adapter in the repo.
- `docker-compose.yml` starts `execution-engine` with the embedded Telegram daemon plus `kalshi-cockpit`.
- `deploy.sh` deploys the lean Kalshi stack to the droplet.

## Scope Boundary

- Active repo scope is Kalshi weather trading only.
- Crypto, spot, stocks, futures, and legacy research surfaces are archived outside the active tree.
- Do not reintroduce non-Kalshi brokers, execution lanes, or proof suites into this repo.

## Safety Principles

- Broker holdings are the only source of truth for live exposure.
- No automatic resume after a halt.
- No inference-based learning labels.
- No broad rewrites of weather execution logic without proof coverage.
- Covariance Netting: Enforced PSD-shrinkage correlation covariance engine with disjoint bracket covariance limit set at (0.08 * B)^2.
- Continuous Kelly Sizing: Log-utility objective routing maker/taker dynamically with ceiled continuous fee model and favorite scaler.

## Key Files

| File | Role |
|---|---|
| `config.py` | Canonical env/config surface |
| `execution/kalshi_broker.py` | Signed REST execution + portfolio sync |
| `forecast/runner.py` | Discovery, quote refresh, strategy eval, monitoring |
| `forecast/strategy_engine.py` | Weather alpha, economics gate, sizing |
| `forecast/resolution_sync.py` | Conservative weather settlement ingestion |
| `data/kalshi_weather_monitor.py` | Ensemble + METAR shadow state |
| `learning/weather_rbi.py` | Post-resolution Brier-based calibration |
| `notifications/telegram_bot.py` | Operator interface |
| `execution_daemon.py` | Long-lived lean execution daemon |
| `sniper_cron.py` | Single-pass execution worker |
| `telegram_daemon.py` | Standalone Telegram daemon |
| `deploy.sh` | Canonical deploy entrypoint |

## Proof Gate

Use the Kalshi proof bundle, not the archived full-suite gate:

```bash
python3 -m pytest \
  tests/proof/test_forecast_lane.py \
  tests/proof/test_resolution_sync.py \
  tests/proof/test_weather_rbi_truth.py \
  tests/proof/test_weather_sovereign.py \
  tests/proof/test_weather_hourly_and_alias_support.py \
  tests/proof/test_lane_gating.py \
  tests/proof/test_trading_control.py \
  tests/proof/test_scheduler_cadence_config.py \
  tests/proof/test_runtime_layer.py \
  -k "forecast or weather or rbi or lane_economics_forecast or forecast_lane"
```

## Operator Commands

```bash
python3 sniper_cron.py
python3 execution_daemon.py
python3 telegram_daemon.py
python3 scripts/verify_kalshi_connection.py
bash scripts/install_hooks.sh
python3 scripts/release_audit.py --local
python3 scripts/release_audit.py --remote
python3 scripts/release_audit.py --promote
python3 scripts/storage_audit.py
python3 scripts/validate.py
```
