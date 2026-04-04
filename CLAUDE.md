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
- Scans Kraken Futures perps 24/7 with a 7-filter pipeline (no hardcoded watchlist)
- Scores every candidate with a two-tower signal engine (technical 0-100 + ML 0-100 → composite)
- Enforces unbreakable emotional safeguards (the amygdala is removed)
- Learns from every completed trade via Bayesian signal attribution + 57-feature ML snapshots
- Writes all notifications to SQLite; dashboard Notifications panel displays them
- Displays everything on a LeBron James / Dragon Ball Z themed dashboard
- Trades 100% autonomously — owner is never asked to approve anything

## Owner Profile
- Mac user (MacBook Air 2020, Python 3.14 at /Library/Frameworks/Python.framework/Versions/3.14/bin/python3)
- Paper account: $10,000 (ACCOUNT_SIZE=10000 in .env)
- Relatively technical but wants zero day-to-day intervention
- Wants the system to WIN — everything tuned for performance
- Prefers simple explanations, hates fluff

## Current Version: v10.1 (2026-04-02)

**Active branch:** `feature/v10-rebuild`
**Clean paper trading started:** 2026-04-02

### Live Architecture (source of truth)

| Component | File | Role |
|---|---|---|
| Scanner | `scanner.py` | Kraken Futures public REST, 7-filter, top 15 candidates |
| Signal engine | `signal_engine.py` | Two-tower: technical 0-100 + ML 0-100 → composite |
| Entry runner | `scheduler/v10_runner.py` | Scan loop, `_attempt_entry()`, economics gate, setup detection |
| Exit manager | `position_manager.py` | 6-priority exit stack (trailing/scale/thesis/hard-stop/risk/kill) |
| Perp execution | `perps_engine.py` → `execution/binance_broker.py` | Paper mode, ISOLATED margin |
| MES execution | `scheduler/v10_runner.py` → `execution/ibkr_broker.py` | IBKR paper port 7497 |
| Indicators | `data/indicators.py` (`add_all_indicators()`) | SuperTrend, Ichimoku, WAE, Fisher, CHOP, WaveTrend, Laguerre RSI, etc. |
| ML features | `ml/feature_builder.py` | 57 features across 11 groups (imports `indicators/` package) |
| ML training | `ml/walk_forward_trainer.py` + `ml/model_store.py` | XGBoost 60% + LightGBM 40%, clean data only |
| Indicators package | `indicators/` | atr_regime, cvd, funding_rate, liquidation_levels, macd_advanced, microstructure, open_interest, orderbook, orderflow, rsi_advanced, vwap_mtf, williams_r |
| Economics gate | `risk/economics_gate.py` | Pre-trade fee/funding EV veto (Kraken 0.065% taker) |
| Position sizer | `risk/unified_sizer.py` | 3-factor: base_risk × quality_mult × heat_factor |
| Learning loop | `learning_loop.py` | 57-feature snapshots, retrain queue, RBI trigger |
| Bayesian learning | `learning/post_trade_analyzer.py` + `learning/signal_performance.py` | Per-signal Bayesian win rates |
| Dynamic weights | `learning/dynamic_weights.py` | Live conviction weights, 5-min cache |
| RBI nightly | `rbi/research_loop.py` + `rbi/backtest_loop.py` + `rbi/incubation_manager.py` | Research 575 combos, promote to live at 25% size |
| Notifications | `notifications/notification_engine.py` | SQLite only, no Telegram |
| Dashboard | `dashboard/app.py` | Streamlit, 4 tabs: LIVE, TRADES, SCANNER, SYSTEM |
| DB | `logs/trades.db` | WAL mode SQLite — all positions, trades, system_events |
| Vector memory | `memory/trade_memory.py` | NumPy cosine similarity, SQLite-backed, 8-dim feature vectors |
| Kill switch | `kill_switch.py` | Balance < 75% of ACCOUNT_SIZE → halt all |
| Risk engine | `risk_engine.py` | VaR/CVaR, correlation gates, margin checks |
| Hedge engine | `hedge_engine.py` | Delta-neutral hedge rebalance (every 5 min) |
| MCP server | `mcp_server/server.py` | 15 FastMCP tools for Claude Code integration |

### Key Decisions

- **Scanner source:** Kraken Futures public REST — US-accessible, no auth, no geo-block
- **Execution:** `binance_broker.py` in paper mode (no live keys required; real API for live)
- **No AI debate for entries:** Two-tower signal engine replaces all v9 debate agents
- **Telegram removed:** Replaced by `notifications/notification_engine.py` (SQLite + dashboard only)
- **Paper = live thresholds:** No reduced thresholds in paper mode (clean data from 2026-04-02)
- **ML training data:** Tagged `pre_v10_contaminated` for all data before 2026-04-02
- **57 features:** 8 price + 6 volume + 5 CVD + 7 momentum + 4 VWAP + 5 OB + 6 deriv + 3 liq + 5 regime + 4 time + 4 onchain
- **Kill switch:** balance < 75% of ACCOUNT_SIZE (= $3,750 on a $5K account), not hardcoded $7,500
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
├── scanner.py                ← Kraken Futures scanner (DO NOT TOUCH)
├── signal_engine.py          ← Two-tower signal engine (DO NOT TOUCH)
├── position_manager.py       ← 6-priority exit stack (DO NOT TOUCH)
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
│   ├── unified_sizer.py      ← 3-factor position sizer (DO NOT TOUCH)
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
│   └── app.py                ← Streamlit, 4 tabs: LIVE/TRADES/SCANNER/SYSTEM (DO NOT TOUCH)
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
│   ├── test_indicators.py
│   ├── test_risk_manager.py
│   └── test_broker_paper.py
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

Entry: composite >= regime threshold (TRENDING=62, RANGING=68, HIGH_VOL=72, UNKNOWN=65). Same threshold paper and live.

## 6-Priority Exit Stack (position_manager.py)

1. Trailing stop — activates after 1x ATR in favor, trails at 1.5x ATR from peak
2. Take profit scale-out — 2R → 33%; 3.5R → 33%; remainder trails
3. Thesis score exit — current composite < entry composite × 0.45 → close all (10-min hold gate)
4. Hard stop — stop-market on exchange, never widened
5. Risk forced exit — margin breach / drawdown / correlation
6. Kill switch — balance < 75% of ACCOUNT_SIZE / API errors / latency

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

**pandas-ta import error** → `pip install pandas-ta==0.3.14b0`
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
