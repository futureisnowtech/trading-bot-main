# GEMINI.md — Kalshi Weather Engine Operating Truth

This repository is the active Kalshi-only execution tree.

## Canonical Runtime Truth

- Repo root: `/Users/joshmacbookair2020/Projects/algo_trading_final`
- Active lane: `forecast`
- Trading mode: live-only Kalshi weather
- Exposure truth: broker-first, ledgerless
- Learning truth: RBI calibrates only on resolved labels
- Fresh-entry scope: strict true hourly weather contracts only
- Non-hourly daily weather contracts may still exist in the data universe, but they are not allowed for fresh entries

## Hard Rules

- Do not invent exchange series tickers.
- Do not assume a city has a valid `KXTEMP...` family unless it can be resolved from already-known official weather series or live Kalshi inventory.
- Keep exchange-series truth separate from city weather-data mapping.
- If a live hourly contract family cannot be resolved safely, fail closed and surface it in release status.
- Do not widen the lane to short-cadence or daily low/high just to force trades.

## Required Local Gate

Run this before treating local changes as healthy:

```bash
python3 scripts/release_audit.py --local
```

This local release audit is the canonical no-error gate. It runs:

- `compileall`
- the proof bundle
- `scripts/validate.py`
- `scripts/repo_truth_gate.py --strict`
- a bounded market scan

## Hook Installation

Install the local git hooks once per clone:

```bash
bash scripts/install_hooks.sh
```

After installation:

- every commit runs the fast truth gate plus config validation
- every push runs `python3 scripts/release_audit.py --local`

## Operator Commands

```bash
python3 sniper_cron.py
python3 execution_daemon.py
python3 telegram_daemon.py
python3 scripts/release_audit.py --local
python3 scripts/release_audit.py --remote
python3 scripts/release_audit.py --promote
python3 scripts/verify_kalshi_connection.py
```
