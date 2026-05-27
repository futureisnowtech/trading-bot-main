# Project Audit — algo_trading_final
# Generated: 2026-03-26 | Current version: v8.0
# Purpose: Honest architectural baseline before the overhaul. No flattery.

---

## 1. Full Directory Tree With Line Counts

```
algo_trading_final/                                     (project root)
├── main.py                                              147 lines
├── config.py                                            212 lines
├── setup.py                                             103 lines
├── run_backtest.py                                      139 lines
├── requirements.txt                                      ~45 lines
├── GEMINI.md                                          ~500 lines
├── CHANGELOG.md                                         varies
│
├── alerts/
│   └── telegram_alert.py                                108 lines
│
├── backtesting/
│   ├── backtest_engine.py                             1,930 lines
│   └── strategy_validator.py                            243 lines
│
├── dashboard/
│   ├── app.py                                         2,666 lines
│   └── terminal.py                                      498 lines
│
├── data/
│   ├── auto_screener.py                                 281 lines
│   ├── coinbase_feed.py                                 588 lines
│   ├── indicators.py                                    963 lines
│   ├── macro_feed.py                                    275 lines
│   ├── market_context.py                                269 lines
│   ├── market_data.py                                   577 lines
│   ├── news_feed.py                                     257 lines
│   └── price_archive.py                                 240 lines
│
├── execution/
│   ├── alpaca_broker.py                                 353 lines
│   ├── bybit_broker.py                                  519 lines
│   ├── coinbase_broker.py                               407 lines
│   ├── tradovate_broker.py                              378 lines
│   └── webull_broker.py                                  16 lines  ← stub/proxy only
│
├── learning/
│   ├── ai_prescreener.py                                175 lines
│   ├── dynamic_weights.py                               188 lines
│   ├── intelligence_bridge.py                           256 lines
│   ├── live_backtest_validator.py                       186 lines
│   ├── meta_learner.py                                  369 lines
│   ├── ml_signal.py                                     267 lines
│   ├── post_trade_analyzer.py                           265 lines
│   ├── signal_performance.py                            567 lines
│   └── tax_tracker.py                                   394 lines
│
├── logging_db/
│   └── trade_logger.py                                  640 lines
│
├── memory/
│   └── trade_memory.py                                  204 lines
│
├── risk/
│   └── risk_manager.py                                  527 lines
│
├── scheduler/
│   └── job_runner.py                                  1,812 lines
│
├── scripts/
│   ├── auto_env_updater.py                              233 lines
│   ├── boot.py                                           31 lines
│   ├── check_readiness.py                               286 lines
│   ├── generate_daily_summary.py                        636 lines
│   ├── generate_system_html.py                        1,001 lines
│   ├── health_check.py                                  631 lines
│   ├── launcher.py                                       24 lines
│   ├── rapid_validate.py                                522 lines
│   ├── replay_signals.py                                559 lines
│   ├── seed_intelligence.py                             233 lines
│   ├── test_brokers.py                                  351 lines
│   ├── tradingview_webhook.py                           216 lines
│   └── validate.py                                      250 lines
│
├── strategies/
│   ├── base_strategy.py                                  74 lines
│   ├── crypto_macd.py                                   283 lines
│   ├── crypto_mean_reversion.py                         227 lines
│   ├── crypto_perp_strategy.py                          211 lines
│   ├── equity_momentum.py                               253 lines
│   ├── futures_scalper.py                               200 lines
│   └── ai_agents/
│       ├── analyst_agents.py                            269 lines
│       ├── debate_engine.py                             239 lines
│       ├── exit_review.py                               310 lines
│       ├── regime_detector.py                           114 lines
│       ├── risk_synthesizer.py                          235 lines
│       └── session_analyst.py                           388 lines
│
└── brain/                                               docs + strategy notes
    ├── README.md
    ├── brain_constitution.md
    ├── brain_execution_os.md
    └── rbi/                                             5 research docs

TOTAL (Python files only): ~24,796 lines across 67 .py files
(Excluding __init__.py stubs, __pycache__, trading-bot/ subdir)
```

