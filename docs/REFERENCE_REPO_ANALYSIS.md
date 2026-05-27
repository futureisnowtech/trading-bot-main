# Reference Repository Analysis
# Generated: 2026-03-26
# Purpose: Foundation document for the algo_trading_final architectural overhaul.
# 13 repos surveyed. moon-dev-ai-agents: PENDING (SSH auth issue at time of analysis).

---

## How to Use This Document

Each section below covers one repo. At the end, a **Cross-Repo Synthesis** table maps
patterns directly to the three lanes of the overhaul:

- **Lane 1** — Stocks / options / Alpaca (US equity)
- **Lane 2** — Crypto futures / Bybit perp (crypto)
- **Lane 3** — Prediction markets / Polymarket + Kalshi

---

## Tier 1 Repos

---

### 1. Claude_Prophet

**GitHub:** https://github.com/JakeNesler/Prophet-Trader
**License:** MIT
**Status note:** Repo README declares itself deprecated in favor of OpenProphet, but
the MCP server and agent definitions remain fully functional and well-documented.

**What it does:**
An aggressive discretionary options trading system built in Go with a Node.js MCP
server layer. Claude Code controls the entire Go backend through 40+ MCP tools
(place orders, fetch options chains, scan news, search past trades via vector
similarity). Designed for PDT-status accounts trading LEAPS + 0-5 DTE scalps +
protective puts simultaneously.

**Directory structure:**
```
cmd/bot/main.go          ← Go entry point
controllers/             ← 5 HTTP handler files (48 functions total)
services/                ← 8 business logic services (63 functions)
database/storage.go      ← SQLite: orders, bars, positions, embeddings, 384-dim vectors
models/models.go         ← 7 DB entity types
interfaces/              ← 80 type definitions: trading.go + options.go
config/config.go         ← env loader
mcp-server.js            ← 1,701-line Node.js MCP server (40+ tools)
.claude/agents/          ← 4 agent definitions: CEO, Strategy, Consultant, Engineer
activity_logs/           ← daily trade journals (JSON)
decisive_actions/        ← per-decision log files
```

**Top 5 most valuable patterns for our project:**

1. **`mcp-server.js` — 40-tool MCP server architecture** (lines 1–1701)
   The single most complete MCP server pattern in this collection. All 40 tools are
   cleanly defined with input schemas and HTTP routing to a backend. Copy this
   architecture wholesale: replace Go backend with our Python backend, keep the
   Node.js MCP layer or rewrite in Python using `mcp` package.
   Key tools: `find_similar_setups`, `store_trade_setup`, `get_trade_stats`,
   `place_managed_position`, `get_options_chain`, `analyze_stocks`.

2. **`.claude/agents/` — multi-agent role separation**
   CEO (portfolio risk), Strategy (setups), Consultant (adversarial), Engineer (code).
   Pattern: each agent has a tight, non-overlapping domain. Directly maps to our
   `funding_regime` / `momentum_structure` / `risk_economics` split and informs
   how to add a Lane 1 options specialist agent.

3. **`services/alpaca_options_data.go` + `interfaces/options.go`**
   Options chain fetching, Greeks extraction, DTE filtering — the complete data model
   for options. Since our Lane 1 needs options, this is the spec to implement in Python
   against the Alpaca options API.

4. **`database/storage.go` — trade_embeddings + trade_vectors tables**
   384-dim embedding storage in SQLite alongside trade metadata. This is cleaner than
   our current LanceDB approach — same concept, zero extra dependency. Pattern to
   adopt for our vector memory migration.

5. **`services/position_manager.go` — automated stop-loss/take-profit monitoring**
   `MonitorPositions()` runs continuously, watching stop and target levels and
   closing managed positions. Tighter and more explicit than our current risk_manager.
   The `db_managed_positions` table structure is worth copying.

**Specific things to copy/adapt:**
- `~/reference_repos/Claude_Prophet/mcp-server.js` → adapt to Python `mcp` package as `mcp_server/server.py`
- `~/reference_repos/Claude_Prophet/interfaces/options.go` → Python dataclass equivalents for options chain
- `~/reference_repos/Claude_Prophet/.claude/agents/` → agent YAML definitions pattern

**Lane relevance:**
- Lane 1 (stocks/options): **HIGH** — options chain tools, Alpaca integration, PDT strategy
- Lane 2 (crypto): LOW
- Lane 3 (prediction markets): LOW

---

### 2. TradingAgents

**GitHub:** https://github.com/TauricResearch/TradingAgents
**License:** Apache 2.0
**Academic paper:** arXiv:2412.20138

