# CHANGELOG
All notable changes to The King's Algo Trading System.

---

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