---

## 2. Architecture Analysis Per File

### Core Files

| File | Purpose | Quality (1-10) | Reusability (1-10) | Notes |
|------|---------|-----------------|---------------------|-------|
| `main.py` (147) | Entry point: parse args, start job_runner | 6 | 4 | Thin wrapper. Fine. |
| `config.py` (212) | All constants, reads .env | 7 | 5 | Good single source of truth, but grows by appending — no YAML structure. |
| `setup.py` (103) | One-time DB + dir setup | 5 | 3 | Works but not idempotent; no migration system. |
| `run_backtest.py` (139) | CLI backtest entry point | 6 | 5 | Clean interface. |

### alerts/

| File | Purpose | Quality | Reusability | Notes |
|------|---------|---------|-------------|-------|
| `alerts/telegram_alert.py` (108) | Gmail SMTP notifications written to SQLite | 5 | 3 | Named "telegram" but uses email/SMTP. Misleading name. No multi-channel support. |

### backtesting/

| File | Purpose | Quality | Reusability | Notes |
|------|---------|---------|-------------|-------|
| `backtesting/backtest_engine.py` (1,930) | Full pipeline: run → validate → archive | 7 | 6 | Most complete file in the project. Walk-forward, price archive integration, intelligence bridge. Complex but functional. |
| `backtesting/strategy_validator.py` (243) | Gate: WR≥45%, Sharpe≥0.5, DD≤20%, trades≥20 | 7 | 7 | Clean. Well-defined pass criteria. |

### dashboard/

| File | Purpose | Quality | Reusability | Notes |
|------|---------|---------|-------------|-------|
| `dashboard/app.py` (2,666) | Streamlit 4-view dashboard | 6 | 2 | Largest file. Functional but monolithic. All views in one file = hard to maintain. No tests. |
| `dashboard/terminal.py` (498) | Terminal-mode display | 5 | 3 | Alternate non-Streamlit view. Duplicates logic. |

### data/

| File | Purpose | Quality | Reusability | Notes |
|------|---------|---------|-------------|-------|
| `data/auto_screener.py` (281) | Finviz + Yahoo + SEC equity discovery | 5 | 4 | Equity-only. No crypto discovery. Depends on Finviz scraping (fragile). |
| `data/coinbase_feed.py` (588) | Coinbase WebSocket + REST | 7 | 5 | Solid. 30s watchdog. WebSocket + REST fallback. |
| `data/indicators.py` (963) | 30+ indicators: MACD, RSI, ATR, ADX, HA, SuperTrend, WAE, Fisher, WaveTrend, Laguerre, Ichimoku, Kalman, AVWAP, Squeeze, OU, Kyle | 8 | 7 | Best-quality file in the project. Comprehensive, well-commented. pandas-ta primary + manual fallback. |
| `data/macro_feed.py` (275) | DXY/SPY/GLD/VIX via yfinance + Coinglass funding rates | 6 | 6 | Clean data gathering. 5-min cache. yfinance dependency = rate-limited. |
| `data/market_context.py` (269) | Session detector + news + macro unified context | 7 | 6 | Good orchestration. `should_block_trade()` pre-gate is valuable. |
| `data/market_data.py` (577) | yfinance, market hours, Fear&Greed, Options IV | 5 | 4 | Mixed responsibilities. Market hours logic + data fetching + options data in one file. |
| `data/news_feed.py` (257) | CryptoPanic API + RSS fallback | 6 | 5 | Crypto-only. No equity news source. 10-min cache. |
| `data/price_archive.py` (240) | SQLite candle flywheel | 8 | 8 | Excellent pattern. WAL mode, zero-API backtest reads. Separating price data from trades.db is right. |

### execution/

