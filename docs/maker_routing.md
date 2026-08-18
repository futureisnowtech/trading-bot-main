# Why maker fills never happen, and what has to be true before fixing it

Written 2026-08-17. This is a diagnosis, not a change. Nothing here is deployed.

## The observation

Fees consumed 296.9% of gross edge (214 settled trades, +$7.84 gross, $23.29
fees, -$15.45 net). 212 of 212 fills paid taker rates.

Two independent causes. The first is fixed (see `docs/fee_hurdle.md`): the
post-only order body misspelled the time-in-force as `good_till_cancelled`, so
every maker attempt was rejected with `invalid_parameters`.

**That fix alone changes nothing**, because of the second cause below. Only
**3 maker attempts were ever made across 645 positions** (~0.5%). The typo
explains why those 3 failed. It does not explain why there were only 3.

## The second cause: the router almost never selects maker

`evaluate_contract` picks a route by expected utility:

```python
expected_u_M = zeta * u_M
...
if expected_u_M > u_T and expected_u_M > 0.0:
    best_is_taker = False
else:
    best_is_taker = True
```

`best_is_taker` becomes `is_taker_override`, which
`kalshi_execution_controller.py:96` turns into `order_type="market"`, which makes
`place_buy_order`'s `if maker_enabled and order_type == "limit"` false. The maker
path is then skipped **without logging anything at all** — absence of `[Maker]`
lines means the router skipped it, presence means it was attempted.

`zeta` is `estimate_zeta()`: `exp(-spread/vol) * (1 - exp(-tau_hours/12))`. It is
not fill history — it is a spread/volatility/time model. The time term dominates
for the same-day contracts this lane trades:

| tau to resolution | 1h | 2h | 4h | 8h | 14h | 24h |
|---|---|---|---|---|---|---|
| zeta (spread .02, vol .05) | 0.05 | 0.10 | 0.19 | 0.33 | 0.46 | 0.58 |

Scoring a missed maker fill as `zeta * u_M` treats it as **zero utility**. That is
not what the code does — `_try_maker_entry` cancels at the timeout and crosses as
taker, so a miss still gets the trade. The correct expectation is roughly

    zeta * u_M + (1 - zeta) * u_T * (1 - adverse_selection)

Under the current scoring, a normal entry (`u_M/u_T ~ 1.55`, maker being both a
better price and ~4x cheaper in fees) routes to **taker at every realistic zeta**.
Under the corrected scoring it routes maker from zeta ~0.04 up.

## Why this was NOT shipped

Changing the route changes which **sizing path** runs, and the two are not
equivalent:

```python
# taker
n_T = calculate_continuous_sizing(..., cap_pct=0.10, conv_tier=3,
                                  hours_to_res=..., lane_ev_threshold=0.05,
                                  book_asks=..., position_cap_usd=position_cap_usd)
# maker
f_star_M, phi_M, n_M = solve_optimal_size(q, p_M, maker=True, bankroll=..., ...)
```

`n_M` never receives `position_cap_usd`, and skips `conv_tier`,
`lane_ev_threshold`, `hours_to_res` and book depth. **The maker route bypasses
`KALSHI_MAX_USD_PER_POSITION`.** A proof test caught this immediately
(`test_weather_no_side_sizing_uses_no_probability` returned 242 contracts where
the taker path returns 7).

On the droplet's current limits the breach is *latent*, because
`KALSHI_MAX_QTY_PER_POSITION=15` clamps first:

| maker price | n_M | after qty clamp | notional |
|---|---|---|---|
| 0.10 | 149 | 15 | $1.50 |
| 0.40 | 35 | 15 | $6.00 |
| 0.60 | 21 | 15 | $9.00 |
| 0.85 | 6 | 6 | $5.10 |

Max $9.00 against a $10 cap — safe today, but only by coincidence of a different
cap, and it breaches on repo defaults (`MAX_QTY=2500`). This sizing path has never
executed in production.

