# Forever Playbook v1
**Grounded:** 2026-04-15 | **DB basis:** 229 clean_paper_v10 closes | **Bankroll tonight:** $500 live

---

## What This Is

The Forever Playbook is not one universal strategy.
It is a routing system that matches market structure to appropriate tactics.

The system currently uses one logic stack for:
- carry majors (BTC, ETH, SOL)
- trend alts (NEAR, LINK, ZEC)
- explosive convex alts (TAO, ENA, LIT)
- reflexive meme names (TRUMP, VIRTUAL, PUMP)
- mean-reverting ranging symbols (DOGE, PAXG)

This leaks edge. A single composite score threshold cannot correctly govern all of them simultaneously.

---

## Current Truth (do not pretend the system is green)

| Metric | Value |
|--------|-------|
| Trustworthy closes | 229 (clean_paper_v10 source) |
| Gross PnL | +$12.72 |
| Fees | −$12.99 |
| Net PnL | −$0.27 |
| Go-live verdict | **AMBER / constrained_live_only** |

**Exit-type breakdown:**
| Exit type | N | Net |
|-----------|---|-----|
| trailing_stop | 49 | **+$12.06** |
| scale_out_33 | 45 | **+$23.92** |
| scale_out_66 | 35 | **+$18.31** |
| thesis_invalidated | 65 | **−$12.19** |
| hard_stop | 35 | **−$42.36** |

**Direction breakdown:**
- Long: +$13.56 net
- Short: −$13.82 net → **shorts suppressed tonight**

**Worst symbols (meaningful sample):**
- PF_ADAUSD: n=17, net=−$6.94 → BLOCKED
- VVV: n=23, net=−$4.15 → BLOCKED
- ALGO: n=11, net=−$3.06 → BLOCKED
- PF_TAOUSD: n=11, net=−$2.49 → CONSTRAINED
- FET: n=7, net=−$2.24 → CONSTRAINED

**Best symbols (meaningful sample):**
- PF_ZECUSD: n=14, net=+$5.41 → **PROMOTED**
- ZEC: n=11, net=+$2.57 → ALLOWED
- PF_SOLUSD: n=6, net=+$2.44 → ALLOWED

---

## The Durable Architecture

### Layer 1 — Capital Allocator

Decides how capital is distributed across market types, instruments, and states.

| Rule | Value tonight |
|------|--------------|
| Total live bankroll | $500 |
| Max deployed | 70% = $350 |
| Max concurrent positions | 4 |
| Per-trade risk | 1% = $5 |
| Leverage | 3× |
| Shorts | BLOCKED (AMBER verdict) |
| PF_* symbols | Paper only |

Sizing by market type:
- CARRY_MAJOR: 100% of standard size
- CLEAN_TREND_ALT: 100% of standard size
- EXPLOSIVE_CONVEX: 50% max
- MEAN_REVERSION: 75% max
- REFLEXIVE_MEME: 0% (blocked)
- DO_NOT_TRADE: 0% (blocked)

### Layer 2 — Market-Type Classifier

Every symbol is bucketed before any tactics are applied.

| Bucket | Examples | Tactics |
|--------|----------|---------|
| **CARRY_MAJOR** | BTC, ETH, SOL, BNB, XRP | Perp preferred when funding favorable; longer hold OK; get paid to be right |
| **CLEAN_TREND_ALT** | NEAR, LINK, AVAX, ZEC, TON, MORPHO | Spot-first; perp only if funding not hostile; directional move > fees |
| **EXPLOSIVE_CONVEX** | TAO, ENA, LIT, WLD, ZRO, FET | Small size; fast confirmation only; no passive holding; no MR attempts |
| **REFLEXIVE_MEME** | TRUMP, VIRTUAL, PUMP, FARTCOIN, VVV | Blocked by default; require explicit governance unlock |
| **MEAN_REVERSION** | DOGE, PAXG, AXS | Low-trend only; tight fee gate; adequate liquidity required |
| **DO_NOT_TRADE** | DOT, ALGO, BTCUSDT, PF_ADAUSD | Hard excluded |

**Classification is data-grounded:**
- 365 days of 1d price data for 39 symbols
- 120 4h bars for 15 symbols
- 229 trustworthy close legs
- 6720 scan candidate rows

