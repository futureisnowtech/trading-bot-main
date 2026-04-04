# CHANGELOG
All notable changes to The King's Algo Trading System.
## 2026-04-04
- fix(dashboard): open positions — live prices via Kraken+HL, unrealized P&L, color-coded rows; fix balance NULL-source filter; fix Styler .hide() crash

## 2026-04-04
- chore: full pre-live cleanup — delete legacy/, purge stale DB, remove dead telegram/bybit/webull code, update CLAUDE.md

## 2026-04-04
- fix(perps_engine): restore positions from SQLite on startup; fix(v10_runner): 2-hr cooldown after close prevents thesis-exit churn; SQLite guard on entry prevents post-restart double-entry

## 2026-04-03
- fix(sizing): apply Kelly to position_usd;  hard cap per position; real deployed_usd passed to manual trades; min notional 

## 2026-04-03
- fix(dashboard): manual execute now uses compute_position_size() — Kelly, ML score, vol regime, 20% cap, leverage schedule; shows lev and kelly in result

## 2026-04-03
- fix(dashboard): balance/winrate/PF now accurate — net fees subtracted, won field used for closes, PF uses net-of-fee PnL

## 2026-04-03
- fix(dashboard): status bar accuracy — Balance shows actual PnL+base, scan age human-readable, profit factor in Win Rate delta, today P&L source-filtered

## 2026-04-03
- fix(perps_engine): open_long/short now call persist_position(); close_position calls delete_position() — open positions finally visible in dashboard

## 2026-04-03
- fix(dashboard): get_last_scan_age() reads log backwards without line cap — fixes false STALE when 1000+ noisy lines follow the scan entry

## 2026-04-03
- feat(dashboard): manual scan redesign — win probability bar + full ℹ detail card per candidate (breakdown table, EV math, raw indicators)

## 2026-04-03
- feat(dashboard): Run Scan button + manual trade approval — force=True on scan(), render_manual_scan() with data_editor checkboxes

## 2026-04-03
- feat(scripts): add force_10_trades.py — test harness that runs full entry→score→execute→close pipeline with threshold=38, verified 10/10 trades completed

## 2026-04-03
- fix(historical_data): add Hyperliquid fallback in get_candles() — HL symbols (HYPE, ETHFI, TON, MORPHO, etc.) were returning 0 bars; now tried after Binance+yfinance fail via _fetch_hyperliquid() POST to candleSnapshot API

## 2026-04-03
- feat(dashboard): add FUTURES tab — st.tabs([CRYPTO PERPS, FUTURES(MES)]); futures tab shows market hours, OR range, daily P&L, position state, today's trades, strategy playbook, config expander; runner writes mes_state to system_events each cycle

## 2026-04-03
- fix(ibkr_broker): rename num_contracts→qty, add stop_price/target_price absolute-price params to buy_mes/short_mes; fixes parameter mismatch with v10_runner calling convention

## 2026-04-03
- feat(futures): VWAP Mean Reversion strategy for MES — 1-min yfinance bars, session VWAP + RSI(14) + ATR(14); entry >2σ from VWAP with RSI confirmation; 1 contract, stop 1.5 ATR, target VWAP; runs 10:00-14:30 ET alongside ORB

## 2026-04-03
- feat(scanner): add Hyperliquid perps exchange — 80+ markets, no geo-block, POST-based API (_post helper, _hl_meta_and_ctxs, _hl_klines, _hl_ob), integrated into 7-step pipeline alongside Kraken

## 2026-04-03
- fix(scanner): ADX recurrence bug — formula was adding full raw DX[i] (Wilder TR-smoothing style) instead of DX[i]/period; all markets showed ADX ≈ 100, blocking ranging_mr setup; fix: use EMA-style ADX[i] = ADX[i-1]*(1-1/n) + DX[i]/n

## 2026-04-03
- feat(scanner): Kraken+Binance dual-exchange, 5 sub-filters (momentum/kst_cross/supertrend/ranging_mr/funding_collect), up to 50 candidates, $500K vol floor, $5K OB floor, parallel ThreadPoolExecutor, scan_setups[] tracking per candidate

## 2026-04-03
- refactor(dashboard): one page, no tabs, no custom HTML cards — st.metric/dataframe/line_chart only; minimal CSS (hide chrome only); all 8 sections scroll vertically; data layer unchanged

## 2026-04-03
- feat(signals): wire SuperTrend/KST/Ichimoku as Tier 1 entries; add cross detections in indicators.py; inject 8 cross features in v10_runner; 6 new Tier 1 setups in signal_engine; TV webhook launchd plist + install_services wired; remove LeBron/Goku theme strings from main.py + config.py

## 2026-04-03
- feat(dashboard): complete ground-up rebuild — 5 tabs (WAR ROOM/PERFORMANCE/SIGNAL BRAIN/SCANNER/SYSTEM), all data read from live DB + bot.log + config imports; Bayesian signal stats table, real 37-condition technical tower, Tier1/Tier2 setups from signal_engine, scanner pipeline steps, economics gate + sizer + exit stack from live constants

## 2026-04-03
- feat(ml): activate 57-feature ML tower — trade_features table stores full feature snapshots per entry; walk_forward_trainer joins snapshots for real 57-col training; perps_engine threads trade_id back to v10_runner; proxy fallback preserved until MIN_TRADES snapshots accumulate

## 2026-04-03
- refactor(cleanup): v10.1 repo cleanup — move v9 files to legacy/, fix stale telegram imports, rewrite CLAUDE.md to v10 truth

## 2026-04-03
- fix(CRITICAL wae-churn): add 10-minute minimum hold before thesis invalidation can fire — wae_explosion was entering and exiting within 30 seconds every 5-min cycle (~20x on PF_TAOUSD today) because WAE bullish flag is a single-bar event that goes false on the next bar; position_manager now skips thesis check until entry_ts + 600s
- feat(kst): wire KST oscillator into live scoring path — kst_bullish (KST > signal line) +8 pts long, kst_bearish (KST < signal line) +8 pts short; injected via add_all_indicators in _attempt_entry
- fix(dashboard): add get_scanner_status() function — scanner tab was crashing with NameError on every render cycle, poisoning the debug tab too

