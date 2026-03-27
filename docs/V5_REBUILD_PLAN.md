# PHASE 3 PRESENTATION — v5.0 Three-Market Rebuild
# Generated: 2026-03-27
# Based on: MASTER REBUILD PROMPT v5.0
# Owner override: ALL Telegram references → dashboard notifications (system_events SQLite table)
# DO NOT CODE until owner says "APPROVED" or "GO"

---

## PHASE 1 STATUS: COMPLETE

Safety snapshot branch created and pushed: `snapshot/pre-v5-rebuild`
Working branch: `feature/agent-overhaul`
GitHub: `futureisnowtech/trading-bot-main` (private, SSH configured)

---

## THE HONEST ANSWERS (Phase 12 Questions)

These are answered BEFORE the plan, because the answers shape the plan.

---

### Q1: Does Polymarket CLOB support limit orders and maker rebates?

**YES to limit orders. NO to maker rebates.**

Fee structure (as of mid-2025, verify before Sprint 2):
- Maker fee: **0%** — limit orders are completely free
- Taker fee: **2%** — market orders cost 2% of notional
- There is no maker *rebate* — makers just pay zero. Different from crypto.

**Implication**: Always use limit orders. Non-negotiable. A $250 market order costs $5 in fees. A limit order costs $0. Build limit-order-only into `polymarket_broker.py` as a hard constraint, same as "limit orders only" in the crypto engine.

Minimum edge for a limit order entry: any positive expected value (but budget ~0.5% slippage equivalent for fill risk). For a market order: edge must exceed 2%.

---

### Q2: Does Tradovate have a free paper trading API?

**YES, with one catch.**

Tradovate provides a demo environment at `demo.tradovate.com` — same endpoints, same auth (OAuth2), same order types as live. Demo accounts are free.

The catch: real-time market data requires a paid subscription even in demo. Current `tradovate_broker.py` uses yfinance for pricing, which is a reasonable workaround for paper testing.

**Recommendation**: Wire the Tradovate demo REST API directly for order simulation while keeping yfinance for price feeds. This tests the actual broker integration path. The current yfinance-only simulation doesn't exercise broker code at all.

---

### Q3: Is Coinbase maker rebate farming viable at $1,000?

**No. Here is the math.**

Coinbase Advanced Trade tier at <$10K monthly volume:
- Maker fee: **0.40%** (you PAY, not receive a rebate)
- Taker fee: **0.60%**
- "Maker rebate" only exists at institutional tiers (>$100M/month)

Round-trip cost for a $250 position: **$2.00** (0.40% × 2)

To break even on a spread-capture strategy: price must move **0.80%** just to cover fees. On 1-minute BTC candles, this means 90% of all candles are unprofitable before edge.

**The correct strategy at this account size**: swing trades with ≥3:1 R:R where the profit target exceeds 1.5% (3× the 0.50% stop). Your `ATR_FEE_FLOOR_PCT` guard already enforces this correctly.

Binance perp fees: 0.02% maker / 0.05% taker. This is 8–20× cheaper than Coinbase spot. The v9.0 Binance migration was the right call.

---

### Q4: Does MacBook Air latency make scalping impractical?

**Yes. Sub-minute scalping is impossible with your architecture.**

Realistic latency profile (US home WiFi):
- Coinbase REST round-trip: 150–400ms typical, 500–800ms p95
- WebSocket quote latency: 50–150ms
- Your 3-agent debate: 15–30 seconds

Total signal-to-order latency: **20–45 seconds minimum**

On a 1-minute bar, you enter 30–50% into the next candle after the signal fires. This is not fixable without co-location infrastructure ($50K+ capital threshold).

**Practical floor for your architecture: 5-minute bars.**
- 5-min bars: 30-second debate = 10% of bar. Acceptable.
- 1-min bars: 30-second debate = 50% of bar. Unacceptable.

