# The fee hurdle, and why EV_THRESHOLD is 0.120

Audited 2026-08-17. This note records where the number comes from so the next
person to touch it argues with the evidence rather than the taste.

## The problem

Across 214 settled trades the lane captured **+$7.84 of gross edge** (~3.7c per
trade) at a ~62% win rate, paid **$23.29 in exchange fees**, and realized
**-$15.45**. Fees were **296.9% of gross edge**. The signal was directionally
right and the cost structure ate it three times over.

Two independent causes, both fixed here.

## Cause 1: every fill was a taker fill

212 of 212 fills paid taker rates. `MAKER_ENTRY_ENABLED=True` was set and the
maker code path was correct, but **every** post-only order was rejected by the
exchange with `invalid_parameters`, silently falling through to the taker path.

The order body sent `time_in_force: "good_till_cancelled"`. Kalshi's enum is
`fill_or_kill | good_till_canceled | immediate_or_cancel` — **one L**. The taker
path used `immediate_or_cancel`, which is spelled correctly, so it worked, and
the maker path had never once succeeded. A one-character typo cost the entire
fee mitigation.

Maker entry roughly halves round-trip cost:

| qty | price | taker round trip | maker entry | saving |
|---|---|---|---|---|
| 1 | 0.55 | 2.96% | 1.96% | 34% |
| 2 | 0.55 | 2.96% | 1.46% | 51% |
| 4 | 0.55 | 2.59% | 1.34% | 48% |
| 15 | 0.77 | 1.87% | 0.94% | 50% |

## Cause 2: the admission gate under-billed itself

`_weather_net_edge` charged `estimate_kalshi_fee_per_contract(ask, rounded=False)`
— the raw rate, entry leg only. Both halves were wrong:

- **`rounded=False`** ignores that Kalshi ceilings the fee to the cent on the
  order total. We trade 1-4 contracts, where the ceiling roughly *doubles* the
  true per-contract fee. At `p=0.55`, raw is 1.73% but the qty=1 reality is 2.00%.
- **Entry leg only** hid the exit. The sizer already priced
  `phi = fee_in + 0.48 * fee_out`; the gate did not, so the gate and the sizer
  disagreed about what a trade costs.

Combined, the gate understated round-trip cost by **~1.2-1.7 percentage points**.
A nominal `EV_THRESHOLD` of 0.080 was really enforcing ~0.063-0.068 of true
net edge.

## The number

With the gate now honest (`rounded=True`, round trip charged), the threshold is
a real post-fee floor. True round-trip taker cost at the sizes we trade is
**2.6-3.0%**; maker entry brings it to **1.3-1.5%**.

`EV_THRESHOLD = 0.120` sets the bar at roughly **4x** the round-trip cost under
maker fills and **~4x** under taker fills at qty=1. That margin is deliberate:
the realized book captured ~3.7c per trade against a modeled edge bar of 8%,
so modeled edge has been running roughly an order of magnitude above realized
edge. The threshold has to absorb that gap, not just the fees.

## Expected cost in volume

From the live veto stream, candidates that clear the gate carry modeled EV
averaging **0.243** (min 0.078, max 0.548, n=22). Only **1 of 22** sat below
0.120, so on this sample the tightening removes ~4.5% of qualifying candidates
while roughly doubling the cost margin on the rest.

That sample is 22 rows from a 26-minute window and all of them are
`duplicate_contract_veto` rows, which is the only veto class that persists `ev`.
It is the best available evidence and it is not strong evidence. Watch the
realized entry rate for a week before concluding anything.

## What is still not measured

`forecast_resolutions` stores 76k outcomes with `q_hat` NULL on **every row**,
and `candidate_outcomes` / `edge_snapshots` / `trade_features` are empty. Until
the forecast is persisted alongside the outcome, none of the above can be
validated against realized calibration — including this threshold. That work is
deliberately not bundled into this change so its effect stays attributable.