## 2026-04-02
- feat(flat-market): 3 strategic fixes for ranging/flat conditions — (1) signal_engine.py: added ranging_mr_long/ranging_mr_short Tier 1 setups (CHOP>61.8 + VWAP dist <-0.15% + LRSI<0.25); all momentum setups (wt_reversal, squeeze_breakout, wae_explosion) gated with chop_ranging==0 so they suppress in flat markets; (2) v10_runner.py: chop_ranging, squeeze_fired, squeeze_direction injected into features dict; economics gate called with is_ranging=bool(chop_ranging>0); (3) economics_gate.py: Bybit fees (0.055%) → Kraken taker (0.065%), ROUND_TRIP=0.130%; added is_ranging param — when True EV floor tightens 0.15%→0.25% and R:R floor 1.2→1.5 (flat markets have smaller expected moves relative to fees)
- fix(v10 live): signal thresholds lowered to 47 for OHLCV-only operation — first paper trade entered (PF_TAOUSD LONG $850 @ $300.42 composite=54.7)
- refactor(entry-path): tier 1 setup triggers replace score gate as primary entry mechanism — score is now sizing-only; economics gate moved after scoring; thesis exit is now condition-based (setup invalidated) for tier 1, score-comparison fallback for tier 2; 4 long + 4 short primary setups defined in signal_engine.py (wt_reversal, squeeze_breakout, wae_explosion, tv_confirmed); entry_setup stored on position for exit tracking; tier 1 full size, tier 2 0.75x
- feat(signal-engine): wire v4.3 indicators + TradingView into live v10 scoring path — SuperTrend (+12), Ichimoku cloud (+8), WAE bullish+exploding (+10/+5), Ehlers Fisher cross (+8), Choppiness trending (+5), WaveTrend oversold cross (+12), Laguerre RSI oversold (+8/+4), TV signal confirmation (+20); bearish mirrors added to short scorer; all injected into features dict after build_features() in _attempt_entry()
- fix(live-path audit): 4 targeted fixes — (1) scanner.py step4: reject candidates with <15 bars or EV calc exception instead of auto-approving with fake expected_profit; (2) signal_engine.py: raise thresholds 47→50 (47 was below the meaningful signal floor; 50 requires majority OHLCV alignment); (3) ml/walk_forward_trainer.py: add AND t.paper=0 to training filter — previously could include paper trades if source was mistagged; (4) v10_runner.py: document model_store=None intentional until 50+ live trades accumulated


## 2026-04-02 — BYBIT REMOVAL / KRAKEN FUTURES MIGRATION

- **fix(scanner)**: complete rewrite to Kraken Futures public REST API — removes Bybit V5 (geo-blocked for US residents). Replaces with `futures.kraken.com` public endpoints: `/derivatives/api/v3/tickers` for universe (21 liquid PF_ perps), `/api/charts/v1/trade/{symbol}/{interval}` for OHLCV, `/derivatives/api/v3/orderbook` for depth. No API key required. All endpoints verified working from US IP. BTC symbol is `PF_XBTUSD`. Round-trip fee updated to 0.13% (Kraken taker). Uses stdlib `urllib` only.
- **delete(bybit_broker)**: `execution/bybit_broker.py` removed — Bybit not viable for US users.
- **fix(main.py)**: banner updated — "Bybit USDT perps" → "Kraken Futures perps".
- **fix(CLAUDE.md)**: data source updated — Bybit V5 REST → Kraken Futures public REST.

## 2026-04-02 — LIVE-READINESS OVERHAUL (full audit + implementation)

### P0 — Live-blocking fixes

- **fix(signal_engine)**: remove paper mode -20pt threshold reduction — `signal_engine.py` now uses identical thresholds in paper and live (TRENDING=62, RANGING=68, HIGH_VOL=72). Prior paper trades at threshold 42-52 were invalid; all such data tagged contaminated in DB.
- **fix(scanner)**: complete rewrite to Bybit V5 REST API — removes Binance fapi (geo-blocked US) and CoinGecko fallback (synthetic fake perp tickers with fabricated OHLCV). `_fetch_tickers()` and `_fetch_klines()` now use `api.bybit.com/v5/market/`; no yfinance fallback; instruments-info validation set (24h cache) rejects non-existent symbols; OB depth check changed fail-closed (candidate rejected if OB unavailable, not silently passed); fee+funding modeled in EV; `_MIN_EXPECTED_PROFIT` restored to $3.00; `_TOP_N` restored to 15.
- **feat(bybit_broker)**: new `execution/bybit_broker.py` — pybit v5 unified account, USDT perpetual, isolated margin, server-side SL/TP, paper mode via SQLite, `get_bybit_broker()` singleton. No Telegram imports.
- **feat(economics_gate)**: new `risk/economics_gate.py` — pre-trade EV veto gate. Models Bybit taker fees (0.055%×2), spread, funding carry (1.5 settlement periods). Outputs quality tier (A+/A/B/VETO), ev_pct, roi_on_margin, edge_score. Wired into `_attempt_entry()` in v10_runner before feature building. Rejection logged to dashboard with reason.
- **fix(ml_training)**: `ml/walk_forward_trainer.py` — removed `paper=?` filter; replaced with `source NOT IN ('backtest', 'pre_v10_contaminated')` — model now trains on clean architecture-consistent data regardless of paper/live mode.
- **fix(ml_tagging)**: `learning/post_trade_analyzer.py` — default `source` changed from `'live'` to `'paper'`; live trades must explicitly pass `source='live_v10'`. Prevents paper trades under relaxed thresholds contaminating future ML training.
- **fix(v10_runner hardcodes)**: `scheduler/v10_runner.py` — replaced `vol_regime=2`, `fg_current=50.0`, `edge_score=0.5` hardcodes with values extracted from feature vector (`regime_vol_mult`, `regime_fg_current`) and economics gate result.

### P1 — Data pipeline and architecture

- **feat(tv_ingestion)**: `scheduler/v10_runner.py` — TradingView signals now read from `system_events WHERE source='tradingview'` (max 5 min old) at the top of every scan cycle. TV candidates prepend scanner candidates with priority. Deduplication via bounded key set.
- **fix(ibkr_broker)**: `execution/ibkr_broker.py` — removed dead `from alerts.telegram_alert import ...`; replaced with `notifications.notification_engine` wrapped in try/except.
- **refactor(unified_sizer)**: replaced 6-factor chain (V×E×D×T×K×M) with clean 3-factor formula: `notional = (account × 1.5% × quality_mult) / stop_dist_pct`, capped by portfolio heat and 25% single-position hard cap. Legacy `get_position_size()` shim preserved.
- **feat(readiness_panel)**: `dashboard/app.py` — added READINESS TRACKER expander in SYSTEM tab. Shows 7 clean-trade metrics (WR, PF, worst day, days running, veto rate, kill triggers). Informational only — no gating logic.
- **fix(main_banner)**: updated banner to show "Bybit USDT perps" instead of "Binance USDT perps".

### Repo cleanup

- Deleted dead execution files: `tradovate_broker.py`, `coinbase_broker.py`, `binance_spot_broker.py`
- Deleted removed ai_agents remnants: `analyst_agents.py`, `debate_engine.py`, `exit_review.py`, `risk_synthesizer.py`
- Deleted Lane 3 prediction market files: `polymarket_broker.py`, `kalshi_broker.py`, `polymarket_feed.py`, `kalshi_feed.py`, `lane3_scanner.py`, `prediction_arb.py`, `pm_calibrator.py`, `tax_tracker.py`
- Archived v9 schedulers (not deleted — audit trail preserved): `v9_crypto_scanner.py.archived`, `v9_perp_scanner.py.archived`, `v9_exit_monitor.py.archived`

### DB migration

- `scripts/migrate_clean_start.py` — run once to tag all existing trade_attribution rows as `pre_v10_contaminated`. Executed: 18,403 rows tagged (18,172 backtest + 231 paper). ML training now starts from zero clean trades; model falls back to 100% technical weighting until clean data accumulates.

