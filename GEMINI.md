# GEMINI.md — Gemini CLI Companion

This file is a concise companion for Gemini-facing workflows.

## Read Order

1. `AGENTS.md` — canonical repo memory
2. touched code / proof tests
3. `brain/01_current_system/*`
4. this file

If this file ever conflicts with `AGENTS.md`, `AGENTS.md` wins.

## Current Repo Truth

- Canonical repo: `/Users/joshmacbookair2020/Projects/algo_trading_final`
- Current version: `v18.16`
- Authoritative live lane: **Coinbase spot scalp**
- Launch target: **tiny live only**
- Canonical live-launch path: `python3 scripts/go_live.py`
- Canonical return-to-paper path: `python3 scripts/go_paper.py`

## What Gemini Should Assume

- Broker truth is canonical for live spot holdings.
- `runtime/spot_position_truth.py` is the first-class truth layer for open exposure.
- `TradingView` is `monitor_only` for live spot.
- `CHOP` is blocked for the live spot lane.
- `maker_first` is the only default live route.
- Dormant lanes remain in-repo but are not authoritative for live spot readiness or health.

## What Gemini Should Not Assume

- Do not assume old `7/7 readiness` language is still valid.
- Do not assume older multi-lane control surfaces reflect current truth.
- Do not assume raw `main.py --mode live` is an acceptable launch path.
- Do not assume dashboard position cards are correct unless they reconcile to broker truth.
- Do not assume TradingView has live edge authority.

## Default Workflow

1. Read `AGENTS.md`
2. Read the directly affected code path
3. Read the closest relevant proof tests
4. Patch the smallest truthful layer
5. Run the smallest meaningful verification
6. Update `AGENTS.md` / `CHANGELOG.md`

## High-Value Entry Points

- `runtime/spot_position_truth.py`
- `spot_engine.py`
- `runtime/spot_strategy.py`
- `scheduler/v10_runner.py`
- `monitoring/health_check.py`
- `runtime/spot_kill_switch.py`
- `scripts/go_live.py`
- `scripts/check_readiness.py`
- `scripts/live_runtime_audit.py`

## Support-Surface Rule

When editing Gemini helper surfaces:
- point them at `AGENTS.md` first
- keep them aligned to the spot truth-lane contract
- prefer concise operator truth over historical detail

