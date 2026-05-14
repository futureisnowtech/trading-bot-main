# AGENTS.md — Algo Trading System Knowledge Base
# Auto-loaded by Codex at the start of every session.
# This file is the canonical repo memory.
# When you change runtime truth, update this file and append CHANGELOG.md.

## Canonical Truth

- Repo root: `/Users/joshmacbookair2020/Projects/algo_trading_final`
- Canonical version: `v18.18` (`2026-05-11`)
- Canonical active lane: **Coinbase spot scalp**
- Canonical launch path: `python3 scripts/go_live.py`
- Canonical guarded deploy path: local `./deploy.sh`
- Canonical memory order:
  1. `AGENTS.md`
  2. repo code and proof tests
  3. `brain/01_current_system/*`
  4. `GEMINI.md` as a concise companion, not the primary source of truth

## Strategic Brain

- Hub: `brain/README.md`
- Governed by: `brain_constitution.md` + `brain_execution_os.md`
- Active operator notes:
  - `brain/01_current_system/Current Active Logic.md`
  - `brain/01_current_system/Known Constraints.md`
  - **Research Finding (2026-05-04):** V14 Archival Vaccination profiles recommend tightening ADF threshold to -3.10 and adding minimum ADX floors to block 'Workhorse' leakage.
- Strategy / governance references:
  - `SCANNER_PRECISION_REPORT.md`
  - `STOP_MATRIX.md`
  - `PROFIT_GOVERNANCE.md`
  - `RUNTIME_INVARIANTS.md`
  - `DEPLOYMENT_STATE_MACHINE.md`
  - `MATRIX_DECISION_UNIVERSE.md`

## What This System Is Now

This repository still contains multiple strategy lanes and historical infrastructure, but operationally it is governed as:

- **Authoritative live lane:** Coinbase spot scalp (Two-Tower: Technical + Local ML)
- **Legacy AI:** **RETIRED**. The multi-agent debate ensemble (Goku, analyst agents, consensus-voting) has been removed to reduce latency and cost.
- **Active AI:** **Gemini Studio** (CLI intelligence/DB queries), **Anthropic Sonnet** (Optional exit thesis sanity checks), and **Sovereign Mobile Gemini** (Telegram agent with code-editing tools).
- **Current live decision standard:** truth-first, fee-aware, route-aware, evidence-gated.
- **Current launch target:** tiny live only
- **Current dashboard / readiness authority:** the spot truth-lane contract
- **Incident Response:** Grafana IRM (pushed via `monitoring/irm_reporter.py`) with OnCall escalation.

## Sovereign Mobile Operator
The Telegram bot acts as a mobile terminal for the Gemini agent. It is authorized to:
- Read and Edit codebase files (`read_file`, `replace_text`).
- Query live exposure and trade history via SQL (`execute_sql`).
- Execute safe diagnostic commands (`ls`, `git status`, `py_compile`).
- View real-time logs and system vitals.

Access is strictly restricted to the `AUTHORIZED_USER_ID`.

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

### Protected deploy automation

- GitHub deploy workflow: `.github/workflows/deploy-nyc.yml`
- GitHub deploy environment: `nyc-production`
- Auto-deploy after CI is opt-in only via repo variable `NYC_AUTO_DEPLOY_ENABLED=true`
- The protected workflow is additive and must not replace local `./deploy.sh` unless explicitly promoted later
- NYC remains a deploy target, never an authoring source

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
- Manual seed holdings (external_manual) DO NOT quarantine a symbol. The bot may execute same-symbol live trades alongside manual bags.
- always visible
- never auto-closed
- never adopted as bot-managed inventory
- do NOT block same-symbol bot entries — `_count_open_spot_positions` checks only `open_positions WHERE strategy LIKE 'spot_%'` (bot-managed rows), not broker balance (which would include manual holdings and produce false blocks)

## Tiny-Live Spot Governance

The live spot lane is intentionally harsh by default.