### Go-live readiness (per owner preference — tracking only, not gates)

- Paper and live now use identical signal thresholds — paper data is valid for assessing live performance
- ML training data is clean from this point forward
- Universe is validated against Bybit instruments (no synthetic tickers)
- Pre-trade economics gate enforces fee/funding viability before any execution
- Owner retains full go-live authority — readiness metrics are displayed in dashboard, never enforced by code

## 2026-04-02

## 2026-04-02
- feat: add risk/economics_gate.py (pre-trade EV veto) + execution/bybit_broker.py (pybit v5 perp broker)

## 2026-04-02
- fix(scanner): replace Binance fapi + CoinGecko with Bybit V5 REST — no geo-block, no fake tickers; instruments-info validation set (24h cache); OB fail-closed (reject on missing data); fee+funding EV model; _MIN_EXPECTED_PROFIT restored to $3.00; _TOP_N restored to 15; no yfinance fallback

## 2026-04-02
- fix(ibkr_broker): replace dead telegram_alert import with notification_engine; wire TradingView signals into v10_runner scan_and_trade as priority candidates

## 2026-04-02
- v10: perp-first tuning — 15 max positions, 95% deployed, no correlation penalty, top-20 scanner

## 2026-04-02
- v10: scanner yfinance fallback + paper trading fixes (US geo-block workaround)

## 2026-04-01
- v10 live: scheduler wired, 1-day paper requirement, system ready to run

## 2026-04-01
- v10 Phases 12-15: learning loop, notification engine, backtest validation, readiness checker — build complete on feature/v10-rebuild

## 2026-04-01
- v10 Phase 2: data pipeline — realtime_feeds.py (WebSocket), historical_data.py (OHLCV cache), sentiment_data.py (F&G + Deribit + onchain)

## 2026-04-01
- v10 Phase 1: architecture doc, DB migration (9 new tables), removed 10 deprecated files (agents, telegram, super_score, meta_learner)

## 2026-04-01
- v9.4: learning loop end-to-end fix — engine signals (cascade/divergence/obi/vwap_reclaim/near_miss) now stored on position at entry and injected into attribution at exit; meta-learner DB-backed counter; perp 4h winner rule; near-miss paper signals; 6-min forced-trade timer; FUTURES_ENABLED=false

## 2026-04-01
- Perp exits: let winners run past 4h (>0.5% PnL = skip close, 8h hard max); fixes avg win  vs avg loss  imbalance causing PF 0.66

## 2026-04-01
- Fix learning loop holes: perp exits now build real market_data; crypto exit candle threshold 20→5; meta-learner counter DB-backed (survives restarts); ML pkl force-retrained (was 40h stale)

## 2026-04-01
- Paper forced-trade timer: one entry every 6 min if no organic trade and slots open

## 2026-04-01
- Fix divergence: compute per-symbol change_pct (was always 0, breaking all divergence signals)

## 2026-04-01
- Paper near-miss signals: relaxed thresholds in paper mode to force pipeline trades; heatmap fix: get_scan_feed now returns structured fields

## 2026-03-30
- v9.3: perp watchdog + traceback logging + risk reduction (post 2026-03-30 audit)

## 2026-03-30
- v9.2: perp exit restart fix + ML data fix + agent_votes format + dashboard overhaul + buy_limit qty fix

## 2026-03-30
- Dashboard: fix comp_positions To Stop / To Target showing entry-based distances instead of live current-price distances; now correctly shows how far current price is from stop and target
## 2026-03-30
- Dashboard: fix NameError TEXT1 (undefined color constant in comp_positions); fix float(None) on low_since_entry in comp_trade_quality; both caused all non-overview tabs to render blank
- Dashboard: Edge Monitor clarity — fix STATUS_CLR mismatch (OK/BLOCKED now colored correctly vs gray); remove fake win_rate_20 row; NO DATA label when window=0; bar normalized to STRONG threshold; PF shown prominently; "X/30 (building)" trades counter; LIVE READY badge on STRONG
## 2026-03-30
- v9.1: scan speed overhaul — parallel inter-symbol debates (ThreadPoolExecutor fan-out in crypto_scanner.py Phase 2), MTF candle cache (240s TTL, eliminates redundant Coinbase REST calls), scan interval halved 30s→15s in config.py
- v9.1: MTF granularity bug fix — _get_5m_candles was passing integer 300 instead of 'FIVE_MINUTE' string; Coinbase silently rejected all 5-min bar requests; all MTF confluence was always absent
- v9.1: low_since_entry persistence fix — persist_position() now saves low_since_entry to DB; load path uses explicit None check; register_position + update_high pass low_since_entry through; fixes perp TypeError '<' not supported between float and NoneType permanently
- v9.1: should_exit None guards — stop_price/target_price/high_since_entry all guarded against None before comparison in stop_loss_manager.py
- v9.1: dead pair cleanup — removed 7 zero-volume/delisted pairs (LTC, NEAR, APT, OP, ARB, SUI, PEPE, MATIC) from CRYPTO_PAIRS; replaced with ATOM, LDO, FIL, AAVE, ICP, SNX, COMP (all liquid on Coinbase USDC)
- v9.1: ML gate log fix — crypto_scanner now shows correct < vs >= and "paper bypass (would BLOCK live)" vs "gate passed"

## 2026-03-29
- 9-feature build: cumulative delta, Deribit IV skew, on-chain whale, derivatives momentum scanner (Lane 4), MTF alignment, partial profit taking (50% at 50% target), tighter trailing stop (2%, activates at 40% target), time-of-day ML features, regime-aware position sizing (R factor)

## 2026-03-28
- Dashboard v12.0: tabbed layout — 6 tabs (Overview/Crypto Spot/Perp/Predictions/Intelligence/System), new comp_crypto_tab and comp_perp_tab functions

## 2026-03-28
- Sprint 5: ml_trainer.py offline trainer, ml_signal.py background retrain, prediction_arb.py, CI branch fix

## 2026-03-28
- Task 1: run_parallel_scan() in job_runner.py — crypto/perp/lane3 now run in parallel ThreadPoolExecutor (3 workers, 5-min timeout each); separate schedule lines for perp/lane3 removed. Task 2: scripts/promote_perp_live.py — 8-check paper-to-live checklist for Binance perp (API keys, testnet conn, trade count, win rate, risk limits, halt state, fee viability, manual confirm)

## 2026-03-28
- Add market_sentiment.py: Reddit + options market signals (P/C ratio, IV rank, term structure) as sentiment layer; wired into market_context.get_context_for_debate() and _helpers._build_market_data()

## 2026-03-28
- Wire super_score through full trading pipeline: signal_performance, post_trade_analyzer, risk_manager, crypto_scanner (pre+post-debate compute, abort<40, size multiplier), exit_monitor (decay exit + attribution)

## 2026-03-28
- Dashboard: add TRADE QUALITY section — entry timing, exit efficiency, thesis hit rate, super score avg, exit mix, open position health cards

