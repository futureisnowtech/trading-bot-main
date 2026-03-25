# Current Active Logic

#active #strategy

**Status as of: 2026-03-25**
**System version: BELIEVED v4.3**
**Source: Code inspection + CHANGELOG — not yet confirmed by live trading data**

---

## CONFIRMED

### System Mode
- PAPER_TRADING=true (default — live requires explicit `python3 main.py --mode live` + typing 'I UNDERSTAND')
- Entry point: `python3 main.py`
- Dashboard: `streamlit run dashboard/app.py` on port 8501

### Active Instruments
- **Crypto**: CONFIRMED ENABLED — Coinbase Advanced Trade (24/7)
- **Futures**: CONFIRMED ENABLED — MES via Tradovate (paper only — no paid API access)
- **Equity**: CONFIRMED DISABLED — `EQUITY_ENABLED=false` in .env (Alpaca broker wired but off)

### Active Pairs
- **Crypto**: Up to 20 pairs from .env `CRYPTO_PAIRS` (default: BTC-USDC, ETH-USDC + 18 others)
- **Perp** (Bybit): `PERP_ENABLED=true` in .env — BELIEVED active

### Candle Granularity
- Crypto: 1-minute candles (CONFIRMED from CHANGELOG v3.2)
- MES futures: 1-minute ORB (opening range breakout)

---

## BELIEVED

### The Full Signal Pipeline (Crypto)

```
1. Fetch 1-min candles (CoinbaseMicrostructureFeed WebSocket)
2. Run add_all_indicators() — 16 indicator blocks
3. Conviction scoring gate (must reach threshold before debate is called):

   TIER 1 — Legacy signals
   MACD 3-variant consensus    +25 pts
   Williams %R ≤ -80           +20 pts
   Momentum + volume breakout  +15 pts

   TIER 2a — Advanced math signals
   BB-Keltner squeeze fire ≥20 bars  +20 pts
   RV ratio ≥ 1.3 vol expansion      +15 pts
   Kalman dev ≤ -1.0%               +10 pts
   AVWAP dev ≤ -0.5%                +10 pts
   OU half-life [3,60] min          + 5 pts
   Kyle lambda ≤ 30th pct           + 5 pts

   TIER 2b — New indicators (v4.3)
   SuperTrend bullish               +12 pts
   WaveTrend cross from oversold    +12 pts
   Ichimoku cloud bullish           + 8 pts
   Ehlers Fisher cross up           + 8 pts
   Laguerre RSI < 0.15              + 8 pts
   WAE bullish + exploding          +10 pts
   WAE bullish only                 + 5 pts
   Choppiness Index trending        + 5 pts
   Laguerre RSI < 0.25              + 4 pts

   TIER 3 — External confirmation
   TradingView webhook buy signal (≤5min old)  +20 pts

4. ATR fee-floor guard: skip if ATR/price < 0.4% (can't clear 2.4% round-trip fees)
5. OBI/TFI microstructure veto: skip if OBI < -0.35 AND TFI < -0.20
6. Dead-zone block: 2:00-5:00 AM ET — conviction floor raised to 70
7. Debate gate: FULL (5 agents) for crypto; QUICK (3 agents) for futures
8. Min agent agreement: 2 of 5 agents must say BUY (explicit count, not percentage)
9. Risk synthesizer → go/no-go
10. Execute → set stop and target immediately
```

### Conviction Thresholds
- Normal hours: 30 pts minimum (BELIEVED from CHANGELOG v3.6 + job_runner.py logic)
- Dead zone (2-5am ET): 70 pts minimum

### Exit Logic
- Primary: Extended thinking exit review — Tudor Jones + Soros + Simons agents
  ANY ONE saying EXIT = exit (asymmetric, on purpose)
- Secondary gates: stop loss, take profit, stagnant trade (45min < 15% progress), 12h max hold
- RSI: EXIT signal only — NOT used as entry gate

---

## TESTING

- TradingView webhook integration (v4.2) — server + ngrok must both be running manually
  Setup is in CLAUDE.md. Not auto-started by launchd.
- Bybit perpetual strategy (v3.8) — `PERP_ENABLED=true` but no confirmed paper trading results
- 7 new indicators (v4.3 — SuperTrend, Ichimoku, WAE, Fisher, CHOP, WaveTrend, LaguerreRSI)
  Added to indicators.py but no production performance data exists yet

---

## RETIRED

- Hurst exponent as entry gate — removed v4.0 (noise in 1-min regime detection)
- RSI as entry gate — removed v4.0 (kept for exit signals only)
- 8-agent debate panel — v3.5 collapsed to 5 focused agents
- Session breakout agent — absorbed into session_active flag
- Williams agent — absorbed into pre-filter conviction scoring
- quant_edge agent — absorbed into risk_manager + indicators

---

## OPEN QUESTIONS

→ See [[01_current_system/Open Questions.md]]

---

## NEXT ACTIONS

1. Run paper trading for ≥14 days, log all results
2. Evaluate new indicator contribution to conviction scoring (did they filter noise or add noise?)
3. Set up TradingView webhook before first live session
4. Confirm Tradovate paper simulation is producing realistic fills
