# INTEGRATION_PLAN.md
# algo_trading_final — 3-Lane Overhaul Master Roadmap
# Generated: 2026-03-26 | Baseline: v8.0
# Source documents: PROJECT_AUDIT.md + REFERENCE_REPO_ANALYSIS.md

---

## Overview

This document is the master blueprint for expanding the system from a single-lane
crypto-focused bot into a true 3-lane autonomous trading operation:

- **Lane 1 — Stocks & Options**: Alpaca paper → live equity + options income
  strategies (covered calls, PMCCs, spreads). Current state: equity live-capable,
  zero options wiring.
- **Lane 2 — Crypto Futures**: Bybit USDT-perp scalping. Current state: testnet only,
  functional but not mainnet-validated.
- **Lane 3 — Prediction Markets**: Polymarket + Kalshi binary outcome trading.
  Current state: completely absent. Zero code exists.

The overhaul also addresses the 8 critical/high architectural gaps identified in the
audit: no MCP server, god-object job_runner, dead Webull broker, options gap,
prediction markets gap, monolithic risk manager, single-LLM dependency, and no
forecast calibration.

---

## 4A. Component-by-Component Comparison Table

For each capability: current state, best reference pattern, source repo, files to
copy or adapt, implementation effort, and priority.

| # | Capability | My Version (v8.0) | Best Reference | Source Repo | Files to Copy / Adapt | Effort | Priority |
|---|-----------|-------------------|---------------|-------------|----------------------|--------|---------|
| 1 | **MCP Server** | MISSING — zero MCP tools, no programmatic Claude control | `mcp_server/server.py` FastMCP pattern: 23 tools in ~200 lines; `@mcp.tool()` decorator pattern | `trading_skills` (primary); `Claude_Prophet` (40-tool full example) | `~/reference_repos/trading_skills/mcp_server/server.py` → `mcp_server/server.py`; `~/reference_repos/Claude_Prophet/mcp-server.js` as tool-catalog reference | M | **P0** |
| 2 | **Multi-agent debate** | 3-agent debate (Bardock/Vegeta/Krillin), 2/3 BUY = BUY. Quality 8/10. | LangGraph state-machine orchestration with bullish/bearish adversary role | `TradingAgents` | `~/reference_repos/TradingAgents/tradingagents/graph/trading_graph.py` for async graph migration reference | L | P2 |
| 3 | **Risk management** | Monolithic `risk_manager.py` (527 lines, 1 class, 8 domains). No VaR. No drawdown heat. | 5-file decomposition: `risk_engine.py` + `stop_loss_manager.py` + `drawdown_controller.py` + `position_sizer.py` + `correlation_manager.py` | `algorithmic-trading-bot` (primary); `Fully-Autonomous-Polymarket` `policy/` (15-point pipeline) | `~/reference_repos/algorithmic-trading-bot/src/risk/` → `risk/` directory (5 files); `~/reference_repos/Fully-Autonomous-Polymarket-AI-Trading-Bot/src/policy/risk_limits.py` pattern | M | **P0** |
| 4 | **Order execution — equity** | `alpaca_broker.py` (353 lines): paper + live equity. No options. Quality 7/10. | Options chain + execution extension; `broker/` pattern from IBKR integration | `trading_skills` | `~/reference_repos/trading_skills/src/trading_skills/broker/` for extension pattern | M | P1 |
| 5 | **Options trading** | MISSING — no options chain model, no Greeks, no strategy selection, no execution | Full options layer: chain fetch, Greeks, 17 strategies, PMCC scanner, spread analysis, execution via Alpaca options API | `claude-trading-skills` (strategy advisor); `trading_skills` (spreads + PMCC scanner); `Claude_Prophet` (options.go interface spec) | `~/reference_repos/claude-trading-skills/skills/options-strategy-advisor/` → `strategies/ai_agents/options_analyst.py`; `~/reference_repos/trading_skills/src/trading_skills/spreads.py` → `strategies/options_spreads.py`; `~/reference_repos/trading_skills/src/trading_skills/scanner_pmcc.py` → `data/options_screener.py` | L | P1 |
| 6 | **Prediction markets** | MISSING — zero Polymarket/Kalshi code anywhere | Complete Polymarket + Kalshi connector + multi-LLM ensemble + calibration + whale tracking + 15-point risk | `Fully-Autonomous-Polymarket` (critical); `CloddsBot` (multi-market abstraction) | `~/reference_repos/Fully-Autonomous-Polymarket-AI-Trading-Bot/src/connectors/` → `data/polymarket_feed.py` + `execution/polymarket_broker.py`; `src/forecast/` → `strategies/ai_agents/ensemble_forecaster.py` + `learning/forecast_calibrator.py`; `src/analytics/wallet_scanner.py` → `data/whale_tracker.py` | L | P1 |
| 7 | **Vector memory** | LanceDB + sentence-transformers (~2GB). Demoted to supplemental in v5.0. Quality 4/10. | SQLite 384-dim embedding columns alongside trade metadata — zero extra deps | `Claude_Prophet` | `~/reference_repos/Claude_Prophet/database/storage.go` trade_embeddings table pattern → replace `memory/trade_memory.py` | S | P2 |
| 8 | **ML / AI layer** | LightGBM gate (19 signal features), Bayesian weights, meta-learner. Quality 7/10. Single LLM (Claude only). | Multi-LLM ensemble (Claude 35% + GPT-4o 40% + Gemini 25%) with per-model Brier score reweighting; offline/online ML split | `Fully-Autonomous-Polymarket` (multi-LLM); `algorithmic-trading-bot` (XGBoost+LSTM+CNN ensemble); `intelligent-trading-bot` (offline/online split) | `~/reference_repos/Fully-Autonomous-Polymarket-AI-Trading-Bot/src/forecast/ensemble.py` → `strategies/ai_agents/ensemble_forecaster.py`; `~/reference_repos/algorithmic-trading-bot/src/models/ensemble/` → upgrade `learning/ml_signal.py` | L | P2 |
| 9 | **Backtesting** | Walk-forward 2-fold, price archive, strategy validator, OOS spec. Quality 7/10. | Walk-forward + optimization sweep + slash-command workflow | `cbt-framework` | `~/reference_repos/cbt-framework/commands/cbt-optimize.md` → `.claude/commands/optimize.md` | S | P2 |
| 10 | **Data feeds** | Coinbase WebSocket (solid), yfinance (rate-limited), CryptoPanic (crypto only), Coinglass. No equity L2. | Data vendor abstraction layer — swap providers at config level | `TradingAgents` | `~/reference_repos/TradingAgents/tradingagents/default_config.py` data vendor pattern → extend `config.py` | S | P2 |
| 11 | **Sentiment** | CryptoPanic (crypto only, -1 to +1). No Reddit. No equity news NLP. No earnings. | Reddit + news dual sentiment pipeline; earnings-call NLP | `algorithmic-trading-bot` | `~/reference_repos/algorithmic-trading-bot/src/sentiment/` → `data/equity_sentiment.py` | M | P1 |
| 12 | **Notifications** | SQLite system_events table + dashboard panel. Gmail SMTP (slow/unreliable). Misnamed as telegram_alert.py. | Telegram Bot API + Discord webhook + Slack; structlog + Sentry observability | `Fully-Autonomous-Polymarket` `src/observability/` | `~/reference_repos/Fully-Autonomous-Polymarket-AI-Trading-Bot/src/observability/` pattern → replace `alerts/telegram_alert.py` | S | P1 |
| 13 | **CI/CD** | MISSING — zero tests, no GitHub Actions, no linting, no type checking | GitHub Actions: lint (ruff) + type check (mypy) + test (pytest) on every push | N/A — build from scratch using GHA standard patterns | New: `.github/workflows/ci.yml`; `tests/` directory | M | P1 |
| 14 | **Crypto futures execution** | `bybit_broker.py` (519 lines, testnet only, pybit v5). Quality 6/10. | CCXT exchange abstraction — one interface, any exchange | `freqtrade` (CCXT usage pattern); `cbt-framework` (Bybit deployment template) | `~/reference_repos/cbt-framework/templates/` Bybit template for deployment; CCXT library directly | M | P1 |
| 15 | **Forecast calibration** | MISSING — AI confidence scores are uncalibrated numbers. "0.8 confidence" has unknown real win rate. | Platt scaling + historical calibration; ensemble spread penalty; auto-retrains after 30 resolved markets | `Fully-Autonomous-Polymarket` | `~/reference_repos/Fully-Autonomous-Polymarket-AI-Trading-Bot/src/forecast/calibrator.py` → `learning/forecast_calibrator.py` | M | P1 |
| 16 | **Agent role definitions** | No `.claude/agents/` directory. No standardized agent specs. | `.claude/agents/` YAML definitions; CEO/Strategy/Consultant/Engineer pattern | `Claude_Prophet` | `~/reference_repos/Claude_Prophet/.claude/agents/` → `.claude/agents/` with our agent roles | S | P1 |
| 17 | **Session context / slash commands** | Manual `brain/` markdown notes. No slash commands. | `.claude/commands/` markdown workflow files; session state YAML persistence | `cbt-framework` | `~/reference_repos/cbt-framework/commands/` → `.claude/commands/` (backtest.md, deploy.md, audit.md, health.md) | S | P2 |
| 18 | **VaR / portfolio risk** | MISSING — no VaR, no correlation-aware sizing, no drawdown heat system | VaR at 95%/99%; drawdown heat (5 levels: normal → half-size → quarter → pause → halt) | `CloddsBot` | `~/reference_repos/CloddsBot/src/risk/var.ts` → translate to `risk/var_calculator.py`; `src/risk/circuit-breaker.ts` → harden circuit breaker | M | P1 |