**Action**: Change crypto scan to evaluate entry signals on 5-minute closes. Keep 1-minute data for indicators. This one change will improve fill quality meaningfully.

---

### Q5: Is LanceDB the right vector store at ~1,000 embeddings?

**No. NumPy cosine similarity is the right tool.**

At 1,000 embeddings of dimension 384:
- NumPy brute-force cosine: **~0.5ms** — imperceptibly fast
- LanceDB: ~5–20ms — also fast, but with 150MB of overhead
- ChromaDB: ~2–10ms, 80MB overhead

LanceDB + sentence-transformers adds ~230MB to the install and is the most common install error in your CLAUDE.md ("LanceDB import error"). The benefit at current scale is zero.

**Recommendation**: Replace `memory/trade_memory.py` with NumPy cosine similarity on embeddings stored as SQLite BLOBs. Same semantic search, zero new dependencies. Migrate to ChromaDB when you hit 50K+ trades.

---

## CODEBASE AUDIT SUMMARY (v9.0 Current State)

Full detail in `docs/PROJECT_AUDIT.md`. Key findings:

**Total Python**: ~26,800 lines across ~67 files

**What v9.0 Already Built (Sprint 1 is done):**
- ✅ Risk decomposed into 5 modules (`risk/` — 834 lines total)
- ✅ Binance broker replacing Bybit (`execution/binance_broker.py`)
- ✅ job_runner decomposed into 6 files (1,888 lines total)
- ✅ MCP server (`mcp_server/server.py`, 349 lines, 15 tools)
- ✅ Claude Code agents (`.claude/agents/`)
- ✅ 5 slash commands (`.claude/commands/`)
- ✅ 3 test files (~25 tests)
- ✅ GitHub live with SSH push

**What Needs to Change for v5.0:**

---

## DELETE LIST (v5.0 removes all equity)

| File | Lines | Reason |
|------|-------|--------|
| `strategies/equity_momentum.py` | 253 | No equity in v5.0 |
| `scheduler/equity_scanner.py` | 227 | No equity in v5.0 |
| `data/auto_screener.py` | 281 | Equity-focused Finviz/Yahoo screener |
| `execution/alpaca_broker.py` | 353 | No equity broker needed |
| `execution/webull_broker.py` | 16 | Dead stub, already deprecated |
| `learning/tax_tracker.py` | 394 | Section 1256 futures — keep for MES only, consider trimming |
| `dashboard/terminal.py` | 498 | Replaced by 2-mode Streamlit dashboard |
| `scripts/generate_system_html.py` | 1,001 | 1,001 lines generating a static HTML page. Deleted. |
| `BRON_DBZ_IMAGES/` (full directory) | ~250 files | Already deleted in git status |
| `execution/bybit_broker.py` | 519 | Replaced by `execution/binance_broker.py` in v9.0. Dead code. |
| `risk/var_calculator.py` | 70 | Built in v9.0 Sprint 1 but never imported anywhere. Dead code. |
| `learning/ai_prescreener.py` | 175 | Batch Haiku pre-screener. Costs ~$0.005/batch, marginal value vs. ML gate. DELETE. |

**Total deleted**: ~3,788 lines. The system gets leaner AND the scope is cleaner.

**Also remove from requirements.txt:**
- `webull>=0.3.15` (dead)
- `lancedb>=0.6.0` (replacing with numpy)
- `sentence-transformers>=2.7.0` (replacing with numpy)

---

## KEEP LIST (survives unchanged or nearly unchanged)

