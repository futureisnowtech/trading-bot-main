# CLAUDE.md — Algo Trading System Knowledge Base
# Auto-loaded by Claude Code at the start of every session.
# This file IS the system memory. Keep it current.
# When you make changes: update this file AND append to CHANGELOG.md.

## Strategic Brain

The `/brain/` directory is the living strategic intelligence layer.
- Hub: `brain/README.md` — governed by `brain_constitution.md` + `brain_execution_os.md`
- Key notes: `brain/01_current_system/`, `brain/03_parameter_sets/`, `brain/10_decisions/`

## What This System Is

Fully autonomous AI trading system: scans Kraken Futures + Binance USDM + Hyperliquid perps 24/7, scores candidates with a two-tower signal engine (technical 0-100 + ML 0-100 → composite), enforces emotional safeguards, learns from every trade via Bayesian attribution + 57-feature ML, writes all notifications to SQLite, displays on LeBron/DBZ-themed dashboard. Owner is never asked to approve anything.

## Owner Profile
- Mac user (MacBook Air 2020, Python 3.14 at `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3`)
- Paper account: $5,000 (`ACCOUNT_SIZE=5000` — config default, no .env override)
- Wants zero day-to-day intervention. Prefers simple explanations, hates fluff.

## Current Version: v16.12 (2026-04-17)

**Active branch:** `feature/v10-rebuild` | **Clean paper trading started:** 2026-04-02 | **Live trading started:** 2026-04-15

### Live Architecture (source of truth)

| Component | File | Role |
|---|---|---|
| Scanner | `scanner.py` | 3 sources: Kraken Futures + Binance USDM + Hyperliquid, 7-filter, top 50 candidates |
| Signal engine | `signal_engine.py` | Two-tower: technical 0-100 + ML 0-100 → composite |
| Entry runner | `scheduler/v10_runner.py` | Scan loop, tier selection, economics gate, setup detection, execution handoff |
| Position sizing | `position_manager.py` | Kelly + ATR sizing, leverage schedule, deployment caps |
| Exit manager | `position_manager.py` | 6-priority exit stack |
| Perp execution | `perps_engine.py` → `execution/coinbase_broker.py` | Coinbase US nano perp futures; CDP JWT auth; ISOLATED margin; BTC/ETH/SOL/XRP only |
| Spot execution | `spot_engine.py` → `execution/coinbase_spot_broker.py` | Coinbase spot BTC-USD/ETH-USD; no leverage, no shorting; gated by SPOT_LANE_ACTIVE; 40% deployment cap |
| MES execution | `scheduler/v10_runner.py` → `execution/ibkr_broker.py` | IBKR paper port 7497 (ARCHIVED — dormant) |
| ForecastEx broker | `execution/forecastex_broker.py` | IBKR ForecastEx event contracts; SecType=OPT, Exchange=FORECASTX; clientId=3; economic markets only; bid/ask/mid pricing only |
| ForecastEx lane | `forecast/runner.py` | Discovery (30m), quote harvest (60s), strategy eval (5m), position monitor (30s) |
| ForecastEx DB | `forecast/db.py` | 5 tables: forecast_markets, forecast_contracts, forecast_quotes, forecast_bars, forecast_resolutions |
| ForecastEx primitives | `forecast/primitives.py` | Log-odds math: x_t, v_t, a_t, σ_t, H_t, Ω_t, G_t, z_t; compute_q_hat; EV; fractional Kelly |
| ForecastEx strategy | `forecast/strategy_engine.py` | 3 families: continuation, mean_reversion, late_repricing; 10-check economics gate; sizing |
| ForecastEx discovery | `forecast/discovery.py` | Scans IBKR for economic event contracts, ranks, upserts to DB |
| ForecastEx harvester | `forecast/quote_harvester.py` | Polls quotes every 60s; builds 5m/30m/1h/4h/1d OHLC bars from midpoint |
| Runtime truth | `runtime/runtime_state.py` | system_runtime_state + lane_runtime_state tables; process mode, lane health, heartbeats |
| Lane registry | `runtime/lane_registry.py` | Control plane for lane activation; crypto/forecast/mes_archived |
| Incident tracker | `runtime/incident_tracker.py` | Groups repeated errors into fingerprint incidents; filters archived lane noise |
| Position reconciler | `runtime/position_reconciler.py` | Reconciles scale_33_done/scale_66_done vs trade ledger at startup |
| Allocator scaffold | `runtime/allocator.py` | Cross-lane capital allocation substrate (v16.0 stub ranker) |
| Economics interface | `runtime/economics.py` | Per-lane friction: taker fee, round-trip cost, min viable edge |
| Live audit hooks | `scripts/live_runtime_audit.py` + `scripts/lane_status_audit.py` | Post-restart operator-grade pass/fail verification |
| Indicators | `data/indicators.py` (`add_all_indicators()`) | SuperTrend, Ichimoku, WAE, Fisher, CHOP, WaveTrend, Laguerre RSI, etc. |
| ML features | `ml/feature_builder.py` | 57 features across 11 groups (imports `indicators/` package) |
| ML training | `ml/walk_forward_trainer.py` + `ml/model_store.py` | XGBoost 60% + LightGBM 40%, PnL regressor, clean data only |
| Economics gate | `risk/economics_gate.py` | Pre-trade fee/funding EV veto (Coinbase 0.030% taker; round-trip 0.060%) |
| Learning loop | `learning_loop.py` | 57-feature snapshots, retrain queue, RBI trigger |
| Bayesian learning | `learning/post_trade_analyzer.py` + `learning/signal_performance.py` | Per-signal Bayesian win rates |
| Dynamic weights | `learning/dynamic_weights.py` | Live conviction weights, 5-min cache |
| RBI nightly | `rbi/research_loop.py` + `rbi/backtest_loop.py` + `rbi/incubation_manager.py` | Research 575 combos, promote to live at 25% size |
| Candidate journal | `logging_db/trade_logger.py` + `learning/candidate_labeler.py` | 8-gate journaling, 15m/1h/4h outcome labeling, nightly audit |
| Integrity substrate | `logging_db/trade_logger.py` (`log_trade_integrity`, `log_exit_evaluation`) | Durable trust tiers (verified/suspect/quarantined/excluded) per close; exit quality capture |
| Backtesting | `backtesting/event_backtester.py` + `backtesting/run.py` | Live-faithful candidate-replay backtester; RESEARCH-GRADE only |
| Promotion engine | `backtesting/promotion_engine.py` | Challenger state machine; PROMOTED_PENDING_HUMAN requires owner confirmation |
| Config sub-package | `config/venue_specs.py` + `config/alpha_specs.py` | Venue fees + futures-native constants separated from strategy thresholds |
| Dashboard integrity | `dashboard/data/integrity.py` | Truth-tiered metrics: verified/suspect counts, attribution coverage, exit quality, promotion state |
| Notifications | `notifications/notification_engine.py` | SQLite only, no Telegram |
| Dashboard | `dashboard/app.py` | Streamlit Operator Panel, 6 tabs: MISSION CONTROL, PERFORMANCE, TRADE APPROVAL, FORECAST TRADING, ARCHIVED FUTURES (MES), SYSTEM SETTINGS |
| DB | `logs/trades.db` | WAL mode SQLite — positions, trades, system_events, scan_candidates, candidate_outcomes, trade_integrity, exit_evaluations, challenger_state, forecast_markets, forecast_contracts, forecast_quotes, forecast_bars, forecast_resolutions, system_runtime_state, lane_runtime_state, incidents |
| Vector memory | `memory/trade_memory.py` | NumPy cosine similarity, SQLite-backed, 8-dim feature vectors |
| Kill switch | `kill_switch.py` | Balance < 75% of ACCOUNT_SIZE → halt all |
| Risk engine | `risk_engine.py` | VaR/CVaR, correlation gates, margin checks |
| Hedge engine | `hedge_engine.py` | Delta-neutral hedge rebalance (every 5 min) |
| Health check | `monitoring/health_check.py` | 7-invariant assertions written to system_events; check 7 includes IBKR |
| MCP server | `mcp_server/server.py` | 15 FastMCP tools for Claude Code integration |
| Verification | `tests/proof/` + `verification/replay.py` + `.github/workflows/ci.yml` | Proof-first pytest harness, deterministic replay, GitHub Actions CI |

