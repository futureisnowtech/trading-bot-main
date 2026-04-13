# CLAUDE.md — Algo Trading System Knowledge Base
# Auto-loaded by Claude Code at the start of every session.
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

## Current Version: v13.7 (2026-04-13)

**Active branch:** `feature/v10-rebuild`
**Clean paper trading started:** 2026-04-02

### Live Architecture (source of truth)

| Component | File | Role |
|---|---|---|
| Scanner | `scanner.py` | **3 sources**: Kraken Futures + Binance USDM perps + Hyperliquid, 7-filter, top 50 candidates |
| Signal engine | `signal_engine.py` | Two-tower: technical 0-100 + ML 0-100 → composite |
| Entry runner | `scheduler/v10_runner.py` | Scan loop, tier selection, economics gate, setup detection, execution handoff |
| Position sizing | `position_manager.py` | Kelly + ATR sizing, leverage schedule, deployment caps |
| Exit manager | `position_manager.py` | 6-priority exit stack (trailing/scale/thesis/hard-stop/risk/kill) |
| Perp execution | `perps_engine.py` → `execution/binance_broker.py` | Paper mode, ISOLATED margin |
| MES execution | `scheduler/v10_runner.py` → `execution/ibkr_broker.py` | IBKR paper port 7497 |
| Indicators | `data/indicators.py` (`add_all_indicators()`) | SuperTrend, Ichimoku, WAE, Fisher, CHOP, WaveTrend, Laguerre RSI, etc. |
| ML features | `ml/feature_builder.py` | 57 features across 11 groups (imports `indicators/` package) |
| ML training | `ml/walk_forward_trainer.py` + `ml/model_store.py` | XGBoost 60% + LightGBM 40%, clean data only |
| Indicators package | `indicators/` | atr_regime, cvd, funding_rate, liquidation_levels, macd_advanced, microstructure, open_interest, orderbook, orderflow, rsi_advanced, vwap_mtf, williams_r |
| Economics gate | `risk/economics_gate.py` | Pre-trade fee/funding EV veto (Kraken 0.065% taker) |
| Learning loop | `learning_loop.py` | 57-feature snapshots, retrain queue, RBI trigger |
| Bayesian learning | `learning/post_trade_analyzer.py` + `learning/signal_performance.py` | Per-signal Bayesian win rates |
| Dynamic weights | `learning/dynamic_weights.py` | Live conviction weights, 5-min cache |
| RBI nightly | `rbi/research_loop.py` + `rbi/backtest_loop.py` + `rbi/incubation_manager.py` | Research 575 combos, promote to live at 25% size |
| Notifications | `notifications/notification_engine.py` | SQLite only, no Telegram |
| Dashboard | `dashboard/app.py` | Streamlit Operator Panel, 5 tabs: MISSION CONTROL, CRYPTO PERFORMANCE, TRADE APPROVAL, S&P 500 FUTURES (MES), SYSTEM SETTINGS |
| DB | `logs/trades.db` | WAL mode SQLite — all positions, trades, system_events |
| Vector memory | `memory/trade_memory.py` | NumPy cosine similarity, SQLite-backed, 8-dim feature vectors |
| Kill switch | `kill_switch.py` | Balance < 75% of ACCOUNT_SIZE → halt all |
| Risk engine | `risk_engine.py` | VaR/CVaR, correlation gates, margin checks |
| Hedge engine | `hedge_engine.py` | Delta-neutral hedge rebalance (every 5 min) |
| MCP server | `mcp_server/server.py` | 15 FastMCP tools for Claude Code integration |
| Verification | `tests/proof/` + `verification/replay.py` + `.github/workflows/ci.yml` | Proof-first pytest harness, dashboard shell tests, deterministic replay, GitHub Actions CI |

### Key Decisions