**What it does:**
A research-grade multi-agent LLM trading framework built on LangGraph. Mirrors a
real trading firm: Fundamentals Analyst, Sentiment Analyst, News Analyst, Technical
Analyst → Bullish/Bearish Researcher debate → Trader Agent → Risk Management →
Portfolio Manager. Supports GPT/Gemini/Claude/Grok/Ollama interchangeably. The
`TradingAgentsGraph.propagate(symbol, date)` call orchestrates the entire pipeline.

**Directory structure:**
```
tradingagents/
├── graph/trading_graph.py      ← LangGraph orchestration (main entry)
├── default_config.py           ← all tunables: LLM, debate rounds, data vendors
├── agents/                     ← individual agent implementations
├── dataflows/                  ← data_cache + market data fetchers
└── llm_clients/                ← multi-provider LLM abstraction
cli/                            ← interactive CLI with rich TUI
```

**Top 5 most valuable patterns for our project:**

1. **`default_config.py` — multi-LLM provider config pattern**
   `llm_provider`, `deep_think_llm`, `quick_think_llm`, `anthropic_effort`,
   `openai_reasoning_effort`, `google_thinking_level`. This is the right way to
   structure a config that switches between providers. We currently hardcode
   Anthropic everywhere. Adopt this pattern to enable model switching.

2. **LangGraph state-machine orchestration**
   The graph approach (nodes + edges + state) is more maintainable than our current
   giant `while True` in `job_runner.py`. Each agent is a node; edges are conditional.
   Errors in one node don't corrupt global state. Worth migrating job_runner to this
   pattern long-term.

3. **Bullish/Bearish researcher debate structure**
   Two agents explicitly assigned opposing views. Forces genuine adversarial analysis.
   Our current 3-agent debate has no formal adversary role. Adding one explicit
   bear-case agent per decision would improve quality.

4. **Data vendor abstraction layer**
   `data_vendors: {core_stock_apis: "yfinance", ...}` lets you swap yfinance for
   Alpha Vantage at config level. Our data layer is hardcoded to specific providers.
   This abstraction pattern prevents single-provider dependency.

5. **`max_debate_rounds` + `max_risk_discuss_rounds` config**
   Configurable debate depth. We fixed ours at 3 agents × 1 round. Making rounds
   configurable allows cost/quality tradeoff tuning per asset class.

**Specific things to copy/adapt:**
- `~/reference_repos/TradingAgents/tradingagents/default_config.py` → extend our `config.py` with multi-provider LLM keys
- `~/reference_repos/TradingAgents/tradingagents/graph/` → reference for migrating job_runner to LangGraph

**Lane relevance:**
- Lane 1 (stocks/options): **HIGH** — fundamentals + news analyst roles, equity focus
- Lane 2 (crypto): MEDIUM — technical/sentiment analysts apply
- Lane 3 (prediction markets): LOW

---

### 3. Fully-Autonomous-Polymarket-AI-Trading-Bot

**GitHub:** https://github.com/dylanpersonguy/Fully-Autonomous-Polymarket-AI-Trading-Bot
**License:** MIT

**What it does:**
Production-grade autonomous Polymarket trading bot with a 3-model AI ensemble
(GPT-4o 40%, Claude 35%, Gemini 25%), 15-point risk check system, fractional Kelly
sizing with 7 multipliers, whale/smart-money tracking, and a 9-tab Flask dashboard.
The most architecturally complete repo in the set for Lane 3.

**Directory structure:**
```
src/
├── analytics/       ← adaptive_weights.py, calibration_feedback.py, regime_detector.py, smart_entry.py, wallet_scanner.py
├── connectors/      ← polymarket_clob.py, polymarket_gamma.py, ws_feed.py, web_search.py, api_pool.py, rate_limiter.py
├── dashboard/       ← Flask app, 9 tabs, glassmorphism UI
├── engine/          ← trading_loop.py, market_classifier.py, market_filter.py, position_manager.py
├── execution/       ← order_builder.py, order_router.py, fill_tracker.py, cancel_handler.py
├── forecast/        ← llm_forecaster.py, ensemble.py, calibrator.py, feature_builder.py
├── observability/   ← structlog, metrics, alerts (Telegram/Discord/Slack), Sentry
├── policy/          ← risk_limits.py, edge_calc.py, position_sizer.py, drawdown.py, portfolio_risk.py, arbitrage.py
├── research/        ← evidence_extractor.py, query_builder.py, source_fetcher.py
└── storage/         ← SQLite WAL, migrations, cache, audit trail, backup
config.yaml          ← full YAML config (scanning, forecasting, ensemble, risk, drawdown, execution, engine, whale)
```

**Top 5 most valuable patterns for our project:**

