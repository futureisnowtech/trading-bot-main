# Strategy Lifecycle — Promotion, Monitoring & Retirement
## Version 1.0 | Created: 2026-03-26

---

## Lifecycle Overview

```
[Research] → [Backtest] → [Incubate] → [Live - Early] → [Live - Full] → [Live - Scaled]
                                              ↓ auto-demotion triggers
                                         [Paused/Review]
                                              ↓ if not fixed
                                         [Retired]
```

Every strategy has an explicit lifecycle state. No strategy is "permanent." Evidence drives every state transition.

---

## Current Strategy Status

| Strategy | Lifecycle State | Phase Entry Date | Live WR | Notes |
|----------|----------------|-----------------|---------|-------|
| crypto_macd | Live - Full | 2026-03-22 | TBD | Primary crypto strategy |
| crypto_mean_reversion | Live - Full | 2026-03-24 | TBD | Ranging/volatile regime |
| futures_scalper | Live - Early | 2026-03-22 | TBD | MES ES scalper |
| crypto_perp | Live - Early | 2026-03-24 | TBD | Bybit USDT perp |
| equity_momentum | Retired | 2026-03-23 | N/A | EQUITY_ENABLED=false ($500 account) |

*Update this table as strategies change state.*

---

## Promotion Criteria (Incubate → Live)

See `brain/rbi/02_incubation_playbook.md` for full checklist.

**Summary:** All 7 `check_readiness.py` criteria + 3 RBIPMS additions:
1. Live paper WR ≥ (backtest OOS WR - 10pp)
2. Agent agreement ≥ 50% on winning trades
3. No single trade > -$10 loss

---

## Live - Early Phase (First 30 Days Live)

**Position sizing:** 75% of target ($187 if target is $250)

**Weekly review:**
- Rolling 14-day WR tracked vs backtest OOS WR
- Flag if divergence > 10pp
- Review 1 winning + 1 losing debate per week

**Graduation to Live - Full:**
- ≥ 30 live (real money) trades
- Rolling 14-day WR ≥ 30%
- No auto-demotion triggers in the 30 days
- Positive cumulative P&L

**Graduation action:**
- Set position size to 100% ($250)
- Log in decision log

---

## Live - Full Phase (Steady State)

**Position sizing:** 100% of target ($250)

**Monthly review (automated via generate_daily_summary.py + weekly summaries):**
- Rolling 30-day WR tracked
- Rolling 30-day PF tracked
- Signal leaderboard reviewed — are weights drifting in expected direction?
- Meta-learner recommendations reviewed

**Auto-demotion triggers (any one triggers Paused/Review state):**

| Trigger | Condition | Action |
|---------|-----------|--------|
| Performance decay | Rolling 14-day WR < 20% | Pause strategy, investigate |
| P&L decay | Rolling 14-day PF < 0.8 | Pause strategy, investigate |
| Halt storm | ≥ 3 system halts in 7 days | Pause strategy, risk review |
| Live-backtest divergence | Live WR < (seeded backtest WR - 20pp) after ≥ 50 trades | Pause strategy, pipeline review |
| Signal dry-up | < 5 trades in 14 days (strategy not finding entries) | Flag (not pause) — review signal thresholds |

**Manual demotion triggers:**
- Major exchange change (new fee structure, new order types)
- Regulatory change affecting the traded pairs
- Evidence that signal is being front-run or gamed
- Better strategy available in same regime space

---

## Live - Scaled Phase

**Scaling criteria:**
- ≥ 30 consecutive profitable days (not calendar days — trading days with at least 1 trade)
- Rolling 30-day WR ≥ 40%
- No auto-demotion triggers in last 30 days
- Account balance has grown ≥ 20% from start of Live - Full phase

**Scaling increments (require decision log entry for each):**

| Scale Level | Position Size | Requirement |
|------------|--------------|-------------|
| Level 0 (Early) | $187 (75%) | First 30 days live |
| Level 1 (Full) | $250 (100%) | 30 live trades, WR ≥ 30% |
| Level 2 | $312 (125%) | 30 consecutive profitable days |
| Level 3 | $375 (150%) | 60 consecutive profitable days + account +20% |
| Level 4+ | STOP | Do not scale beyond 150% without re-running the full RBIPMS framework for the new size |