- **Scanner sources:** Kraken Futures public REST + Binance USDM public REST + Hyperliquid public API
- **Execution:** `binance_broker.py` in paper mode (no live keys required; real API for live)
- **No AI debate for entries:** Two-tower signal engine replaces all v9 debate agents
- **Telegram removed:** Replaced by `notifications/notification_engine.py` (SQLite + dashboard only)
- **Paper = live thresholds:** No reduced thresholds in paper mode (clean data from 2026-04-02)
- **ML training data:** Tagged `pre_v10_contaminated` for all data before 2026-04-02
- **57 features:** 8 price + 6 volume + 5 CVD + 7 momentum + 4 VWAP + 5 OB + 6 deriv + 3 liq + 5 regime + 4 time + 4 onchain
- **Kill switch:** balance < 75% of ACCOUNT_SIZE (= $3,750 on a $5K account), not hardcoded $7,500
- **Live position sizing path:** `scheduler/v10_runner.py` sizes via `position_manager.compute_position_size()`; `risk/unified_sizer.py` is no longer on the live entry path
- **ISOLATED margin** on all perp positions — never CROSS

### Go-Live Readiness (dashboard SYSTEM tab → READINESS TRACKER)

Owner decides when to go live. These are informational readings, not system gates:
- Clean trade count (source=clean_paper_v10 or live_v10)
- Win rate on clean trades
- Profit factor on clean trades
- Worst single day as % of account
- Days running on clean data
- Economics gate veto rate
- Kill switch triggers (14d)

### v13.7 Autonomous Journaling Operationalization (applied 2026-04-13)

- `logging_db/trade_logger.py`: `candidate_outcomes` DDL now includes `price_15m`/`ret_15m_pct` columns directly (avoids ALTER TABLE chicken-and-egg on fresh DBs); `prune_old_candidates(labeled_days=90, unlabeled_days=30)` added; `get_logger()` singleton + `_TradeLoggerHandle` wrapper added for `risk_engine`/`kill_switch` callers; `kill_switch_log` table added to `init_db()`
- `learning/candidate_labeler.py`: 15-minute forward outcome added — fetches 50×15m candles per candidate; `_compute_15m_metrics()` computes `price_15m`, `ret_15m_pct`; both fields passed to `log_candidate_outcome()`
- `monitoring/nightly_audit.py`: full rewrite — exception-only notification model with severity-gated cooldowns (INFO 23h / WARN 6h / CRIT 1h); `_check_candidate_funnel()` for 24h decision-funnel analytics + anomaly detection; `_check_retention()` calls `prune_old_candidates()` + warns on oversized table; `_emit_audit_notification()` force-emits on status change (recovery always notified)
- `dashboard/data/journal_health.py` (NEW): `get_journal_health()` — candidates 24h/7d, labeling rate, backlog, conversion %, funnel breakdown, top veto reasons, outcome quality, last audit result
- `dashboard/widgets/system_settings/dev_config.py`: "Learning & Journaling Health" expander added — 🟢/🟡 status badge, 7 metrics, decision funnel table, top veto reasons table
- `.github/workflows/ci.yml`: branch list updated to include `feature/v10-rebuild`; test target changed to `tests/proof/`; ACCOUNT_SIZE=5000; stale env vars removed; streamlit added to deps
- `tests/proof/test_candidate_journal.py`: 5 new proof tests — 15m fields populated/graceful, DB persistence of 15m fields, 90d/30d retention policy, nightly audit funnel+retention checks; total 25/25 green
- No strategy thresholds changed; no live entry path touched; all new code is fire-and-forget read/write

### v13.6 Candidate Journaling + Automated Outcome Labeling (applied 2026-04-13)

