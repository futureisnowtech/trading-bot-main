# Regime Logic

#active #regime

**Status as of: 2026-03-25**
**Source: Code inspection of `strategies/ai_agents/regime_detector.py` + signal logic in `job_runner.py`**

---

## CONFIRMED: HOW REGIME AFFECTS EXECUTION

The system uses several regime signals that influence strategy selection and conviction scoring:

### ADX — Trend Strength Gate
- `CRYPTO_MIN_ADX = 15.0` — minimum ADX required for most entries
- Mean reversion strategy: **ADX < 22** required (weak trend = ranging = mean-revert)
- Used to prevent trending entries in choppy markets

### Choppiness Index (v4.3)
- CHOP < 38.2 → "trending" regime → +5 conviction pts
- CHOP > 61.8 → "choppy" regime → no boost (implicitly lower conviction)

### RV Ratio (Realized Volatility)
- RV ≥ 1.3 → short-term vol > long-term vol → **expansion regime** → +15 conviction pts
- RV ≤ 0.8 → compressed vol → mean-reversion preferred (not a blocking condition, just informative)

### BB-Keltner Squeeze
- Squeeze fired ≥ 20 bars, direction > 0 → volatility compression about to expand → +20 pts
- This is a directional vol expansion signal: markets compress then explode

### Fear & Greed (Alternative.me)
- Used as a broader macro sentiment overlay
- Source changed from CNN to Alternative.me in v3.7 (CNN was silently returning 50)
- Current value when last checked: 11 (Extreme Fear)

---

## BELIEVED: REGIME DETECTOR AGENT

The `regime_detector.py` file classifies market regime as one of:
- **TRENDING** — high ADX, price making new highs/lows, MACD aligned
- **RANGING** — low ADX, price oscillating around mean, CHOP high
- **VOLATILE** — RV ratio high, squeeze firing, sudden volume spikes

BELIEVED behavior: The regime classification feeds into the agent debate context
so agents know what regime they're evaluating a trade in.

**UNCONFIRMED**: Whether the regime classification actively blocks entries or just informs agents.
Needs code review of `regime_detector.py` to confirm.

---

## STRATEGY FIT BY REGIME

| Regime | Best Strategy | Avoid |
|--------|--------------|-------|
| TRENDING | Crypto MACD (breakout), SuperTrend aligned | Mean Reversion |
| RANGING | Crypto Mean Reversion (Kalman/AVWAP reclaim) | Pure momentum MACD |
| VOLATILE | Squeeze plays, WAE explosion, RV expansion | Both — wait for direction |

---

## DEAD ZONE REGIME (Time-based)

- **2:00–5:00 AM ET**: Conviction floor raised from 30 → 70
- Rationale: Asian session liquidity thin; fills unreliable; stop-hunting common
- Hard block implemented in `job_runner.py` — not AI-controlled

---

## SESSION WINDOWS

- **8:00–11:00 AM ET**: Preferred entry window for session breakout setups
- **No new equity entries 9:30–10:00 AM ET**: Opening volatility avoidance
- **Equity off entirely**: EQUITY_ENABLED=false until further notice

---

## WHAT'S MISSING

The brain currently has no accumulated regime pattern history because paper trading hasn't started.

### What to build once trading begins:
- Track which regime dominated on each day (from ADX + CHOP + RV readings)
- Note which strategy performed best in each regime
- Identify whether Tier 2b indicators add more value in trending vs ranging regimes
- Track whether CHOP index correctly predicted ranging (CHOP > 61.8) vs trending (CHOP < 38.2)

---

## OPEN QUESTION

→ [[01_current_system/Open Questions.md]] Q1: Are the new indicators calibrated for 1-min regime detection?
The Choppiness Index and Ichimoku were designed for daily bars. On 1-min bars,
noise may overwhelm signal. Monitor closely in first 30 trades.