## What has to be true first

1. `n_M` must respect `position_cap_usd` and the same lane gates as `n_T` —
   ideally by routing maker sizing through `calculate_continuous_sizing` with
   `maker=True` rather than calling `solve_optimal_size` directly.
2. A proof test asserting the maker route cannot exceed
   `KALSHI_MAX_USD_PER_POSITION` at any price.
3. Then the routing change, with the adverse-selection haircut as a named
   constant, and `_MAKER_MIN_HOURS_TO_RES` so the last hour keeps the
   conservative scoring.
4. Watch `[Maker]` lines for a day. Fill rate against predicted `zeta` is the
   first real calibration data this system will have about its own execution.

Expected payoff once done: round-trip cost falls from ~2.6-3.0% to ~1.3-1.5% on
routed trades. Fees are the binding constraint on this lane, so this is the
single largest P&L lever available — which is exactly why it deserves its own
change with its own proof, not a rushed bundle.

---

# Live API findings, 2026-08-18

Verified by placing real orders against the production API and cancelling them.
Both were silent-inert failures: the code would have "worked" while doing nothing.

## 1. post-only GTC is accepted (the TIF fix is real)

```
REQUEST  time_in_force=good_till_canceled post_only=True stp=taker_at_cross
RESPONSE error_code=(none)  status=resting
```

The `good_till_canceled` spelling fix is confirmed end to end. `post_only` and
`self_trade_prevention_type=taker_at_cross` coexist fine.

## 2. cancel_order was pointed at a DEPRECATED endpoint

```
DELETE /trade-api/v2/portfolio/orders/{id}
  -> 410 {"code": "deprecated_v1_order_endpoint"}
DELETE /trade-api/v2/portfolio/events/orders/{id}
  -> 200 {"order_id": ..., "reduced_by": "1.00"}   <-- correct
```

Orders are CREATED on `/portfolio/events/orders`; the bare `/portfolio/orders`
cancel is the v1 path. `cancel_order` returned False and **the order stayed
resting on the book**. Every code path that rests an order relies on this to
clean up, so before this fix each maker attempt would have orphaned an order
permanently. This is why the orphan sweep exists as well as the endpoint fix:
belt and braces.

Note the GET paths (`/portfolio/orders/{id}`, `?status=resting`) are NOT
deprecated and still work. Only the DELETE was on the v1 route.

## 3. reduce_only CANNOT be combined with post-only -- maker exits are blocked

```
REQUEST  action=sell reduce_only=True post_only=True tif=good_till_canceled
RESPONSE 400 invalid_order: "reduce_only can only be used with IoC orders"
```

post-only requires GTC; `reduce_only` requires IoC. They are mutually exclusive,
so **an exit cannot rest without giving up `reduce_only`** -- the flag that stops
a sell from opening a short if the position moves underneath it.

This matters because entry-only maker cannot reach breakeven:

| scenario | fees | net |
|---|---|---|
| today (all taker) | $25.44 | -$17.32 |
| entry maker only | ~$15.90 | -$7.78 |
| entry + exit maker | ~$6.36 | +$1.76 |

Breakeven needs a ~68% fee cut; entry-only delivers ~37%. So the exit leg is not
optional for profitability, and the only route to it is dropping `reduce_only`
and replacing that guarantee in-process:

1. cap sell qty at the live position size before placing,
2. re-check the position on every poll of the rest loop and cancel the instant it
   drops below the resting qty,
3. accept a worst case of a few contracts of short exposure for one poll interval.

That is a designed change with a real (if small) new risk, so it is not bundled
here. Until it is done, maker work cannot make this lane profitable on its own.

## 4. v2 position rows use fixed-point string fields

`market_positions[].position_fp` (e.g. `"-1.00"`), not `position`. Reading
`position` returns None and silently looks like a flat book. Production
(`kalshi_broker.py:861`) already reads `position_fp` correctly; ad-hoc scripts
are where this bites.
