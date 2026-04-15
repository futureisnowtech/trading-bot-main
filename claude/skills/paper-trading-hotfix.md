# Skill: Paper Trading Hotfix

Use this skill for the default bugfix workflow in this repository, especially when the task touches the live paper-trading path.

## When To Use
- A runtime bug, logic bug, integration bug, or regression needs a fix.
- A paper-trading behavior is wrong, unstable, or inconsistent with repo truth.
- A small-to-medium patch is needed and the user expects implementation, not just analysis.

## Read First
1. `CLAUDE.md`
2. `AGENTS.md` if present
3. The directly affected module(s)
4. Existing targeted tests, especially under `tests/proof/`

## Operating Principles
- Prefer the actual live path over legacy/reference files.
- Read before editing.
- Make the smallest safe fix that resolves the proven root cause.
- Treat core live files as high-risk.
- Do not change thresholds, sizing, or risk rules casually.
- Avoid touching `DO NOT TOUCH` files unless the evidence says the bug lives there.

## Never Do
- Never claim a cause without tracing the real path.
- Never pad the fix with unrelated cleanup.
- Never skip `CHANGELOG.md`.
- Never say something is verified if you did not run the check.
- Never contaminate paper/live learning boundaries.

## Workflow
1. Restate the bug in one sentence.
2. Identify the real execution path and the exact module/function involved.
3. Read the smallest set of files needed to prove the root cause.
4. Decide whether the bug is:
   - code logic
   - schema/query mismatch
   - stale docs/memory mismatch
   - data contamination / bad assumptions
   - external dependency/runtime edge case
5. Implement the narrowest fix.
6. Add or update targeted proof coverage if the behavior matters long-term.
7. Run the smallest relevant verification set.
8. Update `CLAUDE.md` and `AGENTS.md` if system behavior or workflow truth changed.
9. Append to `CHANGELOG.md` with `bash scripts/log_change.sh "..."`.

## Preferred Verification Order
1. Targeted proof tests in `tests/proof/`
2. Narrow unit tests for the touched module
3. Focused runtime command only if necessary
4. `python3 main.py --mode paper` only when the change touches live runtime behavior materially

## Output Format
- Root cause
- What changed
- Verification run
- Residual risk

## Task Template
```text
Use the Paper Trading Hotfix skill.

Goal:
- Fix the reported bug with the smallest safe patch.

Requirements:
- Read `CLAUDE.md` first.
- Trace the real live path before editing.
- Prefer targeted proof tests.
- Update repo memory if behavior changes.
- Append `CHANGELOG.md`.

Return:
- Root cause
- Files changed
- Verification run
- Residual risk
```