| File | Purpose | Quality | Reusability | Notes |
|------|---------|---------|-------------|-------|
| `execution/alpaca_broker.py` (353) | Alpaca equity broker | 7 | 7 | Clean. Official SDK. Paper + live. **No options support.** |
| `execution/bybit_broker.py` (519) | Bybit USDT-perp execution | 6 | 5 | Functional but bespoke pybit v5 wrapping. Testnet only by default. |
| `execution/coinbase_broker.py` (407) | Coinbase spot crypto execution | 7 | 6 | Uses official coinbase-advanced-py. Solid. |
| `execution/tradovate_broker.py` (378) | MES futures simulation | 4 | 3 | Paper simulation only. Tradovate API requires paid subscription — no free demo. Real pricing via yfinance but execution is simulated. |
| `execution/webull_broker.py` (16) | **Dead file — proxy stub** | 1 | 1 | 16 lines. Just re-exports AlpacaBroker. Webull API is 403-blocked. Should be removed or clearly labeled as deprecated. |

### learning/

| File | Purpose | Quality | Reusability | Notes |
|------|---------|---------|-------------|-------|
| `learning/ai_prescreener.py` (175) | Batch Gemini Haiku score for all crypto pairs | 7 | 5 | Good cost-saving gate. Fail-open on API error. |
| `learning/dynamic_weights.py` (188) | Live conviction weights with Bayesian + meta-learner | 7 | 6 | 5-min cache, invalidates on close. Clean interface. |
| `learning/intelligence_bridge.py` (256) | Backtest → signal_stats pipeline | 7 | 6 | Closes backtest-to-live gap. Good architecture. |
| `learning/live_backtest_validator.py` (186) | Background 30d rolling backtest every 4h | 6 | 4 | Good concept. Zero extra API calls. Results injected into debate. |
| `learning/meta_learner.py` (369) | Gemini analyzes last 100 trades, recommends signal weight deltas | 7 | 5 | Fires after every 10 trade closes. Meta-learning layer on top of Bayesian. |
| `learning/ml_signal.py` (267) | LightGBM gate: P(win) from 90d rolling trade_attribution | 7 | 6 | Retrains every 50 closes. 19 signal features. Sklearn fallback. Clean. |
| `learning/post_trade_analyzer.py` (265) | Why-this-trade-worked/failed engine | 7 | 7 | Called on every close. Structured lesson generation. |
| `learning/signal_performance.py` (567) | Bayesian signal stats: 4 tables | 8 | 8 | Core of the learning system. Prior → posterior formula solid. |
| `learning/tax_tracker.py` (394) | Section 1256, short/long-term, YTD liability | 6 | 5 | Correct concept. Wired into every trade close. Relevant at >$500 account. |

### logging_db/

| File | Purpose | Quality | Reusability | Notes |
|------|---------|---------|-------------|-------|
| `logging_db/trade_logger.py` (640) | SQLite trades.db WAL mode + all queries | 7 | 6 | Single file does too much: schema creation, writes, reads, notifications, TV signals. Should split into schema.py + writer.py + reader.py. |

### memory/

| File | Purpose | Quality | Reusability | Notes |
|------|---------|---------|-------------|-------|
| `memory/trade_memory.py` (204) | LanceDB vector store for qualitative context | 5 | 4 | Supplemental only since v5.0 demotion. Adds lancedb + sentence-transformers as heavyweight deps for marginal benefit. |

### risk/

| File | Purpose | Quality | Reusability | Notes |
|------|---------|---------|-------------|-------|
| `risk/risk_manager.py` (527) | All hard rules, position tracking, persistence | 6 | 4 | **Monolith.** One class handles: entry checks, stop/target calc, position registration, position closing, Kelly sizing, halt/resume, watchdog, perp tracking. Should be 5-6 files. No VaR. No portfolio-level risk. |

### scheduler/

| File | Purpose | Quality | Reusability | Notes |
|------|---------|---------|-------------|-------|
| `scheduler/job_runner.py` (1,812) | The while-True engine | 5 | 2 | **Second-largest file. God object.** Contains equity scan, crypto scan, futures scan, perp scan, exit monitor, watchdog, session analysis, pre-market, opening range — all in one file. No event-driven architecture. Sequential scanning means later symbols wait for earlier ones. |

### strategies/

