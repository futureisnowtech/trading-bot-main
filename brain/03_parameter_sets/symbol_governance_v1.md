# Symbol Governance Registry v1
**Grounded:** 2026-04-15 | Source: `strategies/symbol_governance.py`
**Evidence basis:** 229 clean_paper_v10 closes + price_archive.db structure

---

## Status Definitions

| Status | Entry | Size | Condition to enter |
|--------|-------|------|--------------------|
| **PROMOTED** | Yes | 100% | n ≥ 15, exp > +$0.30, net > 0 |
| **ALLOWED** | Yes | 100% | Standard — adequate evidence, no systematic failures |
| **CONSTRAINED** | Yes | 25–75% | Thin sample, deep drawdown, or marginal evidence |
| **BLOCKED** | No | 0% | n ≥ 8 AND exp < −$0.30; or market type = blocked |
| **RESEARCH_ONLY** | No | 0% | Duplicate, broken PF symbol, or no doctrine |

---

## Current Registry

### PROMOTED (full size, exceeds evidence bar)

| Symbol | Bucket | Evidence | Tonight |
|--------|--------|----------|---------|
| PF_ZECUSD | CLEAN_TREND_ALT | n=14, net=+$5.41 | **Paper only** (PF) |

### ALLOWED (standard operation)

| Symbol | Bucket | Max Size | Notes |
|--------|--------|----------|-------|
| BTC | CARRY_MAJOR | 100% | 4h BULL; primary carry vehicle |
| ETH | CARRY_MAJOR | 100% | 4h BULL; strong 30d |
| SOL | CARRY_MAJOR | 100% | 4h BULL; pullback ongoing |
| BNB | CARRY_MAJOR | 100% | 30d +12.8% |
| NEAR | CLEAN_TREND_ALT | 100% | 4h BULL; spot-first |
| LINK | CLEAN_TREND_ALT | 100% | 4h BULL; spot-first |
| AVAX | CLEAN_TREND_ALT | 100% | 4h BULL; spot-first |
| TON | CLEAN_TREND_ALT | 75% | 4h BULL but deep pullback |
| XRP | CARRY_MAJOR | 75% | Low vol; spot-first |
| ZEC | CLEAN_TREND_ALT | 100% | 30d +30.8%; 4h BEAR now — wait |
| PF_ETHUSD | CARRY_MAJOR | 100% | n=7 net=+$2.20; paper only |
| PF_SOLUSD | CARRY_MAJOR | 100% | n=6 net=+$2.44; paper only |
| PF_XBTUSD | CARRY_MAJOR | 100% | n=4 net=+$1.95; paper only |
| PF_NEARUSD | CLEAN_TREND_ALT | 100% | n=4 net=+$1.87; paper only |

### CONSTRAINED (reduced size, longs only, conditions apply)

| Symbol | Bucket | Max Size | Condition |
|--------|--------|----------|-----------|
| MORPHO | CLEAN_TREND_ALT | 50% | 4h BEAR tonight; enter only on 4h turn |
| TAO | EXPLOSIVE_CONVEX | 50% | PF_TAOUSD n=11 net=−$2.49; 4h BEAR; only on confirmed BULL 4h |
| ENA | EXPLOSIVE_CONVEX | 50% | rvol=95.6%; 90d −55.6% |
| ZRO | EXPLOSIVE_CONVEX | 50% | n=3 net=+$13.23; too thin (likely one big win) |
| LIT | EXPLOSIVE_CONVEX | 50% | n=2 too thin; rvol=120% |
| RENDER | CLEAN_TREND_ALT | 50% | n=4 net=+$0.26; thin |
| ADA | CLEAN_TREND_ALT | 50% | PF_ADAUSD blocked; raw ADA marginal; 90d −39% |
| SUI | CLEAN_TREND_ALT | 50% | 90d −47.3%; deep drawdown |
| UNI | CLEAN_TREND_ALT | 50% | 90d −40.6% |
| CRV | CLEAN_TREND_ALT | 50% | 90d −49%; extreme downtrend |
| FET | EXPLOSIVE_CONVEX | 50% | n=7 net=−$2.24; negative evidence |
| JTO | EXPLOSIVE_CONVEX | 50% | n=4 net=−$1.77 |
| XMR | CLEAN_TREND_ALT | 50% | All econ-vetoed in scanner; fee/spread issues |
| DOGE | MEAN_REVERSION | 75% | Ranging only; tight fee gate |
| AXS | MEAN_REVERSION | 50% | Thin evidence; ranging |
| HEMI | REFLEXIVE_MEME | 25% | n=13 net=−$1.01; near-blocked |
| PENGU | CLEAN_TREND_ALT | 25% | Meme-adjacent; 90d −43% |
| PF_XRPUSD | CARRY_MAJOR | 75% | Paper only |
| PF_AVAXUSD | CLEAN_TREND_ALT | 50% | n=2 net=−$5.34; very thin, alarming |
| PF_LINKUSD | CLEAN_TREND_ALT | 75% | Paper only |
| PF_XMRUSD | CLEAN_TREND_ALT | 50% | Paper only; econ-vetoed underlying |
| PF_SUIUSD | CLEAN_TREND_ALT | 50% | n=3 net=+$1.60; thin sample |

### BLOCKED (no new trades)

