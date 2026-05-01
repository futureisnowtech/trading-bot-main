# AGENTS.md — Algo Trading System Knowledge Base
# Auto-loaded by Codex at the start of every session.
# This file is the canonical repo memory.
# When you change runtime truth, update this file and append CHANGELOG.md.

## Canonical Truth

- Repo root: `/Users/joshmacbookair2020/Projects/algo_trading_final`
- Canonical version: `v18.16` (`2026-04-30`)
- Canonical active lane: **Coinbase spot scalp**
- Canonical launch path: `python3 scripts/go_live.py`
- Canonical memory order:
  1. `AGENTS.md`
  2. repo code and proof tests
  3. `brain/01_current_system/*`
  4. `CLAUDE.md` as a concise companion, not the primary source of truth

## Strategic Brain

- Hub: `brain/README.md`
- Governed by: `brain_constitution.md` + `brain_execution_os.md`
- Active operator notes:
  - `brain/01_current_system/Current Active Logic.md`
  - `brain/01_current_system/Known Constraints.md`
  - `brain/01_current_system/Open Questions.md`
- Strategy / governance references:
  - `SCANNER_PRECISION_REPORT.md`
  - `STOP_MATRIX.md`
  - `PROFIT_GOVERNANCE.md`
  - `RUNTIME_INVARIANTS.md`
  - `DEPLOYMENT_STATE_MACHINE.md`
  - `MATRIX_DECISION_UNIVERSE.md`

## What This System Is Now

This repository still contains multiple strategy lanes and historical infrastructure, but operationally it is governed as:

- **Authoritative live lane:** Coinbase spot scalp
- **Current live decision standard:** truth-first, fee-aware, route-aware, evidence-gated
- **Current launch target:** tiny live only
- **Current dashboard / readiness authority:** the spot truth-lane contract

The following systems remain in-repo but are **not authoritative** for live spot health, readiness, deployment counts, or operator truth:

- Coinbase nano perp futures
- ForecastEx
- MES archived futures
- stocks lane
- older multi-lane regime language
- legacy readiness scripts based on generic paper metrics alone

They are preserved for research, later reactivation, or historical context. They must not be allowed to override the spot lane’s broker-first truth.

## Owner Profile

- Mac user (MacBook Air 2020)
- Python: `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3`
- Prefers simple explanations and zero fluff
- Wants live-capital protection over activity
- Current live cash / holdings must be treated as broker truth, not hardcoded config

## Current Operational Contract

### Active lane

- Venue: Coinbase spot
- Direction: long-only
- Focus: fewer, cleaner, fee-cleared spot scalps
- Live mode target: `TINY_LIVE`

### Dormant / reference lanes

- `perps`
- `forecast`
- `mes_archived`
- `stocks`

These may remain visible in archival, research, or engineering contexts, but they are not allowed to define live spot readiness or live spot health.

## Spot Truth-Lane Contract

`runtime/spot_position_truth.py` is the canonical truth service for live spot exposure.

Broker truth decides:
- whether a spot holding exists
- current quantity
- current deployed notional
- current broker cash

Database truth enriches:
- lineage
- setup family
- setup score
- route
- stop / target profile
- learning linkage

Every live spot symbol must classify to exactly one status:
- `matched_bot_position`
- `external_manual`
- `needs_bot_repair`
- `unclassified`
- `db_only_stale`
- `qty_mismatch`
- `metadata_missing`

Current seeded `external_manual` holdings:
- `BTC`
- `ETH` (covers broker-normalized staked ETH exposure)
- `LTC`
- `SOL`
- `XRP`
- `ADA`
- `MANA`
- `CLOV`
- `STETH`

Rules for `external_manual` holdings:
- always visible
- never auto-closed
- never adopted as bot-managed inventory
- same-symbol bot entries are blocked while they exist

## Tiny-Live Spot Governance

The live spot lane is intentionally harsh by default.

- Allowed regimes: `TREND`, `NEUTRAL`
- Blocked regime: `CHOP`
- Hard quarantine: `pullback_reclaim`
- Allowed setup families for evaluation:
  - `impulse_continuation`
  - `compression_breakout`
  - `trend_resume_after_shakeout`
  - `compression_expansion_retest`
- Route: `maker_first` only
- `taker_fallback`: disabled by default
- Structural confirm minimums:
  - `TREND >= 2`
  - `NEUTRAL >= 3`
- Final score floors:
  - `TREND >= 58`
  - `NEUTRAL >= 60`
