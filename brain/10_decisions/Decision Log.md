# Decision Log

#active #decision

**Purpose: Preserve the why behind major system changes so they don't get re-litigated.**

---

## 2026-03-25 — Add 7 new indicators (v4.3)

**Decision**: Add SuperTrend, Ichimoku, WAE, Fisher, CHOP, WaveTrend, LaguerreRSI to conviction scoring
**Reason**: Extend signal diversity beyond MACD/Williams into trend, oscillator, and volatility families
**Evidence**: Each indicator has theoretical backing; no backtest yet on this system
**Replaces/Overrides**: Nothing removed — additive only
**Expected benefit**: Higher conviction entries with multi-family signal agreement
**Risk**: Signal stacking on strong trends; possible false confidence inflation
**Reversal condition**: If Tier 2b signals don't correlate with outcomes after 30+ trades, demote or remove

---

## 2026-03-25 — TradingView webhook integration (v4.2)

**Decision**: Build webhook server to receive Pine Script alerts; boost conviction +20 pts
**Reason**: TradingView Pro user; TV alerts can confirm bot signals with additional chart evidence
**Evidence**: TV webhooks are reliable; Pine Script mirrors existing bot signal logic
**Replaces/Overrides**: Nothing — additive external signal layer
**Setup required**: TV_WEBHOOK_SECRET in .env, ngrok running, TradingView alert configured
**Risk**: ngrok URL changes on restart; TV signal can lag by up to 5 min (max age window)
**Reversal condition**: If TV boost increases false positives (signals that lose), reduce boost or remove

---

## 2026-03-24 — De-risk overhaul (v4.0)

**Decision**: Cut all risk parameters 50%, remove RSI as entry gate, remove Hurst
**Reason**: System was too aggressive for $500 account. Single bad day could wipe 8%+.
**Evidence**: Fee math showed $500 position × 0.6% taker × 2 = $6/trade. Needed lower sizes.
**What changed**:
- MAX_RISK_PER_TRADE: 2% → 1%
- MAX_DAILY_LOSS: 8% → 4%
- MAX_POSITIONS_CRYPTO: 10 → 5
- Position sizes: $500 → $250
- PERP leverage: 20× → 10×
- Circuit breaker: 8 → 4 consecutive losses
- RSI removed as entry gate (now exit-only)
- Hurst removed entirely (too noisy on 1-min bars)
**Reversal condition**: Scale parameters back up when account size × 5 or win rate confirmed > 55%

---

## 2026-03-24 — Advanced math signal overhaul (v3.9)

**Decision**: Demote MACD from sole gatekeeper to one-of-eight; add 5 new math signal paths
**Reason**: MACD alone as gatekeeper missed mean-reversion setups and vol expansion entries
**Evidence**: Research backed OBI, Kalman, AVWAP, squeeze, RV ratio as complementary signals
**What changed**: 8 independent signal paths now score conviction independently
**Risk**: More signals = more debates per scan cycle = higher API cost
**Mitigation**: ATR fee-floor guard added to skip low-vol symbols before any debate call

---

## 2026-03-24 — Collapse 8 agents → 5 (v3.5)

**Decision**: Remove session_breakout, williams, quant_edge agents from debate panel
**Reason**: Their logic was either duplicated in pre-filter or better handled in code
**Evidence**: session_breakout timing = session_active flag; williams %R = pre-filter conviction; quant_edge = Kelly in risk_manager
**Replaces**: 8-agent v2.0 panel
**Risk**: Losing some analytical diversity in the debate
**Reversal condition**: If trades are being entered at wrong session times or at wrong regime fits

---

## 2026-03-24 — Equity disabled (v3.7)

**Decision**: Set EQUITY_ENABLED=false; crypto + futures only
**Reason**: $500 account — equity requires PDT compliance (3 trades/5 days), high per-trade cost
**Evidence**: At $250 equity position, 2.5% stop = $6.25 loss limit. Equity commissions less predictable.
**Replaces**: Webull equity (which was blocked anyway — migrated to Alpaca before disabling)
**Reversal condition**: Account > $2,500 + strong paper track record; OR a specific equity opportunity arises

---

## 2026-03-24 — Replace CNN Fear&Greed with Alternative.me (v3.7)

**Decision**: Switch Fear&Greed API source from CNN to Alternative.me
**Reason**: CNN API was silently failing — returning stuck value of 50 (Neutral) all day
**Evidence**: Fear&Greed was 11 (Extreme Fear) on Alternative.me while CNN showed 50
**Risk**: Alternative.me has its own data quality issues — but at least it moves
**Reversal condition**: If Alternative.me shows unreliable data patterns

---

## 2026-03-23 — Mean-reversion strategy added (v3.4)

**Decision**: Add crypto_mean_reversion.py running in parallel with AI debate path
**Logic**: Kalman deviation ≤ −0.8% OR AVWAP deviation ≤ −0.5%; RSI<33 + lower BB + ADX<22
**Reason**: Pure momentum/breakout misses ranging market opportunities
**Risk**: Mean-reversion fails in strong trending regimes (gets run over)
**Mitigation**: ADX < 22 gate prevents entry in strong trends

---

## 2026-03-22 — Notifications to SQLite instead of email (v3.1)

**Decision**: Remove email/Telegram alerts; write all notifications to system_events SQLite table
**Reason**: Simpler, offline-capable, no external dependency
**Replaces**: Gmail SMTP via telegram_alert.py
**Note**: telegram_alert.py still exists for backward compat — just writes to DB now

---

## WHAT NOT TO RE-LITIGATE

These questions were debated and settled:

| Question | Answer | Version settled |
|---------|---------|----------------|
| Should Hurst gate entries? | No — too noisy on 1-min | v4.0 |
| Should RSI gate entries? | No — exit only | v4.0 |
| 8 agents or 5? | 5 focused | v3.5 |
| Webull or Alpaca? | Alpaca (Webull blocked) | v3.7 |
| Email alerts or dashboard? | Dashboard (SQLite) | v3.1 |
| CNN or Alternative.me? | Alternative.me | v3.7 |
| $500 or $250 position sizes? | $250 (de-risk) | v4.0 |