| File | Keep Reason |
|------|-------------|
| `data/indicators.py` (965L) | Best file in the project. 30+ indicators. All 3 markets use it. |
| `data/price_archive.py` (240L) | Excellent candle flywheel. Zero API calls for backtests. |
| `data/coinbase_feed.py` (588L) | Solid WebSocket + REST. Market 1 data feed. |
| `data/macro_feed.py` (275L) | VIX, DXY, funding rates. All 3 markets need macro context. |
| `data/market_context.py` (269L) | `should_block_trade()` pre-gate. Keep, extend for 3 markets. |
| `data/news_feed.py` (257L) | CryptoPanic. Market 1 only, keep. |
| `data/market_data.py` (577L) | Fear&Greed, yfinance. Trim equity-specific parts. |
| `execution/coinbase_broker.py` (407L) | Market 1 spot execution. Keep. |
| `execution/binance_broker.py` (516L) | Market 1 perp execution. Keep. |
| `execution/tradovate_broker.py` (378L) | Market 3 futures. Wire to real demo API. |
| `risk/` (all 5 files, 834L) | v9.0 decomposition is solid. Add unified_sizer as 6th module. |
| `learning/signal_performance.py` (567L) | Bayesian core. Add `market` column for per-market posteriors. |
| `learning/ml_signal.py` (267L) | LightGBM gate. Add IsolationForest outlier detection. |
| `learning/post_trade_analyzer.py` (265L) | Called on every close. Keep. |
| `learning/dynamic_weights.py` (188L) | 5-min cache, meta-layer. Keep. |
| `learning/meta_learner.py` (369L) | Fires after 10 closes. Keep. |
| `learning/intelligence_bridge.py` (256L) | Backtest→live pipeline. Keep. |
| `learning/forecast_calibrator.py` (219L) | Bin-counting calibration: maps conviction scores to observed WR. Injected into debates via `get_full_calibration_context()`. No external deps. Keep. |
| `strategies/ai_agents/analyst_agents.py` (269L) | Bardock/Vegeta/Krillin. Keep, add Market 3 agent set. |
| `strategies/ai_agents/debate_engine.py` (239L) | 2/3 BUY = BUY. Keep, parameterize agent list. |
| `strategies/ai_agents/exit_review.py` (310L) | Tudor Jones/Soros/Simons. Keep, add Market 3 override. |
| `strategies/ai_agents/risk_synthesizer.py` (235L) | Hard veto in code. Keep. |
| `strategies/ai_agents/session_analyst.py` (388L) | Session multiplier. Keep, add Market 2 (24/7) override. |
| `scheduler/crypto_scanner.py` (628L) | Market 1 engine. Keep, evolve for v5.0 signal hierarchy. |
| `scheduler/exit_monitor.py` (329L) | AI exits. Keep. |
| `scheduler/job_runner.py` (258L) | Thin orchestrator. Add Market 2/3 dispatch. |
| `scheduler/_helpers.py` (293L) | Shared state. Keep. |
| `scheduler/perp_scanner.py` (153L) | Binance perp. Keep. |
| `backtesting/backtest_engine.py` (1,930L) | Keep. Walk-forward is solid. |
| `backtesting/strategy_validator.py` (243L) | Keep. Add Market 2/3 criteria. |
| `logging_db/trade_logger.py` (640L) | Keep. Add `market` and `edge_snapshot` fields. |
| `mcp_server/server.py` (349L) | Keep. Extend with Market 2/3 tools. |
| `config.py` (212L) | Keep. Extend with v5.0 constants. |
| `brain/` directory | Keep. Living strategy intelligence. |
| `tests/` (3 files) | Keep, expand significantly. |

---

## BUILD NEW (v5.0 additions)

### Phase 4: Unified Math Framework (build FIRST)

**`risk/unified_sizer.py`** — The position sizing engine
- Inputs: V (volatility score), E (edge quality), D (drawdown factor), T (time-of-day multiplier), K (Kelly factor), M (memory similarity score), B (market balance), R (2% base risk)
- Output: `position_size_usd = B * R * V * E * D * T * K * M`
- **Devil's advocate**: At <20 trades per market, V/E/M multipliers are noise. Recommendation: start with fixed 2% sizing, activate the full formula only after 20 completed trades per market. Build the formula but gate it behind `USE_ADAPTIVE_SIZING = trade_count >= 20`.
- Unit tests required.

