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

- [[01_current_system/Current Active Logic.md]] — best-known live state
- [[03_parameter_sets/Param Set - v4.3 Active.md]] — current signal gates + conviction thresholds
- [[10_decisions/Decision Log.md]] — why key changes were made
- [[08_agent_intelligence/Agent Panel.md]] — current 5-agent panel + roles

---

## SYSTEM VERSION

BELIEVED: v4.3 (TradingView integration + 7 new indicators)
Last confirmed commit: ff5782d — 2026-03-25

---

## UNCERTAINTY STATUS

As of 2026-03-25: No live paper trading data yet. All notes are derived from code
inspection and changelog history — not from observed trading outcomes.
Labels used throughout: CONFIRMED | BELIEVED | TESTING | RETIRED | ASSUMPTION
