# Skill: Proof-First Validation

Use this skill whenever a task involves verification, testing, safety claims, or release confidence.

## When To Use
- After a code change
- Before saying a fix is safe
- When the user asks “did you test this?”
- When deciding the smallest honest verification set
- When reviewing whether a bug should get proof coverage

## Core Rule
Validate the narrowest meaningful thing first and report exactly what was and was not verified.

## Read First
1. `CLAUDE.md`
2. Touched files
3. Existing relevant tests in `tests/proof/`
4. `verification/replay.py` if deterministic replay is relevant

## Verification Ladder
1. Static inspection for obviously unreachable or mismatched code paths
2. Existing targeted proof tests
3. Add or update a focused proof test if the bug is behavioral and durable
4. Broader pytest selection only if needed
5. Runtime command only if tests cannot cover the important risk
6. Full paper-mode boot only for material live-path changes

## Decision Rules
- Prefer targeted proof over full-suite brute force.
- Prefer deterministic replay over ad hoc runtime inspection when both can prove the point.
- If the change is docs-only, say no runtime tests were needed.
- If a test fails for unrelated reasons, say so and separate it from the current change.

## Never Do
- Never say “tested” if you only read code.
- Never run huge validation by habit when one small proof test is enough.
- Never skip mentioning gaps.

## Output Format
- What was verified
- Exact commands/tests run
- What those checks prove
- What remains unverified

## Task Template
```text
Use the Proof-First Validation skill.

Goal:
- Choose and run the smallest truthful verification set for this task.

Requirements:
- Read `CLAUDE.md` first.
- Prefer `tests/proof/`.
- Add targeted proof coverage if the behavior deserves a permanent guardrail.
- State any remaining gaps plainly.

Return:
- Verification plan
- Commands run
- Results
- Remaining gaps
```