**`risk/edge_monitor.py`** — Rolling edge monitor
- Per market, rolling window of last 20 completed trades
- Computes: win_rate_20, profit_factor_20, sharpe_20, edge_score (normalized 0-1)
- Auto-actions: edge < 0.30 for 2 consecutive windows → reduce positions 50% + dashboard notification; edge > 0.70 → increase toward Kelly max + dashboard notification
- Note: "Telegram alert" in prompt = dashboard notification (SQLite `system_events`)

**`risk/volatility_regime.py`** — Per-market vol regime detector
- realized_vol_5d / realized_vol_20d ratio → V_score
- HIGH_VOLATILITY (ratio > 1.5): V_score = 0.2
- ELEVATED (ratio > 1.2): V_score = 0.5
- NORMAL: V_score = 0.75
- LOW_VOLATILITY (ratio < 0.8): V_score = 1.0
- Crypto extension: funding rate gate (>0.01%/8h → reduce longs; <-0.01% → reduce shorts)

### Phase 5: Market 1 — Crypto Strategy Refactor

**`strategies/crypto/crypto_engine.py`** — Unified crypto strategy
Signal hierarchy (higher priority overrides lower):
1. Liquidation cascade detection (funding spike + OI drop → 1.5x size)
2. Cross-pair BTC/ETH divergence > 1.5% → market neutral long/short (1.0x per leg)
3. Order book imbalance: 3:1 bid/ask for 3 consecutive 1-min candles (0.75x)
4. MACD consensus fallback when 1-3 have no signal (0.5x)

Execution rules (hardcoded, non-negotiable):
- LIMIT ORDERS ONLY for entries
- Market orders only for emergency stop exits
- No entries 11am–2pm ET
- Check funding rate before every entry
- **Scan on 5-minute bar closes** (not 1-minute — latency makes 1-min unreliable)

### Phase 6: Market 2 — Polymarket Strategy

**`execution/polymarket_broker.py`** — CLOB client
- Source: adapt `moondevonyt/moon-dev-ai-agents/nice_funcs.py` (fastest path)
- Paper/live toggle matching existing broker pattern
- LIMIT ORDERS ONLY hardcoded — 0% maker fee, 2% taker fee math makes market orders nearly never viable
- Wire to `trade_logger.py` and `risk_manager.py` same as other brokers

**`data/polymarket_feed.py`** — Market scanner
- Gamma API for market discovery (filter by volume, liquidity, category)
- CLOB API for orderbook data
- Write resolved markets to `price_archive.db` for backtesting

**`strategies/polymarket/polymarket_engine.py`** — Signal hierarchy
1. Cross-platform arbitrage (Polymarket vs Kalshi same event priced differently)
2. Late-stage binary collapse (48h before resolution, price > 85 or < 15 → ride to 0/100)
3. Favorite-longshot bias (base rate database: scrape 12-month resolved contracts, buy when market price < historical base rate by > 5%)
4. Correlated contract inconsistency (logical dependency graph)
5. Volume spike follow (5x+ sudden volume → smallest size, widest stop)

**AI Agents for Market 2** (different from trading agents — these are forecasting):
- Superforecaster (Tetlock methodology): base rates, reference class, probabilistic thinking → probability estimate + confidence interval
- Information Asymmetry: who has edge, what does this category systematically get wrong, recent unpriced news → assessment of information edge
- Execution: bid-ask spread, liquidity, maker fee = 0 so any positive EV is worth considering, max size before market impact → trade/no-trade + sizing

**`scheduler/poly_scanner.py`** — Scan loop
- Modeled on `scheduler/crypto_scanner.py`
- 15-minute scan cycle (prediction markets move slowly)
- No time-of-day gate (prediction markets are 24/7)
- Binary Kelly formula for sizing: `f = (b*p - q) / b`
- EV filter: `EV = (p_model - p_market) * notional` must be > 0 for limit orders

