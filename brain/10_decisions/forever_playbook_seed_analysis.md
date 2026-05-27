# Forever Playbook Seed Analysis

## Why this exists
This is not the final answer.
It is a seed packet of repo-grounded findings that should be re-verified and then integrated into the final Forever Playbook v1.

## Tonight's real operating context
- Crypto spot and crypto perps are going live tonight
- Futures stay paper-only
- Real starting live bankroll is `$500`
- Older repo memory about `$5,000` or `$10,000` should not override tonight's live sizing doctrine

## Trust-aware performance truth
- Trustworthy closes: 229
- Gross PnL: +12.72
- Fees: -12.99
- Net PnL: -0.27
- Long net: +13.56
- Short net: -13.82
- trailing_stop net: +12.06
- hard_stop net: -42.36
- thesis_invalidated net: -12.19

Interpretation:
- The system is not clearly net positive after fees yet on trustworthy rows.
- Longs materially outperform shorts.
- trailing_stop is the only clearly positive exit type.
- hard_stop and thesis_invalidated should not be treated as proof of alpha.

## Candidate universe truth
Observed from the local DB:
- `scan_candidates`: 6503 rows
- Distinct symbols: 55

This suggests the system should not be optimized around a tiny handpicked subset only.

## Higher-relevance deep-dive basket already identified
- BTC
- ETH
- SOL
- NEAR
- ZEC
- LINK
- AVAX
- TON
- TAO
- MORPHO

## Prior 365d / derivative-style impressions to re-check
- NEAR looked like one of the cleaner trend continuation structures
- LINK looked cleaner on alignment than expected
- MORPHO looked strong on higher frames but not ideal for breakout chasing
- ZEC looked strong but unstable
- TAO often looked like a trap when lower frames rolled over
- meme / reflexive names should likely be governed separately or blocked

## Prior funding/carry impressions to verify with current data
- BTC / ETH / SOL could be the best perp carry-long candidates when funding pays longs
- NEAR / LINK / MORPHO / ZEC often made more sense as spot-first longs when perp funding was hostile

## Architectural implication
The durable upgrade is not "add more indicators."
The durable upgrade is:
- market-type routing
- symbol governance
- spot vs perp instrument routing
- timeframe-aware setup doctrine
- funding-aware hold doctrine
- learning segmentation by market type

## Practical implication for implementation
- Separate the forever doctrine from tonight's `$500` operating profile
- Preserve integrity / audit / candidate-journal systems already in place
- Prefer additive helpers and audits over risky live-path rewrites tonight
