# Current Active Logic

#active #strategy

**Status as of: 2026-04-30**  
**System version: v18.15**  
**Source: runtime code + proof tests + current operator contract**

## Authoritative Live Lane

The only authoritative live lane is:

- **Coinbase spot scalp**

Everything else in the repo is either dormant, research-grade, or archived from a live-operator-truth perspective.

## Live Execution Contract

### Position truth

- Coinbase broker holdings are canonical for whether a spot position exists.
- The DB enriches that holding with lineage and strategy metadata.
- Dashboard open positions must always show broker-held exposure, even if DB lineage is missing.
- Same-symbol bot entries are blocked if the broker already holds that symbol as `external_manual`.

### Active spot statuses

Every live spot symbol must resolve to one of:

- `matched_bot_position`
- `external_manual`
- `needs_bot_repair`
- `unclassified`
- `db_only_stale`
- `qty_mismatch`
- `metadata_missing`

Truth blockers:

- `needs_bot_repair`
- `unclassified`
- `db_only_stale`
- `qty_mismatch`
- `metadata_missing`
- broker snapshot unavailable

## Spot Entry Chain

Every live spot candidate must pass this chain in order:

1. **Universe / symbol truth**
   - symbol is in the active spot universe
   - no same-symbol `external_manual` broker holding
   - no open bot-managed holding on the symbol
   - no symbol-level truth blocker

2. **Spot state availability**
   - no stale fallback in live mode
   - if `build_spot_state()` cannot produce fresh state, block the trade

3. **Regime admission**
   - allowed: `TREND`, `NEUTRAL`
   - blocked: `CHOP`

4. **Setup-family admission**
   - quarantined: `pullback_reclaim`
   - initial live-eligible families:
     - `impulse_continuation`
     - `compression_breakout`
     - `trend_resume_after_shakeout`
     - `compression_expansion_retest`

5. **Setup-score admission**
   - `impulse_continuation >= 0.62`
   - `compression_breakout >= 0.62`
   - `trend_resume_after_shakeout >= 0.60`
   - `compression_expansion_retest >= 0.64`

6. **Structural confirm admission**
   - `TREND >= 2`
   - `NEUTRAL >= 3`

7. **Frame-score admission**
   - `TREND`: `5m >= 52`, `30m >= 55`
   - `NEUTRAL`: `5m >= 55`, `30m >= 58`

8. **Derivative-quality admission**
   - `path_efficiency >= 0.20`
   - `momentum_impulse > 0`
   - `TREND`: `structure_component > 0`
   - `NEUTRAL`: `structure_component >= 0` and `participation_component > 0`

9. **Final-score admission**
   - `TREND >= 58`
   - `NEUTRAL >= 60`

10. **Route admission**
    - `maker_first` only
    - `taker_fallback` disabled
    - if maker does not fill in the allowed window, cancel the entry

11. **Economics admission**
    - projected target must be net-positive after costs
    - `projected_net_win_usd >= 2.0 * fee_usd`
    - `net_rr >= 1.25`
    - cluster must not be quarantined

## Spot Exit Contract

- stop widening: forbidden
- stopless entries: forbidden
- averaging down: forbidden

### Bound exit profiles

- `TREND` uses `precision`
- `NEUTRAL` uses `micro`
- `CHOP` is not live-eligible

### Thesis and stagnation

- thesis invalidation remains active
- stagnation exits should fire faster than old swing-style hold logic
- the design goal is to cut dead trades before fee drag compounds

## TradingView Contract

TradingView is **monitor-only**.

Allowed:
- ingest webhook payloads
- normalize/store them
- report freshness / malformed payloads
- display operator diagnostics

Not allowed:
- create candidates
- add score boost
- veto entries
- alter stops

Binding higher-timeframe context comes from the bot’s internal multi-timeframe stack.

## Dormant Lanes

These remain in-repo but are not current live operator truth:

- perp futures
- ForecastEx
- MES archived futures
- stocks

They should not define live readiness, live health, or live control-tower truth for the active spot lane.

## Current Operating Stance

- trade less
- prefer fewer setups
- prefer clearer route economics
- prefer clean broker-truth visibility over pretty dashboards
- prefer hard blockers over hopeful exceptions

