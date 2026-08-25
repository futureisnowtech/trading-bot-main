# CHANGELOG

## 2026-08-25 (v19.20.0 candidate)
- Superseded the v19.19 commercial-ensemble/ICON candidate with a keyless deterministic GFS/ECMWF/AIGFS production path plus METAR and near-term HRRR; stale ICON payloads are purged and missing GFS fails closed rather than relabeling another provider.
- Moved bounded meteorological adjustments into forecast-variable space before the contract CDF: daily highs receive radiative/precipitation cooling, daily lows receive cloud/moisture/wind-mixing lift, and unrelated hourly/rain/snow/wind probabilities receive no temperature nudge. The method is labeled `bounded_heuristic_v1`; operational wiring is proven, but incremental forecast skill remains pending new-epoch outcomes.
- Made projected deterministic forecast-error sigma and nested HRRR data reach pricing, retired unsafe live-call isotonic refits, and removed the stack-inspection test-only probability branch so proofs execute production math.
- Wired convergence, divergence, predictive-sigma, AIGFS, and same-event multipliers into taker-only IOC quantity; enforced fee-inclusive Kelly and aggregate event-family caps; made covariance errors fail closed; and fixed covariance/hub checks reading a nonexistent quote key.
- Renamed new strategy evidence to `weather_physics`, started RBI epoch `v19.20.0-deterministic-physics-path`, restored the swallowed `sqlite3` import for dynamic configuration, and synchronized operator/deploy/probability-path truth. Deployed NYC remains v19.18.0 until an explicitly authorized deployment.
- Made Kalshi V2 IOC fills authoritative: flat partial-fill responses are booked, canceled partial fills are retained, unfilled remainders never become local resting orders, unexpected resting orders are canceled and confirmed, and stale/uncertain broker position snapshots block entries.
- Re-ran live quotes immediately before every POST; added slippage, spread, depth, buying-power, fee-inclusive position/Kelly/event, sequential regional-hub, and single-pass covariance enforcement; corrected held-side NO basis/exposure/P&L throughout reconciliation and cockpit truth.
- Made official Kalshi settlement sync the only RBI outcome authority, replayed the exact production log-odds/HRRR blend in whole-event chronological validation, and weighted independent events equally so sibling strike count cannot dominate promotion.
- Removed the disconnected legacy learner, dead maker controls, unsafe live-trading audit, autonomous leak-forensics writer, and unregistered hot-patch surface. Dynamic overrides now accept only Kelly fraction and versioned hub conviction floors actually consumed by production.

## 2026-08-24 (v19.19.0 candidate)
- Activated the previously dead `RBI_MIN_DAYS` control as a hard seven-day learning gate. RBI evidence is now tagged with a versioned probability-path epoch, older-path samples are excluded, challenger creation requires at least seven days plus 24 officially settled current-epoch samples, promotion independently rechecks those conditions, and operator truth reports learning progress while the live blend remains on the governed 60/40 baseline.
- Restored the selected legacy convergence guardrail on the real production-pricing branch and extended the same safety rule to ICON: 1.5x only for unanimous same-tail physical-model agreement, soft q_hat/size penalties above a 20-point maximum gap, and a hard veto above 70 points.
- Activated the full fetched weather path: fixed ICON provider keying, parsed all 40 ICON members, preserved ICON through contract projection, reserved a bounded 15% blend share, and corrected the AI uncertainty scaler to compare the actual NCEP AIGFS forecast with the physical ensemble instead of comparing the ensemble with the contract strike. The retired `gfs_graphcast025` request returned HTTP 200 with null data; the provider now uses the supported `ncep_aigfs025` model and correct GFS endpoint. Missing models renormalize away.
- Hardened Open-Meteo parsing against null hours while preserving time indices, so delayed six-hourly AIGFS values and partial model runs cannot crash hydration or shift a forecast onto the wrong settlement hour.
- Fixed promoted RBI weights silently falling back to 60/40 because `forecast/pricing_engine.py` referenced `DB_PATH` without importing it.
- Removed the dead Cheat Code / Goldmine helper block. Its insertion had split `_probability_from_estimate()` and stranded normal threshold probability logic after an unconditional return; the greater-than/less-than deterministic probability path is restored.
- Reconciled version-controlled risk defaults, `.env.example`, CI, and protected-deploy validation with the effective NYC live posture: 90% deployed cap, 20 concurrent positions, five per event family, `max($20, 40% of cash)` regional cap, 15-contract and $10 base position ceilings, quarter Kelly, and a 34-cent entry floor. The live values `KALSHI_KELLY_CAP=0.12` and `KALSHI_MAX_RISK_PER_EVENT_PCT=0.08` are now recorded accurately as declared-but-unenforced knobs rather than misrepresented as hard rails.
- Promoted NYC's previously untracked 27-city `CITY_BLACKLIST` into a versioned source fallback, leaving CHI, DEN, LAX, OKC, and SAT enabled, while preserving the environment variable as an explicit emergency override.
- Recorded the pre-repair architecture discrepancy that production priced GFS+ECMWF with optional near-term HRRR while fetched ICON and the old AI lane were unwired; the bullets above supersede that state for the candidate source revision.

