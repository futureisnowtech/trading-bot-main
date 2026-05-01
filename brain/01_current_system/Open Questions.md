# Open Questions

#active

**Status as of: 2026-04-30**  
**Scope: active Coinbase spot truth-lane only**

Legacy open questions are archived at:
- `brain/01_current_system/archive/Open Questions - legacy through 2026-04-30.md`

## Current Open Questions

### Q1: Which setup/regime cluster earns promotion from `PROBATION` to `ALLOWED` first?
- **Context**: The spot lane is intentionally harsh. `pullback_reclaim` is quarantined, `CHOP` is blocked, and tiny live must remain constrained until at least one cluster proves positive post-fee expectancy.
- **Why it matters**: Tiny live should be evidence-promoted, not vibes-promoted.
- **Resolution**: Use replay + fresh live sample + `SCANNER_PRECISION_REPORT.md` / `PROFIT_GOVERNANCE.md` evidence to promote the first cluster.

### Q2: When should any `external_manual` holding be reclassified into bot-managed inventory?
- **Context**: Current holdings are intentionally treated as manual/external and blocked from bot reuse.
- **Why it matters**: Reclassification changes whether the bot may manage or re-enter those symbols.
- **Resolution**: Only after explicit operator decision plus repaired canonical lineage for that holding.

### Q3: Do any current spot setup families besides the quarantined one deserve tighter symbol-level suppression?
- **Context**: The lane now blocks the obvious weak species, but per-symbol setup risk may still differ materially.
- **Why it matters**: Tiny live should suppress weak clusters before they become live fee burn.
- **Resolution**: Re-evaluate rolling symbol × setup × regime expectancy after new clean closes accumulate.

### Q4: When is there enough fresh evidence to replace conservative static spot suppressions with more dynamic governance?
- **Context**: The lane now defaults to harsh static protections plus rolling governance.
- **Why it matters**: Dynamic rules should only take over when samples are strong enough.
- **Resolution**: Define explicit minimum sample sizes and positive post-fee expectancy thresholds for promotion.

### Q5: How far should we continue narrowing operator surfaces for dormant lanes?
- **Context**: Dormant lanes remain in-repo, but operator truth should stay spot-first.
- **Why it matters**: Confused surfaces create false readiness and false health.
- **Resolution**: Continue hiding or labeling dormant-lane surfaces unless there is a live operational need.

### Q6: Should TradingView webhook infrastructure remain enabled long-term if it stays `monitor_only`?
- **Context**: TV no longer carries live decision weight.
- **Why it matters**: Operational plumbing that adds no measurable value should justify its upkeep.
- **Resolution**: Keep it if monitoring value is real; retire it if payload health adds noise without operator benefit.