### Key Decisions

- **Scanner sources:** Kraken Futures public REST + Binance USDM public REST + Hyperliquid public API. Live scheduler and manual scan now call `scanner.scan(..., core_only=True)`, so the operational universe is limited to `CORE_EXECUTION_UNDERLYINGS`; broad-universe scans are opt-in research mode only.
- **Live crypto execution venue:** Coinbase US nano perp-style futures (`coinbase_broker.py`). Supported: BTC→BIP-20DEC30-CDE, ETH→ETP-20DEC30-CDE, SOL→SLP-20DEC30-CDE, XRP→XPP-20DEC30-CDE. Any other symbol → `CoinbaseSymbolError` (fail-closed).
- **Coinbase auth:** CDP JWT / ES256. Env vars: `COINBASE_CDP_KEY_NAME` + `COINBASE_CDP_PRIVATE_KEY`. Paper mode = zero API calls, no credentials needed.
- **Coinbase futures API path:** CFTC nano futures use `/api/v3/brokerage/cfm/` (CFM = Coinbase Financial Markets), NOT `/api/v3/brokerage/futures/`. The `/futures/` path returns 401. Both `connect()` and `get_wallet_balance()` in `coinbase_broker.py` use `/cfm/balance_summary`.
- **Coinbase fees:** 0.03% taker, 0.00% maker. Round-trip = 0.06%. These feed `risk/economics_gate.py` and `perps_engine.py` fee logging.
- **No AI debate:** Two-tower signal engine replaces all v9 debate agents
- **Telegram removed:** `notifications/notification_engine.py` — SQLite + dashboard only
- **Paper = live thresholds:** No reduced thresholds in paper mode (clean data from 2026-04-02)
- **ML training data:** Tagged `pre_v10_contaminated` for all data before 2026-04-02
- **57 features:** 8 price + 6 volume + 5 CVD + 7 momentum + 4 VWAP + 5 OB + 6 deriv + 3 liq + 5 regime + 4 time + 4 onchain
- **Kill switch:** balance < 75% of ACCOUNT_SIZE (= $3,750 on $5K), not hardcoded
- **Live position sizing path:** `v10_runner.py` → `position_manager.compute_position_size()`; `risk/unified_sizer.py` is NOT on the live entry path
- **ISOLATED margin** on all perp positions — never CROSS
- **ML model:** PnL regressor (XGBRegressor + LGBMRegressor). Score = `50 + 50*tanh(predicted_pnl / pnl_scale)`. Falls back to 50.0 if no pickle files exist.
- **MES daily loss limit:** reads `FUTURES_DAILY_MAX_LOSS_PTS * FUTURES_NUM_CONTRACTS * MES_POINT_VALUE` (MES_POINT_VALUE=5.00). Never hardcode $150.
- **Integrity tiers (v14.0):** every perp close writes to `trade_integrity` table (verified/suspect/quarantined/excluded). Bayesian/Kelly/ML consumers must not use quarantined or excluded rows. Gate: `is_integrity_trusted(close_order_id)`.
- **Replay/synthetic sources:** any `source` containing `"replay"`, `"synthetic"`, `"bootstrap"`, or `"backtest_only"` is set to `excluded` tier — never reaches live Bayesian signal weights.
- **Exit quality (v14.0):** every perp close writes to `exit_evaluations` table (opportunity_loss_pct, stop_overshoot_pct, mfe_at_exit, path_label). Dashboard reads via `dashboard/data/integrity.get_exit_quality_summary()`.
- **config/ package (v14.0):** `config/__init__.py` re-exports all symbols from `config.py` for backward compatibility. Use `from config.venue_specs import KRAKEN_TAKER_FEE` for venue-specific constants; use `from config.alpha_specs import ...` for strategy thresholds.
- **Backtesting (v14.0):** `backtesting/event_backtester.py` replays `scan_candidates` through live stack. Results tagged `source='candidate_replay'` (RESEARCH-GRADE). Promotion requires human confirmation — `backtesting/promotion_engine.py` surfaces `PROMOTED_PENDING_HUMAN` tier; never auto-applies to live.
- **Nightly audit (v14.0):** `monitoring/nightly_audit.py` runs at 08:00 UTC via scheduler. 7 checks: proof suite, candidate journal health, funnel analytics, labeling lag, CLAUDE.md version drift, Bayesian weight changes, retention pruning.
- **Migration scripts:** `scripts/migrate_integrity_backfill.py` backfills integrity tiers for historical closes. Idempotent (INSERT OR IGNORE). Run once after upgrade.
- **ForecastEx venue (v15.0):** IBKR ForecastEx exchange (`Exchange=FORECASTX`, `SecType=OPT`). YES = `Right='C'`, NO = `Right='P'`. Cannot short — flatten by buying the opposite right. Zero commission. ~$100 bankroll.
- **ForecastEx pricing:** bid/ask/midpoint ONLY — never last/trade prints. All OHLC bars built from midpoint series.
- **ForecastEx clientId:** 3 (MES = 2, main IBKR = 1). Must not collide.
- **ForecastEx risk caps (hardcoded, no override):** max deployed 35%, per-event 10%, max concurrent 2, fractional Kelly cap 0.10.
- **ForecastEx economic markets only:** CPI, NFP, FOMC, Unemployment, PCE, GDP, PPI. Sports/politics/entertainment → rejected at discovery (fail-closed).
- **ForecastEx IBKR symbol truth (confirmed 2026-04-15/17 via reqMatchingSymbols):** FORECASTX underliers use SecType=IND with SHORT symbols — NOT FRED codes. Correct short symbols: CPI=573031126, CPIY=712856682, CPIC=727520252, DISSN=806285268, DISSA=804725704, PREMP=582530257 (Nonfarm Payrolls — NOT 'NFP'), UNR=573031117 (Unemployment Rate — NOT 'UR'), RGDP=712856689 (Real GDP — NOT 'GDP'), PCEY=726203930 (PCE YoY — NOT 'PCE'), FEDRO=800591710 (Fed Rate Outstanding), FEDRC=815813254 (Fed Rate Change). CPIAUCSL/UNRATE/PAYEMS/FEDFUNDS do NOT exist on FORECASTX. Discovery uses two-pass: IND confirmation → OPT event contracts. OPT fallback tries right='C'/'P' explicitly if plain OPT returns empty. `refresh_known_underliers()` scans 13 keyword patterns at start of each discovery run and logs any new IND symbols found.
- **ForecastEx account enrollment blocker:** Paper account DUP590699 has IND underliers visible but OPT event contracts hang (IBKR returns no response). ForecastEx event-contract trading requires: (1) live funded IBKR account, (2) explicit ForecastEx enrollment via IBKR portal. IBKR_PORT must be 7496 (live session); .env corrected from 7497→7496.
- **ForecastEx log-odds math:** x_t = log(p/(1-p)); q_hat = logistic(x_t + α·v_1h + β·a_30m - γ·z_t - δ·σ_t - ε·H_t - ζ·Ω_t + η·bias). Defaults: α=0.40, β=0.20, γ=0.30, δ=0.25, ε=0.15, ζ=0.50, η=0.10.
- **ForecastEx MES archival:** MES lane is dormant — code preserved. Dashboard tab renamed "ARCHIVED FUTURES (MES)". Reactivate: set `FUTURES_LANE_ACTIVE=true`.
- **sys.path discipline:** all forecast modules use `if _ROOT not in sys.path: sys.path.insert(0, _ROOT)` (conditional). Test files use `if _ROOT not in sys.path: sys.path.append(_ROOT)` to avoid displacing DASHBOARD_ROOT at collection time.
- **FUTURES_LANE_ACTIVE (v15.1):** gates MES/IBKR lane startup in `scheduler/v10_runner.py` and `monitoring/health_check.py`. Default `false` = archived/dormant. When false, IBKR health check skipped (not a failure), balance.py returns `source='archived'` without connecting. Set `FUTURES_LANE_ACTIVE=true` in `.env` to reactivate MES.
- **FORECAST_LANE_ACTIVE (v15.1):** gates ForecastEx lane startup from `main.py`. Default `false` = standalone only (run `forecast/runner.py` manually or set env var). When true, main.py starts a daemon thread with its own `schedule` instance — no conflict with v10_runner's schedule loop.
- **Mission Control error aggregation (v15.1):** `get_recent_errors_detail()` in `dashboard/data/health.py` filters out archived lane noise (IBKR/MES errors when FUTURES_LANE_ACTIVE=false) via `_is_archived_lane_noise()`. Error headline shows deduped group count (fingerprint-based), not raw row count.
- **Activity feed truth (v15.1):** `_bot_is_alive()` in `activity_log.py` checks DB first (heartbeat/system_events/trades) before showing "start the bot" message. Shows "System alive — no recent log activity" when DB evidence exists but log parser found nothing.
- **Runtime mode (v15.1):** `get_runtime_mode()` in `dashboard/data/health.py` derives 'PAPER'/'LIVE'/'UNKNOWN' from the most recent "Bot started" system_event, not from config assumption.
- **Forecast readiness states (v15.1):** 7-state machine in `get_forecast_readiness()`: LANE_NOT_STARTED / BROKER_DISCONNECTED / NO_UNDERLIERS / UNDERLIERS_ONLY / NO_QUOTES / QUOTES_NO_BARS / OPERATIONAL. No singleton broker instantiation — uses DB-only truth.
- **Discovery stubs (v15.1):** when IND underlier is visible but OPT contracts hang/fail, `forecastex_broker.py._discover_async()` returns a `stub_only=True` dict. `forecast/discovery.py` upserts the underlier to `forecast_markets` with `active=1` but creates no contract rows. Dashboard shows enrollment state via `contracts_unavailable_count`.
- **Dead-money false positive fix (v15.1):** stagnant check in `health_check.py` also exempts positions with `scale_66_done=1` or any partial-close trade in the `trades` table (`action IN ('SELL','CLOSE') OR notes LIKE '%scale_out%' OR notes LIKE '%partial%' AND broker LIKE '%coinbase%'`).
- **IBKR_PORT in config (v15.1):** `config.py` now exports `IBKR_PORT` (default 7497) and `IBKR_HOST` (default 127.0.0.1). Health check uses `config.IBKR_PORT` dynamically in error messages — no more hardcoded 7497 strings in monitored files.
- **Runtime truth tables (v15.2):** `system_runtime_state` (1 row — process mode, startup_ts, active_lanes, global_status) + `lane_runtime_state` (1 row per lane — enabled, active, mode, health, readiness_state, heartbeat). Written by main.py on startup; read by dashboard, validator, audit scripts.
- **Lane registry (v15.2):** `runtime/lane_registry.py` — single control plane for all lane activation. crypto=always active, forecast=FORECAST_LANE_ACTIVE flag, mes_archived=FUTURES_LANE_ACTIVE flag (default false=dormant).
- **Incident model (v15.2):** `incidents` table groups repeated system_events by lane+fingerprint. Dashboard Mission Control reads incidents (not raw rows) for headline truth. Archived MES incidents suppressed when FUTURES_LANE_ACTIVE=false.
- **Position reconciler (v15.2):** `runtime/position_reconciler.py` — run at startup, reconciles `scale_33_done`/`scale_66_done` flags in open_positions against the trades ledger. Trade ledger outranks stale flag columns.
- **Allocator scaffold (v15.2):** `runtime/allocator.py` — GlobalAllocator interface defined; full cross-lane ranking logic deferred to v16.0.
- **Economics interface (v15.2):** `runtime/economics.py` — per-lane friction: crypto=0.030% taker/0.060% round-trip, forecast=0% commission, mes_archived=archived.
- **Live verification hooks (v15.2):** `scripts/live_runtime_audit.py` (post-restart pass/fail audit) + `scripts/lane_status_audit.py` (quick lane snapshot). Run after every restart.
- **Repo truth gate (v15.3):** `scripts/repo_truth_gate.py` — shared gate enforcing Desktop-path-free repo, dynamic hook roots, live-start policy compliance. `--fast` for pre-commit, `--strict` for pre-push/CI. Installed as git pre-push hook via `scripts/install_hooks.sh`. CI step added. 231 proof tests.
- **Truth gate hardening (v15.4):** truth gate now catches both absolute `/Users/*/` + Desktop + `/algo_trading_final` paths AND tilde-prefixed Desktop paths. `.md` added to `ACTIVE_EXTS` so `.claude/commands/`, `.claude/agents/`, `AGENTS.md`, `CLAUDE.md` are scanned. `CHANGELOG.md` added to `SKIP_FILES` (historical commit descriptions only). Pre-commit now runs `repo_truth_gate.py --fast` before `validate.py`. 237 proof tests.
- **Hook path discipline (v15.4):** all `.claude/settings.json` hook commands use `$CLAUDE_PROJECT_DIR/...` (Anthropic documented mechanism) for path-independent execution. `post_cmd_logger.sh` supports `POST_CMD_LOG_OVERRIDE` env var for test-harness isolation and uses `$CLAUDE_PROJECT_DIR` when set. Both approaches ensure the hook works regardless of the CWD at hook invocation time.
- **Stale 7497 closure (v15.4):** `CLAUDE.md` and `AGENTS.md` Common Errors tables updated: IBKR connection error now says "port from IBKR_PORT in .env (7496=live, 7497=paper)". `dashboard/data/health.py` fallback for failed config import changed from hardcoded 7497 to `os.environ.get('IBKR_PORT', '7496')`. No fresh 7497 health_check events possible when FUTURES_LANE_ACTIVE=false (IBKR check skipped entirely).
- **Schedule isolation (v15.5):** `_forecast_daemon` in `main.py` uses `schedule.Scheduler()` private instance (not the global default) to prevent race with `v10_runner`'s `schedule.run_pending()` main loop. Without isolation, the forecast thread consumed exit_monitor jobs from the global scheduler, silently preventing exits from firing. Fix: `_s = _sched_lib.Scheduler()` + `_s.run_pending()` in the daemon loop.
- **DB staleness — peak_price/trailing_active (v15.5):** `perps_engine.update_position_price()` updates `peak_price` and `trailing_active` in-memory only; it does NOT write back to `open_positions.high_since_entry` or `open_positions.trailing_active`. Consequence: health_check stagnant alarm fires false positives for positions whose price has moved. Fix: `_check_stagnant_positions()` in `monitoring/health_check.py` now reads live `peak_price` + `trailing_active` from `perps_engine.get_open_positions()` to augment stale DB values.
- **Error rate filter in dashboard (v15.5):** `get_error_rate_1h()` in `dashboard/data/health.py` now applies the same archived-lane noise filter (`_is_archived_lane_noise()`) that `_check_error_rate()` in `health_check.py` already uses. Without this, the dashboard showed ~679 IBKRBroker errors/hour as real errors when FUTURES_LANE_ACTIVE=false.
- **Controlled live launch (v15.6):** `scripts/boot.py` now supports explicit `paper` vs `live` mode before any config import can cache `.env` state. `scripts/go_live.py` is the single sanctioned Claude/Codex live-launch path: it verifies Coinbase live auth, stops the paper launchd bot, starts `boot.py --mode live --confirm-live`, and waits for runtime truth to confirm `mode=live`. `scripts/go_paper.py` restores the paper launchd bot.
- **Dashboard runtime truth (v15.7):** `dashboard/db.py` exports `_runtime_paper_flag()` — the ONLY place in the dashboard that determines paper vs live mode. It reads `system_runtime_state.process_mode` first (DB truth), falls back to `config.PAPER_TRADING`. ALL dashboard data modules import this helper; no module may define its own `_paper_flag()` that reads config directly. `dashboard/data/positions.py` must NEVER hardcode `WHERE paper=1`. `dashboard/data/balance.py` uses `_balance_paper_mode()` which reads the same runtime state via its own sqlite3 connection (balance.py cannot import from db due to separate sys.path setup). Proof test fixture (`conftest.py`) patches `config.PAPER_TRADING=True` so test trades (paper=1) are found regardless of system mode.
- **Kill switch live-mode policy (v15.8):** `kill_switch.check_balance()` takes a `paper` bool. PAPER mode: threshold = 75% of `initial_balance`. LIVE mode: threshold = 50% of `_live_baseline`. `_live_baseline` is auto-set to the first valid live balance seen (>$50), preventing false-trigger against stale ACCOUNT_SIZE config. `risk_engine.update_balances()` must NOT call `kill_switch.check_balance()` — the kill_switch_monitor() in v10_runner handles that with the correct paper/live flag. Call `kill_switch.set_live_baseline(amount)` on confirmed live balance to lock the baseline manually.
- **kill_switch_log schema (v15.8):** Correct columns: `id TEXT PK, ts TEXT, reason TEXT, balance REAL, peak_balance REAL, positions_closed INT, resumed_at TEXT, trigger_type TEXT`. Old schema (`balance_at_trigger`, `resolved`, `resolved_ts`, `resolved_reason`) is wrong. `logging_db/trade_logger.py` migrates old DBs via ALTER TABLE on init.
- **ISO timestamp T-separator fix (v15.8):** SQLite `ts >= '2026-04-15 19:13:59'` is WRONG against ISO timestamps like `'2026-04-15T17:54:27'` (ASCII 'T'=84 > ' '=32 → ALL same-day rows pass). All dashboard queries use: `datetime(replace(substr(ts,1,19),'T',' ')) >= ?`. Applied to `dashboard/data/execution.py`, `dashboard/data/health.py`, `dashboard/data/scanner_data.py`. The helper constant `_TS_NORM = "datetime(replace(substr(ts,1,19),'T',' '))"` is defined in execution.py.
- **Heartbeat age primary source (v15.8):** `get_heartbeat_age()` in `dashboard/data/health.py` reads `lane_runtime_state.last_heartbeat_at` (updated every minute by v10_runner regardless of scan status) as primary truth. Falls back to `system_events WHERE source='heartbeat'` (only written on successful scans — stale when kill switch blocks scans).
- **Dashboard data namespace collision (v15.9):** `dashboard/data/` is a Python package (`__init__.py` present). When `dashboard/` is first on `sys.path`, any `from data.X import Y` in dashboard code resolves to `dashboard/data/X.py`. All such imports in dashboard widgets are intentional (they target the dashboard data layer). Exception: `data.historical_data` — only exists at repo root, not in `dashboard/data/`. Old `from data.historical_data import get_candles` in `manual_scan.py` crashed the dashboard with `ModuleNotFoundError`. Fixed by using `importlib.util.spec_from_file_location` to load from explicit repo-root path, bypassing `sys.modules` cache.
- **Forecast truth-layer primary source (v15.9):** `dashboard/data/forecast.py:get_forecast_health()` now reads `lane_runtime_state.active` as primary truth for `lane_started`. Falls back to `system_events WHERE source='ForecastRunner'` (normalized timestamp) only when runtime table is unavailable. `validate.py` forecast-lane check similarly prefers runtime state. This eliminates the contradiction where runtime showed active=True but validate/dashboard said "not running".
- **Forecast dashboard zero-state (v15.9):** `forecast_dashboard.py` now renders an operational funnel (lane→broker→underliers→contracts→quotes→bars→strategy→positions→trades), handles zero-state without misleading empty-performance interpretations, shows enrollment explanation when underliers visible but no OPT contracts.
- **Execution universe (v15.10/v16):** `runtime/execution_universe.py` is the single source of truth for symbol→tier mapping. Tiers: `core` (CORE_EXECUTION_UNDERLYINGS in config.py), `research_only` (scanner-visible, no execution), `suppressed`. `_get_underlying()` normalization shared via import — not duplicated. research_only_block gate fires in `_attempt_entry` AFTER econ gate (signals were valid, just wrong universe). Journaled as `decision='research_only_block'` with `econ_approved=1, should_enter_signal=1`.
- **Scanner EV cap (v16):** `_step4_expected_value` in scanner.py uses `effective_position_usd = min(theoretical_position_usd, 100.0)` to prevent phantom EV from tight stops implying unrealistic $7K+ notional on $5K account. Both `scanner_theoretical_position_usd` and `scanner_effective_position_usd` persisted to `scan_candidates` via `log_scan_candidate()`.
- **Exact scan funnel (v16):** `scan_funnels` table (17 columns) stores exact per-cycle counts. `_attempt_entry()` returns terminal decision strings (`data_unavailable`/`below_threshold`/`econ_veto`/`research_only_block`/`sizing_zero`/`execution_failed`/`entered`). `log_scan_funnel()` called at end of each scan cycle. `execution_failed` = open_long/open_short returned None.
- **Bayesian entry priors (v16):** `learning/entry_priors.py` — `estimate_candidate_win_rate(exchange, primary_setup, regime, direction)`. Win label: `hit_1r=1 AND hit_stop=0`. 5-level fallback: exchange+setup+regime+direction → setup+regime+direction → setup+regime → regime+direction → global. Bayesian smoothing: `(prior_n*prior_p + wins)/(prior_n + n)`, prior_p=0.52, prior_n=20, clipped [0.40, 0.70]. Replaces hardcoded `_wr_est = 0.54 if tier == 1 else ...` in v10_runner.py economics gate.
- **Path timing truth (v16.2):** `candidate_outcomes` now also stores `path_timing_evaluated`. `learning/candidate_labeler.py:_compute_path_timing()` anchors 15m/1h/4h timing to `scan_candidates.ts` whenever the fetched candle index supports it, instead of relying only on a tail-of-series heuristic.
- **Manual scan fail-closed (v16.2):** `dashboard/widgets/trade_approval/manual_scan.py` treats execution-policy lookup failures as blocked (`tier='suppressed'`, `execute=False`) and only runs `scanner.scan(..., core_only=True)`. Dashboard execution can no longer silently fail open into non-core names.
- **Manual scan paper flag (v16.4):** `manual_scan.py` Execute button reads `_runtime_paper_flag()` from `dashboard/db.py` to determine paper vs live mode — never hardcoded. Execution symbol uses `_policy["underlying"]` (not raw scanner symbol) so Kraken-format names like `PF_SOLUSD` normalise to `SOL` before reaching the Coinbase broker. Result line shows `[PAPER]` or `[LIVE]` tag.
- **Balance total_usd_balance (v16.4 → v16.6):** Coinbase CFM API `total_usd_balance` is the CBI cash sub-account (~$24), NOT the total tradeable value. `futures_buying_power` (~$1,965) is the correct canonical balance for US nano futures accounts. `balance.py` now uses `futures_buying_power` as primary; falls back to `total_usd_balance` only if buying power is absent.
- **Manual scan minimum contract size (v16.5):** `_min_contract_usd(exec_sym, price)` helper reads `PRODUCT_SPECS[exec_sym]["contract_size"]` and returns `price × contract_size`. After compute_position_size, live-mode execute bumps pos_usd to max(signal_size, min_contract_usd × 1.02) so the Coinbase broker always gets enough for ≥1 whole contract. ETH=0.1 ETH/contract (~$233), SOL=5 SOL/contract (~$430), XRP=500 XRP/contract (~$710), BTC=0.01 BTC/contract (~$845). Without this, all orders returned None silently.
- **Manual scan result persistence (v16.5):** Results stored in `st.session_state["manual_results"]` and rendered via `st.success`/`st.error` at the TOP of `render_manual_scan()` — survive all page rerenders. `st.rerun()` called immediately after execute so results appear without a user interaction. "Clear results" button removes them. New scan also clears stale results.
- **Manual scan balance source (v16.5):** Live-mode execute uses `get_coinbase_balance()["balance"]` (real API, ~$1,966) not `get_account()` which returns config `ACCOUNT_SIZE=$5,000`. Paper mode still uses `get_account()` DB-computed equity.
- **Audit semantics (v16.2):** `scripts/path_truth_audit.py` uses only `path_timing_evaluated=1` rows as the denominator for timing reach percentages. `scripts/entry_truth_audit.py` now separates `scored_total`, `below_threshold`, and `above_threshold`, so conversion and economics-veto rates are calculated from truthful threshold-passed counts.
- **Broker-aligned live universe (v16.3):** `CORE_EXECUTION_UNDERLYINGS` now matches the Coinbase broker-supported set exactly: `BTC`, `ETH`, `SOL`, `XRP`. Unsupported TradingView symbols are dropped before they enter the live candidate path. Default `PERP_PAIRS` / `CRYPTO_PAIRS` were tightened to the same four-name live set.
- **Spot lane (v16.11):** `execution/coinbase_spot_broker.py` + `spot_engine.py`. BTC-USD and ETH-USD spot only, no leverage, no shorting, no margin. Gated by `SPOT_LANE_ACTIVE` (default false). `SPOT_MAX_DEPLOYED_PCT=0.40` (40% of USD available), `SPOT_MIN_ORDER_USD=10.0`. Strategy column = `spot_btc`/`spot_eth` — `get_spot_positions()` filters by `strategy.startswith("spot_")`, never contaminates perp `open_positions`. `get_spot_balance_summary()` in `dashboard/data/balance.py` reads Coinbase spot accounts API (not CFM). Manual Sell/Buy controls added to `render_spot_section()` in `manual_scan.py`.
- **Autonomous live perp gate (v16.11):** `AUTONOMOUS_LIVE_PERP_SYMBOLS` in `config.py` (default `["ETH"]`). In `v10_runner._attempt_entry()`, after the execution-universe gate, live mode checks if the underlying is in this list; if not, returns `"not_autonomous_live_eligible"` (journaled). BTC/SOL/XRP: contract minimums exceed 15% of ~$1,966 account — manual scan only, not autonomous. `CORE_EXECUTION_UNDERLYINGS` stays [BTC,ETH,SOL,XRP] for manual + research.
- **One-live-perp-at-a-time (v16.11):** `perps_engine.open_long()` and `open_short()` check in-process `_open_positions` for any entry with `paper=False` before proceeding in live mode. If count >= 1, returns None with `one_live_perp_max` warning. Paper mode: uncapped. This is an engine-level guard separate from the broker-level duplicate guard.
- **Deployed-cap (existing, confirmed v16.11):** `position_manager.compute_position_size()` enforces 95% deployment cap via `remaining_capacity = account_balance * 0.95 - deployed_usd`. `risk_engine.can_open_new_position()` also blocks at 95% deployed. Both fire on every live entry path.