### Layer 3 — Strategy Router

Different buckets use different entry gates:

**CARRY_MAJOR:**
- Allow perp hold when funding is favorable (longs collect)
- Require trend alignment on 4h (EMA8 > EMA21)
- Hold through minor pullbacks if 1d bias unchanged
- Exit: trailing stop monetizes; hard stop protects

**CLEAN_TREND_ALT:**
- Spot-first doctrine (funding often hostile for these on perp)
- Require 4h BULL confirmation before entry
- No mean-reversion attempts — wait for resumption
- Exit: trailing stop + scale-out; thesis-invalidated gate tight

**EXPLOSIVE_CONVEX:**
- 50% position size max
- Fast entry confirmation required (5m setup active)
- No passive holds — exit if thesis doesn't resolve in 4h
- Never use MR tactics on these names

**REFLEXIVE_MEME:**
- Blocked by default
- If explicitly unlocked: 25% size max, 4h alignment required, hard stop tight

**MEAN_REVERSION:**
- Only when: regime = RANGING, ADX < 25, funding not hostile
- Must have positive expected value after Kraken taker fee (0.065% × 2)
- Exit: fixed target at prior range boundary; hard stop at range midpoint

### Layer 4 — Multi-Timeframe Doctrine

Every live trade must answer all four timeframe questions:

| Timeframe | Question | Role |
|-----------|----------|------|
| **1d** | Structure broken / neutral / supportive? | Chooses directional bias |
| **4h** | Regime trending / rolling over / compressing? | Chooses strategy family |
| **30m** | Setup active? | Chooses setup validity |
| **5m** | Entry timing aligned or forced? | Chooses execution timing |

**Rules:**
- 1d BEAR + 4h setup → reduced size, tighter stop, no scale-out
- 4h BEAR → no new longs for CLEAN_TREND_ALT or CARRY_MAJOR
- 5m entry forced (chasing) → skip; amygdala rule 1 (never chase)
- All four in agreement → full position size

**Current 4h state (from price_archive.db):**
| Symbol | 4h Bias | 20-bar% | Note |
|--------|---------|---------|------|
| BTC | BULL | +1.95% | Carry long eligible |
| ETH | BULL | +3.34% | Carry long eligible |
| SOL | BULL | −1.33% | Pullback in trend; watch |
| NEAR | BULL | −2.48% | Pullback in trend; watch |
| LINK | BULL | +0.21% | Weak bull; proceed cautiously |
| AVAX | BULL | −0.11% | Flat; wait for resumption |
| MORPHO | BEAR | −3.50% | Do not enter long tonight |
| TAO | BEAR | −9.40% | Confirm 4h turn before any long |
| TON | BULL | −4.41% | Pullback; wait |
| ZEC | BEAR | −3.61% | High on 30d but 4h fading |

### Layer 5 — Funding Doctrine

Funding is a **modifier on holding quality**, not a signal.

**Rules:**
1. CARRY_MAJOR + favorable funding → perp preferred; count carry in hold calculation
2. CARRY_MAJOR + hostile funding → route to spot if available; avoid long perp hold
3. CLEAN_TREND_ALT + any funding → spot-first regardless; perp only if carry-positive
4. EXPLOSIVE_CONVEX → spot-first always; hold time too short for funding to matter
5. If carry is favorable but 4h is deteriorating → exit the hold; carry does not justify staying wrong
6. Carry is never a reason to hold through structural breakdown

**Thresholds:**
- Hostile: rate > +0.02%/8h (longs pay)
- Neutral: |rate| ≤ 0.02%/8h
- Favorable: rate < −0.01%/8h (longs collect)
- Carry-positive: rate < −0.03%/8h (strong incentive)

**Current data gap:** scan_candidates funding_rate is 0 for all rows in fresh Projects DB session.
Fetch live rates from exchange APIs before treating any carry routing as confirmed.

### Layer 6 — Symbol Governance

Every symbol has a dynamic status. Movement is evidence-gated.

| Status | Meaning | Entry allowed? | Size |
|--------|---------|----------------|------|
| **PROMOTED** | Exceeds evidence bar | Yes | 100% |
| **ALLOWED** | Standard operation | Yes | 100% |
| **CONSTRAINED** | Reduced risk | Yes | 25–75% |
| **BLOCKED** | No new trades | No | 0% |
| **RESEARCH_ONLY** | Observation only | No | 0% |