- Allowed regimes: `TREND`, `NEUTRAL`, `CHOP`
- Tradeable regimes: `TREND`, `NEUTRAL`, `CHOP`
- Allowed setup families for evaluation:
  - `impulse_continuation`
  - `pullback_reclaim`
  - `compression_breakout`
  - `trend_resume_after_shakeout`
  - `compression_expansion_retest`
  - `wae_momentum_explosion`
  - `breakout_volatility`
- Route: `maker_first` only
- `taker_fallback`: disabled by default
- Structural confirm minimums:
  - `TREND >= 0`
  - `NEUTRAL >= 0`
- Final score floors:
  - `TREND >= 48`
  - `NEUTRAL >= 48`
- Path efficiency minimum: `0.20`
- Frame floors:
  - `TREND`: `5m >= 40`, `30m >= 40`
  - `NEUTRAL`: `5m >= 40`, `30m >= 40`

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
| `execution/coinbase_spot_broker.py` | broker snapshot, spot orders, spot balances — raises on network error (no silent zeros) |
| `runtime/crypto_tradeability.py` | tradeability gates: position count (DB-only, bot-managed), deployed USD, dual exposure |
| `monitoring/health_check.py` | live health assertions for the spot lane |
| `runtime/spot_kill_switch.py` | lane-specific hard halt logic |
| `learning_loop.py` | close-to-snapshot path |
| `learning/post_trade_analyzer.py` | spot-native attribution semantics |
| `learning/entry_priors.py` | spot priors / target semantics |
| `learning/spot_edge_calibrator.py` | spot edge condition derivation |
| `dashboard/app.py` | single-page operator dashboard (no tabs, bot-reasoning-first, v18.15+) |
| `dashboard/data/bot_state.py` | symbol grid, decision log, bot pulse — primary dashboard data layer |
| `scripts/go_live.py` | controlled tiny-live launcher |
| `scripts/go_paper.py` | controlled return to paper |
| `scripts/check_readiness.py` | operator readiness snapshot |
| `scripts/live_runtime_audit.py` | operator runtime truth audit |

## Support-Surface Truth

- `AGENTS.md` is canonical.
- `GEMINI.md` is the concise Gemini-facing companion.
- `.gemini/commands/*` must follow the spot truth-lane contract.
- `.gemini/agents/*` must not describe the old perp-first or multi-agent-debate system as current truth.
- `gemini/skills/*` must read `AGENTS.md` first and use `GEMINI.md` as a companion only.

## Archived Notes

Legacy “open questions” content from older architectures is archived at:
- `brain/01_current_system/archive/Open Questions - legacy through 2026-04-30.md`

If you need history, use git and archived notes. Do not restore old operator assumptions into current live surfaces.

## Operator Commands

```bash
python3 main.py --mode paper
python3 scripts/go_live.py
python3 scripts/check_readiness.py
python3 scripts/live_runtime_audit.py
python3 scripts/go_live_audit.py
python3 scripts/net_truth_audit.py
python3 -m pytest
streamlit run dashboard/app.py --server.runOnSave true
```

## sell_blocked Recovery (v18.19)

When a spot symbol fails three consecutive sell attempts with the same broker
error code (e.g. `INSUFFICIENT_FUND`), `spot_engine.close_spot` flags the
position with `sell_blocked=1` and emits one Telegram alert. The DB row is
retained — the bot will neither retry the sell nor enter the symbol again until
the flag is cleared manually.

To recover after resolving the underlying issue on Coinbase (close the locked
limit order, transfer funds, etc.):

```sql
-- replace SOL with the halted symbol
UPDATE open_positions
SET sell_blocked = 0,
    sell_failure_count = 0,
    sell_blocked_reason = ''
WHERE symbol = 'SOL';
```

Diagnostic context is captured at `logger.warning` level on each failure under
`[spot_engine] sell_failure <SYMBOL>` — grep `logs/bot.log` for the broker
balance snapshot and DB row state that triggered the halt.

## Change Discipline

When behavior changes:
- update `AGENTS.md`
- update `GEMINI.md` if Gemini workflow guidance changed
- append `CHANGELOG.md`
- prefer targeted proof tests in `tests/proof/`
