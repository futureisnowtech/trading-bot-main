# Skill: Paper Trading Hotfix

Use this skill for the default bugfix workflow, especially when the task touches the active spot truth-lane.

## Read First
1. `AGENTS.md`
2. `CLAUDE.md`
3. directly affected modules
4. relevant `tests/proof/`

## Rules
- Prefer the actual active spot path over dormant/reference code.
- Update `AGENTS.md` when runtime truth changes.
- Update `CLAUDE.md` when Claude-facing workflow truth changes.
- Never contaminate the runtime DB during testing.

