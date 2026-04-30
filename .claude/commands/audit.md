---
name: audit
description: Evidence-based audit for the active Coinbase spot truth-lane
argument-hint: "[--period=24h|7d|30d]"
allowed-tools:
  - Read
  - Bash
  - Glob
---

Run a full audit of the **active spot truth-lane**, not the old mixed-lane stack.

## Read First

1. `AGENTS.md`
2. `scripts/net_truth_audit.py`
3. `scripts/go_live_audit.py`
4. `scripts/check_readiness.py`
5. `scripts/live_runtime_audit.py`

## Process

### 1. Runtime truth

```bash
python3 scripts/check_readiness.py
python3 scripts/live_runtime_audit.py
```

### 2. Net performance truth

```bash
python3 scripts/net_truth_audit.py
python3 scripts/go_live_audit.py
```

### 3. Spot truth snapshot

```bash
python3 -c "
from runtime.spot_position_truth import get_spot_position_truth
import json
print(json.dumps(get_spot_position_truth(paper=False), indent=2, default=str))
"
```

### 4. Candidate / setup / route truth

Use DB queries or existing reports to answer:
- what setups actually traded
- what routes actually executed
- what clusters are quarantined
- what symbols are suppressed or probationary

### 5. Learning / monitoring truth

Check:
- spot attribution freshness
- spot feature snapshot freshness
- spot kill switch state
- latest health check status

## Output Format

- Runtime truth verdict
- Performance truth verdict
- Position truth verdict
- Learning / monitoring verdict
- Biggest blockers
- Recommended next actions

## Rules

- Never use old MCP-only agent metrics as the primary truth.
- Never treat dormant-lane status as equal to the active spot lane.
- Never call the system ready if runtime truth and audit truth disagree.