---

## 4B. REMOVE List

Files to delete from the current project, with justification and replacement.

| File | What It Is | Why Remove | What Replaces It |
|------|-----------|-----------|-----------------|
| `execution/webull_broker.py` (16 lines) | Dead proxy stub — silently re-exports `AlpacaBroker`. Webull API is 403-blocked. | Misleading: any code importing `WebullBroker` silently gets Alpaca instead. Creates confusion in code reviews, debugging, and agent reasoning. No Webull functionality has worked since v3.7. | Nothing. `AlpacaBroker` is the equity broker. Any import of `WebullBroker` should be updated to `AlpacaBroker` directly. |
| `webull>=0.3.15` in `requirements.txt` | Package that installs cleanly but all API calls return 403. | Zero functionality. Installs dead weight. Adds to environment setup time. | Remove the line. No replacement needed. |
| `memory/trade_memory.py` (204 lines) — **conditional** | LanceDB + sentence-transformers vector store. Demoted to "supplemental" in v5.0. | `sentence-transformers` downloads ~2GB of model weights. LanceDB is a heavyweight database engine. The ROI is ~0: the system already has Bayesian weights + meta-learner + ML signal. Replace with SQLite embedding columns (384-dim float arrays) per the Claude_Prophet pattern — same semantic search, zero added deps. | New `memory/trade_memory.py` rewritten to use SQLite `trade_embeddings` table (base64-encoded 384-dim vectors using `numpy` + `struct` — already in our stack). |
| `lancedb>=0.6.0` in `requirements.txt` | LanceDB dependency | Removed with trade_memory.py replacement | Remove line |
| `sentence-transformers>=2.7.0` in `requirements.txt` | 2GB model downloader | Removed with trade_memory.py replacement | Remove line; lightweight embedding can use `numpy` dot-product search on stored vectors |
| `dashboard/terminal.py` (498 lines) — **conditional** | Alternate non-Streamlit terminal display. Duplicates logic from `dashboard/app.py`. | Maintenance burden: any dashboard change must be made twice. Usage is near-zero since the Streamlit dashboard is the default. | Delete and accept that the Streamlit dashboard is the single display. If terminal display is desired, add a `--no-dashboard` flag to `main.py` that logs to stdout. |
| `scripts/generate_system_html.py` (1,001 lines) | 1,001-line script generating a static HTML overview page | Extreme complexity relative to value. Requires updating every time the system structure changes. A static page is always stale. | The Streamlit dashboard + CLAUDE.md serve the same purpose. Delete. |

**Summary of removals:** 2 files deleted outright (webull_broker.py, generate_system_html.py), 1 file replaced/rewritten (trade_memory.py), 1 file conditionally deleted (terminal.py), 3 requirements.txt lines removed (webull, lancedb, sentence-transformers).

---

## 4C. KEEP List

Files that survive the overhaul unchanged or nearly unchanged.

| File | Reusability Score (Audit) | Why It Survives | Expected Changes |
|------|--------------------------|-----------------|-----------------|
| `data/indicators.py` (963 lines) | 7/10 | Best-quality file in the project. 30+ indicators with pandas-ta primary + manual fallback. Used by all three lanes. Well-commented. Walk-forward backtests depend on it. | Add options-specific indicators (IV percentile, put/call ratio) as a new section. No structural change. |
| `data/price_archive.py` (240 lines) | 8/10 | Excellent pattern. WAL mode SQLite candle flywheel with zero-API backtest reads. Separating candle data from trades.db is architecturally correct. Used by walk-forward and live_backtest_validator. | Add a `symbol_type` column (crypto/equity/prediction) so the same archive covers all three lanes. Minor schema migration. |
| `backtesting/backtest_engine.py` (1,930 lines) | 6/10 | Most complete file in the project: walk-forward, OOS validation, price archive integration, intelligence bridge. The walk-forward spec matches the cbt-framework standard. Replacing it would be a major regression. | Extract the 3-lane routing logic into a dispatch function. No structural change to the core engine. |
| `backtesting/strategy_validator.py` (243 lines) | 7/10 | Clean. Well-defined pass criteria (WR≥45%, Sharpe≥0.5, DD≤20%, trades≥20). RBIPMS framework validation gate. Used by backtest_engine. | Add Lane 3 criteria: prediction market calibration score ≥ 0.55, min 15 resolved markets. |
| `learning/signal_performance.py` (567 lines) | 8/10 | Core of the learning system. Bayesian posterior formula (PRIOR_N=20 phantom trades) is sound. 4 tables. Prior → posterior per signal/regime is the most defensible ML pattern in the project. | Add a `lane` column to `trade_attribution` so Lane 1/2/3 data is kept separate for per-lane posterior calculations. |
| `learning/post_trade_analyzer.py` (265 lines) | 7/10 | Called on every trade close. Structured lesson generation. Wires into both `signal_stats` and `agent_stats`. | Upgrade prompts using `claude-trading-skills/skills/trader-memory-core/` patterns for richer lessons. |
| `learning/ml_signal.py` (267 lines) | 6/10 | LightGBM gate with sklearn fallback. 19 signal features. Retrains every 50 closes. Clean interface: `get_ml_signal(market_data) -> (p_win, label)`. | Upgrade ensemble (XGBoost + LSTM + CNN) in Sprint 5. The interface stays identical. |
| `learning/dynamic_weights.py` (188 lines) | 6/10 | 5-min cache, meta-learner delta layer on top of Bayesian weights. Invalidates on trade close. Clean architecture. | No changes needed until Lane 3 adds a new regime type. |
| `learning/meta_learner.py` (369 lines) | 5/10 | Fires after every 10 trade closes. Claude analyzes last 100 trades. Meta-learning layer catches systematic biases Bayesian priors miss. | No changes. |
| `strategies/ai_agents/analyst_agents.py` (269 lines) | 7/10 | Clean v8.0 design. 3 non-overlapping domains. Well-named (Bardock/Vegeta/Krillin). The domain separation pattern is the right one. | Add Lane 1 options analyst agent (Bulma: options economics). Add Lane 3 prediction analyst agent. Keep existing 3 for Lane 2. |
| `strategies/ai_agents/debate_engine.py` (239 lines) | 6/10 | 2/3 BUY = BUY logic is clean. Separates quick and full debate paths. | Parameterize the agent list so Lane 1 and Lane 3 can use different agent sets without forking the engine. |
| `strategies/ai_agents/exit_review.py` (310 lines) | 7/10 | Extended thinking exits. Tudor Jones / Soros / Simons. Asymmetric design (any one EXIT = exit) is intentional and correct. Tax-aware notes injected. | Lane 3 exit review: different agents appropriate for binary outcome resolution (exits are market-settlement not price-based). Add optional override for Lane 3. |
| `strategies/ai_agents/risk_synthesizer.py` (235 lines) | 6/10 | Hard veto rules enforced in code (not just prompts). The distinction between code-enforced and prompt-enforced rules is critical. | Extend to call the decomposed risk pipeline (from Sprint 1 refactor) instead of inline checks. |
| `strategies/ai_agents/session_analyst.py` (388 lines) | 5/10 | Fires once per session open. Sets conviction_threshold_multiplier and session_bias. Session routing logic (Asia/London/NY_OPEN dead zone) is valuable. | Add a Lane 3 session override: prediction markets are global and 24/7 — no dead zones for Lane 3. |
| `strategies/base_strategy.py` (74 lines) | 9/10 | Clean minimal interface. Signal dataclass + abstract base. Quality 8/10. Best reusability score in the strategies directory. | Add `lane: str` field to `Signal` dataclass. |
| `risk/risk_manager.py` (527 lines) | 4/10 | Survives Sprint 1 (too risky to remove before decomposition). | Decomposed in Sprint 1 into 5 files per algorithmic-trading-bot pattern. Becomes an orchestrator only. |
| `data/market_context.py` (269 lines) | 6/10 | `should_block_trade()` pre-gate is valuable. Session + news + macro unified context. | Extend `get_context_for_debate()` to include Lane 3 macro context (election cycle, resolution calendar). |
| `data/coinbase_feed.py` (588 lines) | 5/10 | Solid. 30s watchdog. WebSocket + REST fallback. Used by Lane 2. | No changes in overhaul sprints. CCXT migration is Sprint 4. |
| `logging_db/trade_logger.py` (640 lines) | 6/10 | Single source of truth for all SQLite reads/writes. WAL mode. | Add `lane` column to `trades` table. Schema migration needed. Split into `schema.py` + `writer.py` + `reader.py` in Sprint 5. |
| `scripts/check_readiness.py` (286 lines) | 7/10 | Paper→live 7-criteria checker. Automated daily. | Add per-lane readiness criteria. Lane 3 needs different thresholds (calibration score vs win rate). |
| `scripts/seed_intelligence.py` (233 lines) | 6/10 | Run-once Bayesian prior seeding from 90d backtests. Critical for Lane 2 cold start. | Re-run after ML refactor. Add Lane 3 seed path using historical Polymarket market data. |
| `scripts/test_brokers.py` (351 lines) | 7/10 | Broker health check. Useful ops tool. | Extend to test Polymarket + Kalshi connectors in Sprint 2. |
| `config.py` (212 lines) | 5/10 | Single source of truth for all constants. Reads .env. | Extend with multi-LLM provider keys, Lane 3 constants (POLYMARKET_*, KALSHI_*), VaR thresholds. |
| `alerts/telegram_alert.py` (108 lines) | 3/10 | Survives Sprint 1 with a rename. Replace Gmail SMTP with Telegram Bot API in Sprint 2. | Rename to `alert_dispatcher.py`. Replace SMTP with Telegram Bot API. |

