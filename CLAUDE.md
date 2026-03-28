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
- Discovers stocks/crypto/futures opportunities automatically (no watchlist)
- Runs every candidate through 8 legendary investor AI agents who debate it
- Uses extended AI reasoning (interleaved thinking) for exit decisions
- Enforces unbreakable emotional safeguards (the amygdala is removed)
- Learns from every completed trade via Bayesian signal attribution + NumPy vector memory (SQLite-backed)
- Writes all notifications to SQLite; dashboard Notifications panel displays them
- Displays everything on a LeBron James / Dragon Ball Z themed dashboard
- Trades 100% autonomously — owner is never asked to approve anything

## Owner Profile
- Mac user (MacBook Air 2020, Python 3.14 at /Library/Frameworks/Python.framework/Versions/3.14/bin/python3)
- Paper account: $5,000 (ACCOUNT_SIZE=5000 in .env — sized to avoid position-size constraints during paper phase)
- Relatively technical but wants zero day-to-day intervention
- Wants the system to WIN — everything tuned for performance
- Prefers simple explanations, hates fluff

## Current Version: v9.0 (Sprint 2 complete)
- v9.0 Sprint 2 (2026-03-28): Lane 3 — Prediction Markets built. All off by default (LANE3_ENABLED=false).
  - `data/polymarket_feed.py`: Gamma REST scanner — market discovery, classification, tradeability filter
  - `data/kalshi_feed.py`: Kalshi REST feed — CFTC-regulated, demo + live environments
  - `data/whale_tracker.py`: smart money signal via CLOB trade history (±8% prob boost)
  - `execution/prediction_market_base.py`: abstract broker interface (Polymarket + Kalshi interchangeable)
  - `execution/polymarket_broker.py`: CLOB broker — paper logs to SQLite, live uses py-clob-client
  - `execution/kalshi_broker.py`: REST broker — handles settlement resolution + P&L
  - `strategies/ai_agents/ensemble_forecaster.py`: multi-LLM forecaster — Claude-first, +GPT-4o/Gemini optional
  - `learning/pm_calibrator.py`: Platt scaling calibrator (SQLite-backed, activates after 30 resolved markets)
  - `alerts/alert_dispatcher.py`: Telegram + SQLite dual-channel alerts (same public API as telegram_alert.py)
  - `scheduler/lane3_scanner.py`: 15-min scan loop — discover → forecast → calibrate → whale → edge → trade
  - config.py: LANE3_*, PM_*, ENSEMBLE_*, TELEGRAM_*, OPENAI_API_KEY, GOOGLE_API_KEY
  - trade_logger.py: lane column migration (lane1=stocks, lane2=crypto, lane3=prediction)
  - dashboard/app.py: expander_lane3() panel — open predictions, resolutions, calibration stats
  - To activate: set LANE3_ENABLED=true, POLYMARKET_ENABLED=true in .env (paper, no wallet needed)
- v9.0 (2026-03-26): Sprint 1 — Foundation overhaul: MCP server, risk decomposition, Binance migration, job_runner split
  - **MCP server** (`mcp_server/server.py`): 15 FastMCP tools expose full bot state over MCP protocol.
    Tools: get_positions, get_open_trades, get_recent_trades, close_position, get_signal_stats,
    get_agent_accuracy, get_ml_signal, get_price_history, get_macro_context, scan_crypto_pairs,
    get_debate_result, run_backtest, get_daily_summary, get_readiness_score, get_notifications.
    Start: `python3 mcp_server/server.py`
  - **Risk decomposition** (`risk/`): Rewrote 527-line god-class into 5 single-responsibility modules.
    `risk_manager.py` → thin orchestrator. New modules:
    `position_sizer.py` (25%-fractional Kelly, 5-trade clamp),
    `stop_loss_manager.py` (calc_stop_loss, calc_take_profit, should_exit),
    `drawdown_controller.py` (daily loss + fee drag gates),
    `risk_limits.py` (market hours, position limits, deployment cap, fee gate),
    `var_calculator.py` (historical VaR at 95/99%, new capability).
    Public API 100% unchanged — nothing else in the codebase needed to change.
  - **Bybit → Binance migration**: Replaced bybit_broker.py (deleted) with `execution/binance_broker.py`.
    Drop-in replacement using `python-binance`. Server-side SL/TP via STOP_MARKET/TAKE_PROFIT_MARKET.
    Testnet support: `BINANCE_TESTNET=true`. Fees 0.040% (cheaper than Bybit 0.055%).
    `get_bybit_broker` alias preserved. Updated: config.py, requirements.txt, validate.py, .env.
  - **job_runner.py decomposition**: 1,812-line god object → 6 focused modules (86% reduction).
    `scheduler/_helpers.py` (273L): shared state, optional-import flags, 3 helper functions, strategy singletons.
    `scheduler/exit_monitor.py` (329L): AI exits, hard-stop/time/stagnant exits, attribution, EOD close.
    `scheduler/equity_scanner.py` (227L): Clenow ranking, Minervini, AI debate, F&G/IV sizing.
    `scheduler/crypto_scanner.py` (641L): 8-signal gate, ML gate, microstructure veto, 3-agent debate, MR path.
    `scheduler/perp_scanner.py` (153L): Binance perp entry/exit, 4h flat exit.
    `scheduler/job_runner.py` (258L): thin orchestrator — re-exports sub-modules, futures/watchdog/premarket/schedules.
  - **Claude Code agents** (`.claude/agents/`): 4 specialized sub-agents:
    `portfolio_manager.md` (risk, halt/resume, readiness),
    `trade_strategist.md` (signal quality, debate analysis),
    `devil_advocate.md` (overfitting/fee-blindness stress-tester),
    `system_engineer.md` (code changes, debugging).
  - **Claude Code commands** (`.claude/commands/`): 5 slash commands:
    `/health` (30-second check), `/audit` (full 6-dimension audit),
    `/deploy` (pre-flight + DB backup), `/optimize` (walk-forward params),
    `/build-strategy` (scaffold + backtest validation).
  - **Tests** (`tests/`): 3 test files, ~25 tests total.
    `test_indicators.py`: required fields, look-ahead bias, edge cases.
    `test_risk_manager.py`: halt rules, daily loss limit, position limits, stop math, R/R.
    `test_broker_paper.py`: Coinbase, Alpaca, Binance paper-mode smoke tests.
  - **GitHub**: Repository live at `futureisnowtech/trading-bot-main`, branch `feature/agent-overhaul`.
    SSH push configured. Pre-commit validation hook active.