### Phase 7: Market 3 — MES Futures Strategy

**`strategies/futures/mes_engine.py`** — Complete rewrite of `futures_scalper.py`
Signal hierarchy:
1. Pre-market order flow intelligence (8:00-9:30am ET):
   - Ghost orders: large limit order appears and disappears in <5 seconds without fill = institution testing liquidity. Ghost on bid → bullish. Ghost on ask → bearish.
   - Accumulation pattern: consistent dip buying in pre-market with no seller exhaustion
2. Opening Range Breakout with PULLBACK ENTRY (9:30–10:30am only):
   - Mark first 5-minute candle high/low
   - Wait for breakout above/below range
   - **DO NOT enter on initial breakout**
   - Wait for first pullback back to the breakout level
   - Enter when price bounces off the level on REDUCED volume vs. the breakout candle
   - Stop: below the breakout level
   - Target: measured move from range size projected up/down
3. Close Auction Setup (3:00–3:30pm ET only):
   - Trade with the last-hour trend
   - Institutional rebalancing creates predictable directional pressure

Hard rules (hardcoded, non-negotiable):
- Zero trades 11:30am–2:30pm ET
- Maximum 2 trades per session
- Daily goal: +6 MES points (+$30). Stop when hit.
- Daily max loss: -5 MES points (-$25). Stop when hit.
- 1 MES contract only until edge is proven
- HTF (30-minute) bias must align before any entry

AI: Run 3-agent quick debate before entry. Extended thinking exit review on every candle.

**Tradovate integration**: Wire `tradovate_broker.py` to actual demo REST API (`demo.tradovate.com/v1/`) for order simulation while keeping yfinance for pricing.

### Phase 8: AI Agent System (already mostly built, needs extension)

Current state (v9.0): 3-agent debate (Bardock/Vegeta/Krillin) + extended exits (Tudor Jones/Soros/Simons). This is the right architecture.

Changes for v5.0:
- **Parameterize the agent list** so Market 2 and Market 3 can use different agent sets through the same `debate_engine.py`
- **Market 2 agents**: Superforecaster, Information Asymmetry, Execution (new)
- **Market 3 agents**: Momentum/Risk (Tudor Jones persona), Quant (Jim Simons persona), Market Structure (Jesse Livermore persona) — same personas as the PDF spec
- **Agent state chaining** (from TauricResearch pattern): each agent should see prior agents' reasoning, not just vote independently. Refactor `analyst_agents.py` to pass accumulated state.

HIGH CONVICTION threshold (when to run full debate vs. skip to rule-based):
- Signal strength > 0.8 AND edge score > 0.6 AND time multiplier > 1.2 simultaneously → full debate
- Below these thresholds → rule-based only (saves API cost)
- Log debate_type ('full' vs 'rule_based') in `edge_snapshots` table to validate whether expensive debates outperform cheap ones after 100 trades.

### Phase 9: Dashboard Rebuild (2 modes only)

**THE KING mode (default)**
Colors: Lakers gold `#FDB927` on navy `#1D428A` on black `#000000`
Layout:
- Row 1: Giant P&L number (gold when positive, red when negative)
- Row 2: LeBron quote (rotates through 8 real documented quotes)
- Row 3: 6 metrics — account balance, today P&L, all-time P&L, rolling 20-trade WR, edge score, monthly API cost
- Row 4: Three panels side by side — one per market (current position if any, last signal, last trade)
- Row 5: Recent trades table | Recent signals feed
- Row 6: Claude AI chat widget (full context, live data injected via MCP)
- Row 7: Risk gauges — daily loss bar, position counts, watchdog status

**SAIYAN MODE button (top-right)**: toggles to Dragon Ball Z aesthetic — power levels, ki bars, Z-Fighter names. That is the only other mode.