---

## 4D. COPY / ADAPT List

Files from reference repos to bring directly into the project.

---

### MCP Server Foundation

**1. FastMCP Server Template**
- Source: `~/reference_repos/trading_skills/mcp_server/server.py`
- Target: `mcp_server/server.py`
- Adaptation needed: Replace the 23 trading_skills tools with our own tool set (15 tools at minimum: `get_positions`, `get_open_trades`, `get_signal_stats`, `get_agent_accuracy`, `run_backtest`, `get_price_history`, `get_debate_result`, `place_paper_trade`, `close_position`, `get_daily_summary`, `get_readiness_score`, `get_ml_signal`, `scan_crypto_pairs`, `get_macro_context`, `get_notifications`). Keep the `@mcp.tool()` decorator pattern and FastMCP initialization verbatim.
- Dependencies: `pip install mcp` + `pip install fastmcp`. Add both to `requirements.txt`.
- Priority: **P0** — unblocks all Claude Code programmatic control

**2. Claude Agent Role Definitions**
- Source: `~/reference_repos/Claude_Prophet/.claude/agents/` (4 agent definitions: CEO, Strategy, Consultant, Engineer)
- Target: `.claude/agents/` (create directory)
- Adaptation needed: Rename CEO → `portfolio_manager.md` (portfolio risk, halt decisions), Strategy → `trade_strategist.md` (setup evaluation, signal review), Consultant → `devil_advocate.md` (adversarial review), Engineer → `system_engineer.md` (code changes, bot debugging). Update each agent's domain description to reference our system's actual capabilities. Keep the YAML frontmatter format.
- Dependencies: None — purely markdown files consumed by Claude Code
- Priority: P1

**3. Slash-Command Workflows**
- Source: `~/reference_repos/cbt-framework/commands/` (21 slash commands)
- Target: `.claude/commands/` (create directory)
- Adaptation needed: Extract 5 commands relevant to our system: `cbt-build.md` → `build-strategy.md`, `cbt-optimize.md` → `optimize.md`, `cbt-live.md` → `deploy.md`, plus create new `audit.md` (run PROJECT_AUDIT-style analysis) and `health.md` (run scripts/health_check.py and report). Replace cbt-specific references with our file paths.
- Dependencies: `.claude/agents/` must exist first
- Priority: P1

---

### Lane 3 — Prediction Markets

**4. Polymarket Gamma Market Scanner**
- Source: `~/reference_repos/Fully-Autonomous-Polymarket-AI-Trading-Bot/src/connectors/polymarket_gamma.py`
- Target: `data/polymarket_feed.py`
- Adaptation needed: Keep market discovery, filtering by volume/liquidity/category, and market metadata extraction logic. Remove Flask dashboard references. Add `price_archive.py` integration: write resolved market prices to archive for backtesting. Add our `market_context.py` injection pattern for macro context.
- Dependencies: `pip install polymarket-py` or `py-clob-client`; add to `requirements.txt`
- Priority: P1

**5. Polymarket CLOB Order Router**
- Source: `~/reference_repos/Fully-Autonomous-Polymarket-AI-Trading-Bot/src/connectors/polymarket_clob.py`
- Target: `execution/polymarket_broker.py`
- Adaptation needed: Wrap in our `BaseBroker` interface (paper/live toggle like `coinbase_broker.py`). Add `POLYMARKET_PAPER=true` environment variable. Wire order placement to `trade_logger.py` for SQLite logging. Add the same `register_position()` / `close_position()` API our `risk_manager.py` expects.
- Dependencies: `polymarket_feed.py` (item 4); `py-clob-client` package
- Priority: P1

**6. Multi-LLM Ensemble Forecaster**
- Source: `~/reference_repos/Fully-Autonomous-Polymarket-AI-Trading-Bot/src/forecast/ensemble.py`
- Target: `strategies/ai_agents/ensemble_forecaster.py`
- Adaptation needed: Replace GPT-4o 40% / Claude 35% / Gemini 25% hardcoded weights with config-driven weights (`ENSEMBLE_CLAUDE_WEIGHT`, `ENSEMBLE_GPT_WEIGHT`, `ENSEMBLE_GEMINI_WEIGHT` in `config.py`). Replace Brier score history with our `agent_stats` SQLite table. Replace Flask response objects with our `Signal` dataclass. Use our `debate_engine.py` async pattern for the parallel LLM calls.
- Dependencies: `openai` package (GPT-4o); `google-generativeai` package (Gemini); add both to `requirements.txt`; API keys in `.env`
- Priority: P1 (for Lane 3); P2 (for Lanes 1/2 — they work today with Claude alone)

**7. Forecast Calibrator (Platt Scaling)**
- Source: `~/reference_repos/Fully-Autonomous-Polymarket-AI-Trading-Bot/src/forecast/calibrator.py`
- Target: `learning/forecast_calibrator.py`
- Adaptation needed: Replace Flask data models with our SQLite `signal_stats` table. Replace the "resolved markets" trigger (30 markets) with our "closed trades" trigger (30 trades per signal). Add calibration score to `agent_stats` table so debate prompts can show "when Bardock says 0.8 confidence, actual win rate is 0.63." Store calibration curve to SQLite, not JSON file.
- Dependencies: `sklearn.calibration.CalibratedClassifierCV` (already have sklearn)
- Priority: P1

**8. Whale / Smart Money Tracker**
- Source: `~/reference_repos/Fully-Autonomous-Polymarket-AI-Trading-Bot/src/analytics/wallet_scanner.py`
- Target: `data/whale_tracker.py`
- Adaptation needed: Replace Polymarket-specific wallet APIs with the generic CLOB trade history endpoint. Keep the 7-phase pipeline structure (seed → fetch → scan → rank → analyze → score → save). Replace in-memory storage with SQLite `whale_activity` table. Expose `get_whale_signal(market_id) -> float` (edge boost/penalty) matching our conviction scoring API.
- Dependencies: `polymarket_feed.py` (item 4)
- Priority: P2

**9. 15-Point Risk Check Pipeline**
- Source: `~/reference_repos/Fully-Autonomous-Polymarket-AI-Trading-Bot/src/policy/risk_limits.py`
- Target: `risk/risk_limits.py` (new file as part of risk decomposition)
- Adaptation needed: Replace Polymarket-specific position limits with our existing values from `config.py` (MAX_RISK_PER_TRADE=1%, MAX_DAILY_LOSS=4%, etc.). Replace Flask context with direct function calls. Keep the pipeline pattern: each check is an independent function; any failure returns early with a reason string. Wire into `risk_synthesizer.py` as the new `run_risk_checks(signal) -> (bool, str)` function.
- Dependencies: Part of Sprint 1 risk decomposition
- Priority: **P0**

---

### Lane 1 — Options