- Path efficiency minimum: `0.20`
- Frame floors:
  - `TREND`: `5m >= 52`, `30m >= 55`
  - `NEUTRAL`: `5m >= 55`, `30m >= 58`

Exit profile contract:
- stop widening: forbidden
- stopless entry: forbidden
- `TREND` target profile: `precision`
- `NEUTRAL` target profile: `micro`
- faster stagnation / thesis invalidation is preferred over wider patience

## TradingView Contract

TradingView is **monitor-only** for the active live lane.

Allowed:
- webhook ingestion
- payload normalization
- storage in `tv_signals`
- freshness / malformed-payload monitoring
- operator visibility

Not allowed:
- candidate injection
- direct entry trigger
- score boost
- veto of otherwise valid spot entries
- stop logic influence

Binding higher-timeframe context comes from the bot’s own internal stack:
- `SuperTrend`
- `Ichimoku`
- `KST`
- `MACD`
- structural confirms
- `5m / 30m / 4h / 1d` internal state

## Readiness State Machine

Canonical readiness states:
- `NOT_READY`
- `READY_FOR_TINY_LIVE`
- `TINY_LIVE`
- `DEGRADED`
- `HALTED`

Readiness promotion is controlled by runtime truth, not documentation.

Live launch must fail if any of these are true:
- broker spot snapshot unavailable
- `unclassified` holding exists
- `needs_bot_repair` holding exists
- unresolved `qty_mismatch`
- unresolved `metadata_missing`
- spot truth blockers present
- spot learning freshness broken
- stop / scanner / governance blockers active

## Hard Safety Principles

- no broad rewrite
- no new signal bloat
- no fake readiness claims
- no fake “learning is healthy” language
- no live spot persistence from paper-style order artifacts
- no raw `python3 main.py --mode live`
- no live launch outside `scripts/go_live.py`
- no automatic resume after `HALTED`
- no hiding live holdings because the DB is confused

## Key Files For The Active Lane

| File | Role |
|---|---|
| `runtime/spot_position_truth.py` | canonical broker-first spot truth |
| `spot_engine.py` | spot execution lifecycle, stop/target persistence, close reconciliation |
| `runtime/spot_strategy.py` | setup / regime / score / route governance |
| `scheduler/v10_runner.py` | scan loop, admission path, runtime lane state |
| `execution/coinbase_spot_broker.py` | broker snapshot, spot orders, spot balances |
| `monitoring/health_check.py` | live health assertions for the spot lane |
| `runtime/spot_kill_switch.py` | lane-specific hard halt logic |
| `learning_loop.py` | close-to-snapshot path |
| `learning/post_trade_analyzer.py` | spot-native attribution semantics |
| `learning/entry_priors.py` | spot priors / target semantics |
| `learning/spot_edge_calibrator.py` | spot edge condition derivation |
| `dashboard/data/positions.py` | broker-truth holdings rendering |
| `dashboard/data/control_tower.py` | live control surfaces, narrowed to spot authority |
| `scripts/go_live.py` | controlled tiny-live launcher |
| `scripts/go_paper.py` | controlled return to paper |
| `scripts/check_readiness.py` | operator readiness snapshot |
| `scripts/live_runtime_audit.py` | operator runtime truth audit |

## Support-Surface Truth

- `AGENTS.md` is canonical.
- `CLAUDE.md` is the concise Claude-facing companion.
- `.claude/commands/*` must follow the spot truth-lane contract.
- `.claude/agents/*` must not describe the old perp-first or multi-agent-debate system as current truth.
- `claude/skills/*` must read `AGENTS.md` first and use `CLAUDE.md` as a companion only.

## Archived Notes

Legacy “open questions” content from older architectures is archived at:
- `brain/01_current_system/archive/Open Questions - legacy through 2026-04-30.md`

If you need history, use git and archived notes. Do not restore old operator assumptions into current live surfaces.

## Operator Commands

```bash
python3 main.py --mode paper
python3 scripts/go_live.py
python3 scripts/go_paper.py
python3 scripts/check_readiness.py
python3 scripts/live_runtime_audit.py
python3 scripts/go_live_audit.py
python3 scripts/net_truth_audit.py
python3 -m pytest
streamlit run dashboard/app.py --server.runOnSave true
```

## Change Discipline

When behavior changes:
- update `AGENTS.md`
- update `CLAUDE.md` if Claude workflow guidance changed
- append `CHANGELOG.md`
- prefer targeted proof tests in `tests/proof/`