## 2026-08-21
- Fixed `CITY_BLACKLIST` being silently inert: the entry gate compared blacklist entries against `_get_city_hub()`, which returns a macro-region (`WEST`, `MIDWEST`), so a station-key entry like `PHX` matched nothing, and the ticker fallback looked for `KXHIGHPHX-` / `KXLOWPHX-` while live daily tickers are `KXHIGHTPHX-` / `KXLOWTMIN-`. `_blacklisted_city_code()` now resolves the canonical station key first (so every series alias for a city is covered), still honors hub-level entries, and falls back to a series-segment suffix match.
- Added an import-time report of the active blacklist plus a warning for entries that match no known station key or hub, so a typo can no longer disarm the gate unnoticed.
- Reinstated the then-audited blacklist on NYC production as `CITY_BLACKLIST=PHX,MSP`; the later 2026-08-24 policy revision above records the current 27-city live value.
- Pinned the gate in the proof suite (`tests/proof/test_city_blacklist.py`): every ticker form of a blacklisted city is blocked, non-blacklisted cities stay tradeable, hub-level entries still work, and the veto reason names the matched city.

## 2026-08-12 (v19.18.0)
- Fixed the release gate checking every weather contract against the legacy 90-minute freshness fallback, so hourly contracts were held to the daily bar: `runtime.operator_truth.get_release_status` now resolves the SPEC §4.5 window per contract type via the new `forecast.weather_contracts.weather_freshness_limit_minutes`, which the strategy-engine entry veto also routes through so the two surfaces cannot drift apart.
- Taught `get_weather_provider_status` to check every sampled weather series against its own freshness window instead of stopping at the first series with data, reporting `series_freshness` and a worst-breach-first `stale_series`.
- Scoped the gate's staleness verdict to systemic failure: only an entirely stale sample closes the global gate (`stale_ensemble_data`), while partial staleness reports `partial_stale_ensemble_data` as a warning and leaves fresh lanes tradable — the per-contract entry veto is what keeps a stale contract itself untradable.
- Fixed the ensemble refresh cadence being derived from the wide daily window (75 minutes), which left hourly weather data stale for roughly two-thirds of every cycle and made hourly contracts effectively untradable. `WEATHER_REFRESH_TARGET_SEC` is now derived from the tightest (hourly) window minus a fetch margin, giving a 20-minute cycle; cached state still spans the 90-minute daily window.
- Pinned both invariants in the proof gate: refresh cadence must fit inside the hourly freshness window, the per-coordinate fetch cache must not outlive the loop that drives it, and the module-level fallback constants must match the config-derived values.
- Corrected the parameter catalogs, which documented `KALSHI_DATA_FRESHNESS_MINUTES` as a confirmed `180` (3 hours) long after the value became per-contract-type.

## 2026-06-08
- Enabled and fully parameterized Snow and Wind weather trading functionality across the core engine and visual cockpit.
- Upgraded the Open-Meteo data ingestion module (`data/kalshi_weather_monitor.py`) to fetch `wind_speed_10m` data from deterministic and ensemble weather models, constructing and propagating the live `members_wind` and `hourly_members_wind` arrays.
- Implemented `_resolve_generic_prefix_city_key` to dynamically resolve city prefixes for wind (`KXWIND`) and snow (`KXSNOW`) contracts using existing ASOS station suffix aliases, bypassing static list limitations.
- Redesigned the generic 4-step decision funnel into a 5-lane visual "Execution Pipelines" matrix on the cockpit dashboard to map specific mathematical thresholds, spreads, and AI veto rules side-by-side across Hourly Temps, Rain, Snow, Wind, and Daily Temps.
- Fixed a cockpit data discrepancy where the Realized P&L box was calculating rolling totals from the last 25 recent trades only. Replaced it with a database-backed total (`total_won + total_lost`) to display the true, complete session P&L.
- Extended the Trade Curve visualization and CSV data export to use a direct, database-backed query (`load_session_pnl_curve`) reflecting all closed non-zero trades since session start, resolving missing negative/historical trades.

## 2026-06-06
- Replaced the cockpit's raw open-book dump with a live visual trade board that adds fee-aware exposure rollups, position cards, a normalized heat map, and an expiry-pressure chart while keeping the broker-truth table as a fallback tab.
- Added a host-written `host_service_status.json` artifact during deploy and taught the in-container hosted release audit to trust it only when the artifact is fresh and SHA-matched, eliminating the false container-mode blind spot around service-up verification.
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
