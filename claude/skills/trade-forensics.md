# Skill: Trade Forensics

Use this skill when the user asks why a trade happened, why it lost, why it exited, or why the dashboard/logs show a surprising trade-related outcome.

## When To Use
- “Why did this trade enter?”
- “Why did this position exit?”
- “Why did we lose money on this symbol?”
- “Why does the dashboard show this trade/result?”
- “Was this trade valid or contaminated?”

## Read First
1. `CLAUDE.md`
2. `AGENTS.md` if present
3. The trade path modules likely involved
4. Relevant DB tables and event logs

## Likely Evidence Sources
- `scanner.py`
- `signal_engine.py`
- `scheduler/v10_runner.py`
- `position_manager.py`
- `perps_engine.py`
- `learning_loop.py`
- `learning/post_trade_analyzer.py`
- `dashboard/data/*.py`
- SQLite tables in `logs/trades.db`

## Typical Tables To Inspect
- `trades`
- `open_positions`
- `trade_features`
- `trade_attribution`
- `system_events`
- Any execution/failure-related tables used by the dashboard path

## Workflow
1. Identify the exact symbol, approximate time, and event type:
   - entry
   - exit
   - sizing
   - veto
   - attribution
   - dashboard render discrepancy
2. Reconstruct the chain in order:
   - candidate generation
   - setup / score decision
   - economics pass/veto
   - position sizing
   - execution and persistence
   - exit logic
   - attribution / learning
   - dashboard aggregation
3. Anchor each claim to evidence:
   - code path
   - DB row
   - event wording
   - test coverage if relevant
4. Separate these clearly:
   - what definitely happened
   - what is inferred from the evidence
   - what remains uncertain
5. Call out integrity exclusions, contaminated sources, replay/synthetic rows, or suspect-price safeguards if present.

## Never Do
- Never answer from intuition alone.
- Never ignore data-source contamination.
- Never treat dashboard output as source-of-truth without checking its query path.

## Useful SQL Questions
- What trades exist for the symbol/time window?
- What notes/metadata were saved on entry and exit?
- Is there a matching `trade_ref`?
- Was attribution excluded with an `INTEGRITY EXCLUDE:` lesson?
- Was the source clean paper, live, replay, or contaminated?

## Output Format
- Timeline of what happened
- Root cause or explanation
- Evidence used
- Confidence and remaining uncertainty

## Task Template
```text
Use the Trade Forensics skill.

Goal:
- Reconstruct exactly why the trade or trade-related event happened.

Requirements:
- Read `CLAUDE.md` first.
- Trace the full path through code and SQLite evidence.
- Distinguish facts from inference.
- Call out contamination or integrity exclusions explicitly.

Return:
- Timeline
- Explanation
- Evidence
- Remaining uncertainty
```
