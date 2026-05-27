# Agent Panel

#active #agent

**Status as of: 2026-03-25**
**Current panel: 5 agents for FULL debate, 3 for QUICK debate**

---

## CONFIRMED: CURRENT ACTIVE AGENTS

### Full Debate Panel (used for all crypto)

| Agent Key | Identity | DBZ Name | Role |
|-----------|----------|----------|------|
| `microstructure` | Sasha Stoikov / Rama Cont | Vegeta | OBI/TFI order flow + microprice vs midprice |
| `fee_discipline` | Fee Economics / Albers et al. | Krillin | p_min gate; 2.4% min gross move required |
| `flow_tape` | Coinbase Tape / Microstructure | Piccolo | TFI 60-sec window; spread_bps; Kyle lambda; trade intensity |
| `regime_volatility` | Andersen-Bollerslev / TTM Squeeze | Frieza | RV ratio; BB-Keltner squeeze; vol expansion/compression |
| `manipulation_risk` | Kose John / Amin Nejat | Tien | Spoofing detection; news risk; liquidation cascade |

### Quick Debate Panel (3-agent, for initial fast scan)
- `microstructure`, `fee_discipline`, `flow_tape`

---

## HARD VETO AGENTS (enforced in code, not just prompt text)

Since v3.6, two agents have HARD VETO power enforced at code level in `debate_engine.py`:

1. **fee_discipline**: If fees cannot be covered by expected move → VETO, early return
2. **manipulation_risk**: If OBI/TFI conflict detected → VETO, early return

These were previously prompt-only vetoes. Bug fixed in v3.6 — they now actually stop execution.

---

## EXIT REVIEW AGENTS (Extended Thinking)

| Agent | Identity | Veto Condition |
|-------|----------|----------------|
| Tudor Jones | Trend following master | "Is the stop still valid?" |
| Soros | Macro reflexivity | "Is the thesis still intact?" |
| Simons | Statistical arbitrage | "Is the statistical pattern still holding?" |

**Logic: ANY ONE agent saying EXIT = exit immediately**
This is intentionally asymmetric. Soros alone can pull us out of a trade.

---

## RETIRED AGENTS (from v2.0/v3.0 8-agent panel)

| Agent Key | Retired In | Reason |
|-----------|-----------|--------|
| `session_breakout` | v3.5 | Absorbed into session_active flag in job_runner |
| `williams` | v3.5 | Absorbed into pre-filter conviction scoring (Williams %R signal) |
| `quant_edge` | v3.5 | Logic absorbed into risk_manager (Kelly) + indicators (OU, Kalman) |

---

## AGENT QUALITY TRACKING

**As of 2026-03-25: No production data exists yet.**

### What to track (once paper trading starts):
- Per-agent vote on each debate
- Whether the debate winner matched outcome
- Which agents called trades that were profitable after fees
- Which agents vetoed trades that would have been profitable (false negatives)
- Whether `manipulation_risk` is vetoing too aggressively or not aggressively enough

### Hypothesis to test:
- `microstructure` may be the most discriminating agent on 1-min candles
  (OBI/TFI is direct demand signal, not derived from price history)
- `fee_discipline` may block too many trades when position size is $250
  (fee math is different at $250 vs $500 — R:R calculation changes)
- `manipulation_risk` hard veto may be too sensitive to normal bid/ask fluctuations

---

## AGENT DESIGN NOTES

### Why 5 instead of 8?
The original 8-agent panel included agents whose logic was either:
- Already in the pre-filter (Williams %R, session timing)
- Better handled in code than in prompt (OU half-life, Kelly sizing)
- Redundant with microstructure (quant_edge's liquidity check duplicated OBI/TFI)

5 agents reduces API cost per debate and forces each agent to have a clear, non-overlapping role.

### Why asymmetric exits?
A missed exit costs more than a premature exit at $250 position sizes.
With 1.5% stops on $250, one bad exit costs $3.75 + fees.
One trapped position that reverses hard can cost 5-10%.
The asymmetry on exits is correct for small account survival.

### Agent prompts
Defined in `strategies/ai_agents/analyst_agents.py` with prompt caching.
Each agent receives: price data, signal triggers matrix, memory context from LanceDB.
