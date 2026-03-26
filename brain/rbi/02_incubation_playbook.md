# Incubation Playbook
## Version 1.0 | Created: 2026-03-26

---

## What Is Incubation?

Incubation is the period when a strategy has passed backtesting but has NOT yet earned the right to trade real money. It runs in paper mode with reduced position sizing while you verify that the live execution matches backtest expectations.

**Incubation is not practice.** It is a live test under real market conditions with full accounting discipline. If the strategy fails incubation, it does NOT go live.

---

## Pre-Incubation Checklist

Before starting the 14-day clock:

**Strategy verification:**
- [ ] Phase B complete — all backtest gates passed (WR ≥ 30%, PF ≥ 1.2, Sharpe ≥ 0.5, dd ≤ 20%, ≥ 30 OOS trades)
- [ ] Walk-forward OOS passed ≥ 2 folds
- [ ] Fee model verified — break-even WR confirmed in backtest results
- [ ] Regime scope documented — what regime(s) does this strategy target?

**System configuration:**
- [ ] Signal added to `market_data_to_signals()` in `dynamic_weights.py`
- [ ] Prior points set in `SIGNAL_PRIOR_PTS` (use conservative value — tune after real data)
- [ ] Signal triggers registered in `signal_triggers` (so agents see the evidence)
- [ ] `PAPER_TRADING=true` in `.env` (must be)
- [ ] Position size set to **50% of target live size** during incubation

**Documentation:**
- [ ] Decision log entry in `brain/10_decisions/Decision Log.md` (why starting incubation now)
- [ ] Backtest result archived in `backtest_results` table
- [ ] Incubation start date recorded here: ___________

---

## Incubation Position Sizing

| Phase | Size | Rule |
|-------|------|------|
| Backtest | N/A | Math-only |
| Incubation | 50% of live target | $125 if live target is $250 |
| Early live (first 30 days) | 75% of live target | $187 |
| Full live | 100% | $250 |
| First scale | 125% | $312, after 30 profitable days |

**Why 50% in incubation?** Paper trades don't cost money, but the DISCIPLINE of running at half-size is intentional. It forces the system to prove it can generate positive expectancy at a realistic size, not an inflated one.

---

## Daily Checklist (14-Day Minimum)

For each day of incubation, the following must hold. Any failure extends incubation by the noted penalty.

**Day N (fill in dates):**

| Check | Pass Criteria | Penalty if Fail |
|-------|--------------|-----------------|
| No system halt | Zero halts for any reason | +3 days |
| No single-day loss > -4% | Max daily loss rule not triggered | +7 days |
| Win rate trend | Rolling 7-day WR > 20% (not absolute fail, but flag) | Log and review |
| Fee drag | Daily fees < 10% of account ($50) | +1 day per trigger |
| Debate quality | At least 1 debate reviewed manually per 5 trades | N/A (discipline check) |
| Circuit breaker | < 3 consecutive losing days | +7 days (see below) |

**Circuit breaker detail:** If 3 consecutive losing days occur:
1. Pause incubation (do not stop the clock, but stop trading for 7 days)
2. Review the 3 losing debates in `brain/05_trade_reviews/`
3. Identify: was this a signal failure, regime mismatch, or execution issue?
4. Document finding in decision log
5. Resume incubation (clock continues from where it paused)

---

## Mid-Incubation Review (Day 7)

At the 7-day mark, run a formal mid-point review:

```bash
python3 scripts/check_readiness.py
```

**Review questions:**
1. Is the WR directionally aligned with backtest projections? (Within 15pp = acceptable)
2. Are agents agreeing on entries that win? (Check `agent_stats` table)
3. Is the strategy finding trades in its intended regime? (Check `trade_attribution` regime distribution)
4. Is the meta-learner's feedback consistent with expectations? (Check `meta_recommendations`)

**If mid-review shows WR < 15%:**
- Do NOT halt — 7 days is too small a sample
- Flag in decision log
- Commit to reviewing 3 specific losing trades manually
- Continue incubation but monitor closely

---

## Incubation Completion Checklist

At end of minimum 14-day period (or when `check_readiness.py` first passes):

**Original 7 criteria from `check_readiness.py`:**
- [ ] ≥ 14 calendar days paper trading
- [ ] ≥ 30 completed trades
- [ ] Win rate ≥ 52%
- [ ] Zero system halts in last 7 days
- [ ] Positive total paper P&L
- [ ] No single day worse than -4% of account
- [ ] Average P&L per trade ≥ $0.10

**Additional RBIPMS criteria:**
- [ ] Live paper WR ≥ (backtest OOS WR - 10pp) — live can underperform backtest, but not by more than 10 percentage points
- [ ] Agent agreement ≥ 50% on winning trades (look at `agent_stats` for buy_votes on won trades)
- [ ] No single trade hit > -$10 loss (stops are being respected)
- [ ] At least 2 debates manually reviewed and found reasonable
- [ ] Decision log promotion entry drafted

**Fast-track mode:** DISABLED. The original `check_readiness.py` has a fast-track that requires only 2 days + 10 trades. This is not acceptable for real-money deployment. Use the full 14-day minimum.

---

## Promotion Decision

When all checklist items pass:

1. **Write promotion report** — `brain/05_trade_reviews/YYYY-MM-DD_promotion_[strategy].md`
   - Include: final WR, PF, Sharpe, key observations, any surprises vs backtest
   - Note: backtest WR vs live WR delta

2. **Update decision log** — `brain/10_decisions/Decision Log.md`
   - Date, strategy name, metrics, rationale for promotion

3. **Set live configuration:**
   ```
   PAPER_TRADING=false   (in .env)
   CRYPTO_POSITION_SIZE_USD=187  (75% of $250 target for first 30 days live)
   ```

4. **Notify:** Log a `system_events` entry with source='notify' and type='promotion' so the dashboard shows it

5. **Move to Monitor phase** — see `brain/rbi/03_strategy_lifecycle.md`

---

## Failure to Pass Incubation

If after 30 calendar days the strategy has not passed all checklist items:

- **Re-evaluate:** Is the sample size large enough? If < 30 trades in 30 days, extend incubation (strategy isn't finding signals)
- **Root cause:** Review 5 losing trades. Is there a systematic failure mode?
- **Options:**
  - Fix the signal threshold and restart incubation from day 1
  - Restrict to a specific regime and restart
  - Retire the strategy (return to Research phase with new hypothesis)

**Never promote a strategy that hasn't passed the checklist.** The cost of a failed live strategy (real money, real fees, real losses) far exceeds the cost of extending incubation.

---

## Common Incubation Failure Modes

| Failure mode | Symptom | Resolution |
|-------------|---------|------------|
| Regime mismatch | WR ok in trending, fails in ranging | Add regime filter to entry condition |
| Signal overfitting | Good in backtest, flat in paper | Raise signal threshold; reduce conviction points |
| Fee drag | Positive gross, negative net | Raise ATR fee-floor guard threshold |
| Execution gap | Entries taken that backtest would skip | Backtest-to-live logic review |
| Time-of-day effects | Losses concentrated in specific session | Session analyst adjustments |
| Crowded trades | Multiple pairs entering same signal simultaneously | Cross-pair correlation filter |

---
*Playbook version 1.0 — update when check_readiness.py criteria change*
