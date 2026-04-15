# Forever Playbook Acceptance Checklist

A valid result must include:

- A real symbol-bucket taxonomy
- Explicit spot vs perp routing rules
- Explicit funding/carry handling
- Explicit 1d / 4h / 30m / 5m doctrine
- Explicit symbol governance statuses
- Explicit exit-role doctrine
- Explicit learning segmentation doctrine
- Repo-specific implementation paths
- Evidence from local DBs and local chart cache
- Clear separation between trusted truth and low-trust / dirty evidence
- Concrete "stop doing this" recommendations
- Additive scripts or modules that make the doctrine executable or auditable
- Proof tests for the implemented logic
- A separate tonight-only `$500` operating profile
- An allowlist / blocklist for tonight
- A spot vs perp bankroll split for tonight
- A symbol-by-symbol carry suitability view
- A clear label on each proposed implementation: `safe to wire now`, `audit/document only tonight`, or `too risky for tonight`

A weak result is any result that:
- suggests more indicators without routing logic
- treats all symbols the same
- ignores fees
- ignores funding
- ignores integrity filtering
- ignores instrument choice
- ignores timeframe disagreement
- ignores the difference between safety exits and alpha exits
- stops at a memo without implementing support in the repo
- assumes stale account-size memory is the live bankroll tonight