- `logging_db/trade_logger.py`: `scan_candidates` + `candidate_outcomes` tables added to `init_db()`; `log_scan_candidate()`, `get_unlabeled_candidates()`, `log_candidate_outcome()`, `get_candidate_journal_stats()` helpers added
- `scheduler/v10_runner.py`: `_journal_scan_candidate()` helper; per-scan `scan_id` (UUID hex); journaling at 8 decision gates: `dual_exposure_block`, `cooldown_block`, `risk_block`, `data_unavailable`, `below_threshold`, `econ_veto`, `sizing_zero`, `entered`; labeler + nightly audit scheduled
- `learning/candidate_labeler.py` (NEW): background labeling worker (every 15 min, daemon thread); fetches forward 1h candles; computes 1h/4h returns, MFE, MAE, hit_1r, hit_2r, hit_stop; writes `candidate_outcomes`; bounded batch 50 rows/run
- `monitoring/nightly_audit.py` (NEW): daily 08:00 UTC; pytest proof suite + candidate journaling health + repo drift + learning health; writes to system_events; runnable standalone
- `tests/proof/test_candidate_journal.py` (NEW): 10 proof tests — all green
- No thresholds changed; no strategy logic touched; all journal writes are fire-and-forget

### v13.5 Conviction-Adaptive Exit Stack (applied 2026-04-13)

- `position_manager.py` — **Fix #1 (real-R denominator):** Scale-out R calculation now uses `abs(entry - stop_price)` (the actual risk per the live stop) instead of `atr * 1.5`. The hardcoded denominator was half the real stop, causing "2R" to fire at true 1:1 R:R — first 33% always sold the moment the trade broke even.
- `position_manager.py` — **Fix #2 (conviction-adaptive scale targets):** Scale-out first/second R levels and slice size now blend `entry_composite_score` (60 weight) + regime extension potential (40 weight). RANGING + low-conviction → 2.0R/4.5R; TRENDING + high-conviction → 4.0R/8.0R. Replaces flat 2R/3.5R.
- `position_manager.py` — **Fix #3 (regime-aware trailing activation + width):** `_REGIME_TRAIL_CONFIG` dict drives both activation threshold and trail multiplier per regime. `activate_trailing()` now calls `_resolve_trail_config(regime)` and stores `trail_atr_mult` in the position dict. RANGING activates at 1.0×ATR with 2.5× trail; HIGH_VOL at 2.0×ATR with 5.5× trail; etc.
- `position_manager.py` — **Fix #4 (ATR-proportional thesis hold gate):** LONG positions get 1h–6h hold gate proportional to ATR%; SHORT positions 2h–12h (shorts need twice as long historically). Replaces flat 45-min MR / 2h momentum floors.
- `position_manager.py` — **Fix #5 (signal-health trail compression):** When trailing is active, `check_exits()` computes `signal_health = (current_score - thesis_floor) / (entry_score - thesis_floor)`. If health < 65%, trail compresses toward 50% of nominal — the bot tightens its own leash as conviction fades without hard-coding an exit. Returns `trail_compressed` non-exit `ExitDecision` carrying `trail_atr_mult`.
- `position_manager.py` — `update_trailing_stop()` now reads `position.get("trail_atr_mult", 4.0)` instead of hardcoded 4.5, so compression is honoured on every tick.
- `scheduler/v10_runner.py` — Added `trail_compressed` handler: applies compressed `trail_atr_mult` to position dict and recomputes `trailing_stop_price` immediately without closing the position.

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
- `CLAUDE.md`: scanner sources corrected — code actually uses 3 sources (Kraken Futures + Binance USDM + Hyperliquid) every scan cycle; docs were Kraken-centric but code was not

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

## Project Structure (v10.1 — live files only)

