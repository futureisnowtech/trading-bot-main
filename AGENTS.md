# AGENTS.md — Algo Trading System Knowledge Base
# Auto-loaded by Codex at the start of every session.
# This file IS the system memory. Keep it current.
# When you make changes: update this file AND append to CHANGELOG.md.

## Strategic Brain

The `/brain/` directory is the living strategic intelligence layer.
- Hub: `brain/README.md`
- Governed by: `brain_constitution.md` + `brain_execution_os.md`
- Key notes: `brain/01_current_system/`, `brain/03_parameter_sets/`, `brain/10_decisions/`

## What This System Is

A fully autonomous AI-powered trading system that:
- Scans Kraken Futures + Binance USDM + Hyperliquid perps 24/7 with a 7-filter pipeline (no hardcoded watchlist)
- Scores every candidate with a two-tower signal engine (technical 0-100 + ML 0-100 → composite)
- Enforces unbreakable emotional safeguards (the amygdala is removed)
- Learns from every completed trade via Bayesian signal attribution + 57-feature ML snapshots
- Writes all notifications to SQLite; dashboard Notifications panel displays them
- Displays everything on a LeBron James / Dragon Ball Z themed dashboard
- Trades 100% autonomously — owner is never asked to approve anything

## Owner Profile
- Mac user (MacBook Air 2020, Python 3.14 at /Library/Frameworks/Python.framework/Versions/3.14/bin/python3)
- Paper account: $5,000 (ACCOUNT_SIZE=5000 — config default, no .env override)
- Relatively technical but wants zero day-to-day intervention
- Wants the system to WIN — everything tuned for performance
- Prefers simple explanations, hates fluff

## Current Version: v15.6 (2026-04-15)

**Active branch:** `feature/v10-rebuild`
**Clean paper trading started:** 2026-04-02

### Live Architecture (source of truth)

| Component | File | Role |
|---|---|---|
| Scanner | `scanner.py` | **3 sources**: Kraken Futures + Binance USDM perps + Hyperliquid, 7-filter, top 50 candidates |
| Signal engine | `signal_engine.py` | Two-tower: technical 0-100 + ML 0-100 → composite |
| Entry runner | `scheduler/v10_runner.py` | Scan loop, tier selection, economics gate, setup detection, execution handoff |
| Position sizing | `position_manager.py` | Kelly + ATR sizing, leverage schedule, deployment caps |
| Exit manager | `position_manager.py` | 7-priority exit stack (trailing/scale/thesis/hard-stop/risk/kill/dead-money) |
| Perp execution | `perps_engine.py` → `execution/coinbase_broker.py` | Coinbase US nano perp-style futures; paper mode + live CDP JWT; ISOLATED margin; BTC/ETH/SOL/XRP only |
| MES execution | `scheduler/v10_runner.py` → `execution/ibkr_broker.py` | IBKR paper port 7497 (ARCHIVED — dormant) |
| ForecastEx broker | `execution/forecastex_broker.py` | IBKR ForecastEx; SecType=OPT, Exchange=FORECASTX; clientId=3; YES=Right C / NO=Right P; economic markets only |
| ForecastEx lane | `forecast/runner.py` | Discovery (30m), quote harvest (60s), strategy eval (5m), position monitor (30s) |
| ForecastEx DB | `forecast/db.py` | 5 tables: forecast_markets, forecast_contracts, forecast_quotes, forecast_bars, forecast_resolutions |
| ForecastEx primitives | `forecast/primitives.py` | Log-odds math; compute_q_hat; EV; fractional Kelly sizing |
| ForecastEx strategy | `forecast/strategy_engine.py` | 3 families: continuation, mean_reversion, late_repricing; 10-check economics gate |
| ForecastEx discovery | `forecast/discovery.py` | Scans IBKR for economic event contracts, ranks, upserts to DB |
| ForecastEx harvester | `forecast/quote_harvester.py` | Polls quotes every 60s; builds 5m/30m/1h/4h/1d OHLC bars from midpoint |
| Indicators | `data/indicators.py` (`add_all_indicators()`) | SuperTrend, Ichimoku, WAE, Fisher, CHOP, WaveTrend, Laguerre RSI, etc. |
| ML features | `ml/feature_builder.py` | 57 features across 11 groups (imports `indicators/` package) |
| ML training | `ml/walk_forward_trainer.py` + `ml/model_store.py` | XGBoost 60% + LightGBM 40%, PnL regressor, clean data only |
| Indicators package | `indicators/` | atr_regime, cvd, funding_rate, liquidation_levels, macd_advanced, microstructure, open_interest, orderbook, orderflow, rsi_advanced, vwap_mtf, williams_r |
| Economics gate | `risk/economics_gate.py` | Pre-trade fee/funding EV veto (Coinbase 0.03% taker) |
| Learning loop | `learning_loop.py` | 57-feature snapshots, retrain queue, RBI trigger |
| Bayesian learning | `learning/post_trade_analyzer.py` + `learning/signal_performance.py` | Per-signal Bayesian win rates |
| Dynamic weights | `learning/dynamic_weights.py` | Live conviction weights, 5-min cache |
| RBI nightly | `rbi/research_loop.py` + `rbi/backtest_loop.py` + `rbi/incubation_manager.py` | Research 575 combos, promote to live at 25% size |
| Candidate journal | `logging_db/trade_logger.py` + `learning/candidate_labeler.py` | 8-gate journaling, 15m/1h/4h outcome labeling, nightly audit |
| Integrity substrate | `logging_db/trade_logger.py` (`log_trade_integrity`, `log_exit_evaluation`) | Durable trust tiers (verified/suspect/quarantined/excluded) per close; exit quality capture |
| Backtesting | `backtesting/event_backtester.py` + `backtesting/run.py` | Live-faithful candidate-replay backtester; RESEARCH-GRADE only |
| Promotion engine | `backtesting/promotion_engine.py` | Challenger state machine; `PROMOTED_PENDING_HUMAN` requires owner confirmation |
| Config sub-package | `config/venue_specs.py` + `config/alpha_specs.py` | Venue fees + futures-native constants separated from strategy thresholds |
| Dashboard integrity | `dashboard/data/integrity.py` | Truth-tiered metrics: verified/suspect counts, attribution coverage, exit quality, promotion state |
| Operator audits | `scripts/net_truth_audit.py` + `scripts/go_live_audit.py` | Trust-aware net-of-fee scorecards and evidence-backed launch constraints |
| Notifications | `notifications/notification_engine.py` | SQLite only, no Telegram |
| Dashboard | `dashboard/app.py` | Streamlit Operator Panel, 6 tabs: MISSION CONTROL, PERFORMANCE, TRADE APPROVAL, FORECAST TRADING, ARCHIVED FUTURES (MES), SYSTEM SETTINGS |
| DB | `logs/trades.db` | WAL mode SQLite — positions, trades, system_events, scan_candidates, candidate_outcomes, trade_integrity, exit_evaluations, challenger_state, forecast_markets, forecast_contracts, forecast_quotes, forecast_bars, forecast_resolutions |
| Vector memory | `memory/trade_memory.py` | NumPy cosine similarity, SQLite-backed, 8-dim feature vectors |
| Kill switch | `kill_switch.py` | Balance < 75% of ACCOUNT_SIZE → halt all |
| Risk engine | `risk_engine.py` | VaR/CVaR, correlation gates, margin checks |
| Hedge engine | `hedge_engine.py` | Delta-neutral hedge rebalance (every 5 min) |
| Health check | `monitoring/health_check.py` | 7-invariant assertions written to `system_events`; check 7 includes IBKR |
| MCP server | `mcp_server/server.py` | 15 FastMCP tools for Codex integration |
| Verification | `tests/proof/` + `verification/replay.py` + `.github/workflows/ci.yml` | Proof-first pytest harness, dashboard shell tests, deterministic replay, GitHub Actions CI |