- v8.0 (2026-03-26): Architecture overhaul — 3-agent debate, ML signal layer, walk-forward, funding rate wiring
  - **3-agent debate** (`strategies/ai_agents/analyst_agents.py`): Replaced 9 agents + moderator + Goku (up to 11
    API calls) with 3 focused non-overlapping agents (3 calls, 3.5× cheaper, ~4× faster):
    • `funding_regime` (Bardock): crypto-native macro — funding rate, OI, VIX, DXY, macro score.
      Funding > 0.05%/8h = market overheated → HOLD. Neutral/negative funding = best entry window.
    • `momentum_structure` (Vegeta): technical setup quality — ADX, squeeze, WAE, WaveTrend, MACD.
      Requires ≥2 aligned signals for BUY. One signal alone = HOLD.
    • `risk_economics` (Krillin): fee math, ATR vs fees, volume, time-of-day gate. Hard kill switch.
    Decision rule: 2/3 agents BUY = BUY. No moderator. No Goku veto.
  - **ML signal layer** (`learning/ml_signal.py`): LightGBM (sklearn GradientBoosting fallback) trained
    on rolling 90-day trade_attribution data. Features = 19 signal flags + regime encoding. Target = won.
    Retrains every 50 new trade closes. Exposes `get_ml_signal(market_data) -> (p_win, label)`.
    Gate in job_runner: if p_win < ML_SIGNAL_MIN_PROB (0.52) → skip debate (fail-open if no model yet).
    Closes backtest-to-live gap: model trained on LIVE outcomes, not math-only backtests.
  - **Walk-forward OOS validation** (`backtesting/backtest_engine.run_walk_forward()`): 2-fold default
    (train 60d → test 30d per fold). Pass criteria per `brain/rbi/01_backtest_standards.md`:
    WR ≥ 30%, PF ≥ 1.2, Sharpe ≥ 0.5, DD ≤ 20%, ≥15 OOS trades. 75% of folds must pass.
  - **Funding rate wired into market_data**: `_build_market_data` now enriches market_data with
    `funding_rate_pct`, `funding_signal`, `macro_score`, `vix_regime`, `dxy_change`, `spy_change`.
    Agents receive these as structured fields, not just as string context.
  - **Symbol cooldown removed**: 20-min post-loss cooldown eliminated. ML gate and 3 focused agents
    are the quality filter now. A good setup 5 min after a loss is not worse than the same setup 21 min later.
  - **RBIPMS framework** (`brain/rbi/`): 5 research/operational docs created — full lifecycle framework,
    backtest standards (OOS spec, look-ahead checklist), incubation playbook, strategy lifecycle
    (promotion/monitoring/retirement criteria), and full RBI audit 2026-03-26.
  - `config.py`: QUICK/FULL_DEBATE_AGENTS = 3 new agent keys, ML_SIGNAL_MIN_PROB default=0.08
    (auto-raised by auto_env_updater: 50 trades→0.35, 100→0.45, 200→0.52),
    FUNDING_OVERHEATED_PCT=0.05, FUNDING_FAVORABLE_PCT=0.01, GOKU_ENABLED=False
- v7.0 (2026-03-25): AI-first pipeline + backtesting + self-taught learning
  - **AI pre-screener** (`learning/ai_prescreener.py`): batch-scores ALL crypto pairs in ONE
    Claude Haiku call BEFORE any full debate. Catches market-wide noise (all symbols hitting
    same signal = skip all). Symbols scoring < 4/10 skip the expensive debate entirely.
    `PRESCORE_THRESHOLD = 4`. Fail-open on API errors (all pass). Saves debate API cost.
  - **Meta-learner** (`learning/meta_learner.py`): fires after every 10 trade closes.
    Claude analyzes last 100 trade attributions, identifies over/under-weighted signals,
    stores weight-delta recommendations to `meta_recommendations` table.
    `dynamic_weights.get_conviction_score()` now applies meta-deltas on top of Bayesian weights.
    New tables: `meta_recommendations`, `meta_analysis_log`.
  - **Live backtest validator** (`learning/live_backtest_validator.py`): background thread
    runs 30-day rolling backtest every 4 hours on top 3 crypto pairs using price archive
    (zero extra API calls). Results injected into every debate as "ROLLING BACKTEST" context.
    Agents can see "strategy ✅ PASS 58% WR" vs "❌ FAIL 38% WR" and calibrate.
  - **`scheduler/job_runner.py`**: two-phase crypto scan:
    1. Pre-phase: fetch 30 candles + indicators for all pairs → batch prescreener
    2. Main loop: prescreener gate before each debate, + backtest + meta insight injected
  - **`learning/dynamic_weights.py`**: Layer 2 added — meta-learner deltas applied on top
    of Bayesian weights. `meta_adj = get_meta_weight_adjustments(regime)` + `pts = base + delta`
- v6.0 (2026-03-25): AI-first rework — conviction floor removed, AI sees everything
  - Hard conviction gate (30pts) replaced by: any signal fires + `should_block_trade()` macro gate
  - `get_active_signal_stats_brief()`: Bayesian win rates for fired signals injected into every agent
  - `get_agent_self_accuracy()`: each agent sees their own historical accuracy in their prompt
  - Session bias + multiplier injected as readable text (not numeric floor gate)
  - Conviction score + signal list visible to all agents + moderator
- v5.2 (2026-03-25): Goku agent + Data feed layer + Tax tracking
  - `data/news_feed.py`: CryptoPanic API + RSS fallback; sentiment -1 to +1; news_risk HIGH/MEDIUM/LOW
  - `data/macro_feed.py`: DXY/SPY/GLD/VIX/TLT via yfinance + Coinglass funding rates; RISK_ON/NEUTRAL/RISK_OFF; macro_score -5 to +5
  - `data/market_context.py`: Session detector (ASIA/LONDON/NY_OPEN/etc.); no_trade_flags + conviction_hints; get_context_for_debate() injects into every debate; should_block_trade() pre-debate gate
  - `learning/tax_tracker.py`: Section 1256 futures 60/40 blended (~17% vs 32% short-term); YTD P&L by treatment; harvesting opportunities; exit_note injection for Tudor Jones/Soros/Simons
  - `strategies/ai_agents/analyst_agents.py`: Goku (Ultra Instinct) added — 9th agent; Jim Simons/PTJ/Soros; sees all other votes, absolute veto (-100), boost (+25), 1200 tokens, no cache
  - `strategies/ai_agents/debate_engine.py`: Goku runs after moderator when signal=BUY; VETO flips to HOLD; BOOST raises confidence by +0.15; DebateResult tracks goku_verdict
  - `learning/post_trade_analyzer.py`: record_tax_lot() wired into every trade close
  - Tax snapshot added to daily brain summaries
