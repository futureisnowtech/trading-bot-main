# TODO — The King's Algo Trading System
**Last updated: 2026-03-25 | System: v5.3**

This file tracks:
1. **Manual tasks** — things only YOU can do (API keys, platform setup, human review)
2. **In Progress** — active development work
3. **Future Ideas** — strategy + system improvements to build later

---

## 🔑 MANUAL TASKS (Require Your Action)

### API Keys & Credentials
- [ ] **Bybit testnet API key** — go to bybit.com → Account → API Management → Create Key
  Add `BYBIT_API_KEY=` and `BYBIT_API_SECRET=` to `.env`
  (`BYBIT_TESTNET=true` is already set — testnet first, live later)
- [ ] **CryptoPanic API key** — free tier at cryptopanic.com/developers/api/
  Add `CRYPTOPANIC_API_KEY=` to `.env`
  (Without this, news feed falls back to CoinDesk RSS — still works but lower quality)
- [ ] **Alpaca API key** — for when equity gets re-enabled (account > $2,500)
  Free at alpaca.markets → Dashboard → API Keys
  Add `ALPACA_API_KEY=` and `ALPACA_API_SECRET=` to `.env`
- [ ] **TradingView Webhook Secret** — set `TV_WEBHOOK_SECRET=` in `.env` to a random string
  Then update the matching "Webhook Secret" input in your Pine Script indicator on TV

### Platform Setup
- [ ] **Test Bybit testnet** — after adding keys, start bot and check logs for:
  `run_perp_scan()` output and any `bybit_broker.py` entries
  Confirms perp paper trading is actually running
- [ ] **TradingView webhook end-to-end test**:
  1. `python3 scripts/tradingview_webhook.py` (keep running in separate terminal)
  2. `ngrok http 8765` (keep running — free tier URL changes every restart)
  3. Paste ngrok HTTPS URL into TradingView alert → Webhook URL
  4. Trigger a test alert → verify `system_events` table gets a row with `source='tradingview'`
- [ ] **MES contract rollover** — update `MES_SYMBOL` in `execution/tradovate_broker.py`
  MESM6 (June 2026) → MESU6 (Sep 2026) when front month rolls

### Paper Trading Milestones (in order)
- [ ] **Run `replay_signals.py`** — seeds Bayesian priors with REAL regime-bucketed data
  `python3 scripts/replay_signals.py --days 90` (run once before extended paper trading)
  This is better than `seed_intelligence.py` — uses real regime labels (trending/ranging/volatile)
  not 'unknown' which live trading never reads from
- [ ] **Run bot for ≥14 days paper** — minimum before considering live
  `python3 main.py --mode paper`
- [ ] **Check readiness** after 14 days:
  `python3 scripts/check_readiness.py`
  Criteria: 14 days + 30 trades + 52% WR + no halts + positive P&L + no day worse than -4%
- [ ] **Review per-signal win rates** from signal leaderboard in daily brain summaries
  Decide which Tier 2b indicators (v4.3: SuperTrend, Ichimoku, WAE, Fisher, CHOP, WaveTrend, LaguerreRSI)
  actually improve results vs add noise — demote/remove losers before going live

### Before Going Live
- [ ] Switch `PAPER_TRADING=false` in `.env`
- [ ] Switch `BYBIT_TESTNET=false` in `.env` and use real Bybit API key
- [ ] Confirm Coinbase API key has `View + Trade` permissions on Advanced Trade
- [ ] Run `python3 main.py --mode live` and type `I UNDERSTAND` to confirm
- [ ] Watch first 3 live trades manually — verify fills match paper expectations

---

## 🔄 IN PROGRESS — AI-First System Rework

**Goal**: Make AI the primary signal generator, not just a debate filter on top of math gates.
The math scoring becomes *context* the AI sees, not a threshold gate that blocks debate.

### Core Architectural Change
Current flow: `math pre-filter → if score ≥ 30 → AI debate → execute`
Target flow: `fetch data + context → AI sees everything → AI decides entry/size/timing → execute`

The conviction score becomes an *input to the AI prompt* not a gate.
The AI learns what score levels correspond to good vs bad outcomes via Bayesian feedback.

### Specific Changes Needed
- [ ] **Remove conviction gate as hard floor** — instead of `if conviction < 30: skip`,
  pass conviction score + active signals as context to the AI debate.
  Let agents weigh the signals themselves.
- [ ] **Inject full indicator snapshot into every debate prompt** — all 19 signal states,
  their values (not just fired/not fired), and Bayesian win rates per signal from `signal_stats`
- [ ] **Session analyst output → every debate** — session_bias + conviction_threshold_multiplier
  is currently only applied as a numeric floor adjustment. Should be injected as readable
  context ("London session, BULLISH bias, news risk: MEDIUM, macro: RISK_ON")
- [ ] **Wire `market_context.py` should_block_trade() to run BEFORE debate**
  Currently the context is built but the blocking gate may not be in the hot path
- [ ] **Agent accuracy feedback loop** — `agent_stats` tracks per-agent accuracy.
  Inject each agent's historical accuracy into their own system prompt:
  "Your last 50 calls: 62% accurate. Your BUY calls win 58% of the time."
  Agents with low accuracy should have their votes down-weighted in moderator synthesis.
- [ ] **Commit v5.2 + v5.3 changes** — 30+ new files are untracked. Commit before continuing.

---

## 💡 FUTURE IDEAS (Build When Ready)