1. **`src/forecast/ensemble.py` — multi-model parallel forecasting**
   3 LLMs run in parallel (asyncio), each producing an independent probability.
   Aggregated via trimmed mean / median / weighted average. Adaptive weighting
   tracks per-model Brier scores by category and reweights over time. This is the
   core of Lane 3 — copy this file almost verbatim and substitute "probability"
   for "conviction score."

2. **`src/forecast/calibrator.py` — Platt scaling + historical calibration**
   Pulls extreme forecasts toward 0.50. Auto-retrains after 30+ resolved markets.
   Ensemble spread penalty (model disagreement > 10% adds uncertainty). This
   calibration layer is missing entirely from our system.

3. **`src/policy/risk_limits.py` — 15 independent risk checks as a pipeline**
   Each check is a separate function. All must pass; any failure short-circuits.
   Much cleaner than our monolithic `risk_manager.py`. Adopt this decomposition
   pattern for all three lanes.

4. **`src/analytics/wallet_scanner.py` — whale intelligence pipeline**
   7-phase: seed wallets → fetch markets → scan global trades → per-market scan →
   rank → deep analysis → score & save. Edge boost/penalty integration: whale agrees
   = +8% edge, disagrees = -2%. Direct import into Lane 3.

5. **`src/connectors/polymarket_gamma.py` + `polymarket_clob.py`**
   Complete Polymarket API integration — market discovery, CLOB order routing, WebSocket
   feed. This is the connector layer Lane 3 needs. The CLOB interface pattern
   also applies to Kalshi (different API, same conceptual structure).

**Specific things to copy/adapt:**
- `~/reference_repos/Fully-Autonomous-Polymarket-AI-Trading-Bot/src/forecast/ensemble.py` → adapt for `strategies/ai_agents/ensemble_forecaster.py`
- `~/reference_repos/Fully-Autonomous-Polymarket-AI-Trading-Bot/src/forecast/calibrator.py` → adapt for `learning/forecast_calibrator.py`
- `~/reference_repos/Fully-Autonomous-Polymarket-AI-Trading-Bot/src/connectors/polymarket_gamma.py` → new `data/polymarket_feed.py`
- `~/reference_repos/Fully-Autonomous-Polymarket-AI-Trading-Bot/src/connectors/polymarket_clob.py` → new `execution/polymarket_broker.py`
- `~/reference_repos/Fully-Autonomous-Polymarket-AI-Trading-Bot/src/analytics/wallet_scanner.py` → new `data/whale_tracker.py`
- `~/reference_repos/Fully-Autonomous-Polymarket-AI-Trading-Bot/src/policy/` → refactor `risk/risk_manager.py` using decomposed pattern
- `~/reference_repos/Fully-Autonomous-Polymarket-AI-Trading-Bot/config.yaml` → extend our `config.py` with YAML section structure

**Lane relevance:**
- Lane 1 (stocks/options): LOW
- Lane 2 (crypto): MEDIUM — ensemble, risk decomposition, drawdown heat system
- Lane 3 (prediction markets): **CRITICAL** — nearly complete Lane 3 codebase to adapt

---

### 4. cbt-framework

**GitHub:** https://github.com/Trade-With-Claude/cbt-framework
**License:** MIT

**What it does:**
A Claude Code slash-command framework that provides 21 commands for the full
strategy lifecycle: discovery → research → EDA → build → run → analyze →
optimize → deploy. Supports pandas and Polars/Numba/NumPy dual engines.
One-command install via `npx cbt-framework`. Deploys to Bybit, Binance, Kraken,
Hyperliquid.

**Directory structure:**
```
commands/              ← 21 slash-command definitions
├── cbt-build.md       ← strategy code generation prompt
├── cbt-optimize.md    ← walk-forward parameter sweep
├── cbt-live.md        ← exchange deployment
└── ...
engine/                ← pandas + fast (Polars/Numba) dual engine templates
agents/                ← 4 AI agent definitions
templates/             ← exchange-specific strategy templates (Bybit, Binance, etc.)
references/            ← methodology docs, backtesting standards
```

**Top 4 most valuable patterns for our project:**

1. **Slash-command as documented workflow**
   Each command (`/cbt:build`, `/cbt:optimize`) is a markdown file with a structured
   prompt defining what Claude does step-by-step. This is precisely what we need:
   turn our ad-hoc Claude Code usage into reproducible `/backtest`, `/deploy`,
   `/audit` commands.

2. **`commands/cbt-optimize.md` — walk-forward optimization protocol**
   Parameter sweep → grid search → walk-forward. The walk-forward implementation
   matches our v8.0 `run_walk_forward()` spec. The command file documents the exact
   process in a way that Claude follows reliably.