### MES Futures — Critical Contract Facts (v13.9)

- Contract resolved via `localSymbol='MESM26'` + `multiplier='5'` — NOT `lastTradeDateOrContractMonth`. Date-string form fails when contract isn't in TWS's DB.
- `_get_mes_contract()` derives `localSymbol` from `MES_EXPIRY` (month codes: 03→H, 06→M, 09→U, 12→Z). Update `MES_EXPIRY` in `.env` on each quarterly roll.
- Current: `MES_EXPIRY=20260619` → `localSymbol=MESM26`
- Position dict keys from `buy_mes`/`short_mes`: `"entry"` (not `"entry_price"`), `"side"` (`"LONG"` or `"SHORT"`), `"qty"` (always positive integer).
- **Never use `qty > 0` to determine direction** — always `pos.get("side") == "LONG"`.
- Python 3.14: background thread must call `asyncio.set_event_loop(loop)` before `run_forever()`.
- Verified: 10 paper round-trips (5L + 5S) via live TWS, account `DUP590699`.

### Go-Live Readiness (dashboard SYSTEM tab → READINESS TRACKER)

Owner decides when to go live. Informational only — not system gates:
- Clean trade count (source=`clean_paper_v10` or `live_v10`)
- Win rate + profit factor on clean trades
- Worst single day as % of account
- Days running on clean data
- Economics gate veto rate
- Kill switch triggers (14d)

