# Known Constraints

#active

**Status as of: 2026-03-25**
All constraints are CONFIRMED from code/config.py unless labeled otherwise.

---

## ACCOUNT CONSTRAINTS

| Constraint | Value | Source |
|-----------|-------|--------|
| Account size | $500 (starting) | CONFIRMED — config.py ACCOUNT_SIZE default |
| Max deployed capital | 90% | CONFIRMED — MAX_DEPLOYED_PCT = 0.90 |
| Cash reserve | 10% | CONFIRMED |
| Position size crypto | $250 | CONFIRMED — .env CRYPTO_POSITION_SIZE_USD |
| Position size equity | $250 | CONFIRMED — .env EQUITY_POSITION_SIZE_USD |
| Perp position size | BELIEVED $250 | BELIEVED — .env default |

---

## RISK HARD RULES (NO AI CAN OVERRIDE)

| Rule | Value |
|------|-------|
| Max risk per trade | 1% of account |
| Max daily loss → halt | 4% of account ($20 on $500) |
| Max crypto positions | 5 |
| Max equity positions | 3 |
| Max equity trades/day | 3 (PDT cash compliance) |
| Crypto stop loss | 1.5% |
| Crypto take profit | 4.5% (3:1 R:R maintained) |
| Equity stop loss | 2.5% |
| Equity take profit | 7.5% (3:1 R:R maintained) |
| Daily fee cap | 10% of account ($50 on $500) |
| Circuit breaker | 4 consecutive strategy losses → pause |
| Min agent agreement | 2 of 5 agents explicit (not percentage) |
| Symbol cooldown | 20 min after losing crypto exit |
| Stagnant trade exit | 45 min with < 15% target progress |
| Max crypto hold | 12 hours |
| Max equity hold | 6 hours |

---

## PLATFORM CONSTRAINTS

### Coinbase
- 0.6% taker fee / 0.4% maker fee
- Round-trip cost: ~1.2% minimum
- Min gross move to trade: 2.4% (fee floor guard in code: ATR/price < 0.4% = skip)
- API requires "Advanced Trade" scope with View+Trade

### Tradovate (MES Futures)
- No free demo API tier — paper simulation uses yfinance ES prices
- Current front month: MESM6 (June 2026, expires before quarter rollover)
- Paid subscription required for live API

### Alpaca (Equity)
- Currently wired but disabled (EQUITY_ENABLED=false)
- Webull 403-blocked — Alpaca is the actual broker
- PDT rules apply (3 day trades in 5 days with < $25k)

### Bybit (Perp)
- BYBIT_TESTNET=true in .env by default
- Fill BYBIT_API_KEY/SECRET to activate live
- Leverage cap: 10× (halved from 20× in v4.0 de-risk)

---

## INFRASTRUCTURE CONSTRAINTS

| Item | Constraint |
|------|-----------|
| Python version | 3.14 — has .pyc file lock bug (EDEADLK) on some restarts |
| launchd auto-restart | Registered but uses subprocess.Popen bypass (not launchctl) due to EDEADLK |
| TradingView webhook | Requires ngrok running — free tier URL changes on restart |
| LanceDB | Kelly sizing only activates after 15 completed trades |
| Anthropic API | CLAUDE_MODEL = claude-sonnet-4-6 (always latest) |

---

## AMYGDALA REMOVAL RULES (HARDCODED)

These are the emotional safeguards. They cannot be overridden by any parameter change.

1. Never chase — skip if price moved > 3% since signal
2. Never average down — one position per symbol, ever
3. Stop losses are sacred — never moved wider after entry
4. Wins don't justify ignoring rules on the next trade
5. Losses don't justify revenge trading or larger size
6. FOMO is not a signal
7. When in doubt, HOLD — a skipped trade costs nothing
8. Goal is being in business next month, not winning today

---

## FEE ECONOMICS (CRITICAL)

At $250 position size, Coinbase 0.6% taker:
- Round trip cost: $250 × 0.012 = **$3.00 per trade**
- Break-even move: 1.2%
- At 3:1 R:R (1.5% stop / 4.5% target): target = $11.25, stop = $3.75
- Win rate needed to break even: ~26%
- Win rate needed to profit after fees: > 30%

Fee drag halted at $50/day limit (10% of $500 account).