| File | Purpose | Quality | Reusability | Notes |
|------|---------|---------|-------------|-------|
| `strategies/base_strategy.py` (74) | Signal dataclass + abstract base | 8 | 9 | Clean minimal interface. |
| `strategies/crypto_macd.py` (283) | 3-variant MACD fallback | 5 | 4 | Fallback path only (no API key). Rarely executed. |
| `strategies/crypto_mean_reversion.py` (227) | Kalman+AVWAP mean reversion | 6 | 5 | Runs in parallel with AI path. |
| `strategies/crypto_perp_strategy.py` (211) | Long/short perp signal | 6 | 5 | 20-bar breakout + RSI + ADX + funding rate + OI. |
| `strategies/equity_momentum.py` (253) | KST+MACD+VWAP equity fallback | 5 | 4 | Fallback path. No AI debate integration for equity. |
| `strategies/futures_scalper.py` (200) | MES opening range breakout | 6 | 5 | Paper simulation only. |
| `strategies/ai_agents/analyst_agents.py` (269) | 3 agents: Bardock, Vegeta, Krillin | 8 | 7 | Clean v8.0 design. Non-overlapping domains. |
| `strategies/ai_agents/debate_engine.py` (239) | Full + quick debate orchestration | 7 | 6 | 2/3 BUY = BUY logic clean. |
| `strategies/ai_agents/exit_review.py` (310) | Extended thinking: Tudor Jones, Soros, Simons | 8 | 7 | Asymmetric exit design (any one EXIT = exit) is intentional and correct. |
| `strategies/ai_agents/regime_detector.py` (114) | Market regime classification | 5 | 5 | Simple. Minimal logic. Could use the macro-regime-detector skill from claude-trading-skills. |
| `strategies/ai_agents/risk_synthesizer.py` (235) | Final go/no-go with hard rules | 7 | 6 | Hard veto rules enforced in code (not just prompts). Good. |
| `strategies/ai_agents/session_analyst.py` (388) | Session-open AI analysis | 7 | 5 | Fires at Asia/London/NY opens. Sets conviction multiplier. |

### scripts/

| File | Purpose | Quality | Reusability | Notes |
|------|---------|---------|-------------|-------|
| `scripts/validate.py` (250) | Pre-flight: env, config, imports, DB | 7 | 6 | Good practice. |
| `scripts/check_readiness.py` (286) | Paper→live 7-criteria checker | 7 | 7 | Solid. Automated daily via launchd. |
| `scripts/seed_intelligence.py` (233) | Pre-populate Bayesian priors from 90d backtests | 7 | 6 | Run once. |
| `scripts/test_brokers.py` (351) | Broker health check | 7 | 7 | Useful ops tool. |
| `scripts/tradingview_webhook.py` (216) | HTTP server for TV Pine Script alerts | 6 | 5 | Requires ngrok. TV_WEBHOOK_SECRET for auth. |
| `scripts/auto_env_updater.py` (233) | Raises ML gate + position size on milestones | 7 | 4 | Smart auto-progression. launchd every 6h. |
| `scripts/generate_daily_summary.py` (636) | Brain/daily_summaries from DB | 6 | 4 | Useful but fragile if DB schema changes. |
| `scripts/health_check.py` (631) | System health scoring | 6 | 5 | Comprehensive checks. |
| `scripts/rapid_validate.py` (522) | Fast pre-run check | 6 | 5 | Good ops practice. |
| `scripts/replay_signals.py` (559) | Signal replay for debugging | 7 | 6 | Good debugging tool. |
| `scripts/generate_system_html.py` (1,001) | Static HTML system overview | 4 | 2 | 1,001 lines generating an HTML page. Low value relative to complexity. |

---

## 3. Capabilities Inventory