### Key Decisions

- **Scanner sources:** Kraken Futures public REST + Binance USDM public REST + Hyperliquid public API (all 3 every scan — intelligence only)
- **Live crypto execution venue:** Coinbase US nano perp-style futures (`coinbase_broker.py`) — BIP/ETP/SLP/XPP (BTC/ETH/SOL/XRP only). Scanner is broader; only these 4 are routed to live execution.
- **Coinbase auth:** CDP JWT / ES256. Credentials: `COINBASE_CDP_KEY_NAME` (organizations/{org_id}/apiKeys/{key_id}) + `COINBASE_CDP_PRIVATE_KEY` (EC PEM, \\n-escaped in .env). Paper mode: no API calls, zero credentials required.
- **Coinbase products (CFTC-regulated, expire Dec 2030):** BIP-20DEC30-CDE (0.01 BTC/contract), ETP-20DEC30-CDE (0.1 ETH/contract), SLP-20DEC30-CDE (5 SOL/contract), XPP-20DEC30-CDE (500 XRP/contract)
- **Coinbase fees:** 0.03% taker, 0.00% maker (Advanced Trade API direct, promotional). Round-trip cost = 0.06%.
- **Fail-closed:** Any symbol not in {BTC,ETH,SOL,XRP} raises `CoinbaseSymbolError` — never routed to live execution.
- **No AI debate for entries:** Two-tower signal engine replaces all v9 debate agents
- **Telegram removed:** Replaced by `notifications/notification_engine.py` (SQLite + dashboard only)
- **Paper = live thresholds:** No reduced thresholds in paper mode (clean data from 2026-04-02)
- **ML training data:** Tagged `pre_v10_contaminated` for all data before 2026-04-02
- **57 features:** 8 price + 6 volume + 5 CVD + 7 momentum + 4 VWAP + 5 OB + 6 deriv + 3 liq + 5 regime + 4 time + 4 onchain
- **Kill switch:** balance < 75% of ACCOUNT_SIZE (= $3,750 on a $5K account), not hardcoded $7,500
- **Live position sizing path:** `scheduler/v10_runner.py` sizes via `position_manager.compute_position_size()`; `risk/unified_sizer.py` is no longer on the live entry path
- **ISOLATED margin** on all perp positions — never CROSS
- **ML model:** PnL regressor (XGBRegressor + LGBMRegressor). Score = `50 + 50*tanh(predicted_pnl / pnl_scale)`. Falls back to 50.0 if no pickle files exist.
- **MES daily loss limit:** reads `FUTURES_DAILY_MAX_LOSS_PTS * FUTURES_NUM_CONTRACTS * MES_POINT_VALUE` (MES_POINT_VALUE=5.00). Never hardcode $150.
- **Integrity tiers (v14.0):** every perp close writes to `trade_integrity` (verified/suspect/quarantined/excluded). Bayesian/Kelly/ML consumers must not use quarantined or excluded rows. Gate: `is_integrity_trusted(close_order_id)`.
- **Replay/synthetic sources:** any `source` containing `"replay"`, `"synthetic"`, `"bootstrap"`, or `"backtest_only"` is set to `excluded` tier and must never influence live Bayesian signal weights.
- **Exit quality (v14.0):** every perp close writes to `exit_evaluations` (`opportunity_loss_pct`, `stop_overshoot_pct`, `mfe_at_exit`, `path_label`). Dashboard reads via `dashboard/data/integrity.py`.
- **`config/` package (v14.0):** `config/__init__.py` re-exports symbols from `config.py` for backward compatibility. Use `config.venue_specs` for venue constants and `config.alpha_specs` for strategy thresholds.
- **Backtesting (v14.0):** `backtesting/event_backtester.py` replays `scan_candidates` through the live stack. Results are tagged `source='candidate_replay'` and remain research-grade only. Promotion requires human confirmation.
- **Nightly audit (v14.0):** `monitoring/nightly_audit.py` runs at 08:00 UTC and checks proof suite health, candidate journal health, funnel analytics, labeling lag, repo drift, Bayesian weight changes, and retention pruning.
- **ForecastEx venue (v15.0):** IBKR ForecastEx exchange (`Exchange=FORECASTX`, `SecType=OPT`). YES = `Right='C'`, NO = `Right='P'`. Cannot short — flatten by buying the opposite right. Zero commission. ~$100 bankroll.
- **ForecastEx pricing:** bid/ask/midpoint ONLY — never last/trade prints. All OHLC bars built from midpoint series.
- **ForecastEx clientId:** 3 (MES = 2). Never collide.
- **ForecastEx risk caps (hardcoded, no override):** max deployed 35%, per-event 10%, max concurrent 2, fractional Kelly cap 0.10.
- **ForecastEx economic markets only:** CPI, NFP, FOMC, Unemployment, PCE, GDP, PPI. Sports/politics/entertainment rejected at discovery.
- **ForecastEx MES archival:** MES lane is dormant — code preserved. Dashboard tab "ARCHIVED FUTURES (MES)". Reactivate: `FUTURES_LANE_ACTIVE=true`.
- **FUTURES_LANE_ACTIVE (v15.1):** gates MES/IBKR activation. Default false. When false: health check skips IBKR, balance returns archived state.
- **FORECAST_LANE_ACTIVE (v15.1):** gates ForecastEx lane startup from main.py. Default false = standalone. When true, main.py spawns daemon thread with its own schedule instance.
- **Forecast readiness states (v15.1):** LANE_NOT_STARTED / BROKER_DISCONNECTED / NO_UNDERLIERS / UNDERLIERS_ONLY / NO_QUOTES / QUOTES_NO_BARS / OPERATIONAL.
- **Discovery stubs (v15.1):** IND visible but OPT unavailable → stub persisted to forecast_markets, no contracts created, dashboard shows enrollment state.
- **IBKR_PORT in config (v15.1):** `config.IBKR_PORT` and `config.IBKR_HOST` exported. No hardcoded 7497 in monitored files.
- **ForecastEx IBKR symbol truth (confirmed 2026-04-15):** FORECASTX uses SecType=IND with short symbols: CPI=573031126, CPIY=712856682, CPIC=727520252, DISSN=806285268, DISSA=804725704. FRED codes (CPIAUCSL/UNRATE/PAYEMS) do NOT exist. Discovery: two-pass IND→OPT.
- **ForecastEx live blocker:** OPT event contracts require live funded IBKR account with ForecastEx enrollment. Paper account (DUP590699) sees IND underliers but OPT layer hangs. IBKR_PORT=7496 (corrected from 7497 in .env).
- **Runtime truth tables (v15.2):** `system_runtime_state` (1 row — process mode, startup_ts, active_lanes) + `lane_runtime_state` (1 row per lane — enabled, active, mode, health, heartbeat). Written at startup; read by dashboard and audit scripts.
- **Lane registry (v15.2):** `runtime/lane_registry.py` — single control plane: crypto=always active, forecast=FORECAST_LANE_ACTIVE, mes_archived=FUTURES_LANE_ACTIVE (default false).
- **Incident model (v15.2):** `incidents` table groups system_events by lane+fingerprint. Archived MES incidents suppressed when FUTURES_LANE_ACTIVE=false.
- **Position reconciler (v15.2):** `runtime/position_reconciler.py` — startup reconciliation of scale_33_done/scale_66_done against trades ledger. Trade ledger outranks stale flags.
- **Allocator scaffold (v15.2):** `runtime/allocator.py` — GlobalAllocator stub; full cross-lane ranker deferred to v16.0.
- **Economics interface (v15.2):** `runtime/economics.py` — crypto=0.03% taker/0.06% round-trip, forecast=0%, mes_archived=archived.
- **Live audit hooks (v15.2):** `scripts/live_runtime_audit.py` + `scripts/lane_status_audit.py`. Run after every restart.

