# Skill: Learning Integrity Audit

Use this skill for anything involving attribution correctness, trade feature snapshots, clean-vs-contaminated data, replay exclusions, Bayesian stats, or ML training integrity.

## When To Use
- Attribution looks wrong
- Signal win rates seem corrupted
- A short trade PnL sign looks suspicious
- `trade_features` / entry snapshot reload is in question
- Replay/synthetic rows may be leaking into learning
- Clean training boundaries need audit

## Read First
1. `CLAUDE.md`
2. `learning_loop.py`
3. `learning/post_trade_analyzer.py`
4. `learning/signal_performance.py`
5. `scheduler/v10_runner.py`
6. `ml/walk_forward_trainer.py`
7. Relevant proof tests in `tests/proof/`

## High-Risk Integrity Checks
- Direction-aware PnL correctness for shorts
- Presence and stability of `trade_ref`
- Entry snapshot reloaded from `trade_features` instead of exit-state features
- Missing signal lineage fail-closes
- Suspect-price exclusions
- Replay/synthetic source exclusions
- Contaminated data excluded from clean training/performance paths

## Workflow
1. Identify the exact integrity question.
2. Trace where the data originates and where it is consumed.
3. Check exclusion/fail-closed logic before checking downstream stats.
4. Confirm clean-vs-contaminated source boundaries.
5. Inspect whether the dashboard/reporting layer respects the same exclusions.
6. Add or update proof coverage for any integrity rule that can regress.

## Never Do
- Never “fix” downstream metrics before proving the upstream truth.
- Never allow ambiguous lineage to silently update Bayesian stats.
- Never mix replay, synthetic, contaminated, or suspect-price rows into clean learning claims.

## Output Format
- Integrity question
- Actual data path
- Failure or confirmation
- Safeguard status
- Verification

## Task Template
```text
Use the Learning Integrity Audit skill.

Goal:
- Verify or repair the integrity of the learning/attribution path.

Requirements:
- Read `CLAUDE.md` first.
- Trace source data through attribution, snapshots, training, and reporting.
- Prefer fail-closed behavior.
- Add proof coverage for any integrity rule that changes.

Return:
- Integrity finding
- Root cause or confirmation
- Files changed
- Verification
```