3. **`templates/` — Bybit exchange deployment templates**
   Ready-made templates for Bybit USDT perpetual deployment. Since our Lane 2 runs
   on Bybit, these templates are directly applicable to standardize our deployment.

4. **`.cbt/state.yaml` + `handoff.md` — session context persistence**
   Between Claude Code sessions, the framework saves state to `.cbt/state.yaml` and
   `handoff.md`. This solves context loss between sessions — a pattern we should
   adopt given our CLAUDE.md already does this manually.

**Specific things to copy/adapt:**
- `~/reference_repos/cbt-framework/commands/` → create `.gemini/commands/` in our project with `backtest.md`, `deploy.md`, `audit.md` etc.
- `~/reference_repos/cbt-framework/templates/` → reference for Bybit deployment template
- Session handoff pattern → extend `brain/` directory with structured handoff files

**Lane relevance:**
- Lane 1 (stocks/options): MEDIUM — optimize and deploy commands apply
- Lane 2 (crypto/Bybit): **HIGH** — Bybit deployment templates, walk-forward
- Lane 3 (prediction markets): LOW

---

### 5. claude-trading-skills

**GitHub:** https://github.com/tradermonty/claude-trading-skills
**License:** MIT (inferred)

**What it does:**
50 Claude Skills covering every major equity analysis domain: sector rotation,
market breadth, technical analysis, institutional flow (13F), options strategy
(17+ strategies), economic calendar, PMCC screener, Druckenmiller framework,
bubble detector, macro regime detection, VCP screener, CANSLIM screener, earnings
analysis, and more. Skills work in both Claude web app and Claude Code. Each skill
bundles prompts + reference docs + optional Python helper scripts.

**Directory structure:**
```
skills/                       ← 50 skill directories
├── backtest-expert/          ← SKILL.md + references/ + scripts/
├── options-strategy-advisor/ ← Black-Scholes, 17+ strategies, Greeks
├── institutional-flow-tracker/ ← 13F SEC filings, smart money accumulation
├── macro-regime-detector/    ← yield curve, credit, concentration, sector rotation
├── us-market-bubble-detector/ ← Minsky/Kindleberger, put/call, VIX, margin debt
├── trader-memory-core/       ← persistent trade journal + lesson extraction
├── edge-pipeline-orchestrator/ ← multi-skill coordination
└── ... (44 more)
skill-packages/               ← pre-built .skill archives
scripts/generate_skill_docs.py
CLAUDE.md                     ← skill authoring standards
```

**Top 5 most valuable patterns for our project:**

1. **`skills/options-strategy-advisor/` — complete options analysis skill**
   Black-Scholes pricing, all Greeks, 17 strategies (iron condor, straddle, covered
   call, PMCC, verticals, etc.), P/L simulation. This is the options intelligence
   layer Lane 1 needs. Import as a skill or extract the prompt + reference files.

2. **`skills/institutional-flow-tracker/` — 13F smart money tracking**
   Tier-weighted (Berkshire 3.5× vs index funds 0×), multi-quarter ownership trends,
   concentration risk. Complements Lane 1 equity analysis significantly.

3. **`skills/macro-regime-detector/` — cross-asset regime detection**
   RSP/SPY concentration, yield curve, credit, size factor, equity-bond relation,
   sector rotation → identifies Concentration / Broadening / Contraction / Inflationary
   / Transitional regimes. Replaces our rudimentary `regime_detector.py` for Lane 1.

4. **`skills/trader-memory-core/` — persistent trade journal skill**
   Lesson extraction after every trade, searchable trade memory, pattern recognition
   across sessions. The prompts in this skill directly augment our `trade_memory.py`
   and `post_trade_analyzer.py` quality.

5. **`CLAUDE.md` — skill authoring standard**
   SKILL.md format (YAML frontmatter + body), progressive loading (metadata →
   skill body → references → scripts on demand), scripts vs references division.
   This is the standard we should follow when building our own MCP tools / slash
   commands.

**Specific things to copy/adapt:**
- `~/reference_repos/claude-trading-skills/skills/options-strategy-advisor/` → `strategies/ai_agents/options_analyst.py` + reference files
- `~/reference_repos/claude-trading-skills/skills/macro-regime-detector/` → extend `strategies/ai_agents/regime_detector.py`
- `~/reference_repos/claude-trading-skills/skills/trader-memory-core/` → improve `learning/post_trade_analyzer.py` prompts
- `~/reference_repos/claude-trading-skills/CLAUDE.md` → standard for our own slash command authoring

**Lane relevance:**
- Lane 1 (stocks/options): **CRITICAL** — options advisor, 13F tracker, macro regime, VCP/CANSLIM screeners
- Lane 2 (crypto): LOW
- Lane 3 (prediction markets): LOW — scenario-analyzer skill has some relevance