- v5.3 (2026-03-25): AI Session Analyst + Session-aware routing
  - `strategies/ai_agents/session_analyst.py`: Fires at Asia/London/NY opens; outputs session_bias + conviction_threshold_multiplier (0.7–1.5×) + signal overrides + avoid_flags; stored to SQLite session_contexts table
  - `scheduler/job_runner.py`: London window opened (dead zone now 2-3am only); conviction threshold = base × session multiplier; macro+news context injected into every crypto debate; session open triggers at 8pm/3am/8:30am ET
  - `strategies/ai_agents/exit_review.py`: Tax-aware exit — tax note injected into Tudor Jones/Soros/Simons prompts; new entry_ts + asset_class params
- v3.0 baseline: Extended thinking exits, LanceDB memory, regime detection,
  prompt caching, structured outputs, 4-view dashboard, position persistence,
  watchdog, auto cost tuning
- v3.1 (2026-03-22): Ops infrastructure — git, WAL mode, auto-restart,
  daily DB backups, credential backups, paper-to-live readiness tracker
- v3.2 (2026-03-22): Aggressive mode — replaced 8 agents with active trading
  methodologies; 1-min crypto candles; 8 pairs; Williams %R + Fear&Greed + IV rank
- v3.3 (2026-03-23): Gate unlock — min confidence 30% crypto/35% equity;
  vote agreement 37.5%; 5 max crypto / 3 equity positions; min ADX 10
- v3.4 (2026-03-23): Mean-reversion strategy — RSI<33 + lower BB + ADX<22;
  runs in parallel with AI debate path
- v3.5 (2026-03-24): Research-backed overhaul — 5 focused agents (dropped 3);
  10 new math signals (AR(1) autocorr, OU half-life, Kyle lambda R² filter,
  Hurst H min_periods 96, RV ratio gap guard, squeeze direction);
  CoinbaseMicrostructureFeed WebSocket; Kelly sizing; 20 crypto pairs (.env)
- v4.3 (2026-03-25): 7 new indicators added to indicators.py + conviction scoring:
  • SuperTrend (ATR 10, mult 3.0): binary trend direction, ATR-adaptive trailing band
    supertrend_bullish → +12 pts; pandas_ta primary, manual fallback included
  • Ichimoku Cloud (kumo only): price vs Senkou Span A/B — dynamic S/R
    cloud_bullish → +8 pts; TK crosses intentionally omitted (too noisy on 1-min)
  • Waddah Attar Explosion (WAE): MACD(20/40) × BB width — momentum + explosion gate
    wae_bullish + wae_exploding → +10 pts; wae_bullish only → +5 pts
  • Ehlers Fisher Transform: Gaussian-normalised turning point detector
    fisher_cross_up (cross from negative) → +8 pts
  • Choppiness Index (CHOP): trending < 38.2 / choppy > 61.8
    chop_trending → +5 pts; agents see chop value for regime context
  • WaveTrend Oscillator (WT): LazyBear's crypto-popular 2-line momentum oscillator
    wt_oversold_cross (WT1 > WT2 from below -53) → +12 pts
  • Laguerre RSI (γ=0.5): Ehlers 4-tap filter, ~5× less lag than RSI-14
    lrsi < 0.15 → +8 pts; lrsi < 0.25 → +4 pts
  All 7 tagged in signal_triggers so AI agents see them during debate.
- v4.2 (2026-03-24): TradingView Pro webhook integration:
  • scripts/tradingview_webhook.py: standalone HTTP server (port 8765) receives Pine Script alerts
    Validates TV_WEBHOOK_SECRET, normalises symbol (BTCUSDC→BTC-USDC), writes to system_events
    GET /health + POST /webhook — run: python3 scripts/tradingview_webhook.py
  • scripts/tradingview_pine.pine: copy-paste Pine Script v5 template — mirrors bot's 7-signal gate
    (MACD 3/15/3 cross, Williams %R ≤ -80, BB-Keltner squeeze fire)
    alert() sends JSON with symbol/action/price/tf/signal to ngrok → webhook
  • scheduler/job_runner.py: Tier 3 conviction boost — get_recent_tv_signal() checked per symbol
    If buy signal arrived within TV_SIGNAL_MAX_AGE_SECONDS (300s): +TV_SIGNAL_BOOST_CONVICTION pts (20)
    TV signal also tagged in signal_triggers so agents see it during debate
  • config.py: TV_WEBHOOK_PORT, TV_WEBHOOK_SECRET, TV_SIGNAL_BOOST_CONVICTION, TV_SIGNAL_MAX_AGE_SECONDS
  • logging_db/trade_logger.py: get_recent_tv_signal(symbol, max_age_seconds) queries system_events
  • .env: TV_WEBHOOK_PORT=8765, TV_WEBHOOK_SECRET=, TV_SIGNAL_BOOST_CONVICTION=20 placeholders
  Setup: 1) Set TV_WEBHOOK_SECRET in .env, 2) Start webhook server, 3) Run ngrok http 8765,
         4) Paste HTTPS URL into TradingView alert → Webhook URL,
         5) Use tradingview_pine.pine as the indicator
- v4.1 (2026-03-24): OU z-score + Ask Claude upgrade + CI/CD:
  (see v4.1 details below)
- v4.0 (2026-03-24): De-risk overhaul + RSI removal + Hurst removal + min-agreement=2:
  • All risk params cut 50%: MAX_RISK_PER_TRADE 2%→1%, MAX_DAILY_LOSS 8%→4%,
    MAX_POSITIONS_CRYPTO 10→5, stops/targets halved, PERP leverage 20→10,
    FUTURES_NUM_CONTRACTS 3→2, MAX_STRATEGY_LOSS_STREAK 8→4
  • Position sizes: CRYPTO/EQUITY $500→$250 (in .env)
  • Min agent agreement: explicit 2 agents (not % — buy_votes < 2 = VETO)
  • FULL_DEBATE_MIN_AGREEMENT: 0.60→0.40 in config
  • RSI removed as entry gate: crypto_mean_reversion now uses Kalman+AVWAP
    (kalman_dev ≤ -0.8% OR avwap_dev ≤ -0.5%) with autocorr confidence boost
  • Hurst fully removed: indicators.py (calc + _hurst_rs function), job_runner.py
    (HURST_MEAN_REVERT_MAX import, signal 8, conviction scoring, gate condition)
  • FutureWarning fixed: squeeze_fired now uses .astype(bool) before comparison
  • Fee brake raised: MAX_DAILY_FEE_DRAG_PCT 5%→10% ($25→$50 limit)
  • config.py: HURST_MEAN_REVERT_MAX removed
