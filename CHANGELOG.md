# CHANGELOG

## 2026-06-04
- Fixed a live trade blocker in `forecast.strategy_engine._hours_to_resolution()`: ISO Kalshi expiry timestamps were being parsed as `0.0` hours remaining, falsely triggering `RESOLUTION_HORIZON_TOO_SHORT` on otherwise valid weather setups.
- Fixed the Telegram Oracle Gemini handshake by removing the retired hardcoded `gemini-2.0-flash` model and routing Oracle requests through the repo-configured stable Gemini model.
- Added `scripts/gate_audit.py` plus proof coverage so market-reality vetoes and shadow-passed entries can be audited in one command.
- Replaced the production `execution-engine` shell loop with a real `execution_daemon.py` process so weather shadow state survives across trading cycles.
- Added one-shot weather cold-start hydration in `forecast.runner.run_execution_cycle()` so a restart cannot fail closed on `missing_weather_data` before the monitor warms up.
- Made `data/kalshi_weather_monitor.py` idempotent and concurrency-safe enough for live daemon startup, including targeted series refresh for active Kalshi weather contracts.
- Split the active repository into a Kalshi-only execution tree and removed crypto, spot, stocks, futures, research, and legacy dashboard surfaces from the active path.
- Preserved the lean live runtime centered on `sniper_cron.py`, `telegram_daemon.py`, `forecast.runner.run_execution_cycle()`, and `execution/kalshi_broker.py`.
- Hardened weather imports so proof collection is not sensitive to `sys.path` collisions between top-level `data/` and dashboard helper packages.
- Repaired quote-bar writes and added one-shot quote refresh support in `forecast/quote_harvester.py`.
- Replaced heuristic weather sizing with fee-adjusted binary-market sizing in `forecast/strategy_engine.py`.
- Added conservative settlement ingestion in `forecast/resolution_sync.py` so Weather RBI learns only from explicit HIGH/LOW ground truth.
- Decoupled Telegram `/audit` from the local dashboard service by reading runtime truth directly from SQLite.
- Added minimal Kalshi runtime shims for `runtime.economics`, `learning.signal_performance`, `learning_loop`, and `scheduler.v10_runner`.
- Narrowed validator, hook installer, and dependency manifests to the Kalshi-only runtime.
- Retargeted the active proof gate to the Kalshi bundle instead of the archived full-suite tree.

## 2026-06-03
- Added `sniper_cron.py`, `telegram_daemon.py`, and the single-pass `forecast.runner.run_execution_cycle()` path for lean dual-process deployment.
- Tightened weather sizing and truth handling around fee-aware EV and resolved-label RBI calibration.
