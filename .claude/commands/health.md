---
name: health
description: Fast spot truth-lane health check
argument-hint: ""
allowed-tools:
  - Bash
  - Read
---

Run a quick health check for the **active Coinbase spot truth-lane**.

## Read First

1. `AGENTS.md`
2. `scripts/live_runtime_audit.py`
3. `runtime/spot_position_truth.py`

## Process

### 1. Process and mode

```bash
ps aux | grep "main.py" | grep -v grep
python3 -c "from runtime.runtime_state import get_system_state; print(get_system_state())"
```

### 2. Spot truth snapshot

```bash
python3 -c "
from runtime.spot_position_truth import get_spot_position_truth
import json
print(json.dumps(get_spot_position_truth(paper=False), indent=2, default=str))
"
```

### 3. Runtime audit

```bash
python3 scripts/live_runtime_audit.py
```

### 4. Kill-switch state

```bash
python3 -c "
from runtime.spot_kill_switch import kill_switch_status
import json
print(json.dumps(kill_switch_status(), indent=2))
"
```

## Output

Return:
- process state
- launch readiness state
- spot truth snapshot health
- blocker count
- external/manual holdings visibility
- kill-switch state
- verdict: `HEALTHY`, `DEGRADED`, `HALTED`, or `NOT_READY`

## Rules

- Health is about the active spot lane first.
- A dashboard that looks fine but hides broker-held positions is **not healthy**.
- A system with broker snapshot failure or spot truth blockers is **not healthy**.

