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