## 2026-03-28
- Add learning/super_score.py (0-100 unified composite) + trade_logger get_trade_quality_stats / get_open_position_health + super_score DB migration

## 2026-03-28
- Binance spot broker: replace Coinbase execution with Binance spot (4x cheaper fees: 0.10% vs 0.40%)

## 2026-03-28
- Switch debate agents (Bardock/Vegeta/Krillin) from Sonnet to Haiku via CLAUDE_DEBATE_MODEL; exit review (Tudor Jones/Soros/Simons) stays on Sonnet

## 2026-03-28
- generate_daily_summary: add fee-ratio alert (>2x) and backtest-vs-live WR divergence alert (>10pp) to daily markdown summaries

## 2026-03-28
- Add backtesting/full_pipeline_backtest.py: full-pipeline backtest mirroring production 4-signal gate + ML gate with real fees and slippage

## 2026-03-28
- Add data/liquidation_feed.py (Binance L/S ratio, 10-min cache, fail-open) + inject liq_signal/liq_avoid_long/liq_long_ratio into _build_market_data() in scheduler/_helpers.py

## 2026-03-28
- Add scripts/inspect_ml_model.py + wire feature importances into learning/ml_signal.py

## 2026-03-28
- Slippage: wire BACKTEST_SLIPPAGE_PCT from .env into config.py (0.001 default) and as default for all 7 public slippage= params in backtest_engine.py

## 2026-03-28
- Add scripts/backfill_agent_stats.py: reconstructs agent_stats from debate_results x trades join

## 2026-03-28
- Sprint 2: Lane 3 Prediction Markets — Polymarket + Kalshi + ensemble forecaster + calibrator + whale tracker + Telegram alerts + dashboard panel

## 2026-03-27
- Strategy expansion: VWAP reclaim (signal 5), fade-the-rally SHORT, range scalper, wire Kyle Lambda + Amihud — from 4 strategies to 7, fix MR independence bug

## 2026-03-27
- Philosophical reoptimizations: edge monitor, options flow, Landry bar count, stop cooldown

## 2026-03-27
- Sprint 8: Validation — all 3 markets import clean, 66 tests passing, go-live criteria updated to 8-criterion Phase-9 spec

## 2026-03-27
- Sprint 2: unified math framework — volatility_regime.py, edge_monitor.py, unified_sizer.py (V×E×D×T×K×M formula), 5-min candles, 29 tests

## 2026-03-26
- v9.1 — CI/CD + heat system + calibrator + BB bug fix + MACD exit removal

## 2026-03-26
- Remove MACD SELL as exit trigger — was cutting winners at avg $0.27 (57 orphan exits identified via SQL audit). All exits now owned by monitor_exits_with_ai.


## 2026-03-26 (v9.0 — Sprint 1 Foundation)
- **job_runner.py decomposed**: 1,812-line god object → 6 focused modules (86% reduction)
  - `scheduler/_helpers.py` (273L): shared state, optional-import flags, 3 helper fns, strategy singletons
  - `scheduler/exit_monitor.py` (329L): AI exits, hard-stop/time/stagnant exits, post-trade attribution, EOD close
  - `scheduler/equity_scanner.py` (227L): Clenow ranking, Minervini, AI debate, F&G/IV sizing
  - `scheduler/crypto_scanner.py` (641L): 8-signal gate, ML gate, microstructure veto, 3-agent debate, MR path
  - `scheduler/perp_scanner.py` (153L): Binance perp entry/exit, 4h flat exit
  - `scheduler/job_runner.py` (258L): thin orchestrator — re-exports sub-modules, futures/watchdog/premarket/schedules