---

## Tier 2 Repos

---

### 6. CloddsBot

**GitHub:** https://github.com/alsk1992/CloddsBot
**License:** MIT

**What it does:**
TypeScript-based personal AI trading terminal for prediction markets, crypto spot/perp,
token launches, and Bittensor subnet mining. Integrates 21 messaging platforms, 10
prediction markets, 7 futures exchanges (including Solana on-chain via Percolator),
and EVM chains (Base, ETH, Arbitrum via Uniswap V3, 1inch). 119+ trading skills,
whale tracking, arbitrage detection, copy trading, DCA bots — all through natural
conversation. Built in 12 days for Colosseum hackathon.

**Directory structure:**
```
src/
├── risk/           ← circuit-breaker.ts, var.ts, volatility.ts, stress.ts, dashboard.ts, engine.ts
├── arbitrage/      ← index.ts (cross-market arbitrage)
├── strategies/     ← 118+ strategy definitions
├── exchanges/      ← 7 futures exchange connectors
├── trading/        ← execution layer
├── portfolio/      ← portfolio management
├── ml-pipeline/    ← ML signal pipeline
├── opportunity/    ← market opportunity scanner
├── skills/         ← 119+ skill definitions
└── ... (100+ more modules)
```

**Top 4 most valuable patterns for our project:**

1. **`src/risk/circuit-breaker.ts` — typed circuit breaker**
   Hard stop on drawdown thresholds, typed in TypeScript but the logic is clean and
   directly portable to Python. More explicit than our current `risk_manager.py`
   losing-streak logic.

2. **`src/risk/var.ts` — Value at Risk calculation**
   VaR at 95%/99% confidence. Our system has no VaR — this is the gap that explains
   why we don't know our true risk exposure. Copy the calculation logic.

3. **Cross-prediction-market architecture**
   Supports Polymarket + Kalshi + others under a single interface. The abstraction
   pattern (one unified market interface, multiple connectors) is exactly what Lane 3
   needs to support both Polymarket and Kalshi without duplicating logic.

4. **`src/arbitrage/index.ts` — cross-market arbitrage detection**
   Identifies mispriced complementary markets across exchanges. Relevant for Lane 3
   where the same real-world event may be traded on both Polymarket and Kalshi at
   different prices.

**Specific things to copy/adapt:**
- `~/reference_repos/CloddsBot/src/risk/var.ts` → translate VaR logic to `risk/var_calculator.py`
- `~/reference_repos/CloddsBot/src/risk/circuit-breaker.ts` → harden `risk/risk_manager.py` circuit breaker
- Prediction market abstraction pattern → `execution/prediction_market_broker.py` base class

**Lane relevance:**
- Lane 1 (stocks/options): LOW
- Lane 2 (crypto): MEDIUM — exchange connectors, risk engine
- Lane 3 (prediction markets): **HIGH** — multi-market support, arbitrage, whale tracking

---

### 7. trading_skills

**GitHub:** https://github.com/staskh/trading_skills
**License:** MIT (inferred)

**What it does:**
Python MCP server exposing 23 trading analysis tools to Claude Desktop/Code/Cursor.
Tools cover: stock quote, price history, fundamentals, technicals, Greeks, options
chains (via yfinance), spread analysis (vertical/IC/straddle/strangle/diagonal),
PMCC scanner, Piotroski score, correlation matrix, earnings info, IBKR portfolio
management (account, positions, delta exposure, collar candidates, roll candidates),
and PDF report generation. Python package installable via pip.

**Directory structure:**
```
mcp_server/
├── server.py               ← FastMCP server — 23 tool definitions
└── trading_skills/         ← tool implementations
src/trading_skills/
├── quote.py, history.py, fundamentals.py, technicals.py
├── greeks.py, options.py, spreads.py
├── scanner_bullish.py, scanner_pmcc.py
├── piotroski.py, correlation.py, earnings.py, news.py
├── risk.py, report.py
└── broker/                 ← IBKR account/portfolio/options/roll/collar/delta
```

**Top 4 most valuable patterns for our project:**

1. **`mcp_server/server.py` — FastMCP Python server pattern**
   This is the cleanest Python MCP server example in the collection. Uses
   `from mcp.server.fastmcp import FastMCP`, decorates functions with `@mcp.tool()`.
   No Node.js required. 23 tools in ~200 lines of server code. Copy this pattern
   for our own `mcp_server/server.py`.

2. **`src/trading_skills/spreads.py` — options spread analysis**
   Vertical spreads, iron condors, straddles, strangles, diagonal spreads all
   implemented. This is the options math layer Lane 1 needs.

