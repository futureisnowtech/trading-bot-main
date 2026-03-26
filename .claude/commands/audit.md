---
name: audit
description: Full system health audit — signals, agents, risk, P&L, ML gate, and strategy performance
argument-hint: "[--lane=crypto|equity|all] [--period=today|7d|30d]"
allowed-tools:
  - Read
  - Bash
  - Glob
---

Run a comprehensive audit of the trading system's current health and performance.

## Process

### 1. Daily Summary

Call `get_daily_summary` via MCP. Display:
- Net P&L (after fees)
- Win rate today vs all-time
- Fees as % of account
- Halt status

### 2. Signal Leaderboard

Call `get_signal_stats(regime="all", min_fires=5)`.

Build a ranked table:
| Signal | Regime | Win Rate | Bayesian Pts | Fires |

Flag signals with win_rate < 40% (underperforming) and win_rate > 65% (potential overfitting).

### 3. Agent Accuracy

Call `get_agent_accuracy`. For each of the 3 agents (funding_regime, momentum_structure, risk_economics):
- Show accuracy %
- Flag if any agent is below 45% accuracy (worse than coin flip)
- Flag if agents are highly correlated (all 3 always agree = no debate value)

### 4. Open Positions

Call `get_positions`. For each open position:
- Time in trade
- Unrealized P&L
- Distance from stop (in %)
- Distance from target (in %)
- Flag any position > 4h old in crypto (stagnant trade risk)

### 5. ML Signal Gate

Call `get_ml_signal` for BTC-USDC, ETH-USDC, SOL-USDC. Display p_win for each.
Check current ML_SIGNAL_MIN_PROB from config.py.
Flag if p_win is consistently near the threshold (gate calibration check).

### 6. Risk Status

Read `risk/risk_manager.py` status_report. Display deployed capital, halt status, positions count.

### 7. Recent Notifications

Call `get_notifications(limit=20)`. Highlight any ERROR or HALT level events.

### 8. Audit Score

Score 6 dimensions (0–10 each):
1. P&L health (positive/trending up = 10)
2. Signal quality (average Bayesian win rate)
3. Agent calibration (all agents > 50% accuracy)
4. Risk compliance (no limit breaches)
5. ML gate health (model trained + filtering correctly)
6. System stability (no halts/errors in last 24h)

Output: **Audit Score: XX/60** with breakdown.

### 9. Top 3 Action Items

Based on findings, list the 3 highest-priority things to investigate or fix.

## Output Format

Use clear section headers. Lead each section with a one-line verdict.
End with the audit score and action items.