### Go-Live Readiness (dashboard SYSTEM tab → READINESS TRACKER)

Owner decides when to go live. These are informational readings, not system gates:
- Clean trade count (source=clean_paper_v10 or live_v10)
- Win rate on clean trades
- Profit factor on clean trades
- Worst single day as % of account
- Days running on clean data
- Economics gate veto rate
- Kill switch triggers (14d)

### v14.0 Self-Improving Trust Substrate (applied 2026-04-14)

- `logging_db/trade_logger.py`: added durable `trade_integrity`, `exit_evaluations`, and `challenger_state` tables plus helper APIs for trust tiers, exit quality capture, and challenger-state persistence.
- `scheduler/v10_runner.py`: every full close now writes integrity tier and exit-quality evaluations on the live close path; replay/synthetic sources are kept research-grade and blocked from live-equivalent learning.
- `learning/signal_performance.py`: `trade_attribution` DDL/migrations aligned to v14 lineage fields and Bayesian updates now gate out quarantined/excluded closes.
- `backtesting/event_backtester.py` + `backtesting/run.py`: added live-faithful candidate replay backtesting surface with explicit research-only tagging.
- `backtesting/promotion_engine.py`: added challenger promotion state machine; promoted strategies stop at `PROMOTED_PENDING_HUMAN` and never auto-apply to live.
- `config/__init__.py` + `config/venue_specs.py` + `config/alpha_specs.py`: split venue and strategy constants into a futures-native config package without breaking old imports.
- `dashboard/data/integrity.py`: added truth-tiered dashboard reads for integrity counts, attribution coverage, exit quality, and promotion state.
- `monitoring/nightly_audit.py`: extended self-maintenance loop to audit proof health, journal health, funnel anomalies, labeling lag, version drift, Bayesian changes, and retention.
- `scripts/migrate_integrity_backfill.py`: idempotent backfill for historical close integrity tiers.
- `tests/proof/test_integrity_tiers.py`: added 10 proof tests; v14 branch proof suite reached 52/52 passing when Claude finished this layer.

### v13.9 Net Truth Audit + Go-Live Surface (applied 2026-04-14)

- `scripts/truth_audit_lib.py` (NEW): trust-aware audit engine that reads the real SQLite DB, splits evidence into trustworthy headline close-ledger truth vs relaxed/strict attribution diagnostics, parses exit/setup metadata from trade notes, computes net-of-fee scorecards, and generates evidence-backed launch constraints.
- `scripts/net_truth_audit.py` (NEW): operator CLI for repeatable terminal audits (`python3 scripts/net_truth_audit.py`) with trustworthy headline metrics, contamination deltas, direction/exit/setup/symbol breakdowns, recency windows, and attribution coverage visibility; `--json` emits machine-readable output.
- `scripts/go_live_audit.py` (NEW): operator CLI for launch-night recommendations (`python3 scripts/go_live_audit.py`) that turns net truth into concrete constraints such as constrained-live mode, short suppression/deweighting, stale-sample warnings, and symbol watch/suppress lists; `--json` emits machine-readable output.
- `tests/proof/test_net_truth_audit.py` + `tests/proof/test_go_live_audit.py` (NEW): proof coverage for dirty-row exclusion from headline truth, short-win accounting, fee drag, exit aggregation, contamination visibility, and recommendation behavior.
- Real DB truth at the time this audit surface was added: trustworthy close-ledger sample was 229 clean-source close legs after exclusions, net `-$0.27` after fees; longs were positive, shorts were negative; strict setup-level attribution coverage remained too thin to justify live-weight promotion.

### v13.8 Launch Integrity Hotfixes (applied 2026-04-14)

