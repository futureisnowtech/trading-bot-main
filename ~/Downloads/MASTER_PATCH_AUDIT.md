# MASTER PATCH AUDIT LOG (v19.1.KALSHI)
Date: 2026-06-03

## 1. PRE-FLIGHT: SQLite Schema Reset
- **Action:** Dropped `forecast_positions` table.
- **Reason:** Outdated schema used `entry_price` instead of `entry`. Dropping ensures the new integer-standard schema is applied on next boot.

## 2. THE BRAIN: forecast/strategy_engine.py
- **Patch 1 (EV/Fee Fix):**
  - *Old:* `ev_chosen = best_confidence - p_cost`
  - *New:* `ev_chosen = best_confidence - p_cost - KALSHI_FEE_PER_CONTRACT`
  - *Proof:* Mathematical parity. By deducting the $0.07 fee *before* the `EV_THRESHOLD` check, we eliminate trades where the fee eats 100% of the statistical edge.
- **Patch 2 ($10 Ceiling):**
  - *Old:* Simple max check.
  - *New:* Try/Except Import guard + non-negotiable clip.
  - *Proof:* Hard Sovereign Mandate enforcement.
- **Patch 3 (Hub Fix):**
  - *Old:* Declared empty map, never scanned DB truth.
  - *New:* Iterative scan of `open_positions` with hub mapping.
  - *Proof:* Fixes the 'Phantom Hub' leak where the bot ignored concentration risk.

## 3. THE HEART: execution/kalshi_broker.py
- **Patch 1 (Weather Gate):**
  - *Action:* Injected category/ticker check in `discover_markets`.
  - *Reason:* Prevents system bloat from non-alpha markets (politics, economy).
- **Patch 2 (Zero-Dollar Fix):**
  - *Action:* Replaced naive order insertion with a `status == 'executed'` check.
  - *Reason:* Prevents $0.00 'Ghost Trades' caused by logging resting/pending orders as filled positions.

## 4. THE LUNGS: forecast/runner.py
- **Patch (Safe Liquidity):**
  - *Action:* Replaced `flatten_position` (Market) with Liquidity-Checked Limit orders.
  - *Reason:* Market orders on illiquid prediction markets cause massive slippage. New logic checks bid volume before firing.

## 5. THE RULES: config.py
- **Action:** Explicitly appended `KALSHI_MAX_USD_PER_POSITION` and `KALSHI_FEE_PER_CONTRACT`.

## 6. THE EYES: dashboard/db.py
- **Action:** Re-aligned `forecast_positions` schema to `ticker, qty (INT), entry (REAL), side, unrealized_pnl`.

## 7. THE VOICE: notifications/telegram_bot.py
- **Action:** Realigned SQL queries to use `entry` column. Fixed thread collision lock.

## 8. THE PULSE: monitoring/metrics.py
- **Action:** Scrubbed remaining crypto asset-label metrics. Focused strictly on weather tickers.
