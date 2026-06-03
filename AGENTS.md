# AGENTS.md — Algo Trading System Knowledge Base
# Auto-loaded by Codex at the start of every session.
# This file is the canonical repo memory.
# When you change runtime truth, update this file and append CHANGELOG.md.

## Canonical Truth

- Repo root: `/Users/joshmacbookair2020/Projects/algo_trading_final`
- Canonical version: `v19.1.KALSHI` (`2026-06-03`)
- Canonical active lane: **Kalshi Weather Prediction Engine** (Sovereign Precipitation)
- **Status:** **SOVEREIGN WEATHER**. Hardened Risk & Precipitation Alpha.
- **Critical Changes (v19.1.KALSHI):**
  - **Hard Architectural Isolation**: Excised all Coinbase Crypto Spot Scalp logic. The repository is now a dedicated, hardened environment for Kalshi Prediction Markets.
  - **Unified Entry Point**: Consolidated all launch paths into a single `main.py` focusing strictly on weather.
  - **Sovereign SRE Oracle**: Upgraded the Telegram AI agent to `gemini-2.0-flash` with direct technical execution mandates (Action-First).

## What This System Is Now

The repository is operationally governed as:

- **Authoritative live lane:** **Kalshi Weather Engine** (31 US Cities, GFS/ECMWF/HRRR Ensembles).
- **Active AI:** **Sovereign SRE Oracle** (Telegram agent) providing deep analysis and technical execution.
- **Current live decision standard:** ledgerless, broker-first, fee-aware, ensemble-gated.
- **Current launch target:** live-only.
- **Current dashboard authority:** HUD Dashboard API (v19.1.KALSHI).
- **Incident Response:** Grafana IRM with log-watchdog log-alerter.

## Sovereign SRE Oracle (Telegram Agent)
The Telegram bot acts as a mobile terminal for the SRE Oracle. It is authorized and mandated to:
1. **Action First**: Call tools (`execute_sql`, `read_file`, `list_files`, `replace_text`, `run_safe_command`) immediately when asked questions.
2. **Deep Reasoning**: UseSequencing tools to explore the codebase and verify system state.
3. **Technical Execution**: Fix configuration errors or adjust trading parameters via `replace_text`.
4. **Empirical Reporting**: Never speculate; only report what the data shows.

## Purged Systems
The following systems have been **removed or moved to backup**:
- **Coinbase Crypto Spot Scalp**: All brokers, indicators, and ML engines moved to `~/Desktop/crypto_scalp_backup/`.
- **v10 Crypto Runner**: Excised.
- **MES/Futures**: Archived.
- **Paper Trading**: Excised. Strictly live-only.

## Hard Safety Principles
- `AGENTS.md` is the only authoritative repo memory.
- No broad rewrites of core forecasting logic.
- No automatic resume after `HALTED`.
- Broker holdings are the only source of truth for exposure.

## Key Files (Kalshi Lane)

| File | Role |
|---|---|
| `main.py` | Unified system entry point |
| `execution/kalshi_broker.py` | Canonical broker execution (Weather-Only) |
| `forecast/runner.py` | Main execution loop (Discovery, Strategy, Monitor) |
| `forecast/strategy_engine.py` | Weather Alpha / Sizing / Alpha Gating |
| `notifications/telegram_bot.py` | Mobile HUD / Command Interface |
| `notifications/ai_agent.py` | SRE Oracle Reasoning Brain |
| `dashboard/api/server.py` | HUD Dashboard API |
| `monitoring/metrics.py` | Prometheus instrumentation |

## Operator Commands

```bash
python3 main.py
python3 -m pytest tests/proof/test_weather_sovereign.py
```

## Change Discipline
When behavior changes:
- Update `AGENTS.md`.
- Update `GEMINI.md` if workflow guidance changed.
- Prefer targeted proof tests in `tests/proof/`.