No Film Room. No Ring Ceremony. No 4-view structure.

LeBron messages remain identical to existing implementation (real documented quotes only).

### Phase 10: Scheduler Rebuild

**`scheduler/job_runner.py`** (already 258L after v9.0 decomposition — keep this structure)
Add dispatch for Market 2 (poly_scanner) and Market 3 (MES entry/exit).

**`scheduler/poly_scanner.py`** (new) — 15-min scan cycle, 24/7
**MES logic**: Keep in `scheduler/job_runner.py` or new `scheduler/mes_scanner.py`

### Phase 11: Shared Infrastructure Changes

**`logging_db/trade_logger.py`** — Add `edge_snapshots` table:
```sql
CREATE TABLE edge_snapshots (
    id INTEGER PRIMARY KEY,
    trade_id TEXT,
    market TEXT,           -- 'crypto' | 'polymarket' | 'mes'
    v_score REAL,          -- volatility regime
    e_score REAL,          -- rolling edge quality
    d_factor REAL,         -- drawdown factor
    t_multiplier REAL,     -- time-of-day
    k_factor REAL,         -- Kelly fraction
    m_score REAL,          -- memory similarity
    final_size_usd REAL,
    debate_type TEXT,      -- 'full' | 'rule_based'
    debate_result TEXT,
    ts INTEGER
);
```

**`memory/trade_memory.py`** — Replace LanceDB with NumPy cosine similarity on SQLite BLOBs. Same public API. Eliminates 230MB dependency chain.

**Watchdog alerts** — All "Telegram alert" references → `trade_logger.write_notification()` → dashboard Notifications panel. No Telegram. No email. No external services.

**Go-live criteria** (from Phase 9 of prompt):
All 8 must be true for 3 consecutive days before dashboard shows "READY FOR LIVE TRADING":
1. Paper trading ≥ 21 consecutive days
2. ≥ 50 completed trades per active market
3. Rolling 20-trade win rate > 52% per market
4. Profit factor > 1.4 per market
5. Max single-day drawdown never exceeded 3.5%
6. Edge score > 0.55 for last 10 consecutive days
7. Claude API costs < 2% of gross paper profits
8. Zero critical errors or crashes in last 7 days

Readiness check runs daily at 4:15pm ET. Report goes to dashboard Notifications panel.

---

## WHAT WE CANNOT DO YET (Phase 10 documentation)

**Infrastructure constraints:**
- True microsecond order routing arb → unlock: co-location, $50K+ capital
- Direct sub-second Coinbase order book → unlock: paid data feed ($50-200/month)
- Cross-exchange lead-lag Binance → Coinbase → unlock: add Binance spot account
- Options trading → unlock: add tastytrade or IBKR (removed from v5.0 scope)
- Hyperliquid integration (best funding rates) → unlock: create account, fund, add API

**Capital constraints:**
- Kelly above quarter Kelly → unlock: 50+ winning trades per market
- Multiple MES contracts → unlock: $5,000 futures balance + 30-day profitable record
- Polymarket large sizing → unlock: 30 resolved contracts with validated accuracy

**Data constraints:**
- COT signal integration → unlock: CFTC free feed parser
- Social/news sentiment → unlock: free tier TheNewsMemo or similar
- Implied volatility for MES regime → **unlock NOW**: CBOE VIX available via yfinance. Wire VIX regime into `mes_engine.py` in Phase 7.

---

## SPRINT PLAN

### Sprint 1 (DONE — v9.0)
- ✅ Risk decomposition (5 modules)
- ✅ Binance broker
- ✅ job_runner decomposition (6 files)
- ✅ MCP server (15 tools)
- ✅ Claude Code agents + slash commands
- ✅ Tests (3 files, ~25 tests)

