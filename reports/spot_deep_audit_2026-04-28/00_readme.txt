Spot Deep Audit — 2026-04-28

Scope
- Repo audited: /Users/joshmacbookair2020/Projects/algo_trading_final
- Data source: logs/trades.db from the canonical repo
- Focus: live spot trading lane only
- Goal: identify all major root causes behind weak spot performance, separate real learning from nominal learning, and document recent-trade metrics

Why this audit exists
- The live spot bot increased trade volume, but recent performance deteriorated badly.
- The owner asked for a deep, evidence-backed audit of the spot system philosophy, strategy, logic, math, and real learning/ML/RBI loops.
- This folder is the durable deliverable.

Files
- 01_spot_system_audit.txt
  What the live spot lane is actually doing now, from candidate generation to exits.
- 02_real_learning_ml_rbi_audit.txt
  Which learning loops are real, which are dormant, which are broken, and which are misleading.
- 03_root_causes_and_propagation.txt
  Root causes, how they propagate, and why they produced the current failure shape.
- 04_recent_trade_history_metrics.txt
  Human-readable recent trade metrics and counterfactuals.
- 05_root_cause_matrix.csv
  Sheet-like issue matrix for quick sorting and operational triage.
- 06_recent_trade_history_metrics.csv
  Sheet-like recent trade metrics in flat form.
- 07_training_and_edge_repair_plan.txt
  A mathematically grounded repair and retraining plan.

Important window definitions
- "Recent live spot failure window" in these docs is anchored to the last spot-learning activity cutoff:
  2026-04-22T21:36:39.390822+00:00
- That cutoff matters because no new spot trade attribution or spot ML snapshots were written after it.

Headline truth
- The recent live spot problem is not one bug.
- It is a stack failure:
  1. spot closes are not feeding the learning loop
  2. spot edge calibration is effectively non-operational
  3. candidate priors are learning a different target than the live scalp lane needs
  4. live ML inference is stale and generic, not spot-adaptive
  5. the live spot gate philosophy is too permissive and mostly admits low-quality pullback trades
  6. the average gross edge is close to flat, so fees finish the trade PnL