3. **`src/trading_skills/scanner_pmcc.py` — PMCC viability scanner**
   Poor Man's Covered Call scoring: LEAPS delta, short-strike delta, net debit,
   annual yield calculation. Directly usable for Lane 1.

4. **`src/trading_skills/broker/` — IBKR integration pattern**
   Account summary, portfolio, options chain, find_roll_candidates,
   find_collar_candidates, delta_exposure. Shows how to structure a broker
   integration module with clean separation from analysis tools.

**Specific things to copy/adapt:**
- `~/reference_repos/trading_skills/mcp_server/server.py` → template for `mcp_server/server.py` in our project
- `~/reference_repos/trading_skills/src/trading_skills/spreads.py` → `strategies/options_spreads.py`
- `~/reference_repos/trading_skills/src/trading_skills/scanner_pmcc.py` → `data/options_screener.py`

**Lane relevance:**
- Lane 1 (stocks/options): **HIGH** — MCP server pattern, spreads, PMCC, Greeks
- Lane 2 (crypto): LOW
- Lane 3 (prediction markets): LOW

---

### 8. AI-Trader

**GitHub:** https://github.com/HKUDS/AI-Trader (AI-Traderv2)
**License:** MIT

**What it does:**
A marketplace where AI agents (OpenClaw compatible) publish signals, debate strategies,
and enable copy trading. Supports US stocks, A-shares, crypto, Polymarket, forex,
options, futures. Agents register by reading a SKILL.md and connecting to the platform.
The platform provides signal sharing, copy trading, community discussion, and a
financial events dashboard. Launched 2026-03-21.

**Directory structure:**
```
service/     ← backend API
skills/      ← agent skill definitions (SKILL.md format)
assets/      ← logos
```

**Top 3 most valuable patterns for our project:**

1. **SKILL.md self-registration pattern**
   Agents discover and register capabilities by reading a SKILL.md file from a URL.
   `Read https://ai4trade.ai/skill/ai4trade and register.` This is a powerful
   pattern for making our agents discoverable by external platforms.

2. **Copy trading signal publishing**
   Our system generates high-quality AI-debated signals. Publishing them to a
   marketplace like this would create a feedback loop and community validation
   layer. Worth evaluating once Lane 1-3 are live.

3. **Polymarket paper trading support**
   Polymarket paper trading with simulated fills + auto-settlement of resolved
   markets. Useful reference for building our Lane 3 paper mode.

**Lane relevance:**
- Lane 1: LOW
- Lane 2: LOW
- Lane 3 (prediction markets): MEDIUM — Polymarket paper trading, signal marketplace

---

### 9. algorithmic-trading-bot (QuantumSentiment)

**GitHub:** (private/local clone)
**License:** MIT

**What it does:**
Production-grade ML ensemble trading bot refactored from a prototype. Uses XGBoost
(primary), LSTM, CNN, and ensemble for price direction prediction. Integrates Reddit
+ news sentiment, Alpaca broker, PostgreSQL or SQLite storage, comprehensive risk
management (stop-loss manager, drawdown controller, position sizer, correlation
manager). CLAUDE.md included.

**Directory structure:**
```
src/
├── backtesting/    ← backtesting engine
├── broker/         ← Alpaca integration
├── data/           ← data fetching and management
├── features/       ← feature engineering
├── models/         ← base/, xgboost/, lstm/, cnn/, transformers/, ensemble/
├── portfolio/      ← portfolio optimization
├── risk/           ← risk_engine.py, stop_loss_manager.py, drawdown_controller.py, position_sizer.py, correlation_manager.py
├── sentiment/      ← Reddit + news sentiment analysis
└── training/       ← model training pipeline
config/             ← config.yaml, config_small_data.yaml, download_config.yaml
```

**Top 4 most valuable patterns for our project:**

1. **`src/risk/` — decomposed risk management**
   5 separate files each owning one risk domain: `risk_engine.py` (orchestrator),
   `stop_loss_manager.py`, `drawdown_controller.py`, `position_sizer.py`,
   `correlation_manager.py`. This decomposition is the right pattern vs. our
   monolithic 527-line `risk_manager.py`.

2. **`src/models/ensemble/` — ML model ensemble**
   XGBoost + LSTM + CNN combined. More sophisticated than our single
   LightGBM `ml_signal.py`. The ensemble approach increases robustness.

3. **`src/features/feature_engineering.py`**
   Feature pipeline that transforms raw OHLCV + indicators into ML-ready
   feature vectors. Our `ml_signal.py` uses only 19 signal flags — expanding
   with engineered features would improve model quality.

4. **`config/config_small_data.yaml` — reduced-dataset testing config**
   A secondary config for testing on limited data without changing production
   config. Simple pattern we should adopt (we currently have no test-mode config).