### Strategy
- [ ] **Perp SHORT strategy refinement** — current entry: 20-bar breakdown + RSI<45 + funding≥0.01%
  Test whether funding rate threshold matters (backtest SHORT-only with/without funding gate)
- [ ] **Session-specific parameter sets** — London session (3-8am ET) has higher breakout probability.
  Use tighter conviction floor + higher position size during London window only.
- [ ] **Regime-gated position sizing** — in RISK_ON macro + BULLISH session, scale position size
  from $250 → $350. In RISK_OFF, scale down to $150. Currently size is flat.
- [ ] **Correlation-aware position management** — when BTC + ETH both signal BUY simultaneously,
  they're highly correlated. Don't double-count as 2 independent positions. Treat as 1.5x.
- [ ] **Equity re-enable criteria** — define threshold: account > $2,500 + 30-day positive track
  record + Sharpe > 0.8. Add to check_readiness.py as an advisory output.
- [ ] **Futures paper simulation quality** — review `execution/tradovate_broker.py` paper logic.
  ES bid/ask spread is ~0.25 pts (~$12.50). Current simulation may ignore spread cost.
- [ ] **Tax-loss harvesting automation** — `tax_tracker.py` detects harvesting opportunities.
  Wire this into exit_review.py: when a position is at a loss AND a harvesting window exists,
  Tudor Jones/Soros see "TAX NOTE: harvesting window open — consider realizing this loss."

### Learning / Intelligence
- [ ] **Signal fatigue detection** — if the same signal fires 3+ consecutive candles on the same
  symbol with no trade, flag as "signal fatigue" and raise the threshold on that symbol for 30min.
  Prevents stacking conviction on stale conditions.
- [ ] **Per-regime parameter auto-tune** — after 100+ trades, use Bayesian posteriors to
  automatically adjust per-signal conviction weights by regime. Right now weights are hardcoded.
  Dynamic weights system (learning/dynamic_weights.py) is built but only uses simple scaling.
- [ ] **Agent disagreement signal** — when agents disagree heavily (3 BUY vs 2 HOLD), that itself
  is information. Track outcomes of "contested" vs "consensus" debates. Contested entries may have
  lower win rates — if so, require full consensus (4 of 5) before entering contested setups.
- [ ] **Win streak / loss streak adaptive sizing** — during a 3+ trade win streak in TRENDING regime,
  scale up 20%. During a 3+ trade losing streak, cut size 50% automatically (independent of
  the circuit breaker which pauses entirely).

### Infrastructure
- [ ] **Conviction score logging per scan** — add a row to `system_events` for every symbol scanned
  showing the conviction score breakdown (not just debate-callers). Answers: are most symbols
  scoring 0-20 and nothing is firing, or are there frequent 30-50 scores?
- [ ] **Per-agent vote logging** — save each agent's individual BUY/HOLD/SELL verdict to SQLite.
  Required to answer: are agents ever usefully disagreeing or do they mostly all agree?
  `agent_stats` table exists but may not be getting per-debate vote data.
- [ ] **Dashboard: AI-First view** — add a 5th dashboard view (or replace Film Room) that shows:
  live signal state for each symbol, conviction score breakdown, session context, macro regime,
  and the last AI debate reasoning. One screen showing why the system is doing what it's doing.
- [ ] **ngrok auto-restart** — TradingView webhook requires ngrok running. Free tier URL changes
  on restart, breaking the TradingView alert. Either: pay for ngrok static domain ($8/mo),
  or build a script that auto-updates the TradingView alert via TV API when ngrok restarts.
- [ ] **Backtest the full v5.3 pipeline** — `replay_signals.py` backtests math signals only.
  Need a full AI debate backtest: replay historical data through actual Claude API calls.
  Expensive (API cost) but gold standard for validating the full stack before live trading.

### Risk Management
- [ ] **Dynamic daily loss limit** — current: flat 4% ($20). Consider: tighten to 2% on losing
  days, loosen to 5% on winning streaks. Markets are non-stationary; adaptive limits make sense.
- [ ] **Cross-platform correlation halt** — if crypto + perp both hit losses on the same day,
  that's a signal of a macro regime shift. Halt ALL new entries (not just per-strategy circuit
  breaker) when 3+ strategies lose on the same calendar day.

---

## ✅ COMPLETED (for reference)

- [x] v5.3 AI Session Analyst — fires at Asia/London/NY opens, sets session_bias + cv_multiplier
- [x] v5.2 Goku agent — 9th agent, absolute veto/boost, runs after all other agents
- [x] v5.2 Data feed layer — news_feed.py + macro_feed.py + market_context.py
- [x] v5.2 Tax tracking — Section 1256 detection, YTD summary, harvesting opportunities
- [x] v5.0 Self-improving brain — Bayesian signal stats, post-trade analyzer, dynamic weights
- [x] v4.3 7 new indicators — SuperTrend, Ichimoku, WAE, Fisher, CHOP, WaveTrend, LaguerreRSI
- [x] v4.2 TradingView webhook integration
- [x] v4.0 De-risk overhaul — all params cut 50%, RSI/Hurst removed as entry gates
- [x] v3.9 8-signal pre-filter + ATR fee-floor guard
- [x] v3.8 Bybit perp integration + EDEADLK fix
- [x] replay_signals.py — correct Bayesian seeding with real regime buckets (built, not yet run)
- [x] EDEADLK fix (Python 3.14 + launchd) via boot.py + launcher.py
