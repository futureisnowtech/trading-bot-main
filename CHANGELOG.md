# CHANGELOG
All notable changes to The King's Algo Trading System.
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
