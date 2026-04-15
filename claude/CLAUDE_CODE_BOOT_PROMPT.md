# Claude Code Bootstrap Prompt

Paste the prompt below into Claude Code when working in this repository.

```text
You are operating inside the `algo_trading_final` repository. Before doing any substantive work, read `CLAUDE.md` and use it as the primary repo memory. If `AGENTS.md` exists, treat it as parallel repo truth for cross-checking current state.

This repo is a fully autonomous AI trading system with strict safety and truth rules. Follow these rules on every task:

1. Respect repo truth.
- `CLAUDE.md` and `AGENTS.md` are the system memory.
- If code behavior changes, update the relevant memory file(s) and append to `CHANGELOG.md`.
- Do not invent architecture or claim behavior without reading the source.

2. Prefer the live path.
- Focus on the actual live architecture, not legacy/reference code.
- Treat these files as high-risk core path files: `scanner.py`, `signal_engine.py`, `position_manager.py`, `perps_engine.py`, `scheduler/v10_runner.py`, `data/indicators.py`, `ml/feature_builder.py`, `ml/walk_forward_trainer.py`, `ml/model_store.py`, `risk/economics_gate.py`, `learning/post_trade_analyzer.py`, `learning/signal_performance.py`, `learning/dynamic_weights.py`, `notifications/notification_engine.py`, `dashboard/app.py`.
- Do not modify a `DO NOT TOUCH` file unless it is clearly required and you can justify it from evidence.

3. Use the repo-local skills.
- Skill library: `claude/skills/`
- Match the task to the closest skill before acting.
- If multiple skills apply, use the smallest set that covers the task.
- Current skills:
  - `paper-trading-hotfix.md`
  - `trade-forensics.md`
  - `proof-first-validation.md`
  - `dashboard-debug.md`
  - `learning-integrity-audit.md`
  - `release-readiness-check.md`

4. Work evidence-first.
- Read code before editing.
- For incidents and behavior questions, trace the real path through code, logs, and SQLite tables.
- Prefer citing concrete files, functions, SQL tables, test names, and timestamps over general explanations.

5. Verify truthfully.
- Use proof-first validation.
- Run the smallest relevant verification set first, usually under `tests/proof/`.
- If you do not run something, say so plainly.
- For behavior-changing fixes, add or update targeted proof coverage when appropriate.

6. Respect trading-system constraints.
- Never suggest manual approvals as part of normal operation; the system is designed to trade autonomously.
- Never loosen risk controls casually.
- Never contaminate clean learning data paths.
- Never treat replay/synthetic/pre-v10-contaminated data as clean live evidence.
- ISOLATED margin only, never CROSS.

7. Repo hygiene.
- Always use `python3`, not `python`.
- Never commit `.env` or `logs/`.
- Append to `CHANGELOG.md` using: `bash scripts/log_change.sh "description"`.
- Test paper mode before any live-mode changes: `python3 main.py --mode paper`.

8. Communication style.
- Be concise, direct, and evidence-backed.
- Prefer “root cause -> change -> verification -> residual risk”.
- Avoid fluff.

Task routing:
- If the user asks for a bugfix or patch, use `claude/skills/paper-trading-hotfix.md`.
- If the user asks why a trade, entry, exit, or loss happened, use `claude/skills/trade-forensics.md`.
- If the user asks whether a change is verified or safe, use `claude/skills/proof-first-validation.md`.
- If the user reports dashboard inconsistency, wrong metrics, missing counts, or widget failures, use `claude/skills/dashboard-debug.md`.
- If the task touches attribution, trade_features, ML snapshots, contaminated data, integrity exclusions, or Bayesian learning stats, use `claude/skills/learning-integrity-audit.md`.
- If the user asks whether the system is ready, healthy, stable, or close to go-live, use `claude/skills/release-readiness-check.md`.

Default operating sequence:
1. Read `CLAUDE.md`.
2. Read the most relevant skill file(s) in `claude/skills/`.
3. Inspect the relevant code and evidence.
4. Make the smallest correct change or answer.
5. Verify truthfully.
6. Update `CLAUDE.md` / `AGENTS.md` if system behavior changed.
7. Append `CHANGELOG.md`.

When answering, use this structure unless the task is trivial:
- Root cause
- What I changed
- Verification
- Residual risk / next step
```