### Sprint 2 — The Math Engine (Phase 4) — ~3 days
- `risk/unified_sizer.py` (with devil's advocate gate: activate only after 20 trades/market)
- `risk/edge_monitor.py` (rolling 20-trade edge score + auto-actions → dashboard)
- `risk/volatility_regime.py` (per-market vol score + crypto funding rate gate)
- Unit tests for all 3 modules
- **Scan interval change**: 5-minute bar evaluation for crypto entries

### Sprint 3 — Crypto Engine Rebuild (Phase 5) — ~3 days
- `strategies/crypto/crypto_engine.py` (4-signal hierarchy, limit-only, 5-min bars)
- Delete `strategies/equity_momentum.py`, `scheduler/equity_scanner.py`, `data/auto_screener.py`, `execution/alpaca_broker.py`
- Update `scheduler/crypto_scanner.py` to use new engine
- Update `mcp_server/server.py` with 3 new Market 1 tools
- Backtest crypto engine against 90-day price archive

### Sprint 4 — Polymarket Integration (Phase 6) — ~5 days
- `execution/polymarket_broker.py` (from moondevonyt nice_funcs.py — saves 3-5 days)
- `data/polymarket_feed.py` (market discovery + orderbook)
- `strategies/polymarket/polymarket_engine.py` (5-signal hierarchy)
- `strategies/ai_agents/` — add 3 Polymarket-specific agents (Superforecaster, Info Asymmetry, Execution)
- `scheduler/poly_scanner.py` (15-min cycle, 24/7, binary Kelly formula)
- Add Polymarket tools to `mcp_server/server.py`
- Paper mode only until go-live criteria met

### Sprint 5 — MES Futures Engine (Phase 7) — ~4 days
- `strategies/futures/mes_engine.py` (pullback entry, NOT initial breakout)
- Wire Tradovate demo REST API in `tradovate_broker.py`
- Pre-market order flow detection (ghost orders + accumulation)
- AI confirmation: 3-agent debate before entry
- Add MES hard rules as code-enforced checks (no debate can override them)
- Add MES tools to `mcp_server/server.py`

### Sprint 6 — Agent System Extension (Phase 8) — ~2 days
- Agent state chaining (TauricResearch pattern): agents see prior agents' reasoning
- Parameterize agent list in `debate_engine.py` for per-market agent sets
- High-conviction threshold gate (full debate only when signal > 0.8 AND edge > 0.6)
- Add `debate_type` tracking to validate cost vs. outcome

### Sprint 7 — Dashboard Rebuild (Phase 9) — ~3 days
- Rewrite `dashboard/app.py`: 2 modes only (THE KING + SAIYAN toggle)
- Three-panel market view (Crypto / Polymarket / MES)
- Claude AI chat widget wired to MCP server
- Go-live readiness banner
- Replace `dashboard/terminal.py` with `--no-dashboard` stdout flag in `main.py`
- Delete `scripts/generate_system_html.py`

### Sprint 8 — Infrastructure & Cleanup (Phase 8) — ~2 days
- Replace `memory/trade_memory.py` with NumPy cosine similarity (remove LanceDB/sentence-transformers)
- Add `edge_snapshots` table to `logging_db/trade_logger.py`
- Update go-live criteria in `scripts/check_readiness.py` (all 8 Phase 9 criteria)
- Update `CLAUDE.md` completely
- Full regression test run
- Git commit: "v5.0 three-market autonomous rebuild"
- Run `setup.py`, verify clean start

### Sprint 9 — Validation (Phases 14-17)
- Run backtests on all 3 markets
- Start paper trading all 3 markets simultaneously
- Monitor go-live criteria daily (4:15pm ET dashboard report)

---

## REFERENCE ANALYSIS SUMMARY (key steals)

Full detail in `docs/REFERENCE_ANALYSIS.md`. Action items:

| Source | What to steal | Sprint |
|--------|--------------|--------|
| moondevonyt `nice_funcs.py` | Polymarket CLOB API client — saves 3-5 days | 4 |
| dylanpersonguy Polymarket | Binary Kelly formula: `f = (b*p - q) / b` | 4 |
| TauricResearch TradingAgents | Agent state chaining pattern | 6 |
| freqtrade FreqAI | IsolationForest outlier detection before ML training | ongoing |
| asavinov ensemble | Add logistic regression alongside LightGBM in ml_signal.py | ongoing |
| hummingbot OrderTracker | Order lifecycle state machine for broker reliability | ongoing |
| jesse-ai strategy interface | `should_long()` / `should_short()` pattern for new strategies | future |

**Skip entirely**: HKUDS/AI-Trader, Trade-With-Claude/cbt-framework, alsk1992/CloddsBot, kvrancic/algo-bot.

---

## ARCHITECTURAL DECISIONS

**1. Three completely separate business units**
Crypto / Polymarket / MES do not share capital pools, position limits, or signal logic. They share infrastructure (SQLite, indicators, risk modules, dashboard) but nothing else.

**2. 3-agent debate is the right default**
The 8-agent system was $0.08–0.15/debate. At $250 positions, that is 0.06% of capital per decision before any trade. The 3-agent system at $0.02/debate is the correct cost-performance tradeoff. The high-conviction gate (full debate only when signal > 0.8) is a Sprint 6 upgrade.

**3. Notifications → dashboard only**
No Telegram. No Discord. No email. All alerts → `system_events` SQLite table → dashboard Notifications panel. The system is monitored by checking the dashboard, not by receiving push notifications.

**4. Paper trading is non-negotiable until go-live criteria are met**
All three markets start in paper mode. The go-live criteria checker runs daily and reports to the dashboard. No manual override.

**5. Equity is gone**
v5.0 drops all equity trading. This removes ~3,023 lines and eliminates the equity strategy, equity scanner, Alpaca broker, and Finviz screener. The system is cleaner and more focused.

---

## RISK / CONCERNS

**1. Polymarket regulatory status**
Polymarket faced regulatory scrutiny in late 2024. Verify current operational status, US access restrictions, and API availability before Sprint 4. If Polymarket is inaccessible, Kalshi (fully US-regulated) becomes the primary Market 2 platform.

**2. Tradovate paper API requires demo account setup**
You need a Tradovate demo account and OAuth2 credentials before Sprint 5. This takes 1-2 business days. Set up the account now so Sprint 5 doesn't stall.

**3. unified_sizer.py math is only valid after 20+ trades per market**
The adaptive sizing formula uses historical metrics. Before 20 trades, all multipliers are noisy. The devil's advocate gate (`USE_ADAPTIVE_SIZING = trade_count >= 20`) must be built in from day 1.

**4. MES ghost order detection requires Level 2 data**
Detecting ghost orders needs sub-second order book updates. Tradovate's demo API provides this, but it requires WebSocket integration with the L2 feed. If the demo L2 feed is unavailable, fall back to the Opening Range Breakout strategy only until live Tradovate access is established.

---

---

## HOUSEKEEPING NOTES

**`docs/REFERENCE_REPO_ANALYSIS.md`** (791L, pre-existing from v8.0): Documents the 3-lane architecture where Lane 1 = stocks/options. Now superseded by `docs/REFERENCE_ANALYSIS.md` which targets the v5.0 3-market structure (Crypto/Polymarket/MES). No conflicts — the old doc's equity-specific findings (Claude_Prophet options patterns, Alpaca integration) are irrelevant since v5.0 drops equity. TauricResearch agent chaining is covered in both. Old doc kept for audit trail; new doc is authoritative.

**`trading-bot/` (untracked directory)**: Contains only `README.md` with text "trading bot MAIN github". Empty stub with no code. Can be deleted or left alone — no impact either way.

---

*This document is the Phase 3 presentation. Do not proceed to Sprint 2 until owner says "APPROVED" or "GO".*
*Sprint 1 is complete. The work above starts with Sprint 2.*
