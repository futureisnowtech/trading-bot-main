# Taker-only execution policy

Updated 2026-08-25 for the deployed v19.20 production path.

## Production decision

Fresh entries and exits use immediate-or-cancel taker orders. Resting maker
entry is retired from the production decision path. The strategy therefore has
one executable route to price, size, fee, and revalidate: the live taker quote.

The retained cancellation sweep is not an alternate order route. It is an
orphan-safety control that clears any resting order left by an older deployment,
an interrupted manual operation, or an exchange-state anomaly before normal
execution begins. Finding a resting order is exceptional; it must never be
treated as evidence that maker routing is active.

Historical maker fee fields may still appear in Kalshi order/fill records and
remain necessary for correct accounting of old activity. They do not authorize
new post-only orders and do not feed the current route decision.

## Why the former resting-entry lane was retired

The historical implementation had multiple independent hazards:

- the route was selected by a fill-probability approximation rather than
  observed queue performance;
- maker sizing and taker sizing followed different rails;
- a failed cancellation could leave an order resting while the fallback crossed
  as taker, creating duplicate exposure;
- maker exits conflict with the exchange's `reduce_only` IOC constraint;
- live observation showed no reliable maker fills from the attempted route.

Those are execution-complexity risks, not probability-alpha improvements. The
current small account benefits more from a single, bounded, fee-honest IOC path
than from speculative fee savings with uncertain queue and cancellation state.

## Invariants

1. No production decision may select `post_only`, GTC, or a maker-first route.
2. Every admission and final execution check uses taker fees and the executable
   taker ask for the selected YES or NO side.
3. A zero-fill IOC is a no-fill, never a position.
4. The startup orphan sweep must fail closed if resting-order state cannot be
   read or an orphan cannot be confirmed cancelled.
5. Reintroducing maker execution requires a separate governed design, live queue
   evidence, cancellation-state proofs, symmetric risk caps, and a new release
   epoch. It is not a runtime toggle.

## Archived exchange finding

Older live probes established that order creation and cancellation use different
V2 paths: creation is under `/portfolio/events/orders`, and V2 cancellation is
`DELETE /portfolio/events/orders/{order_id}`. The sweep retains the correct
cancellation path solely for orphan cleanup.