| # | Category | Status | Score | Details |
|---|----------|--------|-------|---------|
| 1 | **Market data ingestion** | Partial | 6/10 | Coinbase WebSocket (crypto) solid. yfinance for equities (rate-limited, delayed). No futures real-time feed. No equity Level 2. Fear&Greed via Alternative.me. |
| 2 | **Technical indicators** | Strong | 8/10 | 30+ indicators in `indicators.py`: MACD×4, RSI, ATR, ADX, VWAP, AVWAP, Kalman, Squeeze, SuperTrend, WAE, Fisher, WaveTrend, Laguerre, Ichimoku, OU, Kyle lambda, Heikin-Ashi. pandas-ta primary. |
| 3 | **ML/AI analysis** | Good | 7/10 | LightGBM P(win) gate, Bayesian conviction weights, meta-learner, AI prescreener, 3-agent debate + exit review. Missing: multi-LLM ensemble, forecast calibration, no offline/online ML split. |
| 4 | **Order execution** | Partial | 5/10 | Coinbase spot: live. Alpaca equity: live (paper confirmed, live keys needed). Bybit perp: testnet only. Tradovate: simulated. Webull: dead (proxy stub). No options execution anywhere. |
| 5 | **Risk management** | Good | 7/10 | Hard stop rules, Kelly sizing, daily loss halt, fee drag halt, circuit breaker, position limits, ATR-based stops. Missing: VaR, portfolio-level correlation risk, drawdown heat system. Monolithic file is a maintenance risk. |
| 6 | **Backtesting** | Good | 7/10 | Walk-forward (2-fold), OOS validation, price archive flywheel, strategy validator gate, intelligence bridge. Missing: realistic slippage modeling, survivorship bias handling. |
| 7 | **Paper trading** | Strong | 8/10 | All strategies paper by default. Readiness checker with 7 criteria. Tradovate paper uses real ES prices. |
| 8 | **Live trading** | Partial | 5/10 | Coinbase spot live. Alpaca equity live (awaiting keys). Bybit testnet (not live yet). Tradovate simulated only. No options live execution. |
| 9 | **Position management** | Good | 7/10 | In-memory + SQLite persistence. Stop-loss, take-profit, trailing high-water mark. Stagnant trade killer (45min timeout). Perp flat-exit (4h). |
| 10 | **Logging/monitoring** | Good | 7/10 | SQLite WAL trades.db + price_archive.db. CSV export. Notifications panel in dashboard. Daily summaries. Health check script. Readiness tracker. Watchdog. Missing: structured logging (no structlog), no Sentry. |
| 11 | **Multi-agent orchestration** | Strong | 8/10 | 3 debate agents (Bardock/Vegeta/Krillin), 3 exit agents (Tudor Jones/Soros/Simons), session analyst, AI prescreener, meta-learner. v8.0 is well-designed. |
| 12 | **MCP server / tools** | MISSING | 0/10 | No MCP server. Claude Code cannot call any trading functions as tools. This is the single biggest architectural gap. |
| 13 | **Vector memory** | Weak | 4/10 | LanceDB + sentence-transformers present but demoted to "supplemental" in v5.0. Heavy dependency (sentence-transformers ~2GB) for low incremental value. Approach from Claude_Prophet (SQLite embeddings) is lighter. |
| 14 | **Sentiment analysis** | Partial | 5/10 | CryptoPanic for crypto news (sentiment -1 to +1). No equity-specific sentiment. No Reddit. No social media. No earnings-call NLP. |
| 15 | **Notification system** | Good | 7/10 | All events → SQLite system_events table. Dashboard Notifications panel. TradingView webhook for external signals. Missing: Telegram/Discord/Slack real-time push (alerts file uses Gmail SMTP which is slow and unreliable). |

**Overall capability score: 6.0/10**

---

## 4. Tech Stack Summary

### Languages
- Python 3.14 (macOS-specific — Homebrew/framework install)
- Pine Script v5 (TradingView alerts only)

### Core Data & Analysis
| Package | Purpose |
|---------|---------|
| pandas ≥2.0 | Data manipulation |
| numpy ≥1.24 | Numeric computation |
| yfinance ≥0.2.36 | Equity + macro data |
| pandas-ta 0.4.67b0 | Technical indicators (manual install) |
| requests + beautifulsoup4 | Web scraping (Finviz, news) |