- v3.9 (2026-03-24): Advanced math signal overhaul — MACD demoted from gatekeeper to one-of-eight:
  • scheduler/job_runner.py: pre-filter replaced — 8 independent signal paths now gate debate:
    1. MACD 3-variant consensus (25 pts)
    2. Williams %R ≤ -80 extreme oversold (20 pts)
    3. Momentum + volume breakout (15 pts)
    4. BB-Keltner squeeze fire ≥20 bars, direction > 0 (20 pts) ← NEW
    5. RV ratio ≥ 1.3 volatility expansion (15 pts) ← NEW
    6. Kalman deviation ≤ -1.0% (price below Kalman estimate) (10 pts) ← NEW
    7. AVWAP deviation ≤ -0.5% (reclaim setup) (10 pts) ← NEW
    8. Hurst H < 0.45 mean-reverting regime (10 pts) ← NEW
    OU half-life in [3, 60] min: +5 pts | Kyle lambda ≤ 30th pct: +5 pts
  • ATR fee-floor guard added before any debate call:
    If ATR/price < 0.4%, expected 4×ATR target < 1.6% → can't clear 2.4% round-trip fees
    Skips debate entirely, saving API tokens on untradeable symbols
  • signal_triggers now tags ALL 8 signals so agents see the full evidence matrix
  • config.py: added ATR_FEE_FLOOR_PCT, SQUEEZE_MIN_BARS, RV_EXPANSION_THRESHOLD,
    HURST_MEAN_REVERT_MAX, KALMAN_ENTRY_DEV_PCT, AVWAP_ENTRY_DEV_PCT,
    OU_HALFLIFE_MIN/MAX_MINUTES, KYLE_LAMBDA_LOW_PCT, ATR_STOP/TARGET_MULTIPLIER
- v3.8 (2026-03-24): Bybit perp integration + start-button EDEADLK fix:
  • execution/bybit_broker.py: new — Bybit linear perp (USDT-margined) broker
    Paper mode uses real Bybit public prices, live uses pybit v5 HTTP
    open_long/open_short/close_position/get_funding_rate/get_open_interest
    Server-side stop-loss + take-profit set after entry (set_trading_stop)
  • strategies/crypto_perp_strategy.py: new — long/short perp signal
    20-bar breakout + RSI + ADX + funding rate + OI confirmation
    SHORT: breakdown + RSI<45 + funding≥0.01%/8h (longs paying = bearish)
    LONG: breakout + RSI>55 + funding≤0.03% (not overloaded with longs)
  • scheduler/job_runner.py: run_perp_scan() + _monitor_perp_exit()
    Scans PERP_PAIRS every CRYPTO_SCAN_INTERVAL_SECONDS
    4h flat exit to avoid funding cost drain on stagnant positions
  • risk/risk_manager.py: _perp dict + PERP_MAX_POSITIONS gate
    register/close/get_position/should_exit all handle 'perp' strategy
    _get_deployed uses margin (notional/leverage) not full notional
  • config.py: PERP_ENABLED, BYBIT_*, PERP_* constants
  • .env: BYBIT_API_KEY/SECRET/TESTNET placeholders, PERP_PAIRS, PERP_ENABLED=true
  • dashboard/app.py _start_bot(): bypass launchd, use subprocess.Popen directly
    PYTHONDONTWRITEBYTECODE=1 + start_new_session=True + 6s sleep
    Fixes OSError EDEADLK (Python 3.14 .pyc file lock bug)
  • scripts/reload_on_change.sh: replaced launchctl unload/load with nohup python3
    (launchctl load deprecated, fails silently on modern macOS)
  • BYBIT_TESTNET=true in .env — fill BYBIT_API_KEY/SECRET to go live
