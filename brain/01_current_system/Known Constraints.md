# Known Constraints

#active

**Status as of: 2026-04-30**  
**Scope: active Coinbase spot truth-lane only**

## Account / Truth Constraints

| Constraint | Value | Notes |
|---|---|---|
| Canonical open-position truth | Broker holdings | DB cannot hide live exposure |
| Broker cash | Observational only | Read from broker snapshot, never hardcode |
| Current external/manual symbols | BTC, ETH, LTC, SOL, XRP, ADA, MANA, CLOV, STETH | Visible, blocked from bot reuse, never auto-closed; ETH covers broker-normalized staked ETH |
| Spot truth blockers | `unclassified`, `needs_bot_repair`, `db_only_stale`, `qty_mismatch`, `metadata_missing`, broker snapshot unavailable | Any blocker keeps tiny live unavailable |

## Active Spot Lane Constraints

| Constraint | Value |
|---|---|
| Direction | LONG only |
| Allowed regimes | `TREND`, `NEUTRAL` |
| Blocked regime | `CHOP` |
| Quarantined setup | `pullback_reclaim` |
| Default route | `maker_first` |
| Taker fallback | Disabled |
| Stop widening | Never |
| Stopless entries | Never |
| Same-symbol duplicate exposure | Never |
| TradingView decision weight | None (`monitor_only`) |

## Binding Entry Floors

| Item | TREND | NEUTRAL |
|---|---:|---:|
| Structural confirms | 2 | 3 |
| Final score floor | 58 | 60 |
| 5m frame floor | 52 | 55 |
| 30m frame floor | 55 | 58 |
| Path efficiency | 0.20 | 0.20 |

## Exit Constraints

| Item | Value |
|---|---|
| TREND target profile | `precision` |
| NEUTRAL target profile | `micro` |
| Faster dead-trade handling | Required |
| Hidden override | Forbidden |
| Averaging down | Forbidden |

## Readiness Constraints

Canonical readiness states:

- `NOT_READY`
- `READY_FOR_TINY_LIVE`
- `TINY_LIVE`
- `DEGRADED`
- `HALTED`

Launch constraints:

- launch through `python3 scripts/go_live.py` only
- raw `python3 main.py --mode live` is not acceptable
- `READY_FOR_TINY_LIVE` must already be true before controlled launch
- launch must fail if broker truth or spot truth blockers fail

## Health Constraints

The active spot lane is unhealthy if any of these are stale or broken:

- broker spot snapshot
- spot truth service
- spot attribution freshness
- spot feature snapshot freshness
- route integrity
- scanner governance integrity
- deployment-state integrity
- kill-switch status truth

## TradingView Constraints

- keep webhook operational if needed
- keep secret validation and payload integrity
- keep freshness / malformed-payload reporting
- do not use TradingView to boost, veto, or trigger spot entries

## Dormant-Lane Constraints

Perps / forecast / MES / stocks may remain in the repo, but:

- they are not authoritative for active live health
- they are not authoritative for spot readiness
- they are not allowed to override spot position truth
- their old regime language should not leak into active spot operator guidance

## Amygdala Rules

These remain hard constraints:

1. Never chase
2. Never average down
3. Stop losses are sacred
4. Wins do not justify breaking the next rule
5. Losses do not justify revenge risk
6. FOMO is not a signal
7. When in doubt, hold fire
8. Staying alive matters more than being right today