**Specific things to copy/adapt:**
- `~/reference_repos/algorithmic-trading-bot/src/risk/` → refactor `risk/risk_manager.py` into 5 focused files
- `~/reference_repos/algorithmic-trading-bot/src/models/ensemble/` → upgrade `learning/ml_signal.py` ensemble
- `~/reference_repos/algorithmic-trading-bot/config/` → add `config_test.yaml` to our project

**Lane relevance:**
- Lane 1 (stocks/options): **HIGH** — Alpaca broker, equity ML signals, sentiment
- Lane 2 (crypto): MEDIUM — ML patterns apply
- Lane 3: LOW

---

## Tier 3 Repos

---

### 10. freqtrade

**GitHub:** https://github.com/freqtrade/freqtrade
**License:** GPL 3.0

**What it does:**
Production-grade open-source crypto trading framework. CCXT-based exchange
abstraction supporting 10+ spot exchanges and 6 futures exchanges (including Bybit).
Features: strategy backtesting, hyperparameter optimization (Hyperopt/Optuna),
FreqAI ML module, Telegram/WebUI control, Docker deployment, extensive CI/CD.
~34,000 GitHub stars.

**Top 3 valuable patterns:**

1. **CCXT exchange abstraction**
   One unified interface for Bybit, Binance, Kraken, OKX, Gate.io, etc. Our current
   Bybit broker is bespoke. Using CCXT (the library freqtrade uses) would give us
   any exchange instantly. Lane 2 migration from bespoke `bybit_broker.py` to
   CCXT-based `ccxt_broker.py` is the right long-term move.

2. **FreqAI ML module architecture**
   Separate train/predict cycle, feature pipeline, walk-forward retraining,
   model persistence. Architecturally cleaner than our `ml_signal.py`. Not
   worth copying verbatim (GPL license + Python version constraints), but
   the separation of concerns is worth studying.

3. **`freqtrade.service` systemd unit**
   Production service file for Linux deployment. If we ever move off macOS
   launchd to a Linux VPS, this is the template.

**Lane relevance:**
- Lane 2 (crypto/Bybit): **HIGH** — CCXT abstraction, Bybit support
- Lane 1/3: LOW

---

### 11. hummingbot

**GitHub:** https://github.com/hummingbot/hummingbot
**License:** Apache 2.0

**What it does:**
High-frequency trading framework with 140+ exchange connectors, $34B+ trading
volume reported. Specialized in market-making and arbitrage strategies. Supports
DEX connectors via Gateway middleware. Python-based with Cython for performance.

**Top 2 valuable patterns:**

1. **Exchange connector architecture**
   Each exchange is a separate connector class inheriting from a base class.
   Connection, authentication, order placement, cancel, status are all standardized.
   The base connector interface is the architecture our `execution/` directory
   should adopt.

2. **Gateway DEX middleware**
   Standardized AMM/DEX interaction layer (Uniswap, etc.). Not directly relevant
   now, but the pattern is useful if Lane 3 expands to on-chain prediction markets.

**Lane relevance:**
- Lane 2 (crypto): MEDIUM — connector architecture reference
- Lane 1/3: LOW

---

### 12. jesse

**GitHub:** https://github.com/jesse-ai/jesse
**License:** MIT

**What it does:**
Clean Python crypto trading framework with simple strategy syntax, 300+ indicators,
multi-symbol/timeframe support, optimization mode (uses AI), spot/futures/short,
partial fills. Targets simplicity over feature count. Has JesseGPT for strategy help.

**Top 2 valuable patterns:**

1. **Strategy base class pattern**
   `class MyStrategy(Strategy): def should_long(self): ...`
   Extremely clean interface. Our `base_strategy.py` should be this simple for
   defining new strategies.

2. **Optimization mode**
   Genetic algorithm / random search over strategy parameters, automatically
   identifying robust parameter sets. Our walk-forward is manual; Jesse automates
   this. Reference for a future `/optimize` slash command.

**Lane relevance:**
- Lane 2 (crypto): MEDIUM — strategy interface, optimization
- Lane 1/3: LOW

---

### 13. intelligent-trading-bot

**GitHub:** https://github.com/asavinov/intelligent-trading-bot
**License:** MIT

**What it does:**
ML-focused crypto trading bot with strict offline (batch train) / online (stream
predict) separation. Extensible feature engineering via Python functions. Supports
multiple trade frequencies. Sends signals via Telegram. Comprehensive
download → merge → features → labels → train → predict → signals → output pipeline.

**Top 2 valuable patterns:**