- `learning/post_trade_analyzer.py`: short-trade attribution now computes PnL by direction; attribution fail-closes on missing lineage (`trade_ref`), missing active entry signals, suspect exit-price events, and replay/synthetic sources. Excluded trades still write `trade_attribution` rows, but Bayesian signal stats and agent accuracy are not updated. Excluded rows are marked with `lesson` prefix `INTEGRITY EXCLUDE:`.
- `scheduler/v10_runner.py`: close-time learning/attribution now reloads the original entry snapshot from `trade_features` instead of using exit-state features; new closes pass deterministic `trade_ref` values into attribution; exit price sanity tightened to 5% correction against live price, and >25% candle/live mismatches are flagged suspect and excluded from learning.
- `dashboard/data/execution.py`: execution quality and failure counts now ignore integrity-excluded attribution rows; repeated identical execution errors, scan dropouts, and duplicate-close spam are collapsed into distinct incidents for truer dashboard counts.
- `tests/proof/`: added proof coverage for short PnL sign correctness, fail-closed lineage/signal truth handling, suspect-price exclusion, replay exclusion from learning, entry snapshot reload, and dashboard failure-count de-spam.

### v13.7 Autonomous Journaling Operationalization (applied 2026-04-13)

- `logging_db/trade_logger.py`: `candidate_outcomes` DDL now includes `price_15m`/`ret_15m_pct` inline; `prune_old_candidates()` 90d/30d retention; `get_logger()` singleton; `kill_switch_log` table added
- `learning/candidate_labeler.py`: 15m forward outcome — 50-bar series, `_compute_15m_metrics()`, `price_15m`/`ret_15m_pct` written to DB
- `monitoring/nightly_audit.py`: exception-only notifications with severity cooldowns (INFO 23h / WARN 6h / CRIT 1h); `_check_candidate_funnel()` anomaly detection; `_check_retention()` + `prune_old_candidates()` call
- `dashboard/data/journal_health.py` (NEW): `get_journal_health()` for dashboard health panel
- `dashboard/widgets/system_settings/dev_config.py`: "Learning & Journaling Health" expander with 7 metrics + funnel + veto tables
- `.github/workflows/ci.yml`: branch list, test target, deps, env vars all aligned
- `tests/proof/test_candidate_journal.py`: 5 new tests; 25/25 total green

### v13.6 Candidate Journaling + Automated Outcome Labeling (applied 2026-04-13)

- `logging_db/trade_logger.py`: added `scan_candidates` and `candidate_outcomes` tables to `init_db()`; added `log_scan_candidate()`, `get_unlabeled_candidates()`, `log_candidate_outcome()`, `get_candidate_journal_stats()` helpers
- `scheduler/v10_runner.py`: added `_journal_scan_candidate()` module-level helper; added `scan_id` (UUID hex) per scan cycle; journaling calls at all 7 decision gates: `dual_exposure_block`, `cooldown_block`, `risk_block`, `data_unavailable`, `below_threshold`, `econ_veto`, `sizing_zero`, `entered`; wired `candidate_labeler` (every 15 min, background thread) and `nightly_audit` (daily 08:00 UTC) as scheduled jobs
- `learning/candidate_labeler.py` (NEW): background labeling worker; finds unlabeled candidates >= 4h old; fetches forward 1h candles; computes 1h/4h returns, MFE, MAE, hit_1r, hit_2r, hit_stop; writes to `candidate_outcomes` and marks `scan_candidates.labeled=1`; bounded batch (50 rows/run); never blocks live scan
- `monitoring/nightly_audit.py` (NEW): automated nightly proof + drift + learning audit; runs pytest proof suite in subprocess; checks candidate journaling health (count, labeling rate, backlog); checks repo version drift; checks learning layer health (signal_stats rows, ML snapshots); writes structured report to system_events; can also be run standalone
- `tests/proof/test_candidate_journal.py` (NEW): 10 proof tests covering all new components end-to-end
- `tests/proof/test_dashboard_harness.py`: updated tab name assertion `CRYPTO PERFORMANCE` → `PERFORMANCE` (matches v14 dashboard rename)

### v13.4 Proof Infrastructure + Repo Truth Alignment (applied 2026-04-10)

- `logging_db/trade_logger.py`: added `get_logger()` compatibility wrapper for current live callers (`risk_engine.py`, `position_manager.py`, `kill_switch.py`, RBI modules) and added `kill_switch_log` table creation to `init_db()`
- `risk_engine.py`: startup balances now initialize from configured `ACCOUNT_SIZE` ($5,000) instead of a hardcoded value, so drawdown / kill-switch math tracks the real paper account from process start
- `kill_switch.py`: threshold docs aligned to configured account size; `check_balance()` now defaults its initial balance from config ($5,000)
- `dashboard/data/execution.py`, `dashboard/widgets/mission_control/decision_quality.py`, `dashboard/widgets/crypto_performance/deep_analysis.py`: `trade_attribution` reads aligned to the real schema (`created_at`, no `direction` column)
- `dashboard/data/health.py` + `main.py`: startup event wording aligned so restart counts match runtime (`Bot started — ... v13.4`)
- `CLAUDE.md` + `scripts/validate.py`: repo memory and pre-flight validation now read the current version/source-of-truth state (`AGENTS.md` first, `CLAUDE.md` fallback) so startup checks match runtime reality
- `scripts/validate.py`: optional imports now degrade to warnings even on runtime import errors (for example `pandas_ta`/`numba` cache issues on Python 3.14) instead of aborting validation
- `tests/proof/`: new proof-first pytest suite covering scanner, economics gate, position sizing, risk engine, kill switch, attribution/logging, dashboard harness, and deterministic replay
- `verification/replay.py`: deterministic scanner → signal → economics → sizing → risk → attribution harness for staging proofs
- `.github/workflows/ci.yml` + `pytest.ini`: GitHub Actions proof suite runs automatically on pushes and pull requests; default `pytest` target is the proof harness

### v13.3 ML Upgrade + Dashboard Clarity (applied 2026-04-06)

- `signal_engine.py`: `thesis_still_valid()` now uses regime-conditional thresholds — TRENDING=30%, RANGING=15%, HIGH_VOL=35%, UNKNOWN=25% — instead of fixed 25%. Faster exits in RANGING (fragile setups), more patience in HIGH_VOL (noisy signal).
- `ml/walk_forward_trainer.py`: Binary classifier (predict `won`) replaced with PnL regressor (predict `net_pnl` in USD). XGBRegressor + LGBMRegressor with `reg:squarederror`/`regression` objectives. `pnl_scale` (std of training PnL) saved as `{pair}_{dir}_meta.pkl` alongside models. `_compute_metrics` now gates on `predicted_pnl > 0` instead of `probability >= 0.5`. Optuna HPO uses real PnL Sharpe instead of probability-proxy Sharpe.
- `ml/model_store.py` (NEW): `ModelStore` class loads saved regressor pickles and `pnl_scale` metadata. `predict_ml_score(features, direction)` returns 0-100 via `50 + 50*tanh(predicted_pnl / pnl_scale)`. File-mtime cache — reloads from disk when model is updated by retrainer.
- `scheduler/v10_runner.py`: Both `se.score()` and `check_exits()` now call `_get_model_store()` — returns `ModelStore` if any pickle files exist in `ml/models/`, else returns `None` (ML stays neutral at 50.0). Refreshes hourly.
- `dashboard/app.py`: `get_mes_all_time_stats()` now filters `ts >= '2026-04-02'` and excludes contaminated sources; exit stack description updated to show regime-conditional thresholds; scanner source updated to mention all 3 exchanges.