### AI / ML
| Package | Purpose |
|---------|---------|
| anthropic (implicit) | Gemini API — 3 debate agents + exits |
| LightGBM / sklearn | ML signal gate |
| lancedb ≥0.6.0 | Vector memory (supplemental) |
| sentence-transformers ≥2.7.0 | Text embeddings for LanceDB |

### Brokers / Exchanges
| Package | Purpose |
|---------|---------|
| coinbase-advanced-py ≥1.7.0 | Coinbase spot |
| alpaca-py (inferred) | Alpaca equity |
| pybit ≥5.x (inferred) | Bybit perp |
| webull 0.3.15 | Dead — 403 blocked |

### Storage
| Technology | Purpose |
|-----------|---------|
| SQLite (WAL mode) | trades.db — all trade/signal/agent data |
| SQLite (WAL mode) | price_archive.db — candle flywheel |
| CSV | Backup export |

### Dashboard / UI
| Package | Purpose |
|---------|---------|
| streamlit ≥1.32.0 | 4-view dashboard |
| plotly ≥5.18.0 | Charts |

### Infrastructure
| Technology | Purpose |
|-----------|---------|
| macOS launchd | Auto-start + cron jobs |
| schedule + APScheduler | In-process scheduling |
| loguru | Structured logging |
| python-dotenv | .env config |

### What's NOT in the stack (gaps)
- No MCP package (`mcp`, `fastmcp`) — MCP server doesn't exist
- No LangGraph — orchestration is sequential while-True loop
- No CCXT — exchange connectors are bespoke
- No Docker — macOS-only deployment
- No CI/CD — no tests, no GitHub Actions
- No type annotations (mypy would fail)
- No Prometheus / structlog / Sentry — observability is basic
- No async/await — all blocking I/O

---

## 5. Identified Weaknesses and Gaps

This section is brutally honest. It is organized from most critical to least.

---

### CRITICAL (blocks the overhaul)

**GAP 1: No MCP server**
The most impactful missing piece. Zero MCP tools means Gemini CLI cannot call
trading functions. Every interaction is text-based and unreliable. The overhaul
adds Lane 1 (Alpaca options), Lane 2 (Bybit), and Lane 3 (Polymarket/Kalshi) —
all of which require Gemini to call tools programmatically. Without an MCP server,
the overhaul cannot function as designed. Fix first.

**GAP 2: job_runner.py is a 1,812-line god object**
All scanning logic — equity, crypto, futures, perp, exit monitoring, watchdog,
session analysis — lives in one file with 20+ top-level functions. Consequences:
- A bug in crypto scan can break equity scan (shared state)
- No parallel scanning (later symbols wait for earlier ones to complete)
- Impossible to test individual scan types in isolation
- Every new lane (options, prediction markets) makes it bigger
Target architecture: event-driven with separate scan workers per lane, LangGraph
or asyncio task graph, shared state via message queue or Redis.

**GAP 3: Webull is a dead broker with a misleading proxy**
`webull_broker.py` is 16 lines that silently re-export AlpacaBroker. Any code
that imports `WebullBroker` expecting Webull functionality gets Alpaca instead.
The webull 0.3.15 package in requirements.txt is useless. This creates confusion
for anyone reading the code or docs. Remove or clearly deprecate.

**GAP 4: Bybit is testnet-only with no live validation**
`BYBIT_TESTNET=true` in .env. No documented process for testnet → mainnet
promotion. No API keys configured. Lane 2 (crypto futures) is effectively
simulated. Testnet prices do not match mainnet prices during volatile periods.

**GAP 5: Tradovate is fully simulated, not a real broker**
Tradovate requires a paid subscription for API access — there is no free demo tier.
The current implementation uses yfinance for pricing and simulates fills. The system
GEMINI.md says "futures live trading" but no real futures trades are being executed.
Either accept this is simulation-only or replace with a broker that has a real demo
API (e.g., Interactive Brokers paper via ibkr-web-api).

---

### HIGH (degrades performance or reliability)

