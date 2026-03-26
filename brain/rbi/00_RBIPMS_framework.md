# RBIPMS Framework — algo_trading_final
## Version 1.0 | Created: 2026-03-26

---

## Overview

The standard RBI (Research → Backtest → Incubate) framework covers strategy development up to live deployment but provides no guidance on what happens after. For an autonomous live trading system, that's where most of the risk actually lives.

This document defines the **RBIPMS** lifecycle — six phases every strategy must pass through:

```
R ──→ B ──→ I ──→ P ──→ M ──→ S/R
Research  Backtest  Incubate  Promote  Monitor  Scale / Retire
```

No strategy skips phases. No fast-tracks. Evidence gates every transition.

---

## Phase R — Research

**Goal:** Document a falsifiable signal hypothesis before writing any code.

**Entry criteria:** Idea, market observation, or paper you want to test.

**Required outputs before moving to B:**
- [ ] Signal hypothesis written in plain English: "When [condition X] occurs in regime [Y], price tends to [Z] over [timeframe] horizon"
- [ ] Entry conditions fully specified with numeric thresholds
- [ ] Exit conditions specified: stop, target, time stop
- [ ] Fee model included: break-even gross move = round_trip_fee × R_multiple
- [ ] Regime scope specified: trending / ranging / volatile / any
- [ ] Signal added to `market_data_to_signals()` in `dynamic_weights.py`
- [ ] Prior points assigned in `SIGNAL_PRIOR_PTS` (conservative — start low)
- [ ] Decision log entry in `brain/10_decisions/`

**Break-even formula (3:1 R:R at $250 position):**
```
Coinbase round-trip fee ≈ 1.2% (0.6% × 2)
Target = 4.5%, Stop = 1.5%
Break-even WR = stop / (target + stop) = 1.5 / 6.0 = 25%
Minimum acceptable WR = 30% (5pp margin)
Good WR target = 35%+
```

**Reject criteria (do NOT move to B):**
- Signal is a lagging indicator with no mean-reversion or momentum thesis
- Can't be expressed as a numeric threshold
- No regime scope (works in all regimes → probably works in none)
- Break-even WR > 50% (implies R:R < 1:1 — fee hole too deep)

---

## Phase B — Backtest

**Goal:** Validate the hypothesis on historical data using walk-forward OOS methodology.

**Entry criteria:** Phase R complete. Signal code implemented.

**Required tests:**
1. **In-sample fit test:** Full period (90 days). Must pass validator gate.
2. **Walk-forward OOS test:** See `brain/rbi/01_backtest_standards.md` for exact spec.
3. **Regime breakdown:** Show results by regime (trending/ranging/volatile). A strategy that only works in trending should be labeled as trending-only.
4. **Fee sensitivity:** Confirm results hold if fees increase 25%.

**Pass/fail gate (all four must pass):**
| Metric | Minimum | Target |
|--------|---------|--------|
| Win rate | ≥ 30% | ≥ 35% |
| Profit factor | ≥ 1.2 | ≥ 1.5 |
| Sharpe ratio | ≥ 0.5 | ≥ 1.0 |
| Max drawdown | ≤ 20% | ≤ 15% |
| OOS trade count | ≥ 30 | ≥ 50 |

**Fail actions:**
- If fails on WR but passes PF → investigate R:R structure, may be salvageable
- If fails on DD → reduce position size and re-run, or reject
- If passes in-sample but fails OOS → overfitting; reject without parameter changes

**Artifacts to archive in `backtest_results` table:**
- `strategy_name`, `variant`, `symbol`, `timeframe`
- `period_start`, `period_end` (in-sample + OOS periods)
- `win_rate`, `profit_factor`, `sharpe`, `max_drawdown`, `total_trades`
- `passed` (boolean), `notes` (OOS params)

---

## Phase I — Incubate

**Goal:** Validate live execution in paper trading before real money.

**Entry criteria:** Phase B complete, all gates passed, backtest archived.

**Minimum incubation period:** 14 calendar days. No exceptions. No fast-track.

**Position sizing during incubation:** 50% of target live size.
- If live position size = $250, incubation size = $125
- This is intentional — incubation is not practice, it has skin in the game (paper P&L is real discipline)

**Daily checks (automated via `check_readiness.py`):**
- [ ] Win rate tracking
- [ ] No system halts
- [ ] No single day > -4% of account
- [ ] Fees within daily limit

**Circuit breaker:** 3 consecutive losing days → pause strategy, add 7 days to incubation minimum, review debate reasoning in brain/05_trade_reviews/.

**Required artifacts during incubation:**
- At least 2 manually reviewed trade decisions (read the debate, check agent reasoning)
- 1 weekly summary filed in `brain/07_weekly_summaries/`

