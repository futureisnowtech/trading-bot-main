# CLAUDE.md — Algo Trading System Knowledge Base
# Auto-loaded by Claude Code at the start of every session.
# This file IS the system memory. Keep it current.
# When you make changes: update this file AND append to CHANGELOG.md.

## What This System Is

A fully autonomous AI-powered trading system that:
- Discovers stocks/crypto/futures opportunities automatically (no watchlist)
- Runs every candidate through 8 legendary investor AI agents who debate it
- Uses extended AI reasoning (interleaved thinking) for exit decisions
- Enforces unbreakable emotional safeguards (the amygdala is removed)
- Learns from every completed trade via LanceDB vector memory
- Writes all notifications to SQLite; dashboard Notifications panel displays them
- Displays everything on a LeBron James / Dragon Ball Z themed dashboard
- Trades 100% autonomously — owner is never asked to approve anything

## Owner Profile
- Mac user (MacBook Air 2020, Python 3.14 at /Library/Frameworks/Python.framework/Versions/3.14/bin/python3)
- Starting account: $500 (equity Webull, crypto Coinbase, futures Tradovate)
- Relatively technical but wants zero day-to-day intervention
- Wants the system to WIN — everything tuned for performance
- Prefers simple explanations, hates fluff

## Current Version: v3.1
- v3.0 baseline: Extended thinking exits, LanceDB memory, regime detection,
  prompt caching, structured outputs, 4-view dashboard, position persistence,
  watchdog, auto cost tuning
- v3.1 (2026-03-22): Ops infrastructure — git, WAL mode, auto-restart,
  daily DB backups, credential backups, paper-to-live readiness tracker

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
│   ├── log_change.sh             ← Prepend entry to CHANGELOG.md
│   ├── com.algotrading.king.plist      ← launchd: auto-start + crash restart
│   ├── com.algotrading.backup.plist    ← launchd: daily backup at 2:00 AM
│   └── com.algotrading.readiness.plist ← launchd: readiness check at 7:00 AM
│
├── data/
│   ├── auto_screener.py          ← Finviz + Yahoo + SEC discovery
│   ├── market_data.py            ← yfinance, market hours
│   ├── coinbase_feed.py          ← Coinbase WebSocket + REST
│   └── indicators.py             ← MACD×4, RSI, VWAP, KST, ATR, ADX, HA
│
├── strategies/
│   ├── base_strategy.py          ← Signal dataclass + abstract base
│   ├── equity_momentum.py        ← KST+MACD+VWAP fallback (no API key)
│   ├── crypto_macd.py            ← 3-variant MACD fallback (no API key)
│   ├── futures_scalper.py        ← MES opening range breakout
│   └── ai_agents/
│       ├── analyst_agents.py     ← 8 agents with prompt caching
│       ├── debate_engine.py      ← Full (8-agent) + quick (3-agent) debate
│       ├── exit_review.py        ← Extended thinking exit decisions
│       ├── risk_synthesizer.py   ← Final go/no-go with hard rules
│       └── regime_detector.py    ← Market regime (trending/ranging/volatile)
│
├── memory/
│   └── trade_memory.py           ← LanceDB vector store (learns from history)
│
├── risk/
│   └── risk_manager.py           ← All hard rules, position tracking, persistence
│
├── execution/
│   ├── webull_broker.py          ← Stocks
│   ├── coinbase_broker.py        ← Crypto
│   └── tradovate_broker.py       ← MES futures
│
├── backtesting/
│   └── backtest_engine.py
│
├── logging_db/
│   └── trade_logger.py           ← SQLite trades.db (WAL mode) + CSV + positions
│
├── alerts/
│   └── telegram_alert.py         ← Gmail SMTP alerts (named telegram but uses email)
│
├── dashboard/
│   └── app.py                    ← 4-view dashboard: TheKing/Saiyan/FilmRoom/Ring
│
└── scheduler/
    └── job_runner.py             ← The while True engine