**GAP 6: No options trading capability**
Lane 1 requires options execution. Currently zero options support anywhere:
no options chain data model, no options execution in Alpaca broker, no Greeks
calculation, no strategy selection (covered call, spread, PMCC). The Alpaca
options API is available but unwired.

**GAP 7: No prediction markets support**
Lane 3 is completely absent. No Polymarket connector, no Kalshi connector,
no ensemble forecasting for binary outcomes, no edge calculation, no calibration.
The Polymarket bot reference repo contains nearly everything needed — but nothing
has been integrated yet.

**GAP 8: risk_manager.py is a monolith with dangerous gaps**
One 527-line class handles entry checks, stop/target calc, position registration,
position closing, Kelly sizing, halt/resume, watchdog, and perp tracking.
Critical gaps:
- No VaR calculation (no idea of portfolio tail risk)
- No correlation-based position sizing (BTC/ETH are correlated; system allows 5 positions as if they were independent)
- No drawdown heat system (sizing is binary: normal or halted, with nothing in between)
- The losing-streak clamp (50% size) triggers at 5 consecutive losses — not until
  substantial damage is done

**GAP 9: Single-LLM architecture**
All AI analysis uses only Gemini (Anthropic). If the Anthropic API has an outage,
all three lanes stop functioning. The Polymarket bot reference shows how to run
GPT-4o + Claude + Gemini in parallel for robustness and ensemble quality.
Multi-LLM also reduces model-specific biases in debate results.

**GAP 10: No forecast calibration**
The AI debate outputs a confidence score (0-1) but there is no system to track
whether that confidence correlates with actual win rates. The Polymarket bot's
Platt scaling + historical calibration would show us "when the agents say 0.8
confidence, they actually win 62% of the time" — and adjust accordingly.
Currently confidence scores are uncalibrated numbers.

**GAP 11: Sequential, blocking I/O architecture**
`job_runner.py` scans symbols sequentially: while scanning BTC, ETH waits.
With 20 crypto pairs + 5 equity + futures + perp, one slow API call can delay
the entire scan cycle. All broker calls, indicator calculations, and Gemini API
calls are synchronous and blocking. A modern async architecture (asyncio +
concurrent.futures) would scan all symbols simultaneously.

---

### MEDIUM (suboptimal but functional)

**GAP 12: No CI/CD pipeline**
No GitHub Actions, no automated tests, no linting, no type checking.
Every commit goes directly to main. A production trading system should have at
minimum: unit tests for indicators.py and risk_manager.py, integration tests
for broker paper mode, mypy type checking, ruff linting. A single broken import
currently kills the bot silently.

**GAP 13: Python 3.14 is macOS-specific and cutting-edge**
Python 3.14 is the latest release (2025). Many trading packages target 3.9-3.12.
The EDEADLK bug mentioned in GEMINI.md is a Python 3.14 .pyc file lock issue on
macOS. Deploying to Linux VPS (the standard for always-on trading) requires testing
on a different Python version. The lock-in to one machine running macOS is fragile.

**GAP 14: pandas-ta install is fragile**
`requirements.txt` comments out pandas-ta and requires manual install.
`pandas-ta==0.4.67b0` is not on PyPI and requires `--pre` flag or direct GitHub
install. Any new environment setup will fail on this dependency. Should either
vendor the relevant indicator code directly or switch to `ta-lib` (more stable)
or `pandas_ta` stable release.

**GAP 15: LanceDB adds 2GB of deps for marginal value**
`sentence-transformers` downloads ~2GB of model weights. LanceDB itself is heavy.
After v5.0 demoted vector memory to "supplemental," the ROI of this dependency
is low. Claude_Prophet shows that 384-dim embeddings stored in SQLite achieve
the same semantic search without the heavyweight deps.

**GAP 16: Alert system uses Gmail SMTP (slow, unreliable)**
`alerts/telegram_alert.py` is misnamed — it uses Gmail SMTP. Email for trading
alerts is wrong: email has delivery delays of seconds to minutes, can land in
spam, requires SMTP setup, and cannot be acknowledged quickly. Should be replaced
with Telegram Bot API (which the package is already named for) or Discord webhooks.

