# CHANGELOG

## 2026-06-06
- Hardened the release gate itself so remote hosted audits now emit machine-clean JSON, the outer SSH-based remote audit tolerates mixed stdout safely, and deploys seed a same-SHA provisional `release_audit_pending_new_build` artifact before `execution-engine` starts to prevent stale release-truth drift during startup.
- Fixed the deploy-time seeding path to write the provisional release artifact through a one-shot container against the mounted project volume, avoiding host-permission drift on root-owned `logs/` files.
- Fixed the provisional-artifact seeding command to attach stdin into the one-shot container (`docker run -i`), ensuring the pending-release payload is actually written before the new engine boots.
- Replaced the weather lane's flat `$0.07` pre-trade fee assumption with a shared exchange-derived Kalshi fee model, so strategy EV, sizing, affordability, exposure, exit-edge math, cockpit truth, and broker fee fallback now all use the same live economics.

## 2026-06-04
- Promoted the weather RBI loop from passive scorekeeping into a bounded adaptive learner that writes live GFS/ECMWF blend weights with recency decay, sample-size shrinkage, and runtime cooldown protection.
- Made weather entries and exits probability-coherent by routing both through the same adaptive ensemble blend, including a catastrophic-divergence neutralization path for held-position exit logic.
- Replaced the hard non-catastrophic GFS/ECMWF divergence veto with a bounded confidence-and-size penalty so disagreement reduces aggression before it fully kills a trade.
- Added shortwave-radiation support to the weather ingest path and upgraded the HIGH-temperature cloud veto to require weak solar heating, not just raw cloud cover.
- Surfaced live adaptive-learning state into broker truth, Telegram audit, cockpit cards/funnel/insights, and Oracle tool context so operator explanations now match the math the engine is actually using.
- Made runtime state storage-safe on constrained machines by adding an env-driven runtime root (`ALGO_RUNTIME_DIR` / `DB_PATH` family), low-disk headroom checks for health/preflight and execution entrypoints, and path unification across runtime DB/log consumers.
- Extended quote/bar retention pruning into the lean one-pass daemon path so `execution_daemon.py` no longer relies on the legacy scheduled loop to bound local SQLite growth.
- Restored broker-truth cost basis handling for live Kalshi fills by hydrating executed orders for actual fill price and fees, and by deriving synced position entry prices from Kalshi `total_traded_dollars / position_fp`.
- Fixed live Kalshi entry execution by converting taker-override "market" intents into legal marketable limit orders with hard `buy_max_cost` caps, surfacing broker rate-limit codes back to the runner, and syncing broker positions before strategy/monitor passes.
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