## Signal Engine

Two deterministic towers → composite score → regime threshold gate.

| Tower | Method | Signals |
|-------|--------|---------|
| Technical | Rule-based point scoring, 0-100 | CVD divergence, MACD multi-variant, RSI divergence, funding squeeze, VWAP reclaim, OB imbalance, Williams %R, liq cascade, vol spike, Fear & Greed, options skew, whale signal |
| ML | XGBoost 60% + LightGBM 40% PnL regressor, 0-100 | 57 features across 11 groups |

Entry: composite >= regime threshold: TRENDING_UP/DOWN=58, RANGING=58, HIGH_VOL=60, LOW_VOL=56, UNKNOWN=58. Same threshold paper and live. WAE explosion requires both fast AND slow MACD histogram to agree.

## 7-Priority Exit Stack (position_manager.py + v10_runner.py)

1. **Trailing stop** — regime-aware: RANGING=1.0×ATR activation/2.5× width; TRENDING=1.5×ATR/4.5×; HIGH_VOL=2.0×ATR/5.5×. Compresses toward 50% of nominal when `signal_health < 65%`.
2. **Take profit scale-out** — conviction-adaptive: blends `entry_composite_score` (60%) + regime extension (40%). First cut 20–30% at 2.0–4.0R; second cut 25% at 4.5–8.0R. Denominator = `abs(entry - stop_price)` (actual stop distance, not hardcoded ATR).
3. **Thesis score exit** — composite < entry × regime_fraction: TRENDING=30%, RANGING=15%, HIGH_VOL=35%, UNKNOWN=25%. ATR-proportional hold gate: LONG 1h–6h, SHORT 2h–12h.
4. **Hard stop** — stop-market on exchange, never widened.
5. **Risk forced exit** — margin breach / drawdown / correlation.
6. **Kill switch** — balance < 75% ACCOUNT_SIZE / API errors / latency.
~~7. Dead-money exit~~ — **removed (v16.9).** Thesis holds until one of exits 1–6 triggers. No time-based or drift-based forced exit.

