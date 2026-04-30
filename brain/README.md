# TRADING SYSTEM BRAIN — HUB

#active

This folder is the strategic intelligence layer for the algo trading system.
Governed by [[brain_constitution.md]].
Execution runtime defined in [[brain_execution_os.md]].

---

## WHAT THIS IS

A living markdown brain that tracks system state, parameter history, agent quality,
trade intelligence, regime patterns, and decisions — so the operator can see clearly
and the system can learn over time.

---

## DIRECTORY MAP

| Folder | Purpose |
|--------|---------|
| [[00_inbox/]] | Raw notes, unprocessed signals, staging area |
| [[01_current_system/]] | Live system state, constraints, open questions |
| [[02_strategy_library/]] | Per-strategy notes: logic, edge, status |
| [[03_parameter_sets/]] | Versioned parameter snapshots + change history |
| [[04_regimes/]] | Market regime definitions + signal behavior by regime |
| [[05_trade_reviews/]] | Notable trade post-mortems |
| [[06_daily_summaries/]] | Daily P&L, signal quality, what changed |
| [[07_weekly_summaries/]] | Weekly rollups, pattern recognition |
| [[08_agent_intelligence/]] | Per-agent evaluation: when it adds value, when it doesn't |
| [[09_research_notes/]] | Deep research highlights, signal theory, fee math |
| [[10_decisions/]] | Decision log: what was decided, why, what overrides it |
| [[11_dashboards/]] | Dashboard notes, layout decisions |
| [[templates/]] | Note templates |

---

## PRIORITY READS

- [[01_current_system/Current Active Logic.md]] — current active spot truth-lane state
- [[10_decisions/Decision Log.md]] — why key changes were made
- AGENTS.md (repo root) — canonical source of truth
- CLAUDE.md (repo root) — concise Claude-facing companion

---

## SYSTEM VERSION

**v18.15** (2026-04-30)
Active branch: `feature/v10-rebuild`
Clean paper trading started: 2026-04-02

---

## UNCERTAINTY STATUS

As of 2026-04-30: the active authoritative lane is Coinbase spot scalp governed by
the broker-first spot truth-lane contract. Other lanes remain in-repo but are
not authoritative for live spot readiness or live spot health.
Labels used throughout: CONFIRMED | TESTING | ARCHIVED | RESEARCH | ASSUMPTION

> NOTE: Notes in `brain/` folders dated 2026-03-25 or earlier reference v4.3 architecture
> and are HISTORICAL. The current canonical truth is in AGENTS.md.
