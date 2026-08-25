# Taker fee hurdle and admission policy

Updated 2026-08-25 for the deployed v19.20 production path.

## Evidence behind the hurdle

The audited historical sample contained 214 settled trades: approximately
$7.84 gross edge, $23.29 exchange fees, and -$15.45 net realized PnL. Fees were
about 296.9% of gross edge. The signal was directionally useful, but the
captured edge did not cover execution cost and model error.

The same audit found 212 of 212 fills paid taker rates. Maker attempts did not
produce a dependable alternative, and resting-order execution has now been
retired. Production economics must therefore be viable under taker fees alone.

## Fee accounting invariant

The admission gate and final execution boundary must both use the ceiled Kalshi
taker fee for the actual order quantity. Raw per-contract rates understate small
orders because Kalshi rounds the order fee up to the cent. Entry-only fee math
also understates a strategy that expects to exit before settlement, so the model
must include the configured exit-cost assumption rather than silently treating
the second leg as free.

No maker discount belongs in production opportunity scoring. Maker fee fields
are retained only to reconcile historical fills accurately.

## Why `EV_THRESHOLD = 0.120`

Observed round-trip taker cost at the small quantities this account trades was
roughly 2.6-3.0%. A 12% modeled post-fee edge floor leaves a substantial buffer
for probability error, price movement between evaluation and submission, and
the historical gap between modeled and captured edge.

The threshold is an admission floor, not a profitability claim. Final execution
must re-fetch the selected-side quote, re-price fees and expected value, re-clamp
quantity to every capital rail, and veto the order if the live opportunity no
longer clears the same hurdle.

## What remains outcome-dependent

The 12% value is conservative but not yet proven optimal. RBI 2.0 needs a full
current-epoch learning window with exact pre-trade probabilities and official
settlements before the threshold can be tuned responsibly. Until then, lower
fees are not assumed and the threshold must not be weakened from short-window
candidate counts.