1. **Offline/online separation**
   ML models are trained in batch (offline) and loaded for prediction (online).
   Our `ml_signal.py` trains on the fly during trading — this is risky. The
   offline/online split prevents training-time latency from affecting live trades.

2. **Feature label generation pipeline**
   `scripts/labels.py` generates forward-looking labels (future returns above
   threshold = 1, else 0) from historical data. Our `ml_signal.py` uses only
   `won` from `trade_attribution` — using properly engineered labels would
   improve model quality.

**Lane relevance:**
- Lane 2 (crypto): MEDIUM — ML patterns
- Lane 1/3: LOW

---

### Note: moon-dev-ai-agents

**Status: PENDING** — SSH authentication issue at time of analysis prevented cloning.
This repo was listed as a reference. Re-run analysis when SSH access is restored:
`git clone git@github.com:moon-dev-ai-agents/...`

---

## Cross-Repo Synthesis: What to Build and Where

### MCP Server (the most critical missing piece)

| Repo | Pattern to adopt |
|------|-----------------|
| trading_skills | `FastMCP` Python pattern — 23 tools in ~200 lines |
| Claude_Prophet | 40-tool MCP + `.claude/agents/` definitions |
| cbt-framework | Slash-command as markdown workflow documents |

**Action:** Build `mcp_server/server.py` using FastMCP. Start with 15 tools covering our
existing capabilities (get_positions, get_trades, run_backtest, get_signal_stats, etc.).
Add `.gemini/commands/` directory for reproducible workflows.

---

### Lane 1: Stocks / Options / Alpaca

| Capability | Source repo | Files to adapt |
|-----------|-------------|----------------|
| Options chain data | Claude_Prophet | `interfaces/options.go` → Python dataclass |
| Options strategy analysis | claude-trading-skills | `skills/options-strategy-advisor/` |
| PMCC scanner | trading_skills | `scanner_pmcc.py` |
| Spread analysis | trading_skills | `spreads.py` |
| Fundamentals analyst | TradingAgents | `agents/fundamentals_analyst.py` |
| Macro regime | claude-trading-skills | `skills/macro-regime-detector/` |
| 13F institutional flow | claude-trading-skills | `skills/institutional-flow-tracker/` |
| Sentiment (Reddit/news) | algorithmic-trading-bot | `src/sentiment/` |
| ML ensemble | algorithmic-trading-bot | `src/models/ensemble/` |
| Risk decomposition | algorithmic-trading-bot | `src/risk/` |

---

### Lane 2: Crypto Futures / Bybit

| Capability | Source repo | Files to adapt |
|-----------|-------------|----------------|
| CCXT exchange abstraction | freqtrade | Use `ccxt` library directly |
| Bybit deployment | cbt-framework | `templates/bybit/` |
| Walk-forward optimization | cbt-framework | `commands/cbt-optimize.md` |
| Exchange connector base class | hummingbot | connector architecture pattern |
| ML offline/online split | intelligent-trading-bot | train/predict separation |
| VaR calculation | CloddsBot | `src/risk/var.ts` → Python |

---

### Lane 3: Prediction Markets / Polymarket + Kalshi

| Capability | Source repo | Files to adapt |
|-----------|-------------|----------------|
| Polymarket CLOB connector | Fully-Autonomous-Polymarket | `connectors/polymarket_clob.py` |
| Polymarket Gamma scanner | Fully-Autonomous-Polymarket | `connectors/polymarket_gamma.py` |
| Multi-LLM ensemble | Fully-Autonomous-Polymarket | `forecast/ensemble.py` |
| Forecast calibration | Fully-Autonomous-Polymarket | `forecast/calibrator.py` |
| 15-point risk checks | Fully-Autonomous-Polymarket | `policy/risk_limits.py` |
| Whale/smart money | Fully-Autonomous-Polymarket | `analytics/wallet_scanner.py` |
| Cross-market arbitrage | CloddsBot | `src/arbitrage/index.ts` → Python |
| Multi-market abstraction | CloddsBot | base class pattern |

---

## Priority Order for Adoption

1. **MCP server** (`trading_skills` FastMCP pattern) — enables Gemini control
2. **Lane 3 connector** (Polymarket bot connectors) — new revenue source, zero conflict with existing system
3. **Risk decomposition** (algorithmic-trading-bot `src/risk/`) — fixes our monolith
4. **Options layer** (claude-trading-skills + trading_skills) — unblocks Lane 1
5. **Multi-LLM ensemble** (Polymarket bot `forecast/ensemble.py`) — upgrade to 3-provider AI
6. **CCXT migration** (freqtrade pattern) — future-proofs Lane 2

---

*Document generated 2026-03-26. Re-run analysis after moon-dev-ai-agents SSH issue is resolved.*