- v3.7 (2026-03-24): Broker migration + scan feed fixes + auto-reload fix:
  • EQUITY_ENABLED=false — equity off, crypto+futures only with $500 account
  • execution/webull_broker.py: proxies to AlpacaBroker (Webull API 403-blocked)
  • execution/alpaca_broker.py: new file, full equity broker via official Alpaca API
  • execution/tradovate_broker.py: _paper_trade/_paper_close now use real ES prices
    via yfinance instead of hardcoded $5800 fake price
  • data/market_data.py: Fear&Greed switched to Alternative.me (was CNN API, failing
    silently → stuck at 50 Neutral all day). Current: 11 Extreme Fear
  • scheduler/job_runner.py: hard block new entries 2-5am ET (was just higher floor);
    dead-zone conviction floor raised 50→70 (MACD+Williams alone can't fire)
  • config.py: MAX_DAILY_FEE_DRAG_PCT 3%→5% ($25 limit, was $15 — hit before US hours)
  • scripts/reload_on_change.sh: SIGTERM before launchctl unload, sleep 4s to allow
    Python 3.14 to release file locks — fixes OSError EDEADLK on auto-reload
  • scripts/test_brokers.py: new file — one-command broker health check
  • scripts/alpaca_broker.py: n/a (alpaca-py 0.43.2 installed)
  • FUTURES_ENABLED=true, EQUITY_ENABLED=false in .env
  • Tradovate API access requires paid subscription — no free demo API tier
    Paper simulation uses real ES/yfinance prices instead
- v3.6 (2026-03-24): Win-rate overhaul — all bugs fixed + aggressive tuning:
  • debate_engine.py: hard vetoes (manipulation_risk/fee_discipline) NOW
    enforced in code with early-return — were only in prompt text before
  • coinbase_feed.py: 30s watchdog breaks inner loop on silent disconnect
    (was infinite hang with no reconnect)
  • job_runner.py: full 5-agent debate for crypto (was quick 3-agent, missing
    regime_volatility + manipulation_risk); conviction scoring pre-filter
    (30 normal / 50 dead-zone 2-7am ET); symbol 20-min loss cooldown;
    OBI+TFI microstructure veto (OBI<-0.35 AND TFI<-0.20 = skip debate);
    stagnant trade killer (45min <15% target progress = exit);
    volume threshold 0.5→0.3; regime-MACD double-gate removed
  • config.py: MAX_DEPLOYED_PCT 0.75→0.90; MAX_DAILY_LOSS_PCT 0.05→0.08;
    CRYPTO_TAKE_PROFIT_PCT 0.06→0.09; CRYPTO_MIN_ADX 10→15;
    MODERATOR_MAX_TOKENS 700→900
  • risk_manager.py: Kelly activation 30→15 trades; losing streak clamp
    trigger 3→5 consecutive losses
  • .env: CRYPTO_POSITION_SIZE_USD 50→100 (fee R:R fix: 1.14:1→1.86:1);
    removed stale DEBATE_MAX_TOKENS=400 and EXIT_REVIEW_MAX_TOKENS=800
    (both were silently ignored — config hardcodes those values)
  • dashboard/app.py: Current Brain expander added (AI config, risk rules,
    signal config, live Kelly stats); now shows actual runtime values
    (scan interval, max trades, full 20-pair list)

## Project Structure

```
algo_trading_final/
├── CLAUDE.md                     ← You are here (keep current)
├── CHANGELOG.md                  ← Append entry every time you change anything
├── main.py                       ← Entry point: python3 main.py
├── config.py                     ← All constants (reads .env)
├── setup.py                      ← Run once: python3 setup.py
├── run_backtest.py               ← python3 run_backtest.py --strategy crypto
├── requirements.txt
├── .env                          ← NEVER commit this
├── .env.example                  ← Template
│
├── scripts/                      ← Ops & automation (all new in v3.1)
│   ├── install_services.sh       ← One-command launchd setup (run once)
│   ├── start_bot.sh              ← Wrapper for launchd (paper mode only)
│   ├── backup_db.sh              ← Daily SQLite + CSV backup → ~/.algo_backup/db/
│   ├── backup_credentials.sh     ← Backs up .env → ~/.algo_backup/credentials/
│   ├── check_readiness.py        ← Paper→live readiness checker + email alert
│   ├── validate.py               ← Pre-flight validator (env, config, imports, DB)
│   ├── install_hooks.sh          ← Install git pre-commit / post-commit hooks (run once)
│   ├── tradingview_webhook.py    ← HTTP server for TradingView Pine Script alerts
│   ├── tradingview_pine.pine     ← Pine Script v5 template — mirrors bot signal gates
│   ├── log_change.sh             ← Prepend entry to CHANGELOG.md
│   ├── com.algotrading.king.plist      ← launchd: auto-start + crash restart
│   ├── com.algotrading.backup.plist    ← launchd: daily backup at 2:00 AM
│   ├── com.algotrading.readiness.plist ← launchd: readiness check at 7:00 AM
│   ├── com.algotrading.brain.plist     ← launchd: daily brain summary at 9:47 PM
│   ├── generate_daily_summary.py ← Auto-generates brain/06_daily_summaries/YYYY-MM-DD.md from DB
│   ├── seed_intelligence.py      ← One-time Bayesian prior seeding from 90-day backtests
│   ├── auto_env_updater.py       ← Runs every 6h via launchd; auto-raises ML gate + position size on milestones
│   └── com.algotrading.autoenv.plist ← launchd: auto_env_updater at 0/6/12/18h
│
├── data/
│   ├── auto_screener.py          ← Finviz + Yahoo + SEC discovery
│   ├── market_data.py            ← yfinance, market hours
│   ├── coinbase_feed.py          ← Coinbase WebSocket + REST
│   ├── indicators.py             ← MACD×4, RSI, VWAP, KST, ATR, ADX, HA + 7 v4.3 indicators
│   ├── price_archive.py          ← SQLite candle flywheel (logs/price_archive.db); backtest reads here first
│   ├── news_feed.py              ← CryptoPanic API + RSS fallback; sentiment scoring; 10-min cache (v5.2)
│   ├── macro_feed.py             ← Cross-asset: DXY/SPY/GLD/VIX via yfinance; Coinglass funding rates; RISK_ON/OFF (v5.2)
│   └── market_context.py         ← Unified context: session + news + macro; no_trade_flags; get_context_for_debate() (v5.2)
│
├── strategies/
│   ├── base_strategy.py          ← Signal dataclass + abstract base
│   ├── equity_momentum.py        ← KST+MACD+VWAP fallback (no API key)
│   ├── crypto_macd.py            ← 3-variant MACD fallback (no API key)
│   ├── crypto_mean_reversion.py  ← Mean-reversion for ranging/volatile regimes (v3.4)
│   ├── futures_scalper.py        ← MES opening range breakout
│   └── ai_agents/
│       ├── analyst_agents.py     ← 3 agents (v8.0): funding_regime, momentum_structure, risk_economics
│       ├── debate_engine.py      ← Full (8-agent) + quick (3-agent) debate
│       ├── exit_review.py        ← Extended thinking exit decisions
│       ├── risk_synthesizer.py   ← Final go/no-go with hard rules
│       └── regime_detector.py    ← Market regime (trending/ranging/volatile)
│
├── learning/                     ← v5.0 Self-improving intelligence layer
│   ├── __init__.py
│   ├── signal_performance.py     ← Bayesian signal stats (4 tables: trade_attribution, signal_stats, agent_stats, backtest_results)
│   ├── post_trade_analyzer.py    ← Why-this-trade-worked/failed engine (called on every close)
│   ├── dynamic_weights.py        ← Live conviction weights (5-min cache, invalidates on close)
│   ├── intelligence_bridge.py    ← Backtest → signal_stats pipeline (same table as live)
│   ├── ml_signal.py              ← LightGBM gate: P(win) from 90d rolling trade_attribution; retrains every 50 closes
│   ├── pm_calibrator.py          ← Lane 3: Platt scaling for LLM probability estimates (v9.0 Sprint 2)
│   └── tax_tracker.py            ← Tax lot tracking: Section 1256 futures, short/long-term, YTD liability, harvesting (v5.2)
│
├── memory/
│   └── trade_memory.py           ← LanceDB vector store (supplemental qualitative context)
│
├── risk/                         ← v9.0: decomposed from 1 file to 6
│   ├── risk_manager.py           ← Thin orchestrator; public API unchanged
│   ├── position_sizer.py         ← 25%-Kelly, losing-streak clamp
│   ├── stop_loss_manager.py      ← calc_stop_loss/take_profit/should_exit
│   ├── drawdown_controller.py    ← Daily loss + fee drag gates
│   ├── risk_limits.py            ← Market hours, position limits, deployment cap
│   └── var_calculator.py         ← Historical VaR 95/99% (new capability)
│
├── execution/
│   ├── alpaca_broker.py          ← Stocks (Alpaca paper API)
│   ├── coinbase_broker.py        ← Crypto (Coinbase Advanced Trade)
│   ├── binance_broker.py         ← Perp futures (Binance USD-M; replaced Bybit v9.0)
│   ├── tradovate_broker.py       ← MES futures
│   ├── prediction_market_base.py ← Abstract interface for prediction market brokers (v9.0 Sprint 2)
│   ├── polymarket_broker.py      ← Lane 3: Polymarket CLOB paper/live (v9.0 Sprint 2)
│   └── kalshi_broker.py          ← Lane 3: Kalshi REST paper/live (v9.0 Sprint 2)
│
├── backtesting/
│   ├── backtest_engine.py        ← run_with_intelligence() — full pipeline: run→validate→archive
│   └── strategy_validator.py     ← Gate: win_rate≥45%, Sharpe≥0.5, max_dd≤20%, min_trades≥20
│
├── logging_db/
│   └── trade_logger.py           ← SQLite trades.db (WAL mode) + CSV + positions
│
├── alerts/
│   ├── telegram_alert.py         ← SQLite-only notifier (original, still used by job_runner)
│   └── alert_dispatcher.py       ← Telegram Bot API + SQLite dual-channel (v9.0 Sprint 2)
│
├── dashboard/
│   └── app.py                    ← 4-view dashboard: TheKing/Saiyan/FilmRoom/Ring
│
├── mcp_server/                   ← MCP server (v9.0 Sprint 1)
│   └── server.py                 ← 15 FastMCP tools; start: python3 mcp_server/server.py
│
├── tests/                        ← pytest test suite (v9.0 Sprint 1)
│   ├── test_indicators.py        ← look-ahead bias + edge cases
│   ├── test_risk_manager.py      ← halt rules, position limits, stop math
│   └── test_broker_paper.py      ← Coinbase/Alpaca/Binance paper smoke tests
│
├── data/                         ← (new Sprint 2 additions)
│   ├── polymarket_feed.py        ← Lane 3: Gamma REST scanner, market classification
│   ├── kalshi_feed.py            ← Lane 3: Kalshi REST feed
│   └── whale_tracker.py          ← Lane 3: smart money signal via CLOB trade history
│
└── scheduler/
    ├── job_runner.py             ← Thin orchestrator (v9.0: 258L, was 1812L)
    ├── lane3_scanner.py          ← Lane 3: 15-min predict market scan (v9.0 Sprint 2)
    ├── _helpers.py               ← Shared state: flags, helper fns, strategy singletons
    ├── exit_monitor.py           ← AI exit management + attribution
    ├── equity_scanner.py         ← Equity discovery → debate → execute
    ├── crypto_scanner.py         ← 8-signal gate → ML → debate → execute
    └── perp_scanner.py           ← Binance perp entry/exit
```

## The 3 AI Analyst Agents (v8.0 — replaced 9-agent panel)

3 calls per decision (was up to 11). 2/3 BUY = BUY. No moderator. No Goku.

| Key | Name | DBZ Name | Domain |
|-----|------|----------|--------|
| `funding_regime` | Macro & Funding Intelligence | Bardock | Funding rate (>0.05%/8h = overheated → HOLD), OI trend, macro score, VIX, DXY, SPY |
| `momentum_structure` | Technical Momentum & Structure | Vegeta | ADX, BB-Keltner squeeze, WAE explosion, WaveTrend cross, SuperTrend, MACD consensus. Requires ≥2 aligned signals |
| `risk_economics` | Trade Economics & Risk | Krillin | Fee math (ATR/price ≥ 0.4%), volume gate, time-of-day, ATR-based stop sizing. Hard kill switch |

Decision: **2/3 agents vote BUY = BUY** at average confidence. Otherwise HOLD.
Cost: ~$0.02/debate (was ~$0.08). Latency: ~15s (was ~60s).

**AI Session Analyst** (`strategies/ai_agents/session_analyst.py`): fires ONCE at each session open. Sets conviction_threshold_multiplier (0.7–1.5×) and session_bias.

## Exit Review Agents (Extended Thinking)
- Tudor Jones: "Is the stop still valid?"
- Soros: "Is the thesis still intact?"
- Simons: "Is the statistical pattern still holding?"
Any ONE saying EXIT → we exit. Asymmetric on purpose.

## v5.0 Learning Layer Architecture

### How the System Learns

Every closed trade triggers `analyze_closed_trade()` in `learning/post_trade_analyzer.py`, which:
1. Extracts which signals were active at entry
2. Computes net P&L (gross - fees)
3. Generates a structured lesson ("why this worked/failed")
4. Calls `record_trade_attribution()` → updates `signal_stats` table
5. Calls `record_agent_votes()` → updates `agent_stats` table
6. Calls `invalidate_cache()` → forces fresh weight load next conviction score

### Bayesian Weight Formula
```
posterior_wr = (PRIOR_N * prior_p + N * obs_win_rate) / (PRIOR_N + N)
bayesian_pts = prior_pts * (posterior_wr / prior_p)
```
- `PRIOR_N = 20` phantom trades (confidence in prior)
- `MIN_FIRES_TO_LEARN = 10` (below this, use hardcoded prior)
- Cap: 2.5× the original prior points
- Per signal × regime (trending/ranging/volatile)

### Price Archive Flywheel
Every live candle fetch writes to `logs/price_archive.db` (WAL mode, separate from trades.db).
Backtests check archive first (70% coverage threshold). If coverage OK, zero API calls.
If not, fetches from Coinbase API → yfinance fallback → archives result immediately.

### Seeding Intelligence (run once before live trading)
```bash
python3 scripts/seed_intelligence.py --days 90 --validate
```
Fetches 90 days of BTC/ETH/SOL/etc., runs full backtests, attributes 1000s of simulated trades
into `signal_stats`, pre-populates Bayesian priors so Day 1 is evidence-backed, not guesswork.

### SQLite Tables Added in v5.0
| Table | Purpose |
|-------|---------|
| `trade_attribution` | One row per signal per closed trade; links signal→regime→won→pnl |
| `signal_stats` | Running Bayesian stats per signal/regime; source=live or backtest |
| `agent_stats` | Per-agent vote accuracy; injected into debate prompts |
| `backtest_results` | Archived strategy validation runs with param hash |

(All in `logs/trades.db`)

## The Amygdala Removal Rules (HARDCODED — NO OVERRIDE)
1. Never chase — skip if price moved >3% since signal
2. Never average down — one position per symbol, ever
3. Stop losses are sacred — never moved wider after entry
4. Wins don't justify ignoring rules on the next trade
5. Losses don't justify revenge trading or larger size
6. FOMO is not a signal
7. When in doubt, HOLD — a skipped trade costs nothing
8. The goal is being in business next month, not winning today

## Risk Rules (v3.6 current values)
- **1%** max account risk per trade [was 2%, cut 50%]
- **4%** max daily loss → halt ALL trading [was 8%, cut 50%]
- **90%** max deployed capital
- 3 equity trades/day max (PDT cash account)
- **5** max crypto positions, **3** max equity positions [was 10/5, cut 50%]
- No equity entries 9:30–10:00 ET
- Stop loss set immediately after every fill
- Crypto stop: **1.5%** | take profit: **4.5%** [was 3%/9%] — maintains 3:1 R:R
- Equity stop: **2.5%** | take profit: **7.5%** [was 5%/15%] — maintains 3:1 R:R
- Position sizes: crypto **$250**, equity **$250** [was $500/$500]
- Fees > **10%** of account/day → halt crypto bot ($500 on $5,000)
- Kelly sizing activates after **15** trades
- Losing streak size clamp (50%) triggers after **5** consecutive losses
- Circuit breaker: **4** consecutive strategy losses → pause [was 8]
- Min agent agreement: **2 agents** explicit (not percentage)
- RSI is EXIT signal only — NOT used as entry gate anywhere

## Key Data Formats

### Signal object
```python
Signal(action='BUY'|'SELL'|'HOLD', symbol='AAPL', strategy='equity_momentum',
       confidence=0.0-1.0, reason='string', price=float,
       suggested_size_usd=float, stop_loss=float, take_profit=float)
```

### Trade log (SQLite trades table)
ts, strategy, broker, symbol, action, order_type, qty, price,
value_usd, fee_usd, pnl_usd, paper, order_id, notes

### Position (risk_manager in-memory + SQLite open_positions table)
symbol, strategy, qty, entry, stop, target, high_since_entry, ts_entry

## Vector Memory Schema (logs/memory/trade_memory.db)
Table: trade_experiences — NumPy cosine similarity, SQLite storage (no LanceDB)
- id: TEXT (uuid)
- ts: TEXT
- symbol: TEXT
- strategy: TEXT
- entry_reason: TEXT
- exit_reason: TEXT
- outcome: REAL (pnl_usd)
- won: INTEGER (0/1)
- rsi_at_entry: REAL
- macd_hist_at_entry: REAL
- adx_at_entry: REAL
- vol_spike_at_entry: REAL
- regime: TEXT
- vector: TEXT (JSON 8-dim feature vector)

Vector layout: [rsi/100, tanh(macd*10), adx/100, min(vol/5,1),
                regime_trending, regime_ranging, regime_volatile, regime_unknown]

## edge_snapshots Table (logs/trades.db)
Tracks sizing inputs per trade for attribution and reporting.
- market: TEXT (crypto|futures|perp)
- symbol: TEXT
- v_score, e_score, d_factor, t_multiplier, k_factor, m_score: REAL (edge factors)
- final_size_usd: REAL
- debate_type: TEXT (agents|rule_based)
- notes: TEXT

## How to Start the System
```bash
cd algo_trading_final
python3 main.py                    # Full system (paper mode if .env says so)
python3 main.py --mode paper       # Force paper
python3 main.py --mode live        # Live (requires typing 'I UNDERSTAND')
python3 main.py --crypto-only      # Skip equity
python3 main.py --equity-only      # Skip crypto
streamlit run dashboard/app.py --server.runOnSave true     # Dashboard on :8501 — auto-reloads on file changes
```

## Notifications (v3.1)
All alerts (trade opened/closed, signals, halts, system events, readiness) are
written to the `system_events` SQLite table with `source='notify'`. The dashboard
**Notifications panel** (bottom of the left column in THE KING view) reads and
displays them in real time. No email. No external service. Works offline.

`alerts/telegram_alert.py` keeps the same public API — nothing else in the
codebase needed to change. `get_recent_notifications()` in `trade_logger.py`
queries `system_events WHERE source='notify'`.

## Auto-Start & Auto-Restart (v3.1)
Set up once, runs forever:
```bash
bash scripts/install_services.sh
```
This registers three launchd services:
- **com.algotrading.king** — starts the bot on login, restarts on crash (paper mode)
- **com.algotrading.backup** — backs up DB + credentials at 2:00 AM daily
- **com.algotrading.readiness** — checks paper→live criteria at 7:00 AM daily

Service logs: `logs/service/`
To uninstall: `bash scripts/install_services.sh --uninstall`

## Database Backup
Backups live at `~/.algo_backup/` (outside the repo, never git-tracked).
- **DB backups:** `~/.algo_backup/db/trades_YYYY-MM-DD.db` (30-day retention)
- **Credential backups:** `~/.algo_backup/credentials/.env.TIMESTAMP` (10-version rotation)

Manual backup:
```bash
bash scripts/backup_db.sh
bash scripts/backup_credentials.sh
```

## SQLite Crash Safety (v3.1)
WAL (Write-Ahead Logging) mode is now enabled on every connection in `trade_logger.py`.
WAL means the database file is never left in a corrupt state even if Python crashes
mid-write. The trade history is safe.

## Paper → Live Readiness Checker (v5.0)
Evaluates 8 criteria before flagging the system as ready for live money:
1. ≥ 21 calendar days of paper trading
2. ≥ 50 completed trades
3. Win rate ≥ 52%
4. Profit factor ≥ 1.4 (gross wins / gross losses)
5. No single day worse than -3.5% of account
6. Zero crashes or halts in the last 7 days
7. Positive total paper P&L
8. Average P&L per trade ≥ $0.10

Fast-track mode (--fast-track, after historical validation): 3 days / 20 trades / WR ≥ 48% / PF ≥ 1.2

Run anytime:
```bash
python3 scripts/check_readiness.py
python3 scripts/check_readiness.py --fast-track
```
Fires an alert automatically the first time all criteria pass in a day.
The daily launchd job runs this at 7:00 AM.

## How to Run Backtests
```bash
python3 run_backtest.py --strategy crypto --symbol BTC-USD --period 6mo
python3 run_backtest.py --strategy equity --symbol AAPL --period 1y
python3 run_backtest.py --strategy crypto --variant sniper --symbol ETH-USD
```

## Git Workflow
The project is version-controlled. Branch = main.
```bash
git log --oneline -10          # Recent commits
git diff                       # What changed
git add -p && git commit -m "Description"
```
After any commit that changes behavior, also update CHANGELOG.md:
```bash
bash scripts/log_change.sh "Brief description"
```

## TradingView Pro Integration (v4.2)

How it works:
1. Pine Script on TradingView fires an alert → POSTs JSON to your webhook
2. Webhook server writes signal to SQLite `system_events` (source='tradingview')
3. `job_runner.py` checks for fresh TV buy signals per symbol during scan
4. If a matching signal arrived within 5 min → +20 conviction pts (configurable)
5. Signal also tagged in `signal_triggers` so AI agents see it during debate

Setup steps:
```bash
# 1. Set secret in .env
TV_WEBHOOK_SECRET=your_random_secret_here

# 2. Start webhook server (separate terminal)
python3 scripts/tradingview_webhook.py

# 3. Expose to internet via ngrok (free tier works)
ngrok http 8765

# 4. In TradingView: Alerts → Create → Webhook URL = https://xxxx.ngrok.io/webhook
#    Add to chart: scripts/tradingview_pine.pine
#    Set Pine Script "Webhook Secret" input to match TV_WEBHOOK_SECRET
```

Symbol mapping (TradingView → Coinbase format built in):
- BTCUSDC / BTCUSD / BTCUSDT → BTC-USDC
- ETHUSDC / ETHUSD / ETHUSDT → ETH-USDC
- (all 8 default pairs covered, unknown formats fall back to BASE-USDC)

## Common Errors and Fixes

**webull login fails** → Check WEBULL_MFA in .env, try re-running setup.py
**Coinbase 401** → API key permissions need "Advanced Trade" scope with View+Trade
**LanceDB import error** → pip install lancedb sentence-transformers
**pandas-ta import error** → pip install pandas-ta==0.3.14b0
**Schedule not running** → Make sure nothing is blocking the while True loop
**Tradovate symbol error** → Update MES_SYMBOL in tradovate_broker.py for current quarter
**launchd not starting** → `launchctl list | grep algotrading` to check status; check logs/service/bot_error.log
**DB backup fails** → Ensure sqlite3 CLI is installed: `sqlite3 --version`
**TV webhook 403** → TV_WEBHOOK_SECRET in .env doesn't match Pine Script "Webhook Secret" input
**TV webhook not receiving** → ngrok must be running (`ngrok http 8765`); free tier URL changes on restart, update TradingView alert each time
**TV signal not boosting conviction** → Check TV_SIGNAL_MAX_AGE_SECONDS (default 300s); signal must match symbol exactly; only 'buy' action triggers boost
**ngrok not installed** → `brew install ngrok` or download from ngrok.com

## MES Contract Symbols (update quarterly)
- Q1 (Jan-Mar): MESH6  ← current code uses MESM6
- Q2 (Apr-Jun): MESM6  ← **ACTIVE** (current front month, June 2026)
- Q3 (Jul-Sep): MESU6
- Q4 (Oct-Dec): MESZ6

To update: change MES_SYMBOL in execution/tradovate_broker.py each quarter rollover.

## Dashboard Views
1. THE KING — Lakers gold/navy, LeBron quotes, championship energy (default)
2. SAIYAN MODE — Dragon Ball Z, power levels, ki energy bars
3. FILM ROOM — Chalk/blackboard, full debate reasoning, no animations
4. RING CEREMONY — Unlocks on milestones, trophy room, championship stats

## LeBron Quotes Used in Dashboard
Morning: "We're in the lab. Let's get to work."
Win: "That's preparation meeting opportunity."
Loss: "Losses are tuition. On to the next."
Halt: "Not today. Live to play tomorrow."
Goal: "We came, we worked, we're done."
Patience: "Sometimes the best move is no move."
New high: "This is what the work looks like."
Motivation 1: "Strive for greatness."
Motivation 2: "I like criticism. It makes you strong."
Motivation 3: "I promise you I will do everything in my power."
Motivation 4: "The best come from somewhere. Remember yours."
Motivation 5: "Nothing is given. Everything is earned."

## Version History
- v1.0: Basic MACD equity + crypto, manual watchlist
- v2.0: AI debate engine, auto-screener, Tradovate futures, LeBron dashboard
- v3.0: Extended thinking exits, LanceDB memory, regime detection,
         prompt caching, structured outputs, 4-view dashboard,
         position persistence, watchdog, auto cost tuning
- v3.1 (2026-03-22): Git version control, WAL crash safety, auto-restart via
         launchd, daily DB + credential backups, paper→live readiness tracker,
         CHANGELOG.md + log_change.sh, notifications written to SQLite +
         displayed in dashboard Notifications panel (no email)
- v5.0 (2026-03-25): True Brain — self-improving intelligence layer:
         Bayesian conviction weights (prior → posterior per signal/regime);
         trade attribution engine (learning/signal_performance.py + post_trade_analyzer.py);
         dynamic weights (learning/dynamic_weights.py, 5-min cache, invalidate on close);
         intelligence bridge (backtest trades feed same signal_stats as live trades);
         price archive (data/price_archive.db — candle flywheel, zero re-fetch);
         strategy validator gate (win_rate≥45%, Sharpe≥0.5, max_dd≤20%, trades≥20);
         seed_intelligence.py (pre-populate Bayesian priors from 90-day backtest data);
         agent accuracy tracking (agent_stats table, accuracy injected into debate context);
         daily brain summaries (generate_daily_summary.py + launchd 9:47 PM, signal leaderboard auto-populated);
         SHORT branch attribution fixed; LanceDB demoted to supplemental context
- v8.0 (2026-03-26): 3-agent debate (Bardock/Vegeta/Krillin), ML signal gate (LightGBM),
         walk-forward OOS validation, funding rate wired into market_data, RBIPMS framework
- v9.0 (2026-03-26): Sprint 1 Foundation — MCP server (15 tools), risk decomposition (5 modules),
         Bybit→Binance migration, job_runner → 6-file decomposition (258L orchestrator),
         4 Claude agents, 5 slash commands, 3 test files, GitHub live

## GitHub
- Repository: `futureisnowtech/trading-bot-main` (private)
- Active branch: `feature/agent-overhaul`
- Push: `git push origin feature/agent-overhaul` (SSH configured, no GitHub Desktop needed)
- Sprint plan: `docs/INTEGRATION_PLAN.md` (Sprints 1+2 done, Sprint 3 = Lane 1 Options)

## Claude's Standing Instructions
When making any change to this project:
1. Update CLAUDE.md if the change affects how the system works
2. Append to CHANGELOG.md: `bash scripts/log_change.sh "Description"`
3. Commit when a logical unit of work is done: `git add -p && git commit`
4. Never commit .env or logs/ — .gitignore already excludes them
5. Always use `python3`, not `python`