```

## The 8 AI Analyst Agents

| Key | Name | Philosophy |
|-----|------|------------|
| buffett | Warren Buffett | Value, moats, margin of safety |
| soros | George Soros | Reflexivity, turning points, macro |
| simons | Jim Simons | Pure quant, statistical patterns only |
| tudor_jones | Paul Tudor Jones | Momentum, risk-first, never average down |
| druckenmiller | Stan Druckenmiller | Macro momentum, paradigm shifts |
| cathie_wood | Cathie Wood | Disruptive growth, exponential curves |
| livermore | Jesse Livermore | Tape reading, price action, breakouts |
| dalio | Ray Dalio | All-weather, correlation, debt cycles |

**In Dragon Ball Z mode these are renamed:**
Buffett=Master Roshi, Soros=Cell, Simons=Android 17, Tudor Jones=Vegeta,
Druckenmiller=Piccolo, Cathie Wood=Bulma, Livermore=Goku, Dalio=Whis

## Exit Review Agents (Extended Thinking)
- Tudor Jones: "Is the stop still valid?"
- Soros: "Is the thesis still intact?"
- Simons: "Is the statistical pattern still holding?"
Any ONE saying EXIT → we exit. Asymmetric on purpose.

## The Amygdala Removal Rules (HARDCODED — NO OVERRIDE)
1. Never chase — skip if price moved >3% since signal
2. Never average down — one position per symbol, ever
3. Stop losses are sacred — never moved wider after entry
4. Wins don't justify ignoring rules on the next trade
5. Losses don't justify revenge trading or larger size
6. FOMO is not a signal
7. When in doubt, HOLD — a skipped trade costs nothing
8. The goal is being in business next month, not winning today

## Risk Rules (HARDCODED)
- 2% max account risk per trade
- 5% max daily loss → halt ALL trading, email alert
- 3 equity trades/day max (PDT cash account)
- 2 open positions max per asset class
- No equity entries 9:30–10:00 ET
- Stop loss set immediately after every fill
- Limit orders for entries, market only for emergency stops
- Fees > 1.5% of account/day → halt crypto bot

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

## LanceDB Schema
Table: trade_experiences
- id: str (uuid)
- ts: str
- symbol: str
- strategy: str
- entry_reason: str
- exit_reason: str
- outcome: float (pnl_usd)
- won: bool
- rsi_at_entry: float
- macd_hist_at_entry: float
- adx_at_entry: float
- vol_spike_at_entry: float
- regime: str
- embedding: vector[384]

## How to Start the System
```bash
cd algo_trading_final
python3 main.py                    # Full system (paper mode if .env says so)
python3 main.py --mode paper       # Force paper
python3 main.py --mode live        # Live (requires typing 'I UNDERSTAND')
python3 main.py --crypto-only      # Skip equity
python3 main.py --equity-only      # Skip crypto
streamlit run dashboard/app.py     # Dashboard on :8501
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

## Paper → Live Readiness Checker (v3.1)
Evaluates 7 criteria before flagging the system as ready for live money:
1. ≥ 14 calendar days of paper trading
2. ≥ 30 completed trades
3. Win rate ≥ 52%
4. Zero system halts in the last 7 days
5. Positive total paper P&L
6. No single day worse than -4% of account
7. Average P&L per trade ≥ $0.10

Run anytime:
```bash
python3 scripts/check_readiness.py
```
Sends an email alert automatically the first time all criteria pass in a day.
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

## Common Errors and Fixes

**webull login fails** → Check WEBULL_MFA in .env, try re-running setup.py
**Coinbase 401** → API key permissions need "Advanced Trade" scope with View+Trade
**LanceDB import error** → pip install lancedb sentence-transformers
**pandas-ta import error** → pip install pandas-ta==0.3.14b0
**Schedule not running** → Make sure nothing is blocking the while True loop
**Tradovate symbol error** → Update MES_SYMBOL in tradovate_broker.py for current quarter
**launchd not starting** → `launchctl list | grep algotrading` to check status; check logs/service/bot_error.log
**DB backup fails** → Ensure sqlite3 CLI is installed: `sqlite3 --version`

## MES Contract Symbols (update quarterly)
- Q1 (Jan-Mar): MESH5
- Q2 (Apr-Jun): MESM5
- Q3 (Jul-Sep): MESU5
- Q4 (Oct-Dec): MESZ5

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

## Claude's Standing Instructions
When making any change to this project:
1. Update CLAUDE.md if the change affects how the system works
2. Append to CHANGELOG.md: `bash scripts/log_change.sh "Description"`
3. Commit when a logical unit of work is done: `git add -p && git commit`
4. Never commit .env or logs/ — .gitignore already excludes them
5. Always use `python3`, not `python`