```
algo_trading_final/
├── CLAUDE.md                 ← You are here (keep current)
├── CHANGELOG.md              ← Append every change: bash scripts/log_change.sh "..."
├── main.py                   ← Entry: python3 main.py --mode paper
├── config.py                 ← All constants (reads .env)
├── scanner.py                ← Multi-exchange perp scanner (Kraken + Binance + Hyperliquid) (DO NOT TOUCH)
├── signal_engine.py          ← Two-tower signal engine (DO NOT TOUCH)
├── position_manager.py       ← Live position sizing + 6-priority exit stack (DO NOT TOUCH)
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
│   ├── binance_broker.py     ← Perp execution (paper + live) — Binance USD-M
│   └── ibkr_broker.py        ← MES futures — IBKR via ib_insync, paper port 7497
│
├── notifications/
│   └── notification_engine.py ← SQLite only (DO NOT TOUCH)
│
├── logging_db/
│   └── trade_logger.py       ← SQLite trades.db WAL mode (DO NOT TOUCH)
│
├── dashboard/
│   └── app.py                ← Streamlit Operator Panel (5 tabs, widget architecture) (DO NOT TOUCH)
│
├── memory/
│   └── trade_memory.py       ← 8-dim NumPy cosine similarity, SQLite-backed
│
├── monitoring/
│   └── health_check.py       ← 6-invariant health assertions written to system_events
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
│   ├── check_v10_readiness.py ← Readiness checker
│   ├── validate.py           ← Pre-flight validator
│   ├── tradingview_webhook.py ← TradingView Pine Script alert ingestion
│   ├── tradingview_pine.pine ← Pine Script v5 template
│   ├── log_change.sh         ← Append to CHANGELOG.md
│   ├── backup_db.sh / backup_credentials.sh
│   └── install_services.sh   ← launchd auto-start setup (run once)
```

## Signal Engine (v10 — no AI debate agents)

Two deterministic towers → composite score → regime threshold gate.

| Tower | Method | Signals |
|-------|--------|---------|
| Technical | Rule-based point scoring, normalised 0-100 | CVD divergence, MACD multi-variant, RSI divergence, funding squeeze, VWAP reclaim, OB imbalance, Williams %R, liq cascade, vol spike, Fear & Greed, options skew, whale signal |
| ML | XGBoost 60% + LightGBM 40% walk-forward ensemble, normalised 0-100 | 57 features across 11 groups (price, volume, CVD, momentum, VWAP, orderbook, derivatives, liquidation, regime, time, onchain) |

Entry: composite >= regime threshold (TRENDING_UP/DOWN=58, RANGING=58, HIGH_VOL=60, LOW_VOL=56, UNKNOWN=58). Same threshold paper and live.

## 6-Priority Exit Stack (position_manager.py) — v13.5

1. **Trailing stop** — regime-aware activation (RANGING=1.0×ATR, TRENDING=1.5×ATR, HIGH_VOL=2.0×ATR) and trail width (RANGING=2.5×, TRENDING=4.5×, HIGH_VOL=5.5×). Trail further compresses as signal health fades toward thesis floor (signal-health trail compression, non-restrictive).
2. **Take profit scale-out** — conviction-adaptive targets: _factor blends entry_composite_score (60%) + regime extension (40%). First cut 20–30% at 2.0–4.0R; second cut 25% at 4.5–8.0R. Denominator uses actual stop distance (not hardcoded ATR multiple).
3. **Thesis score exit** — current composite < entry composite × regime_fraction → close all; TRENDING=30%, RANGING=15%, HIGH_VOL=35%, UNKNOWN=25%. ATR-proportional hold gate (LONG: 1h–6h; SHORT: 2h–12h).
4. **Hard stop** — stop-market on exchange, never widened.
5. **Risk forced exit** — margin breach / drawdown / correlation.
6. **Kill switch** — balance < 75% of ACCOUNT_SIZE / API errors / latency.

## v10 Learning Architecture

Every closed trade triggers `learning_loop.record_closed_trade()` which:
1. Persists 57-feature snapshot + outcome to `ml_feature_snapshots` table
2. Calls `learning/post_trade_analyzer.py` → Bayesian attribution on signal stats
3. Checks `ml_retrain_queue` — triggers walk-forward retrain when enough data accumulates
4. Feeds incubating RBI strategies with live results

Bayesian weight formula:
```
posterior_wr = (PRIOR_N * prior_p + N * obs_win_rate) / (PRIOR_N + N)
bayesian_pts = prior_pts * (posterior_wr / prior_p)
```
- PRIOR_N = 20 phantom trades
- MIN_FIRES_TO_LEARN = 10 (use hardcoded prior below this)
- Cap: 2.5x original prior points, per signal per regime

## The Amygdala Removal Rules (HARDCODED — NO OVERRIDE)

