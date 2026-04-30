---
name: build-strategy
description: Add or scaffold a strategy within the current repo architecture without contaminating the active spot truth-lane
argument-hint: "<strategy_name> [--lane=spot|perp|forecast|stocks|research]"
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
  - Grep
---

Build new strategy code carefully. The active live lane is spot; anything else must be clearly marked dormant, research-only, or lane-specific.

## Read First

1. `AGENTS.md`
2. the closest existing lane implementation
3. relevant proof tests

## Process

1. Identify the target lane.
2. State whether the strategy is:
   - active-lane extension
   - dormant-lane work
   - research-only
3. Reuse existing architecture before creating new scaffolding.
4. Keep all new parameters in `config.py`.
5. Update `AGENTS.md` if the repo’s active truth changes.

## Rules

- Do not silently expand the active live lane.
- Do not add indicator bloat to the spot truth-lane.
- If a strategy is not part of the active live spot lane, say so explicitly in docs/comments.