- **Bybit → Binance migration**: `execution/binance_broker.py` (NEW, 320L) replaces deleted bybit_broker.py
  - `python-binance` library, STOP_MARKET/TAKE_PROFIT_MARKET server-side SL/TP, ISOLATED margin
  - `BINANCE_TESTNET=true` in .env — fill keys at testnet.binancefuture.com to activate
  - Fees: 0.040% taker (cheaper than Bybit's 0.055%)
  - `get_bybit_broker` alias preserved; config.py/validate.py/.env all updated
- **Risk decomposition**: 527-line risk_manager.py → thin orchestrator + 5 modules
  - `risk/position_sizer.py`: 25%-fractional Kelly, 5-loss clamp, floor/cap
  - `risk/stop_loss_manager.py`: calc_stop_loss, calc_take_profit, should_exit (hard/trailing/TP)
  - `risk/drawdown_controller.py`: daily loss gate + fee drag gate
  - `risk/risk_limits.py`: market hours, position limits, deployment cap, crypto fee gate; RiskCheckResult moved here
  - `risk/var_calculator.py`: historical simulation VaR (95/99% confidence) — new capability
- **MCP server** (`mcp_server/server.py`): 15 FastMCP tools expose full bot state to Claude Code
  - Tools: positions, trades, signals, agent accuracy, ML signal, price history, macro, scan, debate, backtest, readiness, notifications
  - Start: `python3 mcp_server/server.py`
- **Claude Code agents** (`.claude/agents/`): portfolio_manager, trade_strategist, devil_advocate, system_engineer
- **Claude Code commands** (`.claude/commands/`): /health, /audit, /deploy, /optimize, /build-strategy
- **Test suite** (`tests/`): test_indicators.py, test_risk_manager.py, test_broker_paper.py (~25 tests total)
- **GitHub**: Repository futureisnowtech/trading-bot-main live; SSH push configured; pre-commit validation hook active
- **Dead code removed**: webull_broker.py deleted; bybit_broker.py deleted; scripts/generate_system_html.py removed
- **CLAUDE.md**: Updated to v9.0 with project structure, GitHub section, Sprint 1 changes

## 2026-03-26
- v8.1: auto_env_updater.py — automatic ML threshold + position size progression via launchd every 6h

## 2026-03-26
- v8.0 fixes: walk-forward wired into run_backtest.py, ML schema fixed for trade_attribution, ML threshold calibrated to 0.08 for seeded data

## 2026-03-26
- v8.0: 3-agent debate (11→3 API calls), ML signal layer (LightGBM), walk-forward OOS, funding rate wired, cooldown removed

## 2026-03-26
- v7.1: RBIPMS framework — RBI audit + walk-forward standards + incubation playbook + strategy lifecycle docs

## 2026-03-25
- v7.0: AI-first pipeline — prescreener + meta-learner + live backtest validator

## 2026-03-25 — v6.0: AI-First Rework — Conviction Score Becomes Context, Not Gate

**Core architectural change:**
The hard conviction floor (30pts) that blocked the AI debate has been removed.
The math signal scoring now feeds the AI as rich context rather than deciding for it.

- **scheduler/job_runner.py**: Conviction gate removed. New gate: any signal fires + macro not blocked + ATR fee floor passes. `should_block_trade()` now called pre-debate as the real macro/news gate (RISK_OFF, HIGH news risk, VIX extreme fear). `active_signals` list built per symbol (canonical DB names) and added to `market_data`. Session bias + multiplier injected as readable text into debate context (not a numeric floor).
- **learning/signal_performance.py**: Added `get_active_signal_stats_brief(active_signals, regime)` — returns compact Bayesian win-rate table for fired signals, injected into every agent prompt. Added `get_agent_self_accuracy(agent_name, regime)` — returns per-agent historical accuracy one-liner for their own prompt.
- **strategies/ai_agents/analyst_agents.py**: Conviction score, active signal list, Bayesian signal win-rate table, and per-agent historical accuracy now injected into every agent user prompt. Each agent sees: "CONVICTION SCORE: X/100", which signals fired, how those signals have historically performed, and their own past accuracy.
- **strategies/ai_agents/debate_engine.py**: Conviction score + active signals summary added to moderator synthesis prompt.
- **TODO.md**: Created — tracks manual tasks, in-progress AI-first work, and future improvement ideas.

## 2026-03-25 — v5.3: AI Session Analyst + Session-aware Routing + Full Wiring
- **strategies/ai_agents/session_analyst.py** (NEW): AI Session Analyst — fires at Asia (8pm ET), London (3am ET), NY (8:30am ET) opens. Reads news+macro+signal leaderboard. Outputs: session_bias (STRONGLY_BULLISH→STRONGLY_BEARISH), conviction_threshold_multiplier (0.7–1.5×), signal_weight_overrides, strategies_to_favor, avoid_flags, session_notes. Stores to SQLite session_contexts table + 4h memory cache. Bounded multipliers prevent AI from silencing all signals or going wild.
- **scheduler/job_runner.py** (MODIFIED):
  - London window OPENED: dead zone reduced from 2-5am → 2-3am ET. London session (3am-8am ET) now fully active as HIGH quality breakout window.
  - Session analyst integration: conviction threshold = base × session_cv_multiplier. BULLISH session lowers bar, RISK_OFF raises it. Session bias logged on every skip.
  - Macro+news context wired into every crypto debate: `get_context_for_debate()` + `format_session_context_for_debate()` injected as `context` arg to `run_debate()`. Every agent now sees macro regime, VIX, news sentiment, funding rates before deciding.
  - Session open triggers added to setup_schedules(): ASIA 8pm ET, LONDON 3am ET, NY 8:30am ET
  - `run_session_open_analysis()` function added
- **strategies/ai_agents/exit_review.py** (MODIFIED): Tax-aware exit review — `get_tax_aware_exit_note()` injected into every exit agent prompt. Tudor Jones, Soros, and Simons now explicitly see whether gains are short-term, long-term, or Section 1256 before recommending hold/exit. Added `entry_ts` and `asset_class` parameters.

## 2026-03-25 — v5.2: Goku + Data Feed Layer + Tax Tracking
- **data/news_feed.py** (NEW): Crypto news sentiment — CryptoPanic API primary, CoinDesk RSS fallback; bearish/bullish/risk keyword scoring; sentiment_score -1.0 to +1.0; 10-min cache; format_news_for_debate() injects into agent prompts
- **data/macro_feed.py** (NEW): Cross-asset macro context — yfinance (DXY, SPY, GLD, VIX, TLT, BTC), Coinglass public funding rates; macro_score -5 to +5; RISK_ON/NEUTRAL/RISK_OFF regime; 15-min cache; format_macro_for_debate() injects into agent prompts
- **data/market_context.py** (NEW): Unified context assembler — session detection (ASIA/LONDON/NY_OPEN/PREMARKET/etc.); no_trade_flags (dead zone, HIGH news risk, RISK_OFF, VIX fear, overheated longs); conviction_hints (tailwinds); get_context_for_debate() for debate enrichment; should_block_trade() for pre-debate gate
- **learning/tax_tracker.py** (NEW): Tax lot tracking — SQLite tax_lots table; Section 1256 detection (MES/futures: 60% LTCG + 40% ST = ~17% blended); short/long-term classification; YTD summary by treatment; estimated liability; harvesting opportunity detection; get_tax_aware_exit_note() injected into exit review; format_tax_summary_for_brain() for daily notes
- **strategies/ai_agents/analyst_agents.py** (MODIFIED): Added 'goku' to AGENTS dict — Jim Simons / Paul Tudor Jones / Soros, DBZ: Goku (Ultra Instinct); added run_goku() function — 9th agent, runs LAST, sees all other votes + moderator synthesis, absolute veto (-100) and boost (+25) capability, 1200 token limit, no cache (unique context every call)
- **strategies/ai_agents/debate_engine.py** (MODIFIED): Goku integration — imports GOKU_ENABLED; DebateResult gains goku_verdict/goku_conviction_adjustment/goku_reasoning/goku_insight fields; run_debate() calls run_goku() after moderator when signal=BUY; VETO kills trade + updates reasoning; BOOST raises confidence by +0.15 capped at 0.95; goku verdict shown in __repr__
- **learning/post_trade_analyzer.py** (MODIFIED): Tax lot recording wired into analyze_closed_trade() — calls record_tax_lot() after every close with asset_class mapped from strategy name
- **scripts/generate_daily_summary.py** (MODIFIED): Added TAX SNAPSHOT section — calls format_tax_summary_for_brain() for YTD tax breakdown in every daily brain note; added _get_tax_snapshot() helper
- **config.py** (MODIFIED): Added CRYPTOPANIC_API_KEY, GOKU_ENABLED
- **.env.example** (MODIFIED): Added CRYPTOPANIC_API_KEY and GOKU_ENABLED placeholders

## 2026-03-25 — v5.0 True Brain: Self-Improving Intelligence Layer
- **learning/signal_performance.py** (NEW): Bayesian attribution engine — 4 new SQLite tables (trade_attribution, signal_stats, agent_stats, backtest_results); PRIOR_N=20 phantom trades; 19 signal priors; posterior_wr blend; bayesian_pts = prior_pts × (posterior_wr / prior_p) capped 2.5×; MIN_FIRES_TO_LEARN=10
- **learning/post_trade_analyzer.py** (NEW): Why-this-trade-worked/failed engine; extracts canonical signal names from market_data; generates structured lessons (confluence patterns, fee cannibalization, regime mismatch); called on every trade close
- **learning/dynamic_weights.py** (NEW): Live conviction weights; 5-min TTL cache; invalidate_cache() after every close; get_conviction_score() replaces hardcoded tier blocks; get_weights_snapshot() for dashboard
- **learning/intelligence_bridge.py** (NEW): Backtest trade attribution → same signal_stats table as live (source='backtest'); archive_backtest_result() with MD5 param hash; get_best_backtest() for drift detection
- **data/price_archive.py** (NEW): SQLite candle flywheel at logs/price_archive.db; upsert_candles() + get_candles() + has_data() with 70% coverage threshold; every live fetch auto-archives; backtests read here first (zero re-fetch)
- **backtesting/strategy_validator.py** (NEW): ValidationResult dataclass; gate checks: win_rate≥45%, Sharpe≥0.5, max_dd≤20%, min_trades≥20, avg_pnl>0; archives to backtest_results; check_live_vs_backtest_drift() for divergence detection
- **backtesting/backtest_engine.py** (MODIFIED): fetch_data() checks price_archive first; run_with_intelligence() full pipeline: run→extract_trades→validate→archive→return; _extract_trades_from_stats() helper
- **scheduler/job_runner.py** (MODIFIED): Dynamic conviction weights replace all hardcoded tier blocks; analyze_closed_trade() + invalidate_cache() wired into LONG and SHORT crypto exits, equity exits; candle archiving after every fetch; agent accuracy context injected into debate prompts
- **scripts/seed_intelligence.py** (NEW): One-time Bayesian prior seeding from 90-day backtest data; loops top 8 pairs; runs run_with_intelligence() + upsert_candles(); prints signal leaderboard with Bayesian vs prior pts
- **scripts/generate_daily_summary.py** (UPDATED): Signal attribution auto-populates "What to Keep/Change/Test/Stop" section; queries trade_attribution, signal_stats, agent_stats; real signal leaderboard table in every daily note
- **Bug fix**: SHORT branch of _execute_crypto_exit was missing analyze_closed_trade() call — fixed

## 2026-03-25
- Add generate_daily_summary.py + launchd brain service — auto-writes brain/06_daily_summaries/ at 9:47pm nightly

## 2026-03-25
- Brain architecture — /brain/ directory + 9 intelligence layer notes (constitution-governed)

## 2026-03-25
- v4.3 — 7 new indicators: SuperTrend, Ichimoku cloud, WAE, Fisher Transform, CHOP, WaveTrend, Laguerre RSI

## 2026-03-24
- v4.2 — TradingView Pro webhook integration

## 2026-03-24
- v3.9: Advanced math overhaul — 8-signal pre-filter, ATR fee-floor guard, full conviction scoring for Hurst/Kalman/squeeze/RV/AVWAP


---

## 2026-03-23 (v3.4 patch 4) — Dashboard System Health Overhaul
- **dashboard/app.py config imports**: Added MAX_DAILY_LOSS_PCT, MAX_DAILY_FEE_DRAG_PCT,
  MAX_STRATEGY_LOSS_STREAK, CRYPTO_MIN_ADX to top-level imports so panels never hardcode them.
- **_panel_risk() → _panel_system_health()**: Complete rewrite. Now shows:
  - Bot alive indicator: 🟢 LIVE (Ns ago) / 🟡 STARTING / 🔴 STALE / 🔴 HALTED with reason.
    Uses rm.watchdog_ok(120s) — flags if no scan completed in 2× the scan interval.
  - Daily loss bar: live % vs MAX_DAILY_LOSS_PCT halt threshold, shows $ amounts too.
  - Equity/crypto trade bars: use MAX_TRADES_PER_DAY_EQUITY/CRYPTO from config (was hardcoded
    3/10 — crypto limit was wrong, config has 100).
  - Fee drag bar: fees today as % of real balance vs MAX_DAILY_FEE_DRAG_PCT halt threshold.
    Turns red when ≥80% of limit.
  - Circuit breaker progress: MACD and MeanRev consecutive loss streak vs MAX_STRATEGY_LOSS_STREAK.
    Color-coded: orange at 50%, red at 75%.
  - API cost: monthly total + daily average estimate.
  - Broker status: live Coinbase connection check + PAPER/LIVE mode label.
- **Scan feed "waiting" message**: Now pulls CRYPTO_SCAN_INTERVAL_SECONDS and
  EQUITY_SCAN_INTERVAL_SECONDS from config instead of hardcoding "60s".

## 2026-03-23 (v3.4 patch 3) — Deep Audit Fixes Round 2
- **regime_detector.py**: Added `intraday` parameter. SPY daily threshold bb_width>8% was never
  triggered on crypto 5-min candles (normal bb_width 0.3–1.0%). New intraday thresholds:
  volatile when bb_width>1.2%. Crypto scan now calls `detect_regime(df=df_ind, intraday=True)`.
- **risk_manager.py trailing stop**: Entry buffer before trailing activates changed from 3% to
  0.5% for crypto (strategy names containing 'crypto' or 'mean_reversion'). Kept 2% for equity.
  Old 3% buffer meant trailing never kicked in on normal crypto moves. On a +1% spike that
  reverses, we now trail from the high instead of watching it give back all gains.
- **job_runner.py indicator pre-filter**: Removed RSI from pre-filter. crypto_macd.py explicitly
  documents "Adding RSI as entry filter DESTROYS edge." Pre-filter now gates only on MACD signal
  or unusual volume spike — consistent with the strategy's documented philosophy.
- **coinbase_broker.py buy_limit (live mode)**: Added order status verification — waits 1.5s then
  checks Coinbase for fill status. Cancelled/failed orders no longer register phantom positions.
  Partial fills (< 90% filled) are tracked at actual filled size rather than requested size.
- **exit_review.py**: Reduced API timeout per agent 45s→15s. With 3 agents per position and
  multiple open positions, the old timeout could block the scan loop for 2+ minutes. 15s is still
  generous for API response and matches scan interval scale.

## 2026-03-23 (v3.4 patch 2) — Deep Audit Fixes (MACD params, mean-reversion fee math, mins_in fallback)
- **config.py MACD parameters**: Corrected to match backtested values — MACD1 12/26/9→3/15/3,
  MACD2 5/13/3→4/16/3, MACD3 8/21/5→6/20/5. Running wrong params meant the documented Z-score
  70.81 backtest edge was NOT being applied. This is the most impactful fix in the whole audit.
- **crypto_mean_reversion.py fee math fix**: Stop widened 1.5%→2.0%. Min R:R raised 1.5x→2.5x.
  Added hard minimum reward distance: target must be ≥4% above entry (not just a R:R ratio).
  Fallback TP raised 2.5%→5.5%. Old config needed 90% WR to break even (unachievable).
  New config needs 42% WR (achievable). Strategy will fire much less often but only when viable.
- **job_runner.py mins_in fallback**: Changed from 30→0 on parse failure. Default of 30 could
  trigger time-based exits on brand-new positions with malformed timestamps. Default 0 = safe
  (blocks time exits, assumes just entered).

## 2026-03-23 (v3.4 patch 1) — Fix $0.00 P&L Churn Trades
- **config.py**: Added `CRYPTO_MIN_HOLD_MINUTES = 3` — minimum candles before any strategy SELL fires
- **job_runner.py `run_crypto_scan()`**: Strategy SELL check now gated by `CRYPTO_MIN_HOLD_MINUTES`.
  If position is < 3 minutes old, SELL signal is logged but suppressed — prevents same-candle entry+exit
  at identical price (P&L = $0.00, fee still charged = net loss on every churn trade).
- **job_runner.py `_execute_crypto_exit()`**: Added near-zero P&L warning — logs WARNING to DB when
  `abs(pnl) < estimated_round_trip_fee * 0.5` so churn trades are visible even if they slip through.
- **Root cause**: MACD `_check_exits()` fires SELL when `m1_hist < 0` or `price < vwap * 0.997`.
  These conditions can be true on the same candle as entry if MACD turns negative mid-candle.
  Hard stops in `should_exit()` (price hits stop_loss/take_profit levels) are unaffected — they
  still fire immediately regardless of hold time (those are real price levels, not timing artifacts).

## 2026-03-23 (v3.4) — Mean-Reversion Strategy for Ranging Markets
- strategies/crypto_mean_reversion.py: New strategy — RSI<33 + near lower BB + ADX<22
  in ranging/volatile regimes. Target = mid BB. Stop = 1.5%. Min R:R 1.5x. Conf 0.45–0.75.
- config.py: Added MEAN_REVERSION_ENABLED, MEAN_REVERSION_RSI_ENTRY, MEAN_REVERSION_ADX_MAX
- job_runner.py: run_crypto_scan() now runs mean-reversion path after existing debate path
  when regime is ranging or volatile

---

## 2026-03-23 (v3.3 patch 7) — Notifications Overhaul + Image Overlap Fixes
- **dashboard/app.py `_panel_notifications()`**: Complete rewrite — plain English one-liners ("Closed ETH-USDC → +$2.34 (target reached)"), relative time ("5m ago"), max 6 items, filters out signal spam and system startup noise, shows only trades/halts/summaries. Full history still stored in DB.
- **dashboard/app.py King header**: Removed `position:absolute` court SVG background that was bleeding into content. Header now shows LeBron during market hours, dunk GIFs before/after — never both stacked in the same column.
- **dashboard/app.py King win flash**: Only fires when a trade is <20 minutes old (not just whenever P&L > 0). Big win vs regular win show different animations — never both at once. Removed separate stat-icon row above metrics that created extra height.
- **dashboard/app.py Saiyan layout**: Removed separate `aura_l`/`aura_r` columns (were 0.6 ratio, caused overflow at 150px width). Aura GIFs now live inside character columns at 60px, constrained to column width. Character SVGs reduced 150→130px to fit cleanly.
- **dashboard/app.py Saiyan animations**: Win/buy text animations gated — shows at most one, only when a win trade is <10 min old or there's an active buy signal. Multiple situational animations no longer all fire simultaneously.

## 2026-03-23 (v3.3 patch 6) — BRON_DBZ_IMAGES Full Integration
- **dashboard/app.py**: Integrated local BRON_DBZ_IMAGES asset pack (304 files) across all 4 views. Added `_b64img()`, `_local_img()`, `_local_anim()`, `_saiyan_form()`, `_aura_gif()` helpers + asset dir constants.
- **THE KING**: Header flanked by `dunk_gold_23.gif` + `dunk_celebrate_gold.gif`. Basketball stat icons (ppg/ast/reb/fgpct/blk) above metrics. Win flash shows `dunk_celebrate_gold.gif`. Big win (>$10) fires `power_text_dunk.html`. Every win triggers `power_text_win.html`. Halt shows defense SVG. Court SVG background in header.
- **SAIYAN MODE**: Full transformation system — Kakarot+Prince SVGs auto-upgrade (base→SSJ1→SSJ2→Blue→God→Ultra→Mastered) based on P&L and win rate. Transform GIFs fire on form change. Lightning frame around power level. `power_level_9001.gif` when power > 9000. `power_level_max.gif` > 50000. Looping aura GIFs. Dragon Ball orbs (1–7) earned by trade milestones with gold glow. Ki blast icons next to metrics. Power aura decoratives. Kamehameha GIF in battle log header. Final Flash for strong SELLs. Spirit bomb on halt. Situational HTML animations (powerup/ki charge/kamehameha) based on live state. Energy waves on positions.
- **RING CEREMONY**: Dunk GIFs flanking header. `dunk_celebrate_gold.gif` inside each earned ring card. `power_text_dunk.html` banner on earned rings. `bouncing_basketball.html` idle animation in empty state.
- **FILM ROOM**: Basketball stat icons (20px, 55% opacity) above metrics. Court SVG (40px, 40% opacity) in header.

## 2026-03-23 (v3.3 patch 5) — Dashboard Theme Separation
- **dashboard/app.py**: LeBron (👑) now strictly THE KING view only — `render_chat_column` is now theme-aware with separate icons per view (👑 King, 🐉 Saiyan, 📊 Film Room, 🏆 Ring). Chat headers renamed per view too.
- **dashboard/app.py**: Saiyan mode massively expanded — 9 DBZ characters now rendered (Goku, Vegeta, Gohan, Piccolo, Broly, Trunks, Krillin, Frieza, Cell). All characters use GIF URLs with emoji fallback. Added rotating DBZ quotes (Goku/Vegeta). Second image row for Z-Fighters. SSJ transformation indicator based on P&L. Removed duplicate image strip.
- **dashboard/app.py**: Film Room is now pure analytics — removed DBZ alias comment, no crown icons, no Saiyan language. Vegeta emoji changed from '👑' to '🔥' so the crown is exclusively LeBron.

## 2026-03-23 (v3.3 patch 4) — Rapid Validation + Turbo Paper Mode
- **scripts/rapid_validate.py**: New historical replay validator — fetches 14 days of real Coinbase 5-min candles, simulates full trade lifecycle (stop/target/time-exit), reports Sharpe/drawdown/win-rate/per-pair breakdown. CLI: `--no-ai`, `--days N`, `--pairs`, `--verbose`.
- **scripts/check_readiness.py**: Added `--fast-track` flag — lowers criteria to 2 days / 10 trades / 45% win rate after historical validation passes. Checks for `logs/validation_report.txt` PASS. Fixed trade count to use `pnl_usd != 0` (counts SHORT exits too).
- **`.env`**: `CRYPTO_SCAN_INTERVAL_SECONDS` 60→15 — turbo paper mode scans 4x faster to accumulate trade history quickly.

## 2026-03-23 (v3.3 patch 3) — Deep Bug Sweep Round 2
- **debate_engine.py**: Fixed regime override bug — `run_debate()` was calling `detect_regime()` (SPY-based) and overwriting the per-asset regime that job_runner already detected from the asset's own candles. Now respects pre-computed regime when present.
- **trade_logger.py**: Added `entry_reason` column to `open_positions` table (with safe migration for existing DBs). Updated `persist_position()` to accept and store it.
- **risk_manager.py**: `_restore_positions()` now loads `entry_reason` from DB. `update_high()` now passes `entry_reason` to `persist_position()` so it's never cleared on trailing stop updates.
- **job_runner.py**: `_execute_equity_exit()` and `_execute_crypto_exit()` now accept optional `market_data` param — real RSI/MACD/ADX/vol/regime passed to `store_trade_experience()` instead of hardcoded zeros. Memory store quality vastly improved.
- **job_runner.py**: Crypto exit monitor refactored — fetches indicators once upfront (shared by stop-loss check, time exit, AI review, and memory store). Eliminates duplicate API calls.
- **main.py**: Updated banner version v3.0→v3.3. Added startup sanity-check assertions for risk config values.

## 2026-03-23 (v3.3 patch 2) — Bug Sweep
- **indicators.py**: Added `ema200` calculation (was missing — Minervini 200d MA check and agent context was always seeing `None`)
- **market_data.py**: Guarded `screen_watchlist()` against undefined `EQUITY_WATCHLIST` — returns `[]` cleanly (auto_screener handles discovery anyway)
- **job_runner.py**: Fixed timezone arithmetic bug in both equity and crypto exit paths — `entry_dt.replace(tzinfo=tz if not entry_dt.tzinfo else None)` was backwards; fixed to `entry_dt if entry_dt.tzinfo else entry_dt.replace(tzinfo=tz)`. Was causing wrong `mins_in` values and misfiring time-based exits
- **job_runner.py**: Fixed `entry_reason` storage — was mutating a copy of the position dict (did nothing); now passed directly to `register_position()` for both equity and crypto/SHORT paths
- **risk_manager.py**: `check_entry` and `pre_check_entry` confidence floors aligned with aggressive mode — was hardcoded 0.40 for all; now 0.30 crypto / 0.35 equity matching risk_synthesizer
- **risk_manager.py**: `register_position()` now accepts and stores `entry_reason` in position dict — exit review AI gets full context on why we entered
- **risk_manager.py**: Correlation groups expanded to cover all 20 crypto pairs — BTC/UTXO cluster, ETH ecosystem + L2 DeFi, Alt-L1 cluster, Meme cluster, XRP standalone
- **coinbase_broker.py**: Added zero-size guard on `buy_limit` (prevents silent order rejection on tiny positions)
- **coinbase_broker.py**: `sell_market` now logs trade with taker fee (was silently dropping trade logs on emergency exits); added `_paper_sell_market()` with correct taker fee accounting

## 2026-03-23 (v3.3 patch) — Expanded Crypto Universe + Cost Filter
- **`.env`**: Expanded `CRYPTO_PAIRS` from 8 to 20 — added DOT, LTC, BCH, UNI, NEAR, APT, OP, ARB, SUI, PEPE, WIF, INJ (all USDC pairs on Coinbase Advanced Trade)
- **`job_runner.py`**: Added indicator pre-filter before AI debate call — only debates when MACD histogram is positive OR RSI is emerging (25–55) with volume spike ≥1.3x; avoids burning API budget on dead markets with 20 pairs scanning 24/7

## 2026-03-23 (v3.3) — Aggressive Mode Unlock
- **config.py**: `MAX_POSITIONS_CRYPTO` 3→5, `MAX_POSITIONS_EQUITY` 2→3, `MAX_STRATEGY_LOSS_STREAK` 5→8, `CRYPTO_MIN_ADX` 15→10
- **risk_synthesizer.py**: min confidence crypto 40%→30%, equity 45%→35%; vote agreement 50%→37.5%; position size cap 20%→35% of account (both LONG and SHORT paths)
- **job_runner.py**: ranging regime gate 55%→40% (AI + MACD paths); Minervini filter advisory only (no longer hard-blocks equity); earnings gate 3 days→1 day; F&G scale-down threshold 80→90 with reduced penalty 25%→10%; IV rank threshold 80→90 with reduced penalty 20%→10%; COT filter advisory only (no longer hard-blocks futures longs)

## 2026-03-22 (v3.2)
- **Stats accuracy, terminal dashboard, paper trading parity**
- Fixed `get_all_time_stats()` and `get_win_rate()`: changed `WHERE action='SELL'` to `WHERE pnl_usd != 0` so SHORT exits (logged as `action='BUY'`) are counted correctly
- Added `get_today_stats()` — single authoritative source for today's closed-trade W/L/win-rate/fees/net P&L
- Fixed account balance display in dashboard and risk manager to use `ACCOUNT_SIZE + all_time_pnl` (was hardcoded $500)
- Fixed daily loss limit in `risk_manager.py` to use real balance
- Fixed SHORT exit path in `job_runner.py` to call `alert_trade_closed`
- Created `dashboard/terminal.py` — full 220-column terminal dashboard with ANSI colors and box-drawing characters; renders positions, stats (today + all-time), recent trades, signals, last AI debate, system events
- Fixed terminal dashboard: `_split_open()` replaces `_top()` for correct `├` continuation line
- Fixed terminal dashboard: `_ts()` helper extracts clean HH:MM:SS from ISO timestamps; all panel functions updated to use it
- Fixed terminal dashboard closing line to use clean `├──┴──┤` instead of replace hack
- Integrated terminal dashboard into `job_runner.py` run loop (renders every 5 seconds)

## 2026-03-22 (patch)
- **Notifications reworked: email removed, dashboard panel added**
- `alerts/telegram_alert.py` rewritten — all notifications now write to `system_events` table (`source='notify'`) instead of sending email
- Added `get_recent_notifications()` to `trade_logger.py`
- Added Notifications panel to THE KING dashboard view (left column, below Today's Trades)
- Removed email config (`EMAIL_FROM`, `EMAIL_TO`, `EMAIL_APP_PASSWORD`) from `config.py` and `.env.example`

## 2026-03-22
- **Resilience & ops infrastructure added (v3.1)**
- Added git version control with initial commit (`.gitignore` updated)
- Added SQLite WAL mode for crash-safe database writes (`logging_db/trade_logger.py`)
- Added `scripts/start_bot.sh` — launchd wrapper, always starts in paper mode
- Added `scripts/com.algotrading.king.plist` — auto-restart on crash and Mac reboot
- Added `scripts/backup_db.sh` — daily SQLite + CSV backup to `~/.algo_backup/db/`, 30-day retention
- Added `scripts/com.algotrading.backup.plist` — schedules backup at 2:00 AM daily
- Added `scripts/backup_credentials.sh` — backs up `.env` to `~/.algo_backup/credentials/`, 10-version rotation
- Added `scripts/check_readiness.py` — evaluates 7 criteria for paper → live transition, sends email alert when all pass
- Added `scripts/com.algotrading.readiness.plist` — runs readiness check at 7:00 AM daily
- Added `scripts/install_services.sh` — one-command launchd setup
- Added `scripts/log_change.sh` — helper to prepend entries to this file
- Updated `CLAUDE.md` to document all new infrastructure
- Updated `.gitignore` to exclude backup dirs and service logs

---

## 2026-03-22 (v3.0 baseline — initial commit)
- v3.0: Extended thinking exits, LanceDB vector memory, regime detection
- Prompt caching on all 8 AI agent system prompts (80% cost reduction)
- Structured outputs (guaranteed valid JSON, zero parse failures)
- 4-view Streamlit dashboard: TheKing / Saiyan / FilmRoom / RingCeremony
- Position persistence (SQLite open_positions, restart-safe)
- Watchdog alert if no scan completes in 15 minutes
- Auto debate depth tuning based on account size and win rate
- Full auto-screener: Finviz unusual volume + Yahoo gainers + SEC EDGAR filings

---

_To add an entry: `bash scripts/log_change.sh "Description of change"`_
_Claude should update this file (and CLAUDE.md) whenever project files are modified._