1. Never chase — skip if price moved >3% since signal
2. Never average down — one position per symbol, ever
3. Stop losses are sacred — never moved wider after entry
4. Wins don't justify ignoring rules on the next trade
5. Losses don't justify revenge trading or larger size
6. FOMO is not a signal
7. When in doubt, HOLD — a skipped trade costs nothing
8. The goal is being in business next month, not winning today

## Risk Rules (v10.1 current values)

- **1%** max account risk per trade
- **4%** max daily loss → halt ALL trading (paper: no cap, never halts learning)
- **90%** max deployed capital
- Default **3x** leverage, max **10x** (strict gates in signal_engine)
- ISOLATED margin on all perp positions — never CROSS
- Kraken taker fee: **0.065%** (modeled in economics_gate.py before every entry)
- Kill switch at balance < **75% of ACCOUNT_SIZE**

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
python3 main.py --mode live        # Live (requires typing 'I UNDERSTAND')
streamlit run dashboard/app.py --server.runOnSave true  # Dashboard on :8501
python3 mcp_server/server.py       # MCP server (Claude Code integration)
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

## TradingView Integration (v10 — still wired)
TradingView Pine Script → webhook → SQLite `system_events` (source='tradingview')
v10_runner reads these every scan cycle, prepends as candidates with edge_score=0.6.
```bash
python3 scripts/tradingview_webhook.py   # HTTP server (port 8765)
ngrok http 8765                          # Expose to internet
```
Set TV_WEBHOOK_SECRET in .env. Symbol mapping: BTCUSD → BTCUSDT.

## MES Contract Symbols (update quarterly)
- Q2 (Apr-Jun): MESM6 — **ACTIVE** (current front month, June 2026)
- Update: change MES_EXPIRY in `execution/ibkr_broker.py` (currently '20260619')

## Common Errors and Fixes

**pandas-ta import error** → `pip install "pandas-ta>=0.3.14b0"` (0.3.14b0 removed from PyPI; 0.4.67b0+ works)
**XGBoost openmp error** → `brew install libomp`
**IBKR connection failed** → TWS must be running, port 7497 (paper), API enabled in TWS settings
**DB lock error** → WAL mode is on; usually a stale connection. Restart bot.
**Schedule not running** → Check nothing is blocking the while True loop in v10_runner.py
**TV webhook 403** → TV_WEBHOOK_SECRET in .env doesn't match Pine Script input
**launchd not starting** → `launchctl list | grep algotrading`; check logs/service/bot_error.log
**DB backup fails** → `sqlite3 --version` to confirm CLI installed
**Kraken scanner empty** → Check internet / Kraken status at futures.kraken.com
**ML gate always 0.5** → Not enough clean trades yet (< MIN_TRADES_FOR_ML). Normal during early paper phase.

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
| v13.1–13.4 | 2026-04-05–10 | Scanner/funnel fixes, strategy optimization, ML PnL regressor, proof infrastructure, repo truth alignment |
| v13.5 | 2026-04-13 | Conviction-adaptive exit stack: real-R scale-out denominator, regime-aware trailing, signal-health compression, ATR-proportional hold gates |
| v13.6 | 2026-04-13 | Candidate journaling at 8 decision gates, automated outcome labeling, nightly audit, proof suite (10 tests) |
| v13.7 | 2026-04-13 | 15m labeling, exception-only notifications, funnel analytics, retention pruning, dashboard health panel, CI fix, proof suite 25/25 |

## GitHub
- Repository: `futureisnowtech/trading-bot-main` (private)
- Active branch: `feature/v10-rebuild`
- Push: `git push origin feature/v10-rebuild` (SSH configured)

## Claude's Standing Instructions
When making any change to this project:
1. Update CLAUDE.md if the change affects how the system works
2. Append to CHANGELOG.md: `bash scripts/log_change.sh "Description"`
3. Commit when a logical unit of work is done
4. Never commit .env or logs/ — .gitignore already excludes them
5. Always use `python3`, not `python`
6. Read a file before editing it
7. Test paper mode before any live-mode changes: `python3 main.py --mode paper`
