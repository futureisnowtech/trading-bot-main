# Skill: Release Readiness Check

Use this skill when the user wants a grounded answer to whether the **active spot truth-lane** is healthy, safe, and eligible for tiny live.

## Read First
1. `AGENTS.md`
2. `CLAUDE.md`
3. `scripts/check_readiness.py`
4. `scripts/live_runtime_audit.py`
5. `scripts/go_live_audit.py`

## Evaluate
1. Runtime state machine truth
2. Spot broker-truth health
3. Spot truth blockers
4. Learning / attribution freshness
5. Route / fee / expectancy evidence
6. Proof coverage on recent high-risk changes

## Never Do
- Never use old `7/7` readiness language.
- Never call the lane ready from generic paper metrics alone.
- Never ignore broker-held exposure mismatches.