**GAP 17: No test suite**
Zero test files in the entire project. For a system managing real money, the
minimum required tests are:
- `tests/test_indicators.py` — verify indicator math is correct (no look-ahead bias)
- `tests/test_risk_manager.py` — verify hard rules cannot be bypassed
- `tests/test_broker_paper.py` — smoke test each broker's paper mode
- `tests/test_backtest_engine.py` — verify walk-forward logic

**GAP 18: Webull package wastes 0.3.15 install**
`webull>=0.3.15` in requirements.txt installs a package that provides zero
functionality (all calls 403-blocked). Remove from requirements.txt.

---

### LOW (cosmetic / minor)

**GAP 19: dashboard/app.py is 2,666 lines**
All four dashboard views in one file. Streamlit inherently requires a single file,
but the views should be in separate modules imported by app.py. Currently adding
a new dashboard view means editing a 2,666-line file.

**GAP 20: brain/ directory is not machine-readable**
Excellent human documentation but not queryable by code. If an agent needs to
check the current strategy parameters, it must parse markdown. A structured
YAML/JSON state file alongside the markdown would enable programmatic access.

**GAP 21: Tradovate quarterly contract symbol requires manual update**
`MES_SYMBOL` in `tradovate_broker.py` must be manually updated each quarter
(MESM6 → MESU6 → MESZ6 → MESH7). There is a reminder in GEMINI.md but
no automation. Forgetting this rollover would cause silent simulation failures.

---

## 6. Summary Scorecard

| Dimension | Score | Reason |
|-----------|-------|--------|
| Functionality breadth | 6/10 | 3 lanes partially started; no MCP, no options, no pred markets |
| Code quality | 6/10 | Good individual files; architecture has god objects |
| Risk management | 6/10 | Hard rules good; monolith, no VaR, no drawdown heat |
| AI/ML sophistication | 7/10 | Debate + Bayesian + meta-learner is genuinely advanced |
| Observability | 5/10 | SQLite logging good; no structlog, no Sentry, email alerts |
| Deployment readiness | 4/10 | macOS-only, no Docker, no CI/CD, testnet-only |
| Test coverage | 1/10 | Zero tests |
| **Overall** | **5.5/10** | Sophisticated AI layer on a fragile operational foundation |

---

## 7. Recommended Overhaul Sequence

Based on the gaps above and the reference repo analysis:

**Phase 1 — Foundation (unblocks everything)**
1. Build `mcp_server/server.py` using FastMCP (15 tools, trading_skills pattern)
2. Refactor `risk/risk_manager.py` → 5 focused files (algorithmic-trading-bot pattern)
3. Add `tests/` with indicator + risk tests
4. Remove `webull` from requirements.txt, deprecate `execution/webull_broker.py`

**Phase 2 — Lane 3 (fastest new revenue)**
5. Add `data/polymarket_feed.py` (Polymarket Gamma + CLOB connectors)
6. Add `execution/polymarket_broker.py`
7. Add `strategies/ai_agents/ensemble_forecaster.py` (multi-LLM parallel)
8. Add `learning/forecast_calibrator.py` (Platt scaling)
9. Wire into job_runner under a new `run_prediction_market_scan()` function

**Phase 3 — Lane 1 options**
10. Extend `execution/alpaca_broker.py` with options chain + order execution
11. Add `strategies/options_spreads.py` (trading_skills pattern)
12. Add options analyst agent with Greeks and strategy selection

**Phase 4 — Architecture**
13. Migrate `scheduler/job_runner.py` to async task graph (LangGraph or asyncio)
14. Add Docker + docker-compose.yml for cross-platform deployment
15. Add GitHub Actions CI/CD (lint + type check + test on push)
16. Migrate vector memory from LanceDB to SQLite embeddings (Claude_Prophet pattern)

---

*Audit generated 2026-03-26 from codebase state at v8.0.*
*All line counts are exact at time of generation. File quality scores are relative to*
*a production-ready trading system standard, not to open-source hobby projects.*