### v13.2 Gate Architecture + Execution Quality (applied 2026-04-06)

- `risk/economics_gate.py`: volume floor aligned $3M → $2.5M (matches scanner floor, eliminates dead zone); spread gate added (`_MAX_SPREAD_PCT_GATE = 0.0025`, 25 bps global fallback); depth gate added (`_MIN_NEAR_DEPTH_USD = 5_000`, $5K each side, only fires when depth data available); EV floor upgraded to cost-aware formula: `max(static_tier_b, 2.0 × effective_round_trip_cost)` where effective cost = fees + spread/2 + funding carry
- `scheduler/v10_runner.py`: price sanity check tightened 20% → 5% global fallback (old 20% threshold missed ETH $19 vs $2130 candle issue); depth fields (`bid_depth_usd`, `ask_depth_usd`) now extracted from candidate and passed to economics gate; veto suppression upgraded from time-only cooldown to 3-strike system — first 3 occurrences log normally, 4th emits "suppressing further" notice, silent thereafter until 30-min window resets
- `AGENTS.md`: scanner sources corrected — code actually uses 3 sources (Kraken Futures + Binance USDM + Hyperliquid) every scan cycle; docs were Kraken-centric but code was not

### v13.1 Scanner/Funnel Fixes (applied 2026-04-06)

- `scanner.py`: `_MIN_VOLUME_24H_USD` raised $500K → $2.5M — eliminates MOODENG/ZETA/VIRTUAL/FET from reaching the signal engine
- `scheduler/v10_runner.py`: economics veto log cooldown added (30 min between identical veto messages per symbol+direction+reason); per-scan funnel summary logged at INFO (`funnel: N candidates → scored=X (dropped: dual=Y cooldown=Z) → entries=A (~B vetoed/skipped)`)
- `perps_engine.py`: duplicate close idempotency guard — full close of same symbol within 60s returns None and logs warning; check is atomic under `_lock` to block concurrent callers
- `position_manager.py`: hard stop reason now uses `:.8g` format (e.g. `3.5191e-06`) instead of `:.4f` (`0.0000`) for micro-priced assets like PEPE

### v13 Strategy Optimization (applied 2026-04-05)

- `risk/economics_gate.py`: `stop_multiplier` parameter added — v10_runner now passes 3.0 (was hardcoded 1.5); EV tier thresholds doubled to match (A+=1.6%, A=0.8%, B=0.3%); edge_score cap 3.0%
- `signal_engine.py`: WAE explosion long/short now requires both fast AND slow MACD histogram to agree (eliminates fading momentum false fires); `_live_trade_days()` ISO parse fixed (was always returning 0, keeping ML weight at 20%); thesis threshold docstring corrected to 0.25
- `scheduler/v10_runner.py`: Tier 1 composite floor added (50.0); Tier 2 threshold raised 50→58; win_rate_estimate now 0.54 (Tier 1) or 0.50–0.60 scaled by composite (Tier 2); stop_multiplier=3.0 passed to economics gate
- `position_manager.py`: Kelly query fixed to cover SHORT exits (was `action='SELL'` missing all SHORT trade outcomes)
- DB: REZ phantom -$2.5M close purged; REZ chain tagged `source='pre_v10_contaminated'`

### v10.1 Changes vs v10.0 (applied 2026-04-02–04)

- `scanner.py`: Kraken Futures public REST (no Binance geo-block, no auth required)
- `signal_engine.py`: paper threshold reduction REMOVED
- `execution/bybit_broker.py`: DELETED (geo-blocked for US)
- `execution/ibkr_broker.py`: telegram import removed → notification_engine
- `risk/economics_gate.py`: NEW — pre-trade fee/funding EV veto
- `risk/unified_sizer.py`: replaced 6-factor chain with 3-factor formula; Kelly applied; $100 hard cap
- `scheduler/v10_runner.py`: TV signals wired, economics gate wired; cooldown after close (2h); SQLite entry guard; position restore on startup
- `perps_engine.py`: `load_positions_from_db()` restores positions from SQLite on restart
- `ml/walk_forward_trainer.py`: training filter excludes contaminated data
- `execution/binance_broker.py`: hard telegram import replaced with no-op stubs
- `risk/risk_manager.py`: telegram halt alert replaced with notification_engine
- `position_manager.py`: kill switch docstring fixed ($7,500 → 75% of ACCOUNT_SIZE)
- `legacy/` directory DELETED — all v9 code removed from repo
- DB purged: api_costs, debate_results, agent_stats, backtest_results, pre-v10 trades/signals
- `alerts/telegram_alert.py` references removed from monitoring/health_check.py and scripts/check_readiness.py
- Webull credentials removed from .env

## Project Structure (v14.0 — live files only)

