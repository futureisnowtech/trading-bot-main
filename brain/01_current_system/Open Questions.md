# Open Questions

#active

> ## HISTORICAL SECTION BELOW
> Questions dated 2026-03-25 reference the v4.3 architecture (Coinbase Advanced Trade,
> Bybit perps, Tradovate MES, 5-agent debate). That system no longer exists.
> The current live system is **v15.2** (2026-04-15). See CLAUDE.md for current truth.
> Historical questions are preserved for audit trail only.

---

## v15.2 OPEN QUESTIONS (2026-04-15)

### Q-A: Will ForecastEx OPT contracts become available?
- **Context**: IBKR paper account DUP590699 can see IND underliers (CPI/CPIY/CPIC/DISSN/DISSA)
  but OPT event contracts hang with no response. Likely requires: (1) live funded account,
  (2) explicit ForecastEx enrollment via IBKR portal.
- **Resolution**: Enroll live account; switch IBKR_PORT to 7496.

### Q-B: How many clean paper trades needed before ML model activates meaningfully?
- **Context**: ML score falls back to 50.0 until enough `clean_paper_v10`/`live_v10` trades
  accumulate. MIN_TRADES_FOR_ML threshold gates retraining.
- **Resolution**: Monitor `ml_retrain_queue` table; check after 50+ clean closes.

### Q-C: When should MES lane be reactivated?
- **Context**: MES is DORMANT (FUTURES_LANE_ACTIVE=false). Code and DB tables are preserved.
- **Resolution**: Set FUTURES_LANE_ACTIVE=true in .env + verify TWS on port 7496 + confirm
  MESM26 contract is still current.

---

**Last updated: 2026-04-15**
Historical questions (v4.3 era) preserved below for audit trail only.
These are unresolved questions that affect system behavior or decision quality.
Each question is marked by urgency and what would resolve it.

---

## URGENT — Affects live readiness

### Q1: Are the 7 new indicators (v4.3) additive or noise?
- **Context**: SuperTrend, Ichimoku, WAE, Fisher, CHOP, WaveTrend, LaguerreRSI were added
  to conviction scoring in v4.3. No backtest or paper trading data exists yet.
- **Risk**: Overfitting. Adding 7 signals increases conviction for marginal setups.
  Could lower the practical bar by stacking signals that all fire simultaneously.
- **Resolution**: Run 30+ paper trades. Track which Tier 2b signals were active on winners vs losers.
  If Tier 2b doesn't improve win rate, consider dropping them or raising individual thresholds.

### Q2: Is the TradingView webhook integration actually working end-to-end?
- **Context**: webhook server + Pine Script template are built, but the full chain
  (TV → ngrok → webhook → SQLite → conviction boost) has not been tested live.
- **Risk**: TV_WEBHOOK_SECRET mismatch or ngrok URL stale = silent failure (TV signals never reach bot).
- **Resolution**: Manual test: start webhook server, start ngrok, set TradingView alert, verify
  `system_events` table gets a row with source='tradingview'.

### Q3: What is Tradovate paper simulation actually producing?
- **Context**: Tradovate has no free demo API. Paper mode uses yfinance ES prices.
  Is the simulated fill quality realistic? Are slippage assumptions sensible?
- **Resolution**: Review `execution/tradovate_broker.py` paper simulation logic.
  Compare fills to real MES bid/ask spreads.

---

## MEDIUM — Affects optimization decisions

### Q4: What is the actual conviction score distribution in live scans?
- **Context**: Normal threshold is 30 pts. Dead zone is 70 pts. Max theoretical score
  is ~175 pts (all signals fire simultaneously). What does a typical scan look like?
  Are most symbols scoring 0-20 and nothing is firing? Or are there frequent 30-50 scores?
- **Resolution**: Add conviction score logging to `system_events` for every symbol scanned
  (not just debate-callers). Review after 1-2 days of paper trading.

### Q5: Is the 5-agent panel correctly calibrated for 1-min crypto?
- **Context**: Full panel (5 agents) was narrowed in v3.5 from 8. Min agreement = 2 of 5.
  This means 40% agreement required. Are the agents ever disagreeing in a useful way,
  or do they mostly all agree and the gate provides little filtering?
- **Resolution**: Log individual agent votes to SQLite. Track per-agent agree/disagree rates.

### Q6: Is PERP (Bybit) actually scanning and executing in paper mode?
- **Context**: `PERP_ENABLED=true` is in .env. Bybit testnet is on.
  But no confirmed paper trades via perp have been observed.
- **Resolution**: Start bot, check logs for `run_perp_scan()` output.

---

## LOW — Future improvements

### Q7: Should the system log conviction scores per signal tier?
- **Context**: Right now conviction total is logged but not decomposed by tier.
  Understanding which tiers are contributing most would be very useful for tuning.
- **Resolution**: Add `conviction_breakdown` JSON field to trade log notes.

### Q8: When should equity be re-enabled?
- **Context**: Equity disabled to reduce complexity on $500 account.
  Alpaca broker is wired and ready. When account grows, or when a specific
  equity opportunity arises, what criteria should trigger re-enabling?
- **Resolution**: Define a re-enable threshold (e.g., account > $2,500 + 30-day positive track record).

---

## RESOLVED (for reference)

- ~~Should we use Hurst as an entry gate?~~ → No, removed v4.0 (noise on 1-min)
- ~~Should RSI gate entries?~~ → No, RSI is exit-only since v4.0
- ~~8 agents or 5?~~ → 5 focused agents since v3.5; 3-agent quick debate for crypto
- ~~Webull or Alpaca?~~ → Alpaca (Webull 403-blocked), but equity still disabled
- ~~Should CNN Fear&Greed stay?~~ → Replaced with Alternative.me (CNN was silently failing) v3.7

---

## AUTO-ALERT — 2026-03-25

- No trades today — bot may not be running or no signals fired

*Generated by generate_daily_summary.py — review and resolve or dismiss.*

---

## AUTO-ALERT — 2026-03-28

- No trades today — bot may not be running or no signals fired

*Generated by generate_daily_summary.py — review and resolve or dismiss.*

---

## AUTO-ALERT — 2026-04-15

- No trades today — bot may not be running or no signals fired

*Generated by generate_daily_summary.py — review and resolve or dismiss.*

---

## AUTO-ALERT — 2026-04-16

- No trades today — bot may not be running or no signals fired

*Generated by generate_daily_summary.py — review and resolve or dismiss.*

---

## AUTO-ALERT — 2026-04-17

- No trades today — bot may not be running or no signals fired

*Generated by generate_daily_summary.py — review and resolve or dismiss.*

---

## AUTO-ALERT — 2026-04-18

- No trades today — bot may not be running or no signals fired

*Generated by generate_daily_summary.py — review and resolve or dismiss.*

---

## AUTO-ALERT — 2026-04-19

- No trades today — bot may not be running or no signals fired

*Generated by generate_daily_summary.py — review and resolve or dismiss.*

---

## AUTO-ALERT — 2026-04-23

- No trades today — bot may not be running or no signals fired

*Generated by generate_daily_summary.py — review and resolve or dismiss.*

---

## AUTO-ALERT — 2026-04-24

- No trades today — bot may not be running or no signals fired

*Generated by generate_daily_summary.py — review and resolve or dismiss.*