**See `brain/rbi/02_incubation_playbook.md` for full checklist.**

---

## Phase P — Promote

**Goal:** Formal graduation from paper to live.

**Entry criteria:** All incubation criteria met.

**Promotion gate (all must pass):**

*From `check_readiness.py` (existing 7 criteria):*
1. ≥ 14 calendar days paper trading
2. ≥ 30 completed paper trades
3. Win rate ≥ 52% (note: this is higher than B-phase target — live paper has less noise than backtest)
4. Zero system halts in last 7 days
5. Positive total paper P&L
6. No single day worse than -4% of account
7. Average P&L per trade ≥ $0.10

*Additional RBIPMS criteria (3 new):*
8. Live paper WR ≥ backtest OOS WR - 10pp (live can be worse than backtest, but not by more than 10pp)
9. Agent agreement ≥ 50% on winning trades (agents are agreeing on the right entries)
10. No -$10 single trade (stops are working)

**Promotion action:**
- Set `PAPER_TRADING=false` in `.env`
- Log promotion in `brain/10_decisions/Decision Log.md`
- File promotion report: `brain/05_trade_reviews/YYYY-MM-DD_promotion.md`
- Set position size to 75% of target for first 30 days live (not full size immediately)

---

## Phase M — Monitor

**Goal:** Ongoing performance surveillance to catch strategy decay early.

**Entry criteria:** Live trading active.

**Automated monitoring (live_backtest_validator.py):**
- 30-day rolling backtest runs every 4 hours on top 3 pairs
- Results injected into every debate as context
- Agents see "ROLLING BACKTEST: ✅ PASS" vs "❌ FAIL"

**Manual monitoring (weekly, ~15 minutes):**
- Review `brain/07_weekly_summaries/` auto-generated file
- Check signal leaderboard: are top signals still the ones with highest prior points?
- Check agent accuracy: are agents with high prior weight actually winning?
- Check regime distribution: is strategy trading in its intended regime?

**Auto-demotion triggers (logged as system_events, halt trading for strategy):**
- Rolling 14-day WR < 20% (below break-even)
- Rolling 14-day PF < 0.8 (losing more than winning on a $ basis)
- 3 consecutive halts in 7 days (risk rules firing repeatedly = something wrong)
- Live WR diverges > 20pp below seeded backtest WR after 50 trades (live path is fundamentally different from backtest)

**When auto-demotion triggers:**
1. Strategy paused (not retired yet)
2. Root-cause review in `brain/05_trade_reviews/`
3. Decision: fix and re-incubate, or retire

**See `brain/rbi/03_strategy_lifecycle.md` for full monitor-phase criteria.**

---

## Phase S/R — Scale or Retire

**Goal:** Either grow position sizes based on evidence, or formally retire the strategy.

### Scale path:

**Scaling criteria (all must be true):**
- ≥ 30 consecutive profitable days
- Rolling 30-day WR ≥ 40%
- No auto-demotion triggers in last 30 days
- Account size has grown (don't scale on a flat or down account)

**Scaling increments:**
- Start live: 75% of target size ($187 on $250 target)
- First scale: 100% ($250)
- Second scale: 125% ($312) — requires 60 profitable days
- Maximum: 150% ($375) — requires 90 profitable days and explicit decision log entry

### Retire path:

**Retirement criteria (any one sufficient):**
- Rolling 90-day WR < 25% after full parameter review
- Strategy no longer fires (signal conditions never met in current market)
- Underlying market structure has changed (e.g., exchange delists the pair, regulatory change)
- Better strategy available that covers the same regime/signal space

**Retirement actions:**
- Remove strategy from `CRYPTO_PAIRS` scan or disable via config flag
- Archive parameters in `brain/03_parameter_sets/`
- Log retirement in `brain/10_decisions/Decision Log.md` with evidence
- Keep `signal_stats` data — don't delete (it's historical evidence, useful for research)

**Retirement is not a failure.** A strategy that ran for 6 months and returned +15% before being retired is a success. A strategy that runs forever with -5% YTD is a failure. Know when to stop.

---

## Framework Health Checks

Run quarterly:
1. How many strategies are in each phase? (Should not have > 3 in Incubate simultaneously)
2. How many strategies in Monitor have had auto-demotion triggers? (> 2 in 90 days = review signal design)
3. Is the Bayesian weight distribution healthy? (Are live weights diverging from priors in expected directions?)
4. Is the meta-learner producing consistent recommendations? (Consistent = signal; noisy = not enough data)

---
*Framework version 1.0 — update this file when any phase definition changes*
*Reference: RBI article (Polymarket-based) adapted for Coinbase crypto, 3:1 R:R, AI-debate architecture*