**Promotion gates (both must be met):**
- n ≥ 15 trustworthy closes
- Expectancy > +$0.30/trade

**Blocking gates (any one triggers):**
- n ≥ 8 closes AND expectancy < −$0.30/trade
- Systematic integrity failures

**Current promoted:** PF_ZECUSD (n=14, net=+$5.41) — paper only tonight

### Layer 7 — Exit Doctrine

Exits are three different jobs. Do not evaluate them as if they serve the same purpose.

| Exit type | Job | Net performance |
|-----------|-----|----------------|
| **trailing_stop** | Monetizes edge; lets winners run | +$12.06 ✓ |
| **scale_out** | Books partial profit; adjusts exposure | +$42.23 ✓ |
| **hard_stop** | Protects capital from catastrophic loss | −$42.36 |
| **thesis_invalidated** | Protects from staying wrong | −$12.19 |

**Interpretation:**
- trailing_stop and scale_out are working. Preserve them exactly.
- hard_stop net is −$42.36: this is the cost of being wrong, not a signal of bad exit logic. Do not widen stops.
- thesis_invalidated net is −$12.19: the system is exiting some trades too early on thesis checks OR the entry thresholds need tightening. Do not disable this exit — it prevents larger losses.
- Dead-money exit: protects against open positions that are going nowhere; do not remove.

### Layer 8 — Learning Doctrine

**Segment by market type. Never mix.**

- CARRY_MAJOR outcomes do not train REFLEXIVE_MEME policy
- EXPLOSIVE_CONVEX outcomes do not train CARRY_MAJOR sizing
- Meme names do not pollute major-symbol Bayesian signal weights

**Data cleanliness rules:**
1. Only `clean_paper_v10` and `live_v10` source rows feed Bayesian / ML training
2. `pre_v10_contaminated`, `backtest`, `bybit_paper`, `paper_v10` are excluded
3. Synthetic, replay, bootstrap rows → `excluded` integrity tier → never reach weights
4. First-session live noise (n < 10) does not rewrite signal priors
5. Do not promote live ML/Bayesian weights until n ≥ 50 trustworthy closes per bucket

**Current state:** 0 trustworthy closes in last 7 days (fresh Projects DB). Treat as thin.

### Layer 9 — Launch-State Ladder

| State | Live? | Paper? | Shorts? | Size | Trigger |
|-------|-------|--------|---------|------|---------|
| Research | No | No | No | 0% | New system / untested |
| Paper | No | Yes | Yes | 100% | Pre-launch validation |
| **Constrained Live** | **Yes** | **Yes** | **No** | **50%** | **Tonight — AMBER verdict** |
| Scaled Live | Yes | Yes | Yes | 100% | n≥50, exp>0.15, pf>1.4 |
| Defense Mode | No | No | No | 0% | Kill switch / drawdown / API |

**Tonight is Constrained Live.** This is not a promotion — it is a controlled first exposure at $500 with suppressed shorts and reduced sizing.

---

## What the Repo Is Currently Doing Wrong

1. **One composite score threshold governs all symbol types.** A score of 58 means different things for BTC (carry major) vs FARTCOIN (reflexive meme). The threshold is bucket-agnostic.

2. **No spot vs perp routing.** The system sends everything to Binance USDM perps regardless of funding posture. When funding is hostile (longs pay significantly), the hold cost erodes directional edge.

3. **Shorts are structurally unprofitable.** Short net = −$13.82 on 74 closes vs Long net = +$13.56 on 155 closes. This is not a short-term anomaly — it is a consistent pattern in the sample.

4. **PF_ADAUSD was the worst performer and kept running.** n=17, net=−$6.94. This needed to be blocked after n≥8 with expectancy < −$0.30. The governance layer would have caught this.

5. **ALGO, VVV are systematic losers with adequate sample.** Neither was blocked. Governance would flag both.

6. **Learning is not segmented by market type.** Meme coin outcomes (TRUMP, FARTCOIN) influence the same Bayesian prior pool as BTC and ETH trades.

7. **hard_stop cost is −$42.36 but is treated as neutral.** Hard stops are not exits that prove alpha — they are the cost of being wrong. The current system counts them in the same learning pool as trailing stops and scale-outs.

