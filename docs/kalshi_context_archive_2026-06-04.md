## Kalshi Context Archive

Date: 2026-06-04
Repo: `/Users/joshmacbookair2020/Projects/algo_trading_final`
Version: `19.9.8`
Commit: `60c3fd9f160d157e60b85e389ca4972a3e1cad6a`

### Current Operating Truth

- Active repo scope is Kalshi weather only.
- Canonical execution entrypoint is `sniper_cron.py`.
- Canonical long-lived runtime is `execution_daemon.py`.
- Canonical Telegram process is `telegram_daemon.py`.
- Broker state is the live source of truth for exposure.
- Runtime DB is `logs/trades.db` in WAL mode.

### Verified State On Archive Date

- Full proof suite passed: `102 passed`.
- Kalshi balance verified directly via `python3 scripts/verify_kalshi_connection.py`.
- Verified live balance during archive pass: `$165.00`.
- Verified discovery during archive pass: `382 active contracts`.
- Verified quote path during archive pass: healthy.

### Most Important Strategy Findings

1. Weather state is keyed at the series level, not the contract-date level.
2. Ensemble pricing currently uses only the first 26 forecast hours.
3. Intraday exits use current-day watermarks, not settlement-day-aware state.
4. Weather alpha can still be hard-vetoed by technical `q_hat` divergence logic.
5. Strategy winner selection is confidence-first, not EV-first.
6. The RBI learner is observational only and does not feed back into live entry logic.
7. Quote pairing can spuriously produce `missing_quotes`.
8. Several runtime knobs are dead or only partially wired.

### New Project Queue Added At Archive Time

1. Storage resilience and low-disk operating model.
2. Exit-strategy audit and redesign.
3. Telegram Oracle balance truth and operator-intelligence upgrade.

### Project-Specific Truth Captured

#### Storage

- Current repo footprint is small relative to the machine.
- Repo size observed during archive pass: about `64M`.
- Active runtime/logs footprint observed during archive pass: about `39M`.
- DB size observed during archive pass: about `14.2M`.
- Storage guard is active and currently healthy.
- Guard threshold is `2048 MB`.

#### Exit Logic

- Current exit stack already includes:
  - generic 85c take-profit
  - model invalidation stop
  - bracket bust exits
  - late-day lock exits
  - HRRR-based spoiler exits
  - no-bid liquidity protection in flattening
- Exit stack is real, but it is not yet a unified, contract-date-aware framework.

#### Telegram / Oracle

- Direct Kalshi balance path works in broker code.
- Telegram `/status` path calls broker balance directly.
- Oracle reasoning context currently reads from `system_state`, not broker truth.
- `system_state.update_kalshi()` and `update_strategy()` appear unwired.
- Oracle command whitelist blocks `python3 scripts/verify_kalshi_connection.py`.

### Recommended Immediate Priority Order

1. Fix Telegram/Oracle truth wiring first.
2. Fix weather contract-date state and expiry-aware exits next.
3. Decide whether to move dev/runtime weight off the Mac after measuring non-repo disk pressure.

### Notes

- This archive is a checkpoint, not a deployment approval.
- Any new implementation branch should preserve this file as the pre-change state reference.