**Why not scale further?** At $500 account, 150% of $250 = $375 per position. With 5 max positions that's $1,875 deployed — 375% of account using leverage. This is the risk ceiling for the current account size. At $1,000 account, recalibrate all sizing.

---

## Paused / Review Phase

Entered when auto-demotion triggers or manual demotion occurs.

**Immediate actions:**
1. Strategy stops entering new trades (existing positions managed to exit)
2. Root-cause review begins within 24 hours
3. Review filed in `brain/05_trade_reviews/YYYY-MM-DD_review_[strategy].md`

**Review template:**
```markdown
## Strategy Review — [Name] — [Date]

### Trigger
[What auto-demotion condition fired?]

### Data
- Pause-date rolling 14-day WR: ____%
- Pause-date rolling 14-day PF: ____
- Total live trades: ____
- Seeded backtest WR for this period: ____%

### Root Cause Analysis
[Was this a signal failure, regime mismatch, execution issue, or market regime shift?]

### Evidence
[3-5 specific losing trades reviewed, with debate reasoning]

### Decision
[ ] Fix: [describe change] — return to Incubate phase
[ ] Regime restrict: [specify regime] — return to Incubate with restriction
[ ] Retire: [evidence supporting retirement]

### Retirement / Re-incubation date: ___________
```

**Time limit on Paused state:** Maximum 14 days. Either fix and re-incubate, or retire. A strategy sitting in Paused for > 14 days is either being avoided (fix it or retire it) or forgotten (retire it).

---

## Retirement

**Retirement criteria (any one sufficient):**
- Rolling 90-day WR < 25% after at minimum 2 parameter reviews
- Strategy has been Paused > 3 times in 12 months
- Underlying signal is no longer measurable (exchange removed the data feed)
- Better strategy validated and promoted in same regime space
- Account grows beyond $2,000 and strategy's per-trade sizing no longer makes economic sense

**Retirement actions:**
1. All open positions exited gracefully (not force-closed)
2. Strategy disabled in config: remove from `CRYPTO_PAIRS` scan or add `[STRATEGY]_ENABLED=false` to `.env`
3. Parameters archived: `brain/03_parameter_sets/[strategy]_final_YYYY-MM-DD.json`
4. Decision log entry: retirement date, total trades, cumulative P&L, key lessons
5. Signal stats data PRESERVED in `signal_stats` table (never delete — historical evidence)
6. `brain/05_trade_reviews/YYYY-MM-DD_retirement_[strategy].md` filed

**Retirement is evidence-based, not emotional.** Every retired strategy teaches something. The signal_stats data feeds future research. The decision log captures the lesson.

---

## Strategy Health Dashboard

The following can be queried from SQLite at any time:

```sql
-- Rolling 14-day performance per strategy
SELECT strategy,
       COUNT(*) as trades,
       ROUND(AVG(CASE WHEN pnl_usd > 0 THEN 1.0 ELSE 0.0 END), 3) as win_rate,
       ROUND(SUM(CASE WHEN pnl_usd > 0 THEN pnl_usd ELSE 0 END) /
             NULLIF(ABS(SUM(CASE WHEN pnl_usd < 0 THEN pnl_usd ELSE 0 END)), 0), 2) as profit_factor,
       ROUND(SUM(pnl_usd), 2) as total_pnl
FROM trades
WHERE ts > datetime('now', '-14 days')
  AND pnl_usd IS NOT NULL
GROUP BY strategy;
```

```sql
-- Auto-demotion trigger check
SELECT strategy,
       COUNT(*) as trades_14d,
       AVG(CASE WHEN pnl_usd > 0 THEN 1.0 ELSE 0.0 END) as wr_14d,
       SUM(pnl_usd) as pnl_14d
FROM trades
WHERE ts > datetime('now', '-14 days')
  AND pnl_usd IS NOT NULL
GROUP BY strategy
HAVING wr_14d < 0.20 OR pnl_14d < -20.0;
-- Results here = auto-demotion candidates
```

---

## Lessons From Past Lifecycle Events

*Populate this section as strategies cycle through the lifecycle. Each entry is evidence for future research.*

| Date | Strategy | Event | Key Lesson |
|------|----------|-------|------------|
| 2026-03-23 | equity_momentum | Retired (EQUITY_ENABLED=false) | $500 account too small for equity; PDT limits too constraining. Revisit at $2,500+. |
| — | — | — | — |

---
*Lifecycle version 1.0 — update as strategies change state or new patterns emerge*