```
algo_trading_final/
├── AGENTS.md                 ← You are here (keep current)
├── CHANGELOG.md              ← Append every change: bash scripts/log_change.sh "..."
├── main.py                   ← Entry: python3 main.py --mode paper
├── config.py                 ← Backward-compatible root config module
├── scanner.py                ← Multi-exchange perp scanner (Kraken + Binance + Hyperliquid) (DO NOT TOUCH)
├── signal_engine.py          ← Two-tower signal engine (DO NOT TOUCH)
├── position_manager.py       ← Live position sizing + 7-priority exit stack (DO NOT TOUCH)
├── perps_engine.py           ← Perp execution wrapper (DO NOT TOUCH)
├── risk_engine.py            ← VaR/CVaR/correlation/margin
├── hedge_engine.py           ← Delta-neutral hedge
├── kill_switch.py            ← Hard halt on balance < 75% ACCOUNT_SIZE
├── learning_loop.py          ← Post-trade ML snapshot + RBI trigger
├── pair_intelligence.py      ← Per-pair win rate / vol profile (reads trade_attribution)
├── run_backtest.py           ← Standalone v9-era backtest runner (reference only)
│
├── scheduler/
│   ├── v10_runner.py         ← THE live loop (scan/exit/hedge/kill/rbi) (DO NOT TOUCH)
│   └── __init__.py
│
├── data/
│   ├── indicators.py         ← add_all_indicators() — all v10 indicators (DO NOT TOUCH)
│   ├── historical_data.py    ← get_candles() — OHLCV from Kraken / yfinance fallback
│   └── edge_monitor.py       ← Rolling edge score per market (read by dashboard)
│
├── indicators/               ← v10 indicator modules (all imported by ml/feature_builder.py)
│   ├── atr_regime.py
│   ├── cvd.py
│   ├── funding_rate.py
│   ├── liquidation_levels.py
│   ├── macd_advanced.py
│   ├── microstructure.py
│   ├── open_interest.py
│   ├── orderbook.py
│   ├── orderflow.py
│   ├── rsi_advanced.py
│   ├── vwap_mtf.py
│   └── williams_r.py
│
├── ml/
│   ├── feature_builder.py    ← 57 features (DO NOT TOUCH)
│   ├── walk_forward_trainer.py ← XGBoost + LightGBM walk-forward (DO NOT TOUCH)
│   ├── model_store.py        ← Model persistence (DO NOT TOUCH)
│   ├── calibration.py        ← Platt scaling
│   ├── online_learner.py     ← Incremental updates between retrains
│   └── regime_classifier.py  ← TRENDING/RANGING/HIGH_VOL/UNKNOWN
│
├── risk/
│   ├── economics_gate.py     ← Pre-trade fee/funding EV veto (DO NOT TOUCH)
│   ├── unified_sizer.py      ← Legacy/reference sizer — not on live v10_runner entry path
│   ├── risk_manager.py       ← Thin orchestrator
│   ├── drawdown_controller.py
│   ├── position_sizer.py
│   ├── stop_loss_manager.py
│   ├── risk_limits.py
│   ├── var_calculator.py     ← VaR 95/99%
│   ├── volatility_regime.py
│   └── edge_monitor.py       ← Rolling edge score per market
│
├── rbi/
│   ├── research_loop.py      ← Nightly: 575 signal combo tests
│   ├── backtest_loop.py      ← Walk-forward validation for promoted combos
│   └── incubation_manager.py ← Live trading at 25% size for new combos
│
├── learning/
│   ├── post_trade_analyzer.py  ← Bayesian attribution on every close (DO NOT TOUCH)
│   ├── signal_performance.py   ← Running signal stats (DO NOT TOUCH)
│   └── dynamic_weights.py      ← Live conviction weights, 5-min cache (DO NOT TOUCH)
│
├── execution/
│   ├── coinbase_broker.py    ← Perp execution (paper + live) — Coinbase US nano perp futures (BIP/ETP/SLP/XPP)
│   ├── binance_broker.py     ← Legacy — not on live crypto path; kept for reference
│   └── ibkr_broker.py        ← MES futures — IBKR via ib_insync, paper port 7497
│
├── backtesting/
│   ├── event_backtester.py   ← Candidate replay / historical / stress backtester
│   ├── promotion_engine.py   ← Challenger promotion state machine
│   └── run.py                ← Backtest CLI entrypoint
│
├── config/
│   ├── __init__.py           ← Re-exports root config symbols for compatibility
│   ├── venue_specs.py        ← Venue fees / exchange-specific constants
│   └── alpha_specs.py        ← Strategy / promotion thresholds
│
├── notifications/
│   └── notification_engine.py ← SQLite only (DO NOT TOUCH)
│
├── logging_db/
│   └── trade_logger.py       ← SQLite trades.db WAL mode (DO NOT TOUCH)
│
├── dashboard/
│   ├── app.py                ← Streamlit Operator Panel (5 tabs, widget architecture) (DO NOT TOUCH)
│   └── data/
│       └── integrity.py      ← Truth-tiered integrity / exit-quality data helpers
│
├── memory/
│   └── trade_memory.py       ← 8-dim NumPy cosine similarity, SQLite-backed
│
├── monitoring/
│   ├── health_check.py       ← 7-invariant health assertions written to system_events
│   └── nightly_audit.py      ← 08:00 UTC automated proof + integrity + drift audit
│
├── mcp_server/
│   └── server.py             ← 15 FastMCP tools; start: python3 mcp_server/server.py
│
├── alerts/
│   └── __init__.py           ← Empty — telegram_alert.py deleted in v10
│
├── tests/
│   ├── proof/               ← Default pytest target — self-verification harness
│   ├── test_indicators.py
│   ├── test_risk_manager.py
│   └── test_broker_paper.py
│
├── verification/
│   ├── __init__.py
│   └── replay.py            ← Deterministic replay/staging harness
│
├── scripts/                  ← Ops scripts (mostly still valid for v10)
│   ├── weekly_report.py      ← python3 scripts/weekly_report.py
│   ├── migrate_clean_start.py ← Already run 2026-04-02
│   ├── migrate_integrity_backfill.py ← Idempotent integrity-tier backfill
│   ├── check_v10_readiness.py ← Readiness checker
│   ├── net_truth_audit.py    ← Trust-aware net-of-fee operator audit
│   ├── go_live_audit.py      ← Evidence-backed launch constraint audit
│   ├── truth_audit_lib.py    ← Shared trust-audit metrics engine
│   ├── validate.py           ← Pre-flight validator
│   ├── tradingview_webhook.py ← TradingView Pine Script alert ingestion
│   ├── tradingview_pine.pine ← Pine Script v5 template
│   ├── log_change.sh         ← Append to CHANGELOG.md
│   ├── backup_db.sh / backup_credentials.sh
│   └── install_services.sh   ← launchd auto-start setup (run once)
```

## Signal Engine

Two deterministic towers → composite score → regime threshold gate.

| Tower | Method | Signals |
|-------|--------|---------|
| Technical | Rule-based point scoring, 0-100 | CVD divergence, MACD multi-variant, RSI divergence, funding squeeze, VWAP reclaim, OB imbalance, Williams %R, liq cascade, vol spike, Fear & Greed, options skew, whale signal |
| ML | XGBoost 60% + LightGBM 40% PnL regressor, 0-100 | 57 features across 11 groups |

Entry: composite >= regime threshold: TRENDING_UP/DOWN=58, RANGING=58, HIGH_VOL=60, LOW_VOL=56, UNKNOWN=58. Same threshold paper and live. WAE explosion requires both fast AND slow MACD histogram to agree.