## Learning Architecture

Every closed trade triggers `learning_loop.record_closed_trade()`:
1. Persists 57-feature snapshot + outcome to `ml_feature_snapshots`
2. Calls `learning/post_trade_analyzer.py` → Bayesian attribution
3. Checks `ml_retrain_queue` → triggers walk-forward retrain when enough data
4. Feeds incubating RBI strategies

Bayesian weight formula:
```
posterior_wr = (PRIOR_N * prior_p + N * obs_win_rate) / (PRIOR_N + N)
bayesian_pts = prior_pts * (posterior_wr / prior_p)
```
PRIOR_N=20, MIN_FIRES_TO_LEARN=10, cap=2.5× original prior points per signal per regime.

Candidate journaling: 8 decision gates logged per scan (`dual_exposure_block`, `cooldown_block`, `risk_block`, `data_unavailable`, `below_threshold`, `econ_veto`, `sizing_zero`, `entered`). Labeler runs every 15 min computing 15m/1h/4h returns, MFE, MAE, hit_1r, hit_2r. Nightly audit at 08:00 UTC.

## The Amygdala Removal Rules (HARDCODED — NO OVERRIDE)

1. Never chase — skip if price moved >3% since signal
2. Never average down — one position per symbol, ever
3. Stop losses are sacred — never moved wider after entry
4. Wins don't justify ignoring rules on the next trade
5. Losses don't justify revenge trading or larger size
6. FOMO is not a signal
7. When in doubt, HOLD — a skipped trade costs nothing
8. The goal is being in business next month, not winning today

