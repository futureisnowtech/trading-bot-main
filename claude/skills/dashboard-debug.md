# Skill: Dashboard Debug

Use this skill for Streamlit dashboard bugs, confusing metrics, missing counts, broken widgets, or mismatches between dashboard output and underlying system truth.

## When To Use
- A widget crashes or shows nonsense
- Counts do not match logs/DB
- Labels or captions contradict runtime behavior
- A panel shows stale or inflated incidents
- A schema/query mismatch is suspected

## Read First
1. `CLAUDE.md`
2. `dashboard/app.py`
3. Relevant file(s) under `dashboard/data/`
4. Relevant widget module(s) under `dashboard/widgets/`
5. Any source tables used by the widget/query

## Debug Order
1. Find the visible widget/render function.
2. Trace it backward into its data function/query.
3. Identify the source table(s), filters, and grouping logic.
4. Compare the query assumptions to the real live schema.
5. Check event wording, status filters, timestamp field names, and contamination/integrity exclusions.
6. Fix the narrowest layer that is wrong:
   - widget wording
   - data adapter/query
   - schema assumption
   - startup/log event wording mismatch

## Common Failure Modes In This Repo
- Dashboard query assumes a stale schema
- Wrong timestamp column
- Counting rows instead of distinct incidents
- Including integrity-excluded attribution rows
- Pulling contaminated sources into clean metrics
- Widget text drifting from actual runtime wording

## Never Do
- Never treat the dashboard as source-of-truth without checking its query path.
- Never patch only the display if the data function is wrong.
- Never ignore contamination/source filters in performance/readiness views.

## Output Format
- User-visible symptom
- Broken layer
- Root cause
- Fix
- Verification

## Task Template
```text
Use the Dashboard Debug skill.

Goal:
- Fix the dashboard discrepancy at the correct layer.

Requirements:
- Read `CLAUDE.md` first.
- Trace widget -> data function -> SQL/table -> source-of-truth.
- Check schema assumptions, distinct-incident logic, and contamination/integrity filters.

Return:
- Symptom
- Root cause
- Files changed
- Verification
```