---

## Tonight's $500 Live Operating Profile

**Mode:** Constrained Live  
**Bankroll:** $500  
**Max deployed:** $350 (70%)  
**Max positions:** 4  
**Per-trade risk:** $5 (1%)  
**Leverage:** 3×  
**Instrument:** Binance USDM perps (Hyperliquid as secondary)  
**PF_* symbols:** Paper only  

**Tonight's allowlist (longs only):**

| Symbol | Governance | Size | Condition |
|--------|-----------|------|-----------|
| BTC | ALLOWED | 100% | 4h BULL confirmed |
| ETH | ALLOWED | 100% | 4h BULL confirmed |
| SOL | ALLOWED | 100% | 4h BULL; watch pullback |
| NEAR | ALLOWED | 100% | 4h BULL; watch pullback |
| LINK | ALLOWED | 100% | 4h BULL weak; cautious |
| AVAX | ALLOWED | 100% | 4h flat; wait for push |
| BNB | ALLOWED | 100% | No 4h data; use 1h |
| TON | ALLOWED | 75% | 4h BULL but pullback deep |
| XRP | ALLOWED | 75% | Low vol; carry candidate |
| ZEC | ALLOWED | 100% | 30d+30.8% but 4h BEAR — WAIT for 4h turn |

**Tonight's blocklist:**
- ALL shorts
- ALL PF_* (paper only)
- MORPHO (4h BEAR)
- TAO (4h BEAR, systematic losses)
- All REFLEXIVE_MEME: TRUMP, PUMP, VIRTUAL, FARTCOIN, ASTER, HEMI, VVV, MON, BERA, KAITO, SPX, PENGU, WLFI
- PF_ADAUSD, PF_TAOUSD, ALGO, DOT, VVV (blocked, systematic losers)
- All RESEARCH_ONLY: BTCUSDT, ETHUSDT, duplicate PF symbols

**Constrained allowlist (50% size, additional condition required):**
- TAO: only if 4h turns BULL during session
- ZEC: only if 4h turns BULL during session
- MORPHO: only if 4h turns BULL during session
- ZRO: only if composite ≥ 65 (thin sample, thin evidence)
- ENA: only if composite ≥ 68 (high vol, constrained)

---

## Remaining Uncertainty

1. **Funding rates not captured.** scan_candidates funding_rate = 0 for all rows in Projects DB. All routing uses neutral-funding assumptions. Fetch live rates before trusting carry routing.

2. **No labeled candidate outcomes in Projects DB.** The 4100 candidate_outcomes rows exist but no 4h forward returns for current-session candidates are labeled yet. The labeler needs to run.

3. **229 closes is marginal for per-bucket segmentation.** Dividing by 6 buckets = ~38 per bucket on average. Too thin for independent ML training per bucket. Use bucket classification for routing and sizing; keep shared Bayesian pool for now, with contamination guards.

4. **TAO, ZEC 4h structure is BEAR today.** The 30d data is bullish but 4h is fading. Do not enter these tonight unless 4h turns during session.

5. **ZRO n=3 net=+$13.23 is one large win.** Do not treat as confirmed edge. Constrained until n ≥ 10.

6. **FARTCOIN n=16 net=+$0.87 is surprisingly positive** but rvol=122% and trend_eff=0.039 (pure noise). The positive result is likely from short-duration longs that caught a spike. Keep REFLEXIVE_MEME classification; do not promote.

---

## Files Created

| File | Type | Status |
|------|------|--------|
| `strategies/market_type_classifier.py` | Module | safe to wire now |
| `strategies/symbol_governance.py` | Module | safe to wire now |
| `strategies/funding_instrument_router.py` | Module | safe to wire now |
| `scripts/forever_playbook_audit.py` | Script | safe to wire now |
| `scripts/funding_carry_audit.py` | Script | safe to wire now |
| `brain/10_decisions/forever_playbook_v1.md` | Doctrine | reference |
| `brain/03_parameter_sets/market_type_router_v1.md` | Doctrine | reference |
| `brain/03_parameter_sets/symbol_governance_v1.md` | Doctrine | reference |
| `tests/proof/test_forever_playbook_rules.py` | Proof tests | mandatory |
