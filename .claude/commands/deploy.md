---
name: deploy
description: Controlled deployment workflow for paper mode, paper recovery, and spot tiny-live preflight
argument-hint: "[--mode=paper|live]"
allowed-tools:
  - Read
  - Bash
  - Glob
---

Use the controlled deployment path only. Raw `main.py --mode live` is never the correct answer.

## Read First

1. `AGENTS.md`
2. `scripts/go_live.py`
3. `scripts/go_paper.py`
4. `scripts/check_readiness.py`
5. `scripts/live_runtime_audit.py`

## Process

### 1. Parse mode

- Default mode: `paper`
- `--mode=live` means: prepare a **tiny-live** launch only

### 2. Validate environment

```bash
python3 scripts/validate.py
```

Stop immediately if validation fails.

### 3. Inspect current runtime truth

```bash
python3 scripts/check_readiness.py
python3 scripts/live_runtime_audit.py
```

For live deployment, do not proceed unless:
- runtime state is already `READY_FOR_TINY_LIVE`
- spot truth blockers are zero
- broker snapshot is healthy
- spot kill switch is not halted

### 4. Backup runtime DB

```bash
bash scripts/backup_db.sh
```

### 5. Launch

Paper:
```bash
python3 scripts/go_paper.py
```

Live tiny mode:
```bash
python3 scripts/go_live.py
```

### 6. Post-launch verification

Run:

```bash
python3 scripts/live_runtime_audit.py
python3 scripts/check_readiness.py
```

If live launch succeeds, runtime truth should show:
- `process_mode=live`
- crypto lane `connected=1`
- readiness `TINY_LIVE`

### 7. Output

Return:
- mode requested
- exact command run
- readiness before launch
- readiness after launch
- spot truth blocker summary
- whether launch is paper / tiny live / failed

## Rules

- Never use raw `python3 main.py --mode live`
- Never claim live readiness from old `7/7` language
- Never skip broker-truth checks
- Never treat dormant lanes as blockers for the active spot lane unless they directly contaminate live truth