## Risk Rules (current values)

- **1%** max account risk per trade
- **4%** max daily loss → halt ALL trading (paper: no cap, never halts learning)
- **90%** max deployed capital
- Default **3×** leverage, max **10×**
- ISOLATED margin on all perp positions — never CROSS
- Coinbase taker fee: **0.030%** (modeled in economics_gate.py before every entry; round-trip = 0.060%)
- Kill switch at balance < **75% of ACCOUNT_SIZE**
- Economics gate EV tiers: A+=1.6%, A=0.8%, B=0.3%; stop_multiplier=3.0; spread gate=25bps; depth gate=$5K/side
- Volume floor: $2.5M/24h (scanner + economics gate aligned)

## Key Data Formats

### Trade log (SQLite `trades` table)
`ts, strategy, broker, symbol, action, order_type, qty, price, value_usd, fee_usd, pnl_usd, paper, order_id, notes`

### Position (in-memory + SQLite `open_positions`)
`symbol, strategy, qty, entry, stop, target, high_since_entry, ts_entry`

### Vector Memory (`logs/memory/trade_memory.db` → `trade_experiences`)
8-dim: `[rsi/100, tanh(macd*10), adx/100, min(vol/5,1), regime_trending, regime_ranging, regime_volatile, regime_unknown]`

## How to Start the System
```bash
python3 main.py --mode paper       # Force paper
python3 scripts/go_live.py         # Controlled live transition (Claude-safe path)
python3 scripts/go_paper.py        # Return to paper launchd bot
streamlit run dashboard/app.py --server.runOnSave true  # Dashboard :8501
python3 mcp_server/server.py       # MCP server
python3 scripts/weekly_report.py   # Weekly performance report
python3 -m pytest                  # Proof-first verification suite (tests/proof/)
```

## Notifications
All alerts → `system_events` SQLite table via `notifications/notification_engine.py`. No Telegram, no email. `alerts/` directory is empty.

## Auto-Start (launchd)
```bash
bash scripts/install_services.sh
```
Services: `com.algotrading.king` (paper bot, restarts on crash), `com.algotrading.backup` (2am daily), `com.algotrading.readiness` (7am daily). Logs: `logs/service/`. Use `python3 scripts/go_live.py` for a controlled live transition and `python3 scripts/go_paper.py` to restore the paper bot.

## TradingView Integration
Pine Script → webhook → SQLite `system_events` (source='tradingview'). v10_runner reads every scan cycle, prepends as candidates with `edge_score=0.6`.
```bash
python3 scripts/tradingview_webhook.py   # port 8765
ngrok http 8765
```
Set `TV_WEBHOOK_SECRET` in .env. Symbol mapping: BTCUSD → BTCUSDT.

## MES Contract Symbols (update quarterly)
- Q2 2026 (Apr-Jun): `MESM26` — **ACTIVE** (`MES_EXPIRY=20260619`)
- On quarterly roll: update `MES_EXPIRY` in `.env`

## Common Errors and Fixes

| Error | Fix |
|-------|-----|
| pandas-ta import | `pip install "pandas-ta>=0.4.67b0"` |
| XGBoost openmp | `brew install libomp` |
| IBKR connection failed | TWS running, API enabled in TWS settings; port from `IBKR_PORT` in .env (7496=live, 7497=paper) |
| DB lock | WAL mode on; stale connection — restart bot |
| Schedule not running | Check while-True loop in v10_runner.py not blocked |
| TV webhook 403 | `TV_WEBHOOK_SECRET` mismatch |
| launchd not starting | `launchctl list \| grep algotrading`; check `logs/service/bot_error.log` |
| Kraken scanner empty | Check internet / futures.kraken.com |
| ML gate always 0.5 | Not enough clean trades yet (< MIN_TRADES_FOR_ML). Normal early paper phase. |
| IBKR Error 200 | Use `localSymbol='MESxxx'` not `lastTradeDateOrContractMonth` |

## Version History

