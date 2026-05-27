# Market-Type Router v1
**Grounded:** 2026-04-15 | Source: `strategies/market_type_classifier.py`

---

## Bucket Definitions

### CARRY_MAJOR
**Symbols:** BTC, ETH, SOL, BNB, XRP (and their PF_ Kraken variants)

**Characteristics:**
- Annualized 30d realized volatility ≤ 55%
- Adequate liquidity for perp holds
- Funding sometimes pays longs (favorable carry)
- Longer holds viable (1–3 days) when 1d + 4h aligned

**Tactics:**
- Perp preferred when funding rate < −0.01%/8h
- Spot preferred when funding rate > +0.02%/8h
- Longer trailing stop width (regime-aware, ~4.5× ATR trending)
- Scale-out allowed (evidence: scale_out net = +$42.23)
- Objective: get paid to be right (directional + carry)

**Do NOT use on CARRY_MAJOR:**
- Mean-reversion entries
- Explosive small-size tactics
- Reflexive momentum chasing

---

### CLEAN_TREND_ALT
**Symbols:** NEAR, LINK, AVAX, MORPHO, TON, ZEC, XMR, RENDER, ADA, SUI, UNI, CRV

**Characteristics:**
- Directional structure visible on 1d + 4h
- Moderate vol (55–80% annualized)
- Perp funding often hostile to longs → spot-first
- Multi-session holds viable on clear trend

**Tactics:**
- Spot-first doctrine; perp only if carry-positive funding
- Entry requires 4h BULL confirmation (EMA8 > EMA21)
- Trailing stop: 4.5× ATR in trending regimes
- Objective: capture directional move that exceeds fees

**Do NOT use on CLEAN_TREND_ALT:**
- EXPLOSIVE_CONVEX fast-flip tactics
- Mean-reversion counter-trend plays
- Passive perp hold when 4h rolling over

---

### EXPLOSIVE_CONVEX
**Symbols:** TAO, ENA, LIT, WLD, ZRO, LDO, ARB, FET, JUP, AAVE, XPL, DASH, HYPE

**Characteristics:**
- Annualized 30d realized volatility ≥ 80–120%
- Fast momentum, reflexive, often thesis-breaks quickly
- Carry via perp is dangerous (hold time too short; funding unpredictable)

**Tactics:**
- 50% position size maximum
- Fast confirmation required: 30m setup + 5m entry timing
- Exit if thesis not resolving within 4h window
- No passive holds, no mean reversion
- Spot preferred (avoid funding risk on short holds)

**Do NOT use on EXPLOSIVE_CONVEX:**
- CARRY_MAJOR hold doctrine
- Extended trailing stops
- Averaging down (amygdala rule 2)

---

### REFLEXIVE_MEME
**Symbols:** TRUMP, WLFI, PUMP, VIRTUAL, FARTCOIN, SPX, ASTER, VVV, HEMI, MON, BERA, KAITO, POPCAT, STBL, PROMPT

**Characteristics:**
- Narrative/sentiment driven
- No persistent structural edge
- Outcomes pollute learning pools if mixed with majors

**Tactics:**
- BLOCKED by default
- Explicit governance unlock required
- If unlocked: 25% size max, 4h alignment required, hard stop tighter than standard

**Critical:** Outcomes from REFLEXIVE_MEME must not feed the same Bayesian pool as CARRY_MAJOR or CLEAN_TREND_ALT.

---

### MEAN_REVERSION
**Symbols:** DOGE, PAXG, AXS

**Characteristics:**
- Low trend efficiency (< 0.15)
- ADX typically < 25 in tradeable regimes
- Adequate liquidity for round-trip within fee budget

**Tactics:**
- Only when regime = RANGING (confirmed by WAE, CHOP, ADX)
- Long from lower range boundary with target at upper boundary
- Must clear fee gate: E[return] > 2 × Kraken taker fee (0.13% round-trip)
- Spot preferred; avoid perp holds for MR (funding unpredictable)

**Do NOT use on MEAN_REVERSION:**
- Trend-following tactics
- Extended trailing stops
- Perp holds beyond same-session

---

### DO_NOT_TRADE
**Symbols:** DOT, ALGO, BTCUSDT, ETHUSDT, PF_ADAUSD, PF_ALGOUSD, PF_XLMUSD, PF_GALAUSD, PF_RAVEUSD, PF_BCHUSD, PF_DASHUSD, PF_XAUTUSD, ALT, ZK

**Reason:** Duplicate tickers, systematic losers with evidence, or structurally broken PF symbols.

**Action:** Hard exclude from scanner output. If currently open, exit on next valid exit signal. Do not allow new entries.

---

## Dynamic Reclassification Rules

When 1d price data is available in price_archive.db, these dynamic rules override the seed classification for UNKNOWN symbols only (seed symbols are only demoted, not promoted, dynamically):

| Condition | Classification |
|-----------|---------------|
| rvol ≥ 90% AND trend_eff < 0.06 | REFLEXIVE_MEME |
| rvol ≥ 90% | EXPLOSIVE_CONVEX |
| trend_eff < 0.06 AND rvol > 60% | REFLEXIVE_MEME |
| rvol ≤ 55% AND dd90 ≥ −30% | CARRY_MAJOR candidate |
| rvol ≤ 80% AND trend_eff ≥ 0.06 | CLEAN_TREND_ALT candidate |
| rvol > 80% | EXPLOSIVE_CONVEX |

Seed symbols (known) can only be DEMOTED dynamically:
- CARRY_MAJOR → CLEAN_TREND_ALT if rvol suddenly > 80%
- CLEAN_TREND_ALT → EXPLOSIVE_CONVEX if rvol suddenly > 110%

---

## Multi-Timeframe Gate

| Timeframe | Role | Gate condition |
|-----------|------|----------------|
| 1d | Directional bias | Long only if 1d not in confirmed downtrend |
| 4h | Strategy family | Long only if 4h EMA8 > EMA21 (CARRY + TREND) |
| 30m | Setup validity | Setup must be active (signal score ≥ threshold) |
| 5m | Entry timing | Entry not forced (price within 3% of signal candle) |

**4h BULL required for:**
- CARRY_MAJOR long entries
- CLEAN_TREND_ALT long entries

**4h not required for (but preferred):**
- EXPLOSIVE_CONVEX (30m setup sufficient)
- MEAN_REVERSION (4h RANGING preferred)

---

## Implementation Notes

- Module: `strategies/market_type_classifier.py`
- Entry point: `classify(symbol, price_db=None)`
- Bulk: `classify_many(symbols, price_db=None)`
- Safe to import anywhere without side effects
- Dynamic override requires `price_archive.db` access
- Fallback for unknown symbols: EXPLOSIVE_CONVEX (conservative)
