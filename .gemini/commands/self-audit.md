---
name: self-audit
description: Evidence-first self-audit for the active spot truth-lane and repo support surfaces
argument-hint: "[--emergency|--docs-only|--runtime]"
allowed-tools:
  - Bash
  - Read
  - Glob
---

Run a self-audit against the current repo truth. This audit should challenge both code changes and support-surface drift.

## Read First

1. `AGENTS.md`
2. touched files
3. relevant proof tests
4. `scripts/check_readiness.py`
5. `scripts/live_runtime_audit.py`

## Phase 1 — Git / file evidence

```bash
git status --short
git diff --stat
git diff --name-only
```

Flag immediately if touched files include:
- `spot_engine.py`
- Broker holdings (Ledgerless v19.1)
- `runtime/spot_strategy.py`
- `scheduler/v10_runner.py`
- `monitoring/health_check.py`
- `runtime/spot_kill_switch.py`
- `scripts/go_live.py`
- `dashboard/data/positions.py`

## Phase 2 — Runtime truth evidence

```bash
python3 scripts/check_readiness.py
python3 scripts/live_runtime_audit.py
```

If `--docs-only`, you may stop here after confirming support surfaces remain aligned.

### Spot truth snapshot

```bash
python3 -c "
from execution.coinbase_spot_broker import get_spot_broker
import json
b = get_spot_broker()
b.connect()
print(json.dumps(b.sync_live_holdings(), indent=2, default=str))
"
```


Ask:
- are broker-held positions visible?
- are blockers surfaced?
- are manual holdings classified?

## Phase 4 — Support-surface drift scan

Search for stale phrases:

```bash
rg -n "7/7|raw .*mode live|TradingView.*boost|Coinbase Advanced Trade|Kraken Futures perps|paper mode on Kraken Futures perps|ready for live trading" AGENTS.md CLAUDE.md .claude claude brain scripts
```

Any hits in active support surfaces are audit failures unless clearly historical / archived.

## Phase 5 — Verification evidence

Run the narrowest relevant tests for the changed files.

If runtime-truth files changed, prefer:

```bash
python3 -m pytest tests/proof/test_spot_truth_lane_contract.py tests/proof/test_live_runtime_truth.py tests/proof/test_spot_tv_context_gate.py -q
```

## Output Format

Return exactly:
- Claim being audited
- Evidence gathered
- Drift or contradiction findings
- Verification run
- Remaining risks
- Safe next move

## Rules

- Never treat documentation as proof of behavior.
- Never treat runtime truth as healthy if support surfaces tell operators the wrong thing.
- Never accept old readiness language once the spot truth-lane contract exists.