| Version | Date | Summary |
|---------|------|---------|
| v1.0–v9.x | 2026-03 | MACD equity, AI debate, Bybit/Tradovate, LightGBM gate, risk decomposition |
| v10.0 | 2026-04-01 | Full rewrite: two-tower signal engine, 57-feature ML, 6-priority exit stack, RBI loop |
| v10.1 | 2026-04-02–04 | Kraken scanner, economics gate, Bybit deleted, legacy purged, clean ML data |
| v13.1–13.4 | 2026-04-05–10 | Scanner/funnel fixes, strategy optimization, ML PnL regressor, proof infrastructure |
| v13.5 | 2026-04-13 | Conviction-adaptive exit: real-R denominator, regime trailing, signal-health compression |
| v13.6 | 2026-04-13 | Candidate journaling at 8 gates, automated 1h/4h outcome labeling, nightly audit |
| v13.7 | 2026-04-13 | 15m labeling, exception-only notifications, funnel analytics, retention pruning, CI fix |
| v13.8 | 2026-04-13 | Dead-money exit; health_check dedup; UNHEALTHY substring fix; live health dashboard panel; 41 proof tests |
| v13.9 | 2026-04-14 | MES audit: contract localSymbol fix, asyncio event loop fix, SHORT monitoring fix, EOD close fix, daily loss limit from config, 7th health check (ibkr); 10 TWS trades verified |
| v14.0 | 2026-04-14 | Self-improving architecture: integrity tiers, candidate replay backtester, promotion engine, futures config sub-package, dashboard truth surfaces, recurring self-maintenance loops, 52 proof tests (0 failures) |
| v14.1 | 2026-04-14 | Coinbase US crypto lane migration: coinbase_broker.py (CDP JWT/ES256, 4 CFTC products BIP/ETP/SLP/XPP), fee model → 0.03% taker, fail-closed CoinbaseSymbolError, executable launch validator, 42 new proof tests, 158 total (0 failures) |
| v15.0 | 2026-04-15 | ForecastEx event-contract lane: forecastex_broker.py (IBKR clientId=3, economic markets only, YES=Right C/NO=Right P), 5 new DB tables, log-odds probability engine, 3 strategy families (continuation/mean_reversion/late_repricing), 10-check economics gate, fractional Kelly sizing, dashboard FORECAST TRADING tab, MES archived, 37 new proof tests, 195 total (0 failures) |
| v15.1 | 2026-04-15 | Lane gating hardened: FUTURES_LANE_ACTIVE/FORECAST_LANE_ACTIVE flags in config.py; IBKR health check skips when dormant; balance.py returns archived state; forecast lane wired into main.py as daemon thread; forecast readiness 7-state machine; discovery stubs for OPT-unavailable underliers; Mission Control deduped error types + archived lane noise filter; activity feed DB-first truth; dead-money exempt on partial-close; IBKR_PORT in config; 10 new proof tests, 205 total (0 failures) |
| v15.2 | 2026-04-15 | Runtime truth layer: system/lane state tables, lane registry, incident model, position reconciler, allocator scaffold, economics interface, live audit hooks, 219 proof tests |
| v15.3 | 2026-04-15 | Repo truth closure: repo_truth_gate.py (Desktop path + hook root + live-start policy + CI checks), 18-file Desktop path purge, BLOCK 1b implicit live-start policy, pre-push git hook, CI strict gate, 231 proof tests |
| v15.4 | 2026-04-15 | Final truth-closure: tilde ~/Desktop pattern in truth gate; .md added to ACTIVE_EXTS (covers .claude/commands/, AGENTS.md, CLAUDE.md); pre-commit hardened with repo_truth_gate.py --fast; post_cmd_logger supports POST_CMD_LOG_OVERRIDE + $CLAUDE_PROJECT_DIR; all settings.json hook paths use $CLAUDE_PROJECT_DIR; stale 7497 eliminated from CLAUDE.md/AGENTS.md/dashboard fallback; 237 proof tests |
| v15.5 | 2026-04-15 | Dashboard error fixes: schedule isolation for forecast daemon (exit_monitor race fix), error rate filter in dashboard/data/health.py, stagnant check uses live perps_engine peak_price to avoid false positives from stale DB high_since_entry; 240 proof tests |
| v15.6 | 2026-04-15 | Controlled live-launch workflow: mode-aware boot.py, go_live.py/go_paper.py mode transitions, hook allowlist for the sanctioned launcher scripts, docs updated so Claude uses the controlled path instead of raw `--mode live` |
| v15.7 | 2026-04-15 | Dashboard live-mode paper gate fix: added `_runtime_paper_flag()` to dashboard/db.py (reads system_runtime_state.process_mode; config fallback); positions.py hardcoded paper=1 replaced; account.py + performance.py local _paper_flag() replaced; balance.py mode detection uses runtime DB via _balance_paper_mode(); conftest patches config.PAPER_TRADING=True for test isolation; 10 new proof tests covering DB-overrides-config, paper positions hidden in live, cross-contamination prevention; 253 proof tests |
| v15.8 | 2026-04-15 | Kill switch live-mode fix: removed hardcoded $7,500 threshold; live mode uses 50% of live_baseline (~$983 for ~$1,966 account); removed false-triggering kill_switch call from risk_engine.update_balances(); fixed kill_switch_log schema mismatch (balance/resumed_at/trigger_type columns); fixed ISO timestamp T-separator bug in execution.py/health.py/scanner_data.py (datetime(replace(substr(ts,1,19),'T',' '))); fixed economics veto operator-precedence bug; deduplicated execution errors into distinct incidents; unified dashboard mode detection via _runtime_paper_flag(); heartbeat age uses lane_runtime_state as primary source; epoch timestamps display correctly in alert feed; 260 proof tests |
| v15.9 | 2026-04-16 | Forecast truth-layer + trade approval import fix: manual_scan.py execute-trade crash fixed (from data.historical_data collision with dashboard/data namespace — replaced with importlib.util explicit-path load); forecast dashboard truth now reads lane_runtime_state as primary source (not stale system_events); validate.py forecast-lane check uses runtime state primary + ISO-normalized timestamp fallback; eliminated validate.py vs runtime contradiction; forecast dashboard upgraded to operational-funnel display + zero-state graceful handling + exact operator wording for NO_TRADABLE_CONTRACTS; 276 proof tests |
| v15.10 | 2026-04-16 | Execution-universe split: runtime/execution_universe.py (single source of truth for tier lookup), CORE_EXECUTION_UNDERLYINGS in config.py (BTC/ETH/SOL/XRP/DOGE/AVAX/LINK/AAVE/INJ/NEAR), research_only_block gate in _attempt_entry (fires after econ gate), tier labels + disabled checkboxes in manual_scan.py, research_only_blocks_24h/7d in journal_health.py, dev_config.py metrics, 10 new proof tests |
| v16 | 2026-04-16 | Truth/instrumentation tranche: scanner EV cap (effective_position_usd = min(theoretical, $100) prevents phantom EV); exact scan_funnels persistence with log_scan_funnel() + terminal decision strings from _attempt_entry; Bayesian entry priors (learning/entry_priors.py — 5-level fallback, hit_1r=1 AND hit_stop=0 win label, smoothed with prior_n=20/prior_p=0.52, clipped [0.40,0.70]); path timing columns (time_to_05r_min/time_to_1r_min/time_to_2r_min/peak_r_4h in candidate_outcomes, computed from 15m bars in candidate_labeler.py); entry_truth_audit.py (6 sections: funnel/EV/source/setup/symbol-class/integrity); path_truth_audit.py (4 sections: R-reach/timing/path-by-group/exit-quality); scanner_theoretical/effective_position_usd persisted to scan_candidates; 318 proof tests |
| v16.1 | 2026-04-16 | Audit/version cleanup: entry_truth_audit fixed for real DB schema, symbol class audit uses actual underlyings, UTC timestamp handling aligned, repo version truth corrected, 319 proof tests |
| v16.2 | 2026-04-16 | Truth hardening + core-only alignment: scanner/runner/manual scan default to core-only universe, manual scan fails closed on policy lookup errors, candidate timing anchored to candidate ts with path_timing_evaluated flag, path_truth_audit denominator fixed, entry_truth_audit threshold math fixed, scanner EV journaling fallback corrected, 325 proof tests |
| v16.3 | 2026-04-16 | Live universe alignment: CORE_EXECUTION_UNDERLYINGS reduced to actual Coinbase-supported BTC/ETH/SOL/XRP set, TradingView live candidates filtered through execution policy, default crypto/perp pair lists tightened to supported coins, proof coverage updated |
| v16.4 | 2026-04-16 | Manual scan live-mode fixes: manual_scan.py paper flag reads _runtime_paper_flag() instead of hardcoding True (positions now write paper=0 in live mode and appear in dashboard); exec_sym uses _policy["underlying"] so PF_SOLUSD/PF_ETHUSD normalise to SOL/ETH for Coinbase broker; balance.py total_usd_balance string-"0" bug fixed (Coinbase API returns "0" truthy string — now falls back to futures_buying_power when converted value ≤ 0, shows real balance ~$1,966 instead of $0) |
| v16.5 | 2026-04-16 | Manual scan execution stack fixed end-to-end: (1) Coinbase broker returns None when size_usd < 1 contract — manual_scan now calls _min_contract_usd() and bumps pos_usd to 1-contract minimum with 2% buffer before calling perps; (2) balance source corrected — live mode uses get_coinbase_balance() real API balance (~$1,966) not config ACCOUNT_SIZE ($5,000); (3) candle/price fetch uses exec_sym (SOL/ETH/XRP) with PF_xxx fallback — ATR and price now computed from correct instrument; (4) execution results stored in st.session_state["manual_results"] and rendered via st.success/st.error at page top — survive all rerenders; (5) st.rerun() called after execute so results appear immediately; (6) error messages explain WHY broker returned None (size < 1 contract vs other). Live tested: ETH LONG 1 contract @ $2326.86, Coinbase order 7b0044a7, paper=0 confirmed in DB. |
| v16.6 | 2026-04-16 | Balance root-cause fix: Coinbase CFM API total_usd_balance (~$24) is the CBI cash sub-account, not the total futures account value. futures_buying_power (~$1,965) is the correct canonical balance. balance.py now uses futures_buying_power as primary; falls back to total_usd_balance only if buying power is absent. Dashboard now shows real account value. |
| v16.7 | 2026-04-16 | Duplicate/opposing live trade prevention: (1) coinbase_broker.py live open_long/open_short now updates self._open_positions after a real API order, so the in-process duplicate guard fires for subsequent calls in the same session (previously live path never populated _open_positions, letting SOL LONG → SOL SHORT both place real opposing Coinbase orders); (2) manual_scan builds _held_syms from DB + _batch_syms from current execution batch — blocks same-underlying entry if already held OR already queued in this click; (3) per-trade size cap: if 1-contract minimum > 15% of account, trade is skipped with explanation (prevents XRP $710/SOL $435 force-bumps on a ~$1,965 account); excess signal sizing also capped at 15% of account. |
| v16.11 | 2026-04-17 | Spot lane + live perp safety: (1) execution/coinbase_spot_broker.py — CDP JWT spot adapter for BTC-USD/ETH-USD, no leverage/shorting/margin, paper mock fills, buy_spot/sell_spot/get_spot_balance; (2) spot_engine.py — manages one spot position, 5 block reasons, writes to trades (broker=coinbase_spot) and open_positions (strategy=spot_*), get_spot_positions() filters by strategy prefix (no perp contamination); (3) AUTONOMOUS_LIVE_PERP_SYMBOLS=["ETH"] in config.py — only symbols where contract min < 15% of account; (4) v10_runner._attempt_entry() adds not_autonomous_live_eligible gate after execution-universe gate for live mode — BTC/SOL/XRP log+journal and return, ETH passes; (5) perps_engine.open_long/open_short adds one_live_perp_max guard — any live position (paper=False) in _open_positions blocks new live entry; paper uncapped; (6) get_spot_balance_summary() in dashboard/data/balance.py reads Coinbase spot accounts (not CFM); (7) render_spot_section() in manual_scan.py shows SPOT status + buy/sell controls; (8) manual_scan shows "Manual only — not autonomous-live-eligible" note for BTC/SOL/XRP in live mode; 19 new proof tests (SP-01..08, LP-01..10 + LP-08 stub), 346 total (0 failures) |
| v16.12 | 2026-04-17 | ForecastEx symbol expansion: KNOWN_FORECASTX_CONIDS expanded with 6 newly confirmed IND symbols (PREMP=582530257 Nonfarm Payrolls, UNR=573031117 Unemployment Rate, RGDP=712856689 Real GDP, PCEY=726203930 PCE YoY, FEDRO=800591710 Fed Rate Outstanding, FEDRC=815813254 Fed Rate Change); ECONOMIC_UNDERLIERS updated with correct IBKR short symbols (NFP→PREMP, UR→UNR, GDP→RGDP, PCE→PCEY, FOMC→FEDRO/FEDRC); OPT fallback in _discover_async now tries right='C'/'P' explicitly before emitting stub (some TWS versions require explicit right); stub emitted when OPT returns empty but ind_conid is known (preserves IND visibility regardless of enrollment state); refresh_known_underliers() method added — called at start of discover_markets(), scans 13 keyword patterns via reqMatchingSymbols, logs any new FORECASTX IND symbols not in hardcoded map for future sessions |