## 7-Priority Exit Stack (position_manager.py + v10_runner.py)

1. Trailing stop — regime-aware: RANGING=1.0x ATR activation / 2.5x width; TRENDING=1.5x / 4.5x; HIGH_VOL=2.0x / 5.5x. Compresses toward 50% of nominal when `signal_health < 65%`.
2. Take profit scale-out — conviction-adaptive: blends `entry_composite_score` (60%) + regime extension (40%). First cut 20-30% at 2.0-4.0R; second cut 25% at 4.5-8.0R. Denominator = `abs(entry - stop_price)`.
3. Thesis score exit — composite < entry x regime_fraction: TRENDING=30%, RANGING=15%, HIGH_VOL=35%, UNKNOWN=25%. ATR-proportional hold gate: LONG 1h-6h, SHORT 2h-12h.
4. Hard stop — stop-market on exchange, never widened
5. Risk forced exit — margin breach / drawdown / correlation
6. Kill switch — balance < 75% of ACCOUNT_SIZE / API errors / latency
7. Dead-money exit (`exit_type=dead_money_exit`) — held >24h and `|current - entry| < 0.5x atr_at_entry` with no trailing activation / scale-out; hard backstop at 96h.

## Learning Architecture

Every closed trade triggers `learning_loop.record_closed_trade()`:
1. Persists 57-feature snapshot + outcome to `ml_feature_snapshots`
2. Calls `learning/post_trade_analyzer.py` → Bayesian attribution
3. Checks `ml_retrain_queue` → triggers walk-forward retrain when enough data accumulates
4. Feeds incubating RBI strategies

Bayesian weight formula:
```
posterior_wr = (PRIOR_N * prior_p + N * obs_win_rate) / (PRIOR_N + N)
bayesian_pts = prior_pts * (posterior_wr / prior_p)
```
- PRIOR_N = 20 phantom trades
- MIN_FIRES_TO_LEARN = 10
- Cap: 2.5x original prior points, per signal per regime

Candidate journaling: 8 decision gates logged per scan (`dual_exposure_block`, `cooldown_block`, `risk_block`, `data_unavailable`, `below_threshold`, `econ_veto`, `sizing_zero`, `entered`). Labeler runs every 15 min computing 15m/1h/4h returns, MFE, MAE, hit_1r, hit_2r. Nightly audit runs at 08:00 UTC.

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
- Default **3x** leverage, max **10x**
- ISOLATED margin on all perp positions — never CROSS
- Coinbase taker fee: **0.030%** (modeled in economics_gate.py before every entry; round-trip = 0.060%)
- Kill switch at balance < **75% of ACCOUNT_SIZE**
- Economics gate EV tiers: A+=1.6%, A=0.8%, B=0.3%; `stop_multiplier=3.0`; spread gate=25 bps; depth gate=$5K/side
- Volume floor: $2.5M/24h (scanner + economics gate aligned)

## Key Data Formats

### Trade log (SQLite trades table)
ts, strategy, broker, symbol, action, order_type, qty, price,
value_usd, fee_usd, pnl_usd, paper, order_id, notes

### Position (risk_manager in-memory + SQLite open_positions table)
symbol, strategy, qty, entry, stop, target, high_since_entry, ts_entry

### Vector Memory Schema (logs/memory/trade_memory.db)
Table: trade_experiences — NumPy cosine similarity, SQLite storage (no LanceDB)
8-dim vector: [rsi/100, tanh(macd*10), adx/100, min(vol/5,1), regime_trending, regime_ranging, regime_volatile, regime_unknown]

## How to Start the System
```bash
python3 main.py --mode paper       # Force paper
python3 scripts/go_live.py         # Controlled live transition (Claude-safe path)
python3 scripts/go_paper.py        # Return to paper launchd bot
streamlit run dashboard/app.py --server.runOnSave true  # Dashboard on :8501
python3 mcp_server/server.py       # MCP server (Codex integration)
python3 scripts/weekly_report.py   # Weekly performance report
python3 -m pytest                  # Proof-first verification suite
```

## Notifications (v10)
All alerts written to `system_events` SQLite table via `notifications/notification_engine.py`.
Dashboard Notifications panel reads and displays them in real time.
No Telegram, no email. Works offline. `alerts/` directory is empty (telegram_alert.py deleted).

## Auto-Start & Auto-Restart
```bash
bash scripts/install_services.sh
```
Registers three launchd services:
- **com.algotrading.king** — starts bot on login, restarts on crash (paper mode)
- **com.algotrading.backup** — backs up DB + credentials at 2:00 AM daily
- **com.algotrading.readiness** — readiness check at 7:00 AM daily

Service logs: `logs/service/`

Controlled live transition:
- `python3 scripts/go_live.py` — verifies Coinbase live auth, stops the paper launchd bot, starts a live `boot.py` process, waits for runtime truth to confirm `mode=live`
- `python3 scripts/go_paper.py` — stops the live `boot.py` process and restores the paper launchd bot

## TradingView Integration (v10 — still wired)
TradingView Pine Script → webhook → SQLite `system_events` (source='tradingview')
v10_runner reads these every scan cycle, prepends as candidates with edge_score=0.6.
```bash
python3 scripts/tradingview_webhook.py   # HTTP server (port 8765)
ngrok http 8765                          # Expose to internet
```
Set TV_WEBHOOK_SECRET in .env. Symbol mapping: BTCUSD → BTCUSDT.

## MES Contract Symbols (update quarterly)
- Q2 2026 (Apr-Jun): `MESM26` — **ACTIVE** (`MES_EXPIRY=20260619`)
- On quarterly roll: update `MES_EXPIRY` in `.env`

## Common Errors and Fixes

**pandas-ta import error** → `pip install "pandas-ta>=0.4.67b0"`
**XGBoost openmp error** → `brew install libomp`
**IBKR connection failed** → TWS must be running, API enabled in TWS settings; port from `IBKR_PORT` in .env (7496=live, 7497=paper)
**DB lock error** → WAL mode is on; usually a stale connection. Restart bot.
**Schedule not running** → Check nothing is blocking the while True loop in v10_runner.py
**TV webhook 403** → TV_WEBHOOK_SECRET in .env doesn't match Pine Script input
**launchd not starting** → `launchctl list | grep algotrading`; check logs/service/bot_error.log
**DB backup fails** → `sqlite3 --version` to confirm CLI installed
**Kraken scanner empty** → Check internet / Kraken status at futures.kraken.com
**ML gate always 0.5** → Not enough clean trades yet (< MIN_TRADES_FOR_ML). Normal during early paper phase.
**IBKR Error 200** → Use `localSymbol='MESxxx'` rather than `lastTradeDateOrContractMonth`

