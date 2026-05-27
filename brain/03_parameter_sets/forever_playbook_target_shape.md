# Forever Playbook Target Shape

## Router buckets
- Carry Major
- Clean Trend Alt
- Explosive Convex Alt
- Reflexive/Meme/Unstable
- Mean-Reversion Eligible
- Do Not Trade

## Governance statuses
- Promoted
- Allowed
- Constrained
- Blocked
- Research only

## Required doctrine outputs
- Bucket classification rules
- Spot/perp routing rules
- Funding modifiers
- Multi-timeframe state machine
- Exit-by-bucket guidance
- Learning segmentation boundaries
- Launch-state ladder
- Tonight-only `$500` operating profile
- Tonight allowlist / blocklist
- Carry suitability table
- Safety labels on implementation scope (`safe to wire now`, `audit/document only tonight`, `too risky for tonight`)

## Candidate additive modules
- `market_type_classifier`
- `symbol_governance_registry`
- `funding_aware_instrument_router`
- `forever_playbook_audit`
- `funding_carry_audit`
- proof tests for routing and governance

## Preferred artifact paths
- `brain/10_decisions/forever_playbook_v1.md`
- `brain/03_parameter_sets/market_type_router_v1.md`
- `brain/03_parameter_sets/symbol_governance_v1.md`
- `scripts/forever_playbook_audit.py`
- `scripts/funding_carry_audit.py`
- `tests/proof/test_forever_playbook_rules.py`

## Implementation philosophy
- Prefer additive helpers over broad rewrites
- Honor existing integrity hotfixes and audit logic
- Use local DB truth first
- Treat funding as a holding modifier, not a standalone signal
- Keep high-risk live-path edits minimal unless overwhelmingly justified
- Preserve candidate-journal / shadow-labeling infrastructure
- Avoid disturbing running services for this task