## GitHub
- Repository: `futureisnowtech/trading-bot-main` (private)
- Active branch: `feature/v10-rebuild`
- Push: `git push origin feature/v10-rebuild` (SSH configured)

## Claude's Standing Instructions

When making any change to this project:
1. **Update CLAUDE.md immediately** — not at end of session. CLAUDE.md is the source of truth for the next session; if it's stale, the next session starts blind.
2. Append to CHANGELOG.md: `bash scripts/log_change.sh "Description"`
3. Commit when a logical unit of work is done. Never commit `.env` or `logs/`.
4. Always use `python3`, not `python`.
5. Read a file before editing it.
6. Test paper mode before any live-mode changes: `python3 main.py --mode paper`
7. **Proof tests are part of done** — any change to a data layer function (`dashboard/data/`, `logging_db/`, `config.py` constants) requires a proof test that defines the invariant. If it would have caught the bug, it's mandatory.
8. **No tunnel vision on partial changes** — when changing one function, grep for all callers and related functions touching the same data. Example: changing `get_recent_errors_detail()` requires checking `get_error_rate_1h()` and `get_health_status()` too.
9. **MES position dict keys:** `"entry"` (not `"entry_price"`), `"side"` (`"LONG"`/`"SHORT"`), `"qty"` (always positive). Never use `qty > 0` for direction — always `pos.get("side") == "LONG"`.
10. **DO NOT TOUCH without explicit instruction:** `scanner.py`, `signal_engine.py`, `position_manager.py`, `perps_engine.py`, `scheduler/v10_runner.py`, `data/indicators.py`, `ml/feature_builder.py`, `ml/walk_forward_trainer.py`, `ml/model_store.py`, `risk/economics_gate.py`, `learning/post_trade_analyzer.py`, `learning/signal_performance.py`, `learning/dynamic_weights.py`, `notifications/notification_engine.py`, `logging_db/trade_logger.py`, `dashboard/app.py`, `forecast/primitives.py`, `forecast/strategy_engine.py`, `execution/forecastex_broker.py`, `spot_engine.py`, `execution/coinbase_spot_broker.py`.