## Dashboard Views
1. THE KING — Lakers gold/navy, LeBron quotes, championship energy (default)
2. SAIYAN MODE — Dragon Ball Z, power levels, ki energy bars
3. FILM ROOM — Chalk/blackboard, full reasoning, no animations
4. RING CEREMONY — Unlocks on milestones, trophy room

## LeBron Quotes Used in Dashboard
Morning: "We're in the lab. Let's get to work."
Win: "That's preparation meeting opportunity."
Loss: "Losses are tuition. On to the next."
Halt: "Not today. Live to play tomorrow."
Goal: "We came, we worked, we're done."
Patience: "Sometimes the best move is no move."
New high: "This is what the work looks like."
Motivation 1-5: "Strive for greatness." / "I like criticism. It makes you strong." / "I promise you I will do everything in my power." / "The best come from somewhere. Remember yours." / "Nothing is given. Everything is earned."

## Version History (compact)

| Version | Date | Summary |
|---------|------|---------|
| v1.0 | 2026-03 | Basic MACD equity + crypto, manual watchlist |
| v2.0 | 2026-03 | AI debate engine, auto-screener, Tradovate futures, LeBron dashboard |
| v3.x | 2026-03-22–24 | Extended thinking exits, WAL crash safety, launchd auto-restart, 8-signal gate, Bybit perps, TradingView webhook, ATR math signals |
| v5.0 | 2026-03-25 | True Brain: Bayesian attribution, dynamic weights, price archive flywheel, NumPy vector memory |
| v8.0 | 2026-03-26 | 3-agent debate (Bardock/Vegeta/Krillin), LightGBM gate, walk-forward OOS, RBIPMS framework |
| v9.0 | 2026-03-26–28 | Risk decomposition (5 modules), MCP server, parallel lane scanning, SUPER SCORE, Binance Spot, Lane 3 prediction markets |
| v9.x | 2026-03-30 – 2026-04-01 | Perp time-exit watchdog, ML data poison fix, full-market perp scanner, race condition lock, dashboard overhaul |
| v10.0 | 2026-04-01 | Full rewrite: 3-agent debate → two-tower signal engine, 57-feature ML, 6-priority exit stack, RBI loop, clean architecture |
| v10.1 | 2026-04-02 | Live-readiness overhaul: Kraken scanner, economics gate, sizer simplification, clean ML data (tagged pre_v10_contaminated), Bybit deleted |
| v10.1 cleanup | 2026-04-03 | All v9/legacy code, dead imports, stale DB data, and old credentials purged; legacy/ directory deleted; repo and DB fully clean for go-live |
| v13.1–13.4 | 2026-04-05–10 | Scanner/funnel fixes, strategy optimization, ML PnL regressor, proof infrastructure |
| v13.5 | 2026-04-13 | Conviction-adaptive exit stack, dead-money exit groundwork, dashboard clarity |
| v13.6 | 2026-04-13 | Candidate journaling at 8 gates, automated outcome labeling, nightly audit |
| v13.7 | 2026-04-13 | 15m labeling, exception-only notifications, funnel analytics, retention pruning, CI fix |
| v13.8 | 2026-04-14 | Launch integrity hotfixes: short attribution PnL fix, fail-closed learning truth, tighter exit sanity, dashboard de-spam |
| v13.9 | 2026-04-14 | Net truth audit + go-live audit surface for trustworthy net-of-fee launch decisions |
| v14.0 | 2026-04-14 | Self-improving architecture: integrity tiers, candidate replay backtester, promotion engine, futures config sub-package, dashboard truth surfaces, recurring self-maintenance loops |
| v14.1 | 2026-04-14 | Coinbase US crypto lane migration: coinbase_broker.py (CDP JWT/ES256, 4 CFTC products), fee model → 0.03% taker, fail-closed CoinbaseSymbolError, 158 proof tests |
| v15.0 | 2026-04-15 | ForecastEx event-contract lane: forecastex_broker.py, 5 new DB tables, log-odds engine, 3 strategy families, 10-check economics gate, fractional Kelly, FORECAST TRADING dashboard tab, MES archived, 195 proof tests |
| v15.1 | 2026-04-15 | Lane gating: FUTURES_LANE_ACTIVE/FORECAST_LANE_ACTIVE flags; forecast readiness state machine; discovery stubs; Mission Control dedup; activity feed DB-first; dead-money partial-close exempt; IBKR_PORT in config; 205 proof tests |
| v15.2 | 2026-04-15 | Runtime truth layer: system/lane state tables, lane registry, incident model, position reconciler, allocator scaffold, economics interface, live audit hooks, 219 proof tests |
| v15.3 | 2026-04-15 | Repo truth closure: Desktop path purge, repo_truth_gate.py, stronger git hooks/CI, hook path hardening, 231 proof tests |
| v15.4 | 2026-04-15 | Final truth closure: tilde Desktop detection, markdown truth surfaces, pre-commit truth gate, stale 7497 cleanup, 237 proof tests |
| v15.5 | 2026-04-15 | Dashboard/runtime truth fixes: forecast schedule isolation, archived-lane error filter, stagnant false-positive fix using live perps state, 240 proof tests |
| v15.6 | 2026-04-15 | Controlled live-launch path: mode-aware boot.py, go_live.py/go_paper.py transitions, hook allowlist for controlled mode changes, Claude-safe live launch docs |

## GitHub
- Repository: `futureisnowtech/trading-bot-main` (private)
- Active branch: `feature/v10-rebuild`
- Push: `git push origin feature/v10-rebuild` (SSH configured)

## Codex's Standing Instructions
When making any change to this project:
1. Update AGENTS.md if the change affects how the system works
2. Append to CHANGELOG.md: `bash scripts/log_change.sh "Description"`
3. Commit when a logical unit of work is done
4. Never commit .env or logs/ — .gitignore already excludes them
5. Always use `python3`, not `python`
6. Read a file before editing it
7. Test paper mode before any live-mode changes: `python3 main.py --mode paper`
8. Proof tests are part of done — if a change touches data-layer truth, logging, integrity, or operator audits, add or update proof tests that would have caught the bug.
9. No tunnel vision on partial fixes — grep for all callers and related surfaces touching the same data before declaring a change complete.
10. MES position dict keys: `"entry"` (not `"entry_price"`), `"side"` (`"LONG"`/`"SHORT"`), `"qty"` (always positive). Never infer direction from `qty > 0`.