| Symbol | Reason |
|--------|--------|
| PF_ADAUSD | **n=17 net=−$6.94** — worst performer with adequate sample |
| ALGO | **n=11 net=−$3.06** — systematic loser |
| VVV | **n=23 net=−$4.15** — worst by sample size |
| PF_TAOUSD | **n=11 net=−$2.49** — TAO PF version blocked |
| HYPE | n=5 net=−$2.59; go-live audit watch-list |
| DOT | 1y −67%, 90d −45%; all econ-vetoed; persistent loser |
| MON | n=4 net=−$2.46; high loss per trade |
| TRUMP | Reflexive/meme; n=3 net=−$1.96 |
| WLFI | Reflexive/meme; 90d −52% |
| PUMP | Reflexive/meme by design |
| VIRTUAL | Reflexive/meme; AI narrative |
| FARTCOIN | rvol=122%; trend_eff=0.039; pure noise |
| SPX | Reflexive meme token |
| ASTER | Meme/reflexive; thin history |
| BERA | New chain reflexive; n=3 too thin to trust |
| KAITO | Reflexive/narrative |
| POPCAT | Meme; n=1 net=−$2.08 |
| STBL | Meme; thin |
| PROMPT | Meme; n=3 net=−$0.72 |

### RESEARCH_ONLY (no trades; data collection only)

| Symbol | Reason |
|--------|--------|
| BTCUSDT | Duplicate of BTC |
| ETHUSDT | Duplicate of ETH |
| PF_ALGOUSD | Broken PF symbol |
| PF_XLMUSD | Broken PF symbol |
| PF_GALAUSD | Broken PF symbol |
| PF_RAVEUSD | Broken PF symbol |
| PF_BCHUSD | Broken PF symbol |
| PF_DASHUSD | Broken PF symbol |
| PF_XAUTUSD | Gold perp; no crypto doctrine |
| PF_PEPEUSD | Meme PF symbol |
| TST, ALT, ZK | Too thin for any classification |
| PAXG | Mean-reversion only; no perp doctrine yet |
| DASH | rvol=125%; trend_eff=0.065; noise |
| XPL | rvol=120%; thin evidence |

---

## Governance Transition Rules

### Promote: CONSTRAINED → ALLOWED
- n ≥ 8 trustworthy closes
- Expectancy > +$0.05/trade
- No integrity failures in last 20 closes

### Promote: ALLOWED → PROMOTED
- n ≥ 15 trustworthy closes
- Expectancy > +$0.30/trade
- Net PnL > 0

### Demote: ALLOWED → CONSTRAINED
- n ≥ 5 AND expectancy < −$0.20/trade

### Block: any → BLOCKED
- n ≥ 8 AND expectancy < −$0.30/trade
- OR: repeated integrity failures (quarantined tier)
- OR: price sanity failures > 20% of scans

### Unblock: BLOCKED → CONSTRAINED (manual only)
- Requires owner confirmation
- Minimum 30-day observation window
- Must have structural reason for expected improvement

---

## Tonight's Carry Suitability (Instrument Routing)

| Symbol | Carry Suitability | Default Route | Tonight |
|--------|------------------|---------------|---------|
| BTC | high | perp_tolerated (neutral funding) | ✅ Live perp |
| ETH | high | perp_tolerated (neutral funding) | ✅ Live perp |
| SOL | medium | perp_tolerated | ✅ Live perp |
| BNB | medium | perp_tolerated | ✅ Live perp |
| XRP | low | spot_preferred | ✅ Live perp (spot not available) |
| NEAR | low | spot_preferred | ✅ Live perp (spot not available) |
| LINK | low | spot_preferred | ✅ Live perp (spot not available) |
| AVAX | low | spot_preferred | ✅ Live perp (spot not available) |
| TON | low | spot_preferred | ✅ Live perp (spot not available) |
| ZEC | low | spot_preferred | ✅ Live perp (spot not available) |

**Note:** Spot infrastructure not live tonight. All allowed crypto goes through Binance USDM perps or Hyperliquid perps. The spot-first doctrine is aspirational until spot order routing is implemented. When it is:
- BTC/ETH: keep perp if funding neutral or favorable
- NEAR/LINK/AVAX/ZEC/XMR: route to spot first

---

## Learning Segmentation

To prevent cross-contamination between buckets:

| Learner | Allowed input sources |
|---------|----------------------|
| CARRY_MAJOR Bayesian weights | CARRY_MAJOR closes only |
| CLEAN_TREND_ALT Bayesian weights | CLEAN_TREND_ALT closes only |
| EXPLOSIVE_CONVEX Bayesian weights | EXPLOSIVE_CONVEX closes only |
| ML PnL regressor | All ALLOWED + PROMOTED buckets, integrity=verified only |
| Kelly sizing | CARRY_MAJOR + CLEAN_TREND_ALT; exclude meme and explosive |

**Hard exclusions from all learners:**
- source = pre_v10_contaminated, backtest, bybit_paper
- integrity tier = quarantined or excluded
- source contains: replay, synthetic, bootstrap, backtest_only
- First-session live: n < 10 per symbol does not update priors

---

## Implementation

Module: `strategies/symbol_governance.py`

```python
from strategies.symbol_governance import get_policy

policy = get_policy("BTC")
# policy.governance    → GovernanceStatus.ALLOWED
# policy.can_long      → True
# policy.can_short     → False (suppressed tonight)
# policy.max_size_pct  → 1.0

policy = get_policy("VVV")
# policy.governance    → GovernanceStatus.BLOCKED
# policy.can_enter     → False
```

Safe to wire now. Additive — does not modify any live-path behavior.
