# Skill: Release Readiness Check

Use this skill when the user wants a grounded answer to whether the system is healthy, stable, and approaching live readiness.

## When To Use
- “Are we ready to go live?”
- “How healthy is the system right now?”
- “What are the biggest blockers?”
- “Can we trust current paper results?”

## Read First
1. `CLAUDE.md`
2. `AGENTS.md` if present
3. Dashboard readiness definitions
4. Recent proof coverage / verification status
5. Relevant DB-backed metrics if the task requires a current snapshot

## Evaluate These Areas
1. Clean trade count and source boundaries
2. Win rate / profit factor on clean data only
3. Worst day and drawdown behavior
4. Economics gate veto rate and scan quality
5. Kill-switch triggers and execution failures
6. Attribution/learning integrity
7. Proof coverage on recent high-risk changes
8. Open operational blockers:
   - broker/runtime instability
   - stale schema/query drift
   - contamination risk
   - missing validation

## Classification Style
- `Not ready` — material integrity, risk, or execution blockers
- `Progressing` — architecture is sound but sample size or stability is still thin
- `Close` — clean metrics and stability are strong, with only minor blockers
- `Owner decision` — system meets informational thresholds; live switch is a human call

## Never Do
- Never call the system “ready” from vibes.
- Never use contaminated or replay data to support readiness.
- Never hide sample-size weakness.

## Output Format
- Current status
- Evidence for that status
- Biggest blockers
- Next highest-value actions

## Task Template
```text
Use the Release Readiness Check skill.

Goal:
- Assess current system readiness using clean evidence only.

Requirements:
- Read `CLAUDE.md` first.
- Use the repo’s own readiness framing.
- Separate hard blockers from informational metrics.
- State sample-size or verification weakness plainly.

Return:
- Status
- Evidence
- Blockers
- Recommended next actions
```