**10. Options Spread Analysis**
- Source: `~/reference_repos/trading_skills/src/trading_skills/spreads.py`
- Target: `strategies/options_spreads.py`
- Adaptation needed: Replace yfinance options chain calls with Alpaca options API calls (already in `alpaca_broker.py` framework). Keep Black-Scholes math verbatim (it's math, not broker-specific). Add output to our `Signal` dataclass with `strategy='options_spread'`. Add `lane='lane1'` tag.
- Dependencies: Alpaca options API access (requires Alpaca account with options enabled); `alpaca-py>=0.43.2` already in requirements
- Priority: P1

**11. PMCC Viability Scanner**
- Source: `~/reference_repos/trading_skills/src/trading_skills/scanner_pmcc.py`
- Target: `data/options_screener.py`
- Adaptation needed: Replace IBKR data calls with Alpaca options chain API. Keep LEAPS delta check (0.70–0.85), short-strike delta check (0.25–0.35), net debit, and annual yield calculation verbatim. Add results to `data/auto_screener.py` output so the main job_runner sees PMCC candidates alongside equity momentum candidates.
- Dependencies: `strategies/options_spreads.py` (item 10); Alpaca options chain data
- Priority: P1

**12. Options Strategy Advisor Prompts**
- Source: `~/reference_repos/claude-trading-skills/skills/options-strategy-advisor/` (SKILL.md + references/)
- Target: `strategies/ai_agents/options_analyst.py` (new agent: Bulma)
- Adaptation needed: Extract the options strategy selection logic (when to use covered call vs PMCC vs iron condor vs protective put) from the SKILL.md into a Python prompt template. Add as the 4th debate agent for Lane 1 decisions only. Domain: "options economics — IV rank, DTE selection, strike selection, max profit/loss, Greeks". Follows the same agent interface as `analyst_agents.py`.
- Dependencies: `strategies/options_spreads.py` (item 10) for Greeks data
- Priority: P1

**13. Macro Regime Detector (Enhanced)**
- Source: `~/reference_repos/claude-trading-skills/skills/macro-regime-detector/` (SKILL.md + references/)
- Target: Extends `strategies/ai_agents/regime_detector.py`
- Adaptation needed: Extract the regime classification logic (Concentration, Broadening, Contraction, Inflationary, Transitional) and the 6 cross-asset signals (RSP/SPY, yield curve, credit, size factor, equity-bond relation, sector rotation) into our `regime_detector.py`. Replace the current simple trending/ranging/volatile classification for Lane 1 specifically. Keep the crypto-focused regime for Lane 2.
- Dependencies: `data/macro_feed.py` already fetches DXY/SPY/GLD/VIX — add TLT, IWM, HYG as new yfinance pulls
- Priority: P2

**14. Reddit / News Sentiment (Equity)**
- Source: `~/reference_repos/algorithmic-trading-bot/src/sentiment/`
- Target: `data/equity_sentiment.py`
- Adaptation needed: Keep the Reddit PRAW integration and news sentiment scoring. Replace PostgreSQL storage with our SQLite pattern. Expose `get_equity_sentiment(symbol) -> float` matching our `news_feed.py` interface (`-1.0 to +1.0`). Add to `market_context.py` `get_context_for_debate()` for Lane 1 debates only.
- Dependencies: `praw` (Reddit API); `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET` in `.env`
- Priority: P1

---

### Risk Decomposition

**15. Risk Engine Decomposition Pattern**
- Source: `~/reference_repos/algorithmic-trading-bot/src/risk/` (5 files: risk_engine.py, stop_loss_manager.py, drawdown_controller.py, position_sizer.py, correlation_manager.py)
- Target: `risk/` directory — decompose `risk_manager.py` into 5 files:
  - `risk/stop_loss_manager.py` — ATR-based stop/target calculation, stop validation
  - `risk/drawdown_controller.py` — daily loss tracking, halt/resume, heat system (5 levels)
  - `risk/position_sizer.py` — Kelly sizing, streak clamp, size by lane
  - `risk/correlation_manager.py` — BTC/ETH correlation gate, portfolio-level exposure
  - `risk/risk_engine.py` — orchestrator that calls all 4, replaces current `risk_manager.py`
- Adaptation needed: All internal logic stays the same; only the class boundaries change. The public API (`should_enter()`, `register_position()`, `close_position()`) remains identical so `job_runner.py` needs no changes.
- Dependencies: 15-point risk checks (item 9) wired into risk_engine.py
- Priority: **P0**

**16. VaR Calculator**
- Source: `~/reference_repos/CloddsBot/src/risk/var.ts`
- Target: `risk/var_calculator.py`
- Adaptation needed: Translate TypeScript VaR logic to Python. Replace typed TypeScript interfaces with Python dataclasses. Use numpy for the historical simulation (already in stack). Expose `get_var(confidence=0.95) -> float` and `get_var(confidence=0.99) -> float`. Wire result into `risk_engine.py` as an additional halt condition: if 1-day 99% VaR > 4% of account → reduce position sizes by 50%.
- Dependencies: `risk/risk_engine.py` (item 15)
- Priority: P1

**17. Bybit Deployment Template**
- Source: `~/reference_repos/cbt-framework/templates/` (Bybit USDT perp template)
- Target: Reference for standardizing `execution/bybit_broker.py` paper→live promotion checklist
- Adaptation needed: Extract the deployment checklist (API key validation, testnet drain, mainnet balance check, first-order size guard) and convert into a new `scripts/promote_bybit_live.py` script. Not copying code directly — using the checklist as specification.
- Dependencies: Bybit mainnet API keys in `.env`
- Priority: P1

---

## 4E. BUILD NEW List

Components that must be built from scratch, combining patterns from multiple reference repos.

---

**1. Kalshi Connector**
- What it does: Fetches markets from Kalshi REST API, executes binary outcome trades (YES/NO contracts), monitors open positions for resolution, logs fills to `trades.db`. Kalshi uses a REST+WebSocket API that differs from Polymarket's CLOB.
- Reference repos: `Fully-Autonomous-Polymarket` for the CLOB interface pattern; `CloddsBot` for the multi-market abstraction base class
- Target file: `execution/kalshi_broker.py` + `data/kalshi_feed.py`
- Estimated size: 400–500 lines
- Priority: P1
- Notes: Kalshi API docs at `kalshi.com/docs/api`. `KX` ticker prefix for prediction contracts. Requires `KALSHI_API_KEY` + `KALSHI_API_SECRET` in `.env`. Paper trading uses the Kalshi demo environment (`demo.kalshi.co`).

**2. Prediction Market Broker Base Class**
- What it does: Abstract base class `BasePredictionMarketBroker` that `polymarket_broker.py` and `kalshi_broker.py` both inherit from. Standardizes: `get_markets() -> list[Market]`, `get_orderbook(market_id) -> OrderBook`, `place_order(market_id, side, size, price) -> OrderResult`, `get_positions() -> list[Position]`, `resolve_position(market_id) -> float`. Enables cross-market arbitrage detection without market-specific branching.
- Reference repos: `CloddsBot` multi-market abstraction pattern; `hummingbot` base connector architecture
- Target file: `execution/prediction_market_base.py`
- Estimated size: 150–200 lines
- Priority: P1

**3. Lane 3 Scan Worker**
- What it does: Dedicated scan function `run_prediction_market_scan()` that runs on a 15-minute cycle. Fetches top markets by volume from both Polymarket and Kalshi, scores each through the ensemble forecaster, applies 15-point risk checks, places paper trades on qualifying markets, monitors open positions for resolution. Designed as a standalone function that `job_runner.py` calls in parallel with existing lane scans.
- Reference repos: `Fully-Autonomous-Polymarket` `engine/trading_loop.py` for loop structure; `cbt-framework` `commands/cbt-live.md` for deployment checklist
- Target file: Section in `scheduler/job_runner.py` (short-term) → `scheduler/lane3_runner.py` (Sprint 4 refactor)
- Estimated size: 300–400 lines
- Priority: P1

**4. Options Chain Data Model**
- What it does: Python dataclasses for the full options domain: `OptionsChain`, `OptionsContract`, `Greeks` (delta, gamma, theta, vega, rho), `SpreadAnalysis`, `PMCCOpportunity`. Bridges Alpaca's options API response format to our indicator + agent pipeline. The Go interface from Claude_Prophet (`interfaces/options.go`) is the specification; this is the Python implementation.
- Reference repos: `Claude_Prophet` `interfaces/options.go` as specification; `trading_skills` `src/trading_skills/greeks.py` for Greeks calculation
- Target file: `data/options_chain.py`
- Estimated size: 250–350 lines
- Priority: P1

**5. Alpaca Options Execution Extension**
- What it does: Extends existing `execution/alpaca_broker.py` with options-specific methods: `get_options_chain(symbol, expiry_range) -> OptionsChain`, `place_options_order(contract, side, qty) -> OrderResult`, `get_options_position(contract_id) -> Position`, `calculate_buying_power_reduction(contract) -> float`. Alpaca's options API is documented at `alpaca.markets/docs/api-references/trading-api/options/`.
- Reference repos: `trading_skills` `src/trading_skills/broker/` for IBKR interface pattern (adapt to Alpaca); `Claude_Prophet` `services/alpaca_options_data.go` for data model
- Target file: Extends `execution/alpaca_broker.py` (add ~200 lines to existing file)
- Estimated size: 200–250 new lines
- Priority: P1

**6. Cross-Market Arbitrage Detector**
- What it does: Monitors complementary prediction market contracts across Polymarket and Kalshi simultaneously. Identifies when the same real-world event is mispriced on one platform vs the other (e.g., "Biden wins Iowa" at 0.62 on Polymarket vs 0.71 on Kalshi = 9-point arb). Exposes `scan_arbitrage() -> list[ArbOpportunity]`. Requires positions on both platforms simultaneously.
- Reference repos: `CloddsBot` `src/arbitrage/index.ts` (translate to Python); `Fully-Autonomous-Polymarket` for market-equivalence matching logic
- Target file: `strategies/prediction_arb.py`
- Estimated size: 300–400 lines
- Priority: P2

**7. Test Suite Foundation**
- What it does: pytest-based test suite covering the 4 highest-risk modules. `tests/test_indicators.py` (verify no look-ahead bias in all 30+ indicators — most critical), `tests/test_risk_engine.py` (verify hard rules cannot be bypassed via mocking), `tests/test_broker_paper.py` (smoke test all brokers' paper mode), `tests/test_backtest_engine.py` (walk-forward with synthetic price data — regression prevention).
- Reference repos: None — standard pytest patterns. `intelligent-trading-bot` `tests/` structure for reference.
- Target files: `tests/__init__.py`, `tests/test_indicators.py`, `tests/test_risk_engine.py`, `tests/test_broker_paper.py`, `tests/test_backtest_engine.py`
- Estimated size: 800–1,200 lines total
- Priority: **P0**

**8. GitHub Actions CI Pipeline**
- What it does: On every push to `main`: (1) `ruff check .` for linting, (2) `mypy` for type checking key files, (3) `pytest tests/` for test suite, (4) post result as commit status. Prevents a broken import from silently killing the bot after a deploy. Runs on Python 3.12 (not 3.14 — GHA runners don't have 3.14 yet; this also validates cross-version compatibility).
- Reference repos: None — standard GitHub Actions YAML. `algorithmic-trading-bot` `.github/workflows/` for reference structure.
- Target file: `.github/workflows/ci.yml`
- Estimated size: 80–120 lines
- Priority: P1

**9. Drawdown Heat System**
- What it does: Replaces the binary halt/normal sizing with a 5-level heat system. Level 0 (normal): full Kelly size. Level 1 (−1.5% day): 75% size. Level 2 (−2.5% day): 50% size. Level 3 (−3.5% day): 25% size. Level 4 (−4% day): HALT all new entries. Smooth degradation vs cliff-edge halts. Integrates into `risk/drawdown_controller.py`.
- Reference repos: `Fully-Autonomous-Polymarket` `src/policy/drawdown.py` for heat level pattern; `CloddsBot` `src/risk/circuit-breaker.ts` for typed circuit breaker logic
- Target file: Part of `risk/drawdown_controller.py` (Sprint 1)
- Estimated size: 100–150 lines
- Priority: P1

**10. Telegram Alert Dispatcher**
- What it does: Replaces `alerts/telegram_alert.py` (currently Gmail SMTP). Uses Telegram Bot API (`python-telegram-bot` package). Sends trade opens, trade closes, daily P&L, halt events, Lane 3 prediction resolutions. Keeps the same public API as the current file so all callers need zero changes. Adds Discord webhook as a fallback channel.
- Reference repos: `Fully-Autonomous-Polymarket` `src/observability/alerts.py` for multi-channel dispatcher pattern
- Target file: `alerts/alert_dispatcher.py` (rename from `telegram_alert.py`)
- Estimated size: 150–200 lines
- Priority: P1

---

## 4F. Architectural Patterns to Adopt

Ranked by impact. Each pattern is assessed on value vs implementation cost.

---

### Pattern 1: MCP Server (FastMCP Python)
**Source:** `trading_skills` FastMCP pattern; `Claude_Prophet` tool catalog for completeness

**Why adopt:** This is the most critical missing architectural piece. Without MCP, every interaction between Claude Code and the trading system is unstructured text. With MCP, Claude Code can call `get_positions()`, `run_backtest(symbol='BTC-USD', period='30d')`, `place_paper_trade(symbol='ETH-USD', size=250)` as first-class tool calls — with input validation, structured outputs, and audit trails. The overhaul adds three new lanes, all of which require Claude to orchestrate complex multi-step workflows. MCP makes those workflows reliable and repeatable.

**Implementation:** `from mcp.server.fastmcp import FastMCP` + `@mcp.tool()` decorator. Start with 15 tools wrapping existing functions. Add a tool per new capability as lanes come online.

**Effort vs value:** Medium effort (3–5 days), transformative value. Build in Sprint 1.

---

### Pattern 2: Multi-LLM Ensemble (Polymarket bot — Claude + GPT + Gemini)
**Source:** `Fully-Autonomous-Polymarket` `src/forecast/ensemble.py`

**Why adopt:** Currently all AI analysis uses only Claude (Anthropic). Single-provider dependency means: (1) Anthropic API outage = all three lanes halt, (2) model-specific biases go uncorrected (no adversarial cross-check), (3) no calibration feedback distinguishes which provider is more accurate on which market type. The ensemble pattern (parallel async calls, weighted by per-model Brier score) addresses all three. Adaptive weighting means the system discovers that Claude is more accurate on crypto debates while GPT is more accurate on macro prediction markets.

**Implementation:** `asyncio.gather()` for parallel LLM calls. Trimmed mean aggregation. Per-model score tracking in `agent_stats` table. Requires adding `OPENAI_API_KEY` and `GOOGLE_API_KEY` to `.env`.

**Effort vs value:** Large effort (1–2 weeks), high strategic value. Build in Sprint 5 (after lanes are functional with Claude alone).

---

### Pattern 3: Async Task Graph (replace sequential while-True loop)
**Source:** `TradingAgents` LangGraph state machine; asyncio patterns from multiple repos

**Why adopt:** `job_runner.py` is 1,812 lines scanning sequentially. With 3 lanes + 20 crypto pairs + 5 equity + options screen + 2 prediction platforms, sequential scanning means a 60-second Claude API call for BTC blocks all other symbols. Async architecture: each lane runs as a separate coroutine, each symbol scan is a task, shared state via SQLite (already WAL-safe). Total scan cycle time drops from minutes to seconds.

**Implementation:** Python `asyncio` + `concurrent.futures.ThreadPoolExecutor` for broker I/O. NOT LangGraph yet (too large a migration for Sprint 1). Gradual: extract each lane into its own `async def run_lane_X()` and run them concurrently in `main.py`.

**Effort vs value:** Extra-Large effort (full refactor). Defer to Sprint 4. Do not block Sprints 1–3 on this.

---

### Pattern 4: SQLite Embeddings (replace LanceDB)
**Source:** `Claude_Prophet` `database/storage.go` trade_embeddings table

**Why adopt:** LanceDB + sentence-transformers adds ~2GB of dependencies for the vector memory that was demoted to "supplemental" in v5.0. The Claude_Prophet pattern stores 384-dim float vectors as BLOB columns in the existing trades.db — same semantic similarity search, zero new packages. The vectors can be generated using a lightweight local method (numpy random projection for approximate search, or a 50MB embedding model via `fastembed` instead of the 2GB sentence-transformers).

**Implementation:** Add `trade_embeddings` table to trades.db schema. Replace `lancedb.connect()` calls in `trade_memory.py` with SQLite BLOB storage. For query: load vectors into numpy array, compute cosine similarity with query vector, return top-k.

**Effort vs value:** Small effort (1–2 days), meaningful reduction in dependency footprint. Build in Sprint 1 alongside LanceDB removal.

---

### Pattern 5: Agent Role Definitions (.claude/agents/)
**Source:** `Claude_Prophet` `.claude/agents/` (4 agent definitions)

**Why adopt:** Currently Claude Code has no structured understanding of which agent to use for which task. Creating named agent definitions (portfolio_manager.md, trade_strategist.md, devil_advocate.md, system_engineer.md) gives every Claude Code session a consistent starting context. The portfolio manager agent, for example, always considers halt conditions and risk budgets before any trade action. The devil's advocate agent always challenges entry theses. These roles complement our existing debate engine by providing meta-level orchestration.

**Implementation:** Create `.claude/agents/` directory. Write 4 markdown files with YAML frontmatter following Claude_Prophet's format. No code changes required.

**Effort vs value:** Small effort (1 day), high value for operational reliability.

---

### Pattern 6: Per-Lane Config Structure
**Source:** `TradingAgents` `default_config.py` multi-provider pattern; `Fully-Autonomous-Polymarket` `config.yaml` YAML structure

**Why adopt:** Currently `config.py` is a flat file with ~212 constants, all mixed together. With 3 lanes, this grows to 400+ constants. Organizing by lane (`LANE1_*`, `LANE2_*`, `LANE3_*` prefix groups or a nested dict) and by concern (data, execution, risk, AI) makes the file navigable and reduces accidental cross-lane configuration bleed.

**Implementation:** Keep `config.py` as the Python entry point (for .env reading). Add a structured section layout using comment headers and constant name prefixes. Optionally add `config/config.yaml` alongside for human-readable reference (like `algorithmic-trading-bot`'s dual config approach).

**Effort vs value:** Small effort (2–3 hours), meaningful maintainability improvement.

---

### Pattern 7: Offline / Online ML Split
**Source:** `intelligent-trading-bot` train/predict separation

**Why adopt:** Currently `ml_signal.py` retrains LightGBM during a live scan (every 50 trade closes). Retraining takes 3–10 seconds and blocks the scan cycle. The offline/online split: training happens in a background process (or scheduled job) and saves a model artifact to disk. The live scanner loads the pre-trained model and only calls `.predict()` — which is microseconds. This prevents a slow retrain from delaying a time-sensitive trade entry.

**Implementation:** Split `learning/ml_signal.py` into `learning/ml_trainer.py` (offline: train, save to `logs/ml_model.pkl`) and `learning/ml_predictor.py` (online: load at startup, predict). Trainer runs as a launchd job every 4 hours or after 50 trade closes — not inline.

**Effort vs value:** Small effort (1 day), prevents scan-cycle latency spikes.

---

## 4G. Recommended Sprint Plan

Each sprint has a clear definition of done. Sprints are approximately 1–2 weeks of focused work.

---

### Sprint 1: Foundation
**Theme:** Remove dead code. Add test coverage. Build MCP server. Decompose risk monolith.
**Why first:** Sprints 2–5 all add new code on top of the existing foundation. If the foundation has dead stubs (Webull), untested critical paths (risk rules), and no programmatic control surface (MCP), every subsequent sprint is riskier.

**Files to CREATE:**
| File | Draws from |
|------|-----------|
| `mcp_server/__init__.py` | — |
| `mcp_server/server.py` | `trading_skills` FastMCP pattern |
| `risk/stop_loss_manager.py` | `algorithmic-trading-bot/src/risk/stop_loss_manager.py` |
| `risk/drawdown_controller.py` | `algorithmic-trading-bot/src/risk/drawdown_controller.py` + Polymarket `policy/drawdown.py` |
| `risk/position_sizer.py` | `algorithmic-trading-bot/src/risk/position_sizer.py` |
| `risk/correlation_manager.py` | `algorithmic-trading-bot/src/risk/correlation_manager.py` |
| `risk/risk_engine.py` | `algorithmic-trading-bot/src/risk/risk_engine.py` (orchestrator) |
| `risk/risk_limits.py` | Polymarket `src/policy/risk_limits.py` (15-point pipeline) |
| `risk/var_calculator.py` | `CloddsBot/src/risk/var.ts` translated to Python |
| `tests/__init__.py` | — |
| `tests/test_indicators.py` | Standard pytest; `intelligent-trading-bot/tests/` structure |
| `tests/test_risk_engine.py` | Standard pytest |
| `tests/test_broker_paper.py` | Standard pytest |
| `.claude/agents/portfolio_manager.md` | `Claude_Prophet/.claude/agents/` |
| `.claude/agents/trade_strategist.md` | `Claude_Prophet/.claude/agents/` |
| `.claude/agents/devil_advocate.md` | `Claude_Prophet/.claude/agents/` |
| `.claude/agents/system_engineer.md` | `Claude_Prophet/.claude/agents/` |
| `.claude/commands/audit.md` | `cbt-framework/commands/` |
| `.claude/commands/health.md` | `cbt-framework/commands/` |

**Files to MODIFY:**
| File | Change |
|------|--------|
| `execution/alpaca_broker.py` | Remove any `webull_broker.py` imports |
| `risk/risk_manager.py` | Convert to thin orchestrator calling `risk_engine.py`. Keep public API identical. |
| `memory/trade_memory.py` | Replace LanceDB with SQLite BLOB embeddings pattern |
| `requirements.txt` | Remove: `webull>=0.3.15`, `lancedb>=0.6.0`, `sentence-transformers>=2.7.0`. Add: `mcp`, `fastmcp`, `pytest`, `ruff` |
| `alerts/telegram_alert.py` | Rename to `alert_dispatcher.py`. Replace Gmail SMTP with Telegram Bot API. Keep same public API. |
| `config.py` | Add section headers (LANE1_, LANE2_, LANE3_ prefix groups). Add `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` constants. |
| `.env.example` | Add `TELEGRAM_BOT_TOKEN=`, `TELEGRAM_CHAT_ID=` placeholders |

**Files to DELETE:**
| File | Reason |
|------|--------|
| `execution/webull_broker.py` | Dead proxy stub |
| `scripts/generate_system_html.py` | 1,001 lines of low-value static HTML generation |

**Definition of Done:**
- [x] `python3 -m pytest tests/` passes (~25 tests across 3 files) ✅ 2026-03-26
- [ ] `python3 main.py --crypto-only` runs without errors for 60 seconds (pending Binance keys)
- [x] `mcp_server/server.py` built with 15 tools ✅ 2026-03-26
- [x] `webull_broker.py` deleted; `bybit_broker.py` deleted ✅ 2026-03-26
- [x] `risk_manager.py` decomposed into 5 modules; public API unchanged ✅ 2026-03-26
- [ ] Telegram alert sends a test message (Gmail SMTP still in place; Telegram migration = Sprint 2)
- [x] `.claude/agents/` directory exists with 4 agent definitions ✅ 2026-03-26
- [x] `scheduler/job_runner.py` reduced to 258 lines (was 1,812) ✅ 2026-03-26
- [x] GitHub repo live: futureisnowtech/trading-bot-main, branch feature/agent-overhaul ✅ 2026-03-26

**Sprint 1 Status: COMPLETE** (2026-03-26) — 1 item deferred to Sprint 2 (Telegram), 1 pending Binance keys

---

### Sprint 2: Lane 3 — Prediction Markets
**Theme:** Build the fastest new revenue capability. Prediction markets have no existing code, so there is no risk of breaking working lanes while building this.
**Why second:** Lane 3 is the fastest path to demonstrating multi-lane capability. It has zero conflicts with existing Lane 2 crypto code. The Polymarket bot reference repo provides nearly the complete implementation. Building Lane 3 before touching Lane 1 options avoids the complexity of the options API while proving the 3-lane architecture works.

**Files to CREATE:**
| File | Draws from |
|------|-----------|
| `data/polymarket_feed.py` | Polymarket bot `connectors/polymarket_gamma.py` |
| `execution/prediction_market_base.py` | `CloddsBot` multi-market abstraction; `hummingbot` base connector |
| `execution/polymarket_broker.py` | Polymarket bot `connectors/polymarket_clob.py` |
| `execution/kalshi_broker.py` | Kalshi REST API docs + Polymarket broker as structural template |
| `data/kalshi_feed.py` | Kalshi REST API; Polymarket Gamma pattern |
| `data/whale_tracker.py` | Polymarket bot `analytics/wallet_scanner.py` |
| `strategies/ai_agents/ensemble_forecaster.py` | Polymarket bot `forecast/ensemble.py` |
| `learning/forecast_calibrator.py` | Polymarket bot `forecast/calibrator.py` (Platt scaling) |
| `alerts/alert_dispatcher.py` | Polymarket bot `observability/alerts.py` for multi-channel pattern |
| `.github/workflows/ci.yml` | Standard GitHub Actions; `algorithmic-trading-bot` for reference |
| `.claude/commands/deploy.md` | `cbt-framework/commands/cbt-live.md` |

**Files to MODIFY:**
| File | Change |
|------|--------|
| `scheduler/job_runner.py` | Add `run_prediction_market_scan()` function called from main loop |
| `logging_db/trade_logger.py` | Add `lane` column to `trades` table via ALTER TABLE migration |
| `risk/risk_engine.py` | Add Lane 3 routing: prediction markets use different position limits |
| `config.py` | Add `LANE3_*` constants: `POLYMARKET_PAPER=true`, `KALSHI_PAPER=true`, `POLYMARKET_API_KEY=`, `KALSHI_API_KEY=`, `ENSEMBLE_CLAUDE_WEIGHT=0.35`, `ENSEMBLE_GPT_WEIGHT=0.40`, `ENSEMBLE_GEMINI_WEIGHT=0.25`, `PM_MIN_VOLUME_USD=10000`, `PM_MAX_POSITION_USD=25`, `PM_MIN_EDGE_PCT=3.0` |
| `.env.example` | Add `POLYMARKET_PRIVATE_KEY=`, `KALSHI_API_KEY=`, `KALSHI_API_SECRET=`, `OPENAI_API_KEY=`, `GOOGLE_API_KEY=` |
| `scripts/test_brokers.py` | Add Polymarket and Kalshi paper mode tests |
| `dashboard/app.py` | Add Lane 3 panel to THE KING view: open predictions, recent resolutions, calibration score |

**Definition of Done:**
- [ ] `python3 main.py` runs all 3 lanes without errors for 5 minutes
- [ ] `run_prediction_market_scan()` logs at least one market evaluation to `trades.db`
- [ ] Polymarket paper trade placed and logged with correct `lane='lane3'` tag
- [ ] Kalshi paper trade placed and logged
- [ ] `learning/forecast_calibrator.py` stores calibration curve to SQLite
- [ ] Dashboard shows Lane 3 panel with open predictions
- [ ] GitHub Actions CI passes on push to main
- [ ] Multi-LLM ensemble returns result (even if only Claude is active initially)

---

### Sprint 3: Lane 1 — Options
**Theme:** Add options income strategies to the existing Alpaca equity lane.
**Why third:** Options require the most groundwork (data model, chain fetching, strategy selection, execution) but build directly on the working Alpaca equity broker. By Sprint 3, the MCP server and risk decomposition from Sprint 1 are stable, so the options layer has a clean foundation.

**Files to CREATE:**
| File | Draws from |
|------|-----------|
| `data/options_chain.py` | `Claude_Prophet` `interfaces/options.go` (Python dataclass translation) |
| `strategies/options_spreads.py` | `trading_skills` `spreads.py` + Black-Scholes math |
| `data/options_screener.py` | `trading_skills` `scanner_pmcc.py` |
| `strategies/ai_agents/options_analyst.py` | `claude-trading-skills` `skills/options-strategy-advisor/` |
| `data/equity_sentiment.py` | `algorithmic-trading-bot` `src/sentiment/` |
| `.claude/commands/options-scan.md` | `cbt-framework` slash-command format |
| `tests/test_options_spreads.py` | Standard pytest — verify Black-Scholes math |

**Files to MODIFY:**
| File | Change |
|------|--------|
| `execution/alpaca_broker.py` | Add ~200 lines: `get_options_chain()`, `place_options_order()`, `get_options_positions()` |
| `strategies/ai_agents/analyst_agents.py` | Add `options_analyst` (Bulma) as 4th agent for Lane 1 decisions |
| `strategies/ai_agents/debate_engine.py` | Parameterize agent list by lane: Lane 1 uses 4 agents, Lane 2 uses 3 |
| `strategies/ai_agents/regime_detector.py` | Add full macro regime (Concentration/Broadening/Contraction/Inflationary/Transitional) using `claude-trading-skills` macro-regime-detector patterns |
| `data/auto_screener.py` | Wire `options_screener.py` output into equity screener results |
| `data/market_context.py` | Add `equity_sentiment.py` results to debate context for Lane 1 |
| `config.py` | Add `LANE1_OPTIONS_ENABLED=false` (paper first), `OPTIONS_MAX_POSITIONS=3`, `OPTIONS_DTE_MIN=21`, `OPTIONS_DTE_MAX=45`, `OPTIONS_DELTA_TARGET=0.30`, `OPTIONS_PMCC_LEAPS_DELTA=0.80` |
| `.env.example` | Add `REDDIT_CLIENT_ID=`, `REDDIT_CLIENT_SECRET=`, `LANE1_OPTIONS_ENABLED=false` |
| `scripts/check_readiness.py` | Add Lane 1 options readiness criteria |

**Definition of Done:**
- [ ] `LANE1_OPTIONS_ENABLED=false` (paper equity runs, options disabled until manual promotion)
- [ ] `data/options_chain.py` fetches a real options chain from Alpaca paper API
- [ ] `data/options_screener.py` finds at least one PMCC candidate in a test run
- [ ] `strategies/options_spreads.py` calculates correct Black-Scholes price (verified by test)
- [ ] Options analyst agent (Bulma) participates in Lane 1 debate and returns a BUY/HOLD vote
- [ ] `tests/test_options_spreads.py` passes with correct Greeks math
- [ ] Dashboard shows Lane 1 options panel (PMCC candidates, IV rank, pending options positions)
- [ ] `execution/alpaca_broker.py` can place a paper options order without errors

---

### Sprint 4: Lane 2 — Crypto Futures Live
**Theme:** Promote Bybit from testnet to mainnet. Introduce async architecture foundation.
**Why fourth:** Lane 2 (Bybit) is the most mature unfinished lane — all the logic exists, it just needs mainnet keys and the promotion checklist. Sprint 4 also begins the async architecture migration (not completing it, but starting the pattern).

**Files to CREATE:**
| File | Draws from |
|------|-----------|
| `scripts/promote_bybit_live.py` | `cbt-framework` Bybit deployment checklist; `scripts/check_readiness.py` pattern |
| `scheduler/lane2_runner.py` | Extract `run_crypto_scan()` + `run_perp_scan()` from `job_runner.py` — first step of async decomposition |
| `scheduler/lane1_runner.py` | Extract `run_equity_scan()` from `job_runner.py` |
| `scheduler/lane3_runner.py` | Migrate `run_prediction_market_scan()` from `job_runner.py` |

**Files to MODIFY:**
| File | Change |
|------|--------|
| `execution/bybit_broker.py` | Add `BYBIT_TESTNET=false` path validation; add mainnet connection test; add first-order size guard ($10 max for first live trade) |
| `scheduler/job_runner.py` | Import from lane runner files; convert to orchestrator only; add `asyncio.gather(lane1, lane2, lane3)` for parallel lane scanning |
| `config.py` | Add `LANE2_BYBIT_LIVE=false` flag (requires manual promotion via `promote_bybit_live.py`) |
| `.env.example` | Add `BYBIT_MAINNET_API_KEY=`, `BYBIT_MAINNET_API_SECRET=`, `LANE2_BYBIT_LIVE=false` |

**Definition of Done:**
- [ ] `python3 scripts/promote_bybit_live.py` runs all 8 promotion checks and outputs pass/fail per check
- [ ] Bybit mainnet API keys accepted (validation step in promote script)
- [ ] First live Bybit order placed at $10 notional — confirmed in Bybit dashboard
- [ ] `scheduler/job_runner.py` reduced to <500 lines (from 1,812) by extraction to lane runners
- [ ] Lane 1, 2, 3 runners execute concurrently (asyncio) without shared-state conflicts
- [ ] All existing tests still pass after job_runner refactor

---

### Sprint 5: Intelligence + Ops
**Theme:** Upgrade the AI layer, add ML model ensemble, wire CI/CD, and close remaining observability gaps.
**Why fifth:** This sprint assumes all three lanes are live and generating trade data. The multi-LLM ensemble, forecast calibration, and ML upgrade are most valuable once there is real outcome data to train and calibrate against. Doing this in Sprint 1 would be premature optimization on a system with no data.

**Files to CREATE:**
| File | Draws from |
|------|-----------|
| `learning/ml_trainer.py` | `intelligent-trading-bot` offline/online split |
| `learning/ml_predictor.py` | `intelligent-trading-bot` online predictor pattern |
| `strategies/prediction_arb.py` | `CloddsBot` `src/arbitrage/index.ts` translated to Python |
| `tests/test_backtest_engine.py` | Standard pytest with synthetic price data |
| `tests/test_forecast_calibrator.py` | Standard pytest |

**Files to MODIFY:**
| File | Change |
|------|--------|
| `learning/ml_signal.py` | Replace with thin shim calling `ml_predictor.py`. Remove inline retraining. |
| `strategies/ai_agents/ensemble_forecaster.py` | Add GPT-4o and Gemini providers alongside Claude. Enable adaptive weighting from Brier scores. |
| `learning/forecast_calibrator.py` | Wire per-agent calibration curves into debate prompt injection. |
| `logging_db/trade_logger.py` | Split into `logging_db/schema.py` + `logging_db/writer.py` + `logging_db/reader.py`. Reduce single-file complexity. |
| `dashboard/app.py` | Add calibration curve chart. Add multi-LLM provider accuracy comparison panel. |
| `scripts/generate_daily_summary.py` | Add Lane 3 daily prediction resolution summary. |
| `config.py` | Add `OPENAI_API_KEY`, `GOOGLE_API_KEY` with multi-LLM ensemble weights. |

**Definition of Done:**
- [ ] `ml_trainer.py` trains model as a background process, saves `logs/ml_model.pkl`
- [ ] `ml_predictor.py` loads `ml_model.pkl` at startup and predicts in <10ms
- [ ] Multi-LLM ensemble running with at least 2 of 3 providers (Claude + GPT minimum)
- [ ] `learning/forecast_calibrator.py` shows nonzero calibration data after 30 closed trades
- [ ] Cross-market arbitrage scanner runs once per 15-minute cycle
- [ ] `logging_db/` split into 3 files; all tests pass
- [ ] Dashboard shows Brier score comparison: Claude vs GPT vs Gemini accuracy by lane

---

## 4H. Broker Account Setup Instructions

Step-by-step setup for all three lane brokers. Follow these before starting Sprint 1 (Lane 2), Sprint 2 (Lane 3), and Sprint 3 (Lane 1 options).

---

### Alpaca — Paper Trading Activation (Lane 1)

Alpaca paper trading is already wired in `execution/alpaca_broker.py`. The key issue is ensuring the paper keys are correctly configured and the account has options enabled.

**Step 1: Create or log into Alpaca account**
- Go to `alpaca.markets` → Sign up or log in
- Dashboard URL: `app.alpaca.markets`

**Step 2: Get paper trading API keys**
- In the Alpaca dashboard: click "Paper Trading" in the left sidebar
- Click "Your API Keys" → "Generate New Key"
- Copy `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY`
- Add to `.env`:
  ```
  ALPACA_API_KEY=your_paper_key_id
  ALPACA_SECRET_KEY=your_paper_secret_key
  ALPACA_BASE_URL=https://paper-api.alpaca.markets
  ```

**Step 3: Validate paper connection**
```bash
python3 scripts/test_brokers.py
```
Look for: `[PASS] Alpaca paper: connected, buying power = $100,000`

**Step 4: Enable options trading (required for Lane 1 options)**
- In Alpaca dashboard: Account → Trading → Options Agreement
- Complete the options trading application (level 2 is sufficient for covered calls, PMCCs, and spreads)
- Alpaca reviews and approves within 1–3 business days
- After approval: `LANE1_OPTIONS_ENABLED=true` in `.env`

**Step 5: For live trading (after paper readiness check passes)**
- In Alpaca dashboard: switch to "Live Trading"
- Get live API keys (different from paper keys)
- Fund the account ($500 minimum for margin; cash account has PDT restrictions)
- Update `.env`:
  ```
  ALPACA_API_KEY=your_live_key_id
  ALPACA_SECRET_KEY=your_live_secret_key
  ALPACA_BASE_URL=https://api.alpaca.markets
  ```
- Run `python3 scripts/check_readiness.py` — must show all 7 criteria passing before going live

---

### Polymarket — Wallet Setup (Lane 3)

Polymarket uses an Ethereum wallet on the Polygon network for settlement. All trades are on-chain. The API uses a private key for signing orders.

**Step 1: Create a dedicated trading wallet**
- Install MetaMask browser extension: `metamask.io`
- Create a NEW wallet specifically for Polymarket (do not use an existing personal wallet)
- Save the 12-word seed phrase to a secure password manager (1Password, Bitwarden)
- Copy the wallet address (0x...)

**Step 2: Fund the wallet with USDC on Polygon**
- Polymarket uses USDC on the Polygon network (not Ethereum mainnet — gas is cheap)
- Purchase USDC on Coinbase and send to your Polygon wallet address
- Alternatively: bridge existing USDC from Ethereum to Polygon via `bridge.connext.network` or `wallet.polygon.technology/bridge`
- Minimum recommended: $50 USDC for initial testing

**Step 3: Set up Polymarket API access**
- Go to `polymarket.com` → Connect Wallet → sign in with your MetaMask wallet
- Navigate to `polymarket.com/profile` → API Keys → Generate API key
- This generates an API key, secret, and passphrase
- Alternatively, the `py-clob-client` SDK uses the wallet's private key directly for signing

**Step 4: Export private key for the bot**
- In MetaMask: Account Details → Export Private Key → enter password → copy key
- CRITICAL SECURITY NOTE: this private key gives full access to the wallet's funds. Store only in `.env` (never commit). Use a dedicated wallet with only the amount you are willing to risk.
- Add to `.env`:
  ```
  POLYMARKET_PRIVATE_KEY=0x_your_private_key_here
  POLYMARKET_API_KEY=your_api_key
  POLYMARKET_API_SECRET=your_api_secret
  POLYMARKET_PASSPHRASE=your_passphrase
  POLYMARKET_PAPER=true
  ```

**Step 5: Install Polymarket SDK**
```bash
pip3 install py-clob-client
```
Add to `requirements.txt`: `py-clob-client>=0.12.0`

**Step 6: Validate paper connection**
```bash
python3 scripts/test_brokers.py  # After Sprint 2 adds Polymarket tests
```

**Step 7: Go live (after Sprint 2 paper validation)**
- Set `POLYMARKET_PAPER=false` in `.env`
- Start with `PM_MAX_POSITION_USD=10` (config: max $10 per prediction trade)
- Monitor first 5 fills manually in `polymarket.com/profile/positions`

---

### Kalshi — Account Setup (Lane 3)

Kalshi is a CFTC-regulated prediction market exchange (US legal). Unlike Polymarket (crypto-based), Kalshi uses USD directly and is connected to US banking.

**Step 1: Create Kalshi account**
- Go to `kalshi.com` → Sign up
- Complete KYC identity verification (government ID required — required by CFTC)
- Wait for account approval (typically 1–24 hours)

**Step 2: Get API credentials**
- Log in to `kalshi.com` → Account → API → Create API key
- Select permissions: `Read` + `Create Orders` + `Close Orders`
- Copy `API_KEY` and `API_SECRET` (also called `API_KEY_ID` and `API_ACCESS_KEY` in Kalshi docs)
- Add to `.env`:
  ```
  KALSHI_API_KEY=your_kalshi_api_key
  KALSHI_API_SECRET=your_kalshi_api_secret
  KALSHI_PAPER=true
  KALSHI_BASE_URL=https://demo.kalshi.co/trade-api/v2
  ```

**Step 3: Use the Kalshi demo environment**
- Kalshi has a demo environment at `demo.kalshi.co` with paper money
- The demo API URL: `https://demo.kalshi.co/trade-api/v2`
- Demo account requires separate registration at `demo.kalshi.co`
- Demo credentials may differ from production credentials

**Step 4: Fund the live account (after paper validation)**
- Kalshi uses USD — fund via ACH bank transfer from `kalshi.com/account/funding`
- Minimum deposit: $10
- Recommended starting amount: $50–$100 for initial live testing

**Step 5: Install Kalshi SDK**
```bash
pip3 install kalshi-python
```
Add to `requirements.txt`: `kalshi-python>=1.0.0`

**Step 6: Validate connection**
```bash
python3 scripts/test_brokers.py  # After Sprint 2 extends test_brokers.py
```

**Step 7: Go live**
- Set `KALSHI_PAPER=false` in `.env`
- Set `KALSHI_BASE_URL=https://trading-api.kalshi.com/trade-api/v2`
- Start with `PM_MAX_POSITION_USD=10` (shared config with Polymarket max size)

---

### Bybit Mainnet — Promotion Checklist (Lane 2)

Run `python3 scripts/promote_bybit_live.py` (built in Sprint 4) which automates the following checks:

**Step 1: Create Bybit account**
- Go to `bybit.com` → Sign up → Complete KYC verification
- Enable 2FA (required for API access)

**Step 2: Create mainnet API key**
- Bybit dashboard → API Management → Create New Key
- Permissions: `Contract — Orders` (enable), `Contract — Positions` (enable), `Spot` (enable if needed)
- IP whitelist: add your Mac's IP address (or leave open for paper testing)
- Copy `API_KEY` and `API_SECRET`
- Add to `.env`:
  ```
  BYBIT_API_KEY=your_bybit_mainnet_key
  BYBIT_API_SECRET=your_bybit_mainnet_secret
  BYBIT_TESTNET=false
  ```

**Step 3: Fund the mainnet account**
- Transfer USDT to your Bybit Derivatives account (not spot)
- Minimum recommended: $100 USDT (enough for 1–2 test positions at our $250 max size with 10× leverage)

**Step 4: Run promotion script**
```bash
python3 scripts/promote_bybit_live.py
```
This script verifies:
1. API key connects successfully
2. Minimum account balance ($100 USDT)
3. At least 14 days of testnet paper trading with logs
4. Walk-forward backtest passes (WR≥30%, PF≥1.2)
5. No system halts in last 7 days
6. First-order size guard (config: max $10 for first live trade)
7. Stop-loss order confirmed placed on first position
8. Funding rate not overheated (below `FUNDING_OVERHEATED_PCT=0.05%`)

**Step 5: First live trade protocol**
- Set `BYBIT_TESTNET=false` in `.env`
- Set `PERP_POSITION_SIZE_USD=10` in `.env` temporarily (not $250)
- Monitor first trade manually: check Bybit dashboard for order confirmation, stop-loss placement
- After 5 successful $10 trades: raise to $25, then $50, then $100, then $250

---

## Summary: The Master Dependency Map

```
Sprint 1 (Foundation)
├── MCP server           ← enables all Claude Code control
├── Risk decomposition   ← enables Lane 3 routing (different limits per lane)
├── Test suite           ← enables confident refactoring in Sprints 2-5
└── Dead code removal    ← simplifies codebase before expansion

Sprint 2 (Lane 3)        depends on: Sprint 1 (risk decomposition, MCP)
├── Polymarket connector
├── Kalshi connector
├── Multi-LLM ensemble (Claude only initially)
└── Forecast calibration

Sprint 3 (Lane 1 Options) depends on: Sprint 1 (MCP, risk decomp)
├── Options chain data model
├── PMCC + spread analysis
└── Options analyst agent (Bulma)

Sprint 4 (Lane 2 Live)   depends on: Sprint 1 (risk decomp), Sprint 2 (architecture)
├── Bybit mainnet promotion
└── Async lane runners (partial)

Sprint 5 (Intelligence)  depends on: Sprints 2-4 (needs real trade data)
├── Multi-LLM ensemble (full 3-provider)
├── ML ensemble upgrade (XGBoost + LSTM)
├── Cross-market arbitrage
└── Offline/online ML split
```

**Estimated total scope:** ~8,000–10,000 lines of new or substantially modified code across all 5 sprints.

**Current system capability:** 5.5/10 (audit baseline)
**Target capability after Sprint 5:** 8.5/10

The biggest single improvement is Sprint 1 (MCP server + risk decomposition) — it unblocks everything and converts the system from a text-driven manual bot into a programmatically controllable trading platform.

---

*Document generated 2026-03-26. Source: PROJECT_AUDIT.md + REFERENCE_REPO_ANALYSIS.md.*
*Update this document after each sprint by appending a sprint retrospective section.*
