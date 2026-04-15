---
name: self-audit
description: Evidence-based self-audit for the v13 trading bot — git diff, changed files, behavior-freeze compliance, test status, scanner funnel, economics veto patterns, duplicate closes, runtime warnings, and next safe move
argument-hint: ""
allowed-tools:
  - Bash
  - Read
  - Glob
---

You are performing an evidence-based self-audit of the v13 autonomous trading bot repository.
Stop and gather actual evidence before drawing any conclusions.
Do NOT rely on memory, documentation, or assumptions — read the code and logs directly.

## Step 1: Gather Git Evidence

```bash
echo "=== CURRENT BRANCH ==="
git rev-parse --abbrev-ref HEAD

echo ""
echo "=== WHAT CHANGED (since last commit) ==="
git diff HEAD --stat 2>/dev/null || git diff --cached --stat

echo ""
echo "=== CHANGED FILES ==="
git diff HEAD --name-only 2>/dev/null; git diff --cached --name-only 2>/dev/null; git ls-files --others --exclude-standard | grep -E '\.(py|sh|json|md)$'

echo ""
echo "=== LAST 5 COMMITS ==="
git log --oneline -5
```

## Step 2: High-Risk File Check

Flag immediately if any of these files appear in the diff:

```bash
CHANGED=$(git diff HEAD --name-only 2>/dev/null; git diff --cached --name-only 2>/dev/null)
HIGH_RISK="scanner.py signal_engine.py position_manager.py perps_engine.py economics_gate.py unified_sizer.py v10_runner.py trade_logger.py notification_engine.py feature_builder.py walk_forward_trainer.py post_trade_analyzer.py dynamic_weights.py"

echo "=== HIGH-RISK FILE TOUCH REPORT ==="
for f in $HIGH_RISK; do
  if echo "$CHANGED" | grep -q "$f"; then
    echo "  TOUCHED: $f  ← REQUIRES EXTRA SCRUTINY"
  fi
done
echo "(done)"
```

## Step 3: Behavior Freeze Compliance

Verify v13 calibrations are intact. Compare code vs expected values.

```bash
echo "=== BEHAVIOR FREEZE CHECK ==="

echo ""
echo "--- scanner.py: volume floor ---"
grep "_MIN_VOLUME_24H_USD" scanner.py | grep -v "log\|#\|debug"
echo "EXPECTED: _MIN_VOLUME_24H_USD = 2_500_000"

echo ""
echo "--- economics_gate.py: EV thresholds + volume floor ---"
grep "_TIER_APLUS_EV\|_TIER_A_EV\|_TIER_B_EV\|_MIN_VOLUME_USD" risk/economics_gate.py | grep -v "#.*old\|#.*was"
echo "EXPECTED: A+=0.016 (1.6%), A=0.008 (0.8%), B=0.003 (0.3%), vol=$3M"

echo ""
echo "--- v10_runner.py: tier thresholds + stop_multiplier ---"
grep "_TIER1_COMPOSITE_FLOOR\|composite.*58\|stop_multiplier=3\|win_rate_estimate.*0\." scheduler/v10_runner.py | head -8
echo "EXPECTED: Tier1 floor=50.0, Tier2 threshold=58, stop_multiplier=3.0"

echo ""
echo "--- position_manager.py: trailing stop ATR multiplier ---"
grep "4\.5\|trail.*atr\|atr.*trail" position_manager.py | head -5
echo "EXPECTED: trailing stop trails at 4.5x ATR from peak"

echo ""
echo "--- signal_engine.py: WAE requires BOTH fast AND slow MACD ---"
grep -A3 "WAE.*Long\|WAE.*Short\|wae_explosion" signal_engine.py | grep -i "slow\|fast\|both\|and" | head -6
echo "EXPECTED: WAE gate requires macd_fast AND macd_slow to agree (not fast-only)"

echo ""
echo "--- perps_engine.py: duplicate close guard ---"
grep "_IDEMPOTENCY_WINDOW\|_recent_close_ts" perps_engine.py | head -3
echo "EXPECTED: _IDEMPOTENCY_WINDOW = 60.0 seconds"

echo ""
echo "--- position_manager.py: hard stop precision ---"
grep "Hard stop hit\|:.8g" position_manager.py | head -3
echo "EXPECTED: hard stop reason uses :.8g format (not :.4f)"
```

## Step 4: Run Smoke Tests

```bash
echo "=== SMOKE TESTS ==="
python3 -m pytest tests/test_indicators.py tests/test_exit_logic.py -q --tb=short --no-header -p no:warnings 2>&1 | tail -15
```

Also run targeted test if high-risk files were touched:
```bash
CHANGED=$(git diff HEAD --name-only 2>/dev/null)
if echo "$CHANGED" | grep -qE "scanner|signal_engine|economics_gate|unified_sizer"; then
  echo "--- Running economics/signal tests ---"
  python3 -m pytest tests/test_perp_momentum.py tests/test_risk_manager.py -q --tb=short --no-header -p no:warnings 2>&1 | tail -10
fi
```

## Step 5: Scanner Funnel Analysis

Check recent bot log for funnel data. This reveals whether candidates are actually getting through.

```bash
echo "=== SCANNER FUNNEL (last 10 scan cycles) ==="
grep "\[v10\] funnel:" logs/bot.log 2>/dev/null | tail -10 || echo "No funnel data (logs/bot.log not found or bot not running)"

echo ""
echo "=== SCAN SUMMARY (last 5 scans) ==="
grep "\[v10\] scan:" logs/bot.log 2>/dev/null | tail -5

echo ""
echo "=== SCANNER STEP OUTPUT (last 3 scans) ==="
grep "\[scanner\] Complete\|\[scanner\] Step" logs/bot.log 2>/dev/null | tail -10
```

## Step 6: Economics Veto Patterns

Identify which symbols are being repeatedly vetoed and why.

```bash
echo "=== ECONOMICS VETO SUMMARY (last 2 hours of log) ==="
LINES=$(grep "\[v10\].*ECONOMICS VETO" logs/bot.log 2>/dev/null | tail -50)
echo "Total recent vetoes: $(echo "$LINES" | grep -c "VETO" 2>/dev/null || echo 0)"
echo ""
echo "Top vetoed symbols:"
echo "$LINES" | grep -oE 'PF_[A-Z]+USD|[A-Z]+USDT|[A-Z]+' | sort | uniq -c | sort -rn | head -8
echo ""
echo "Veto reasons:"
echo "$LINES" | grep -oE 'reason=[^(]+' | sort | uniq -c | sort -rn | head -5
echo ""
echo "Most recent vetoes:"
echo "$LINES" | tail -5
```

## Step 7: Duplicate Close / Reporting Contradictions

Check for the PF_PEPEUSD-style duplicate close pattern.

```bash
echo "=== DUPLICATE CLOSE EVENTS (last 24h) ==="
if [ -f "logs/trades.db" ]; then
  sqlite3 logs/trades.db "
    SELECT symbol,
           strftime('%Y-%m-%dT%H:%M', ts) as minute,
           COUNT(*) as close_count,
           GROUP_CONCAT(reason, ' | ') as reasons
    FROM trades
    WHERE action IN ('SELL','BUY')
      AND ts > datetime('now', '-24 hours')
      AND (notes LIKE '%reason=%' OR reason != '')
    GROUP BY symbol, strftime('%Y-%m-%dT%H:%M', ts)
    HAVING COUNT(*) > 1
    ORDER BY ts DESC
    LIMIT 10;
  " 2>/dev/null || echo "Cannot query trades.db (not found or locked)"
else
  echo "logs/trades.db not found"
fi

echo ""
echo "=== DUPLICATE CLOSE IN BOT LOG (last hour) ==="
python3 -c "
import re, subprocess
try:
    out = subprocess.check_output(['tail', '-500', 'logs/bot.log'], text=True, timeout=5)
    closes = {}
    for line in out.splitlines():
        if 'PAPER CLOSE' in line or 'hard_stop' in line.lower():
            m = re.search(r'(PF_\w+|\w+USD)', line)
            if m:
                sym = m.group(1)
                closes.setdefault(sym, []).append(line[:80])
    dupes = {k: v for k, v in closes.items() if len(v) > 1}
    if dupes:
        for sym, lines in dupes.items():
            print(f'POSSIBLE DUPE: {sym}')
            for l in lines[:3]:
                print(f'  {l}')
    else:
        print('No duplicate close patterns found in last 500 log lines.')
except Exception as e:
    print(f'Cannot check: {e}')
" 2>/dev/null
```

## Step 8: Runtime Warnings

Safely read recent bot activity for anomalies.

```bash
echo "=== RECENT BOT ERRORS AND WARNINGS ==="
grep -i "error\|warning\|FAIL\|SANITY\|sanity\|price.*fail\|candle.*fail" logs/bot.log 2>/dev/null | tail -10 || echo "No log found"

echo ""
echo "=== RECENT SYSTEM EVENTS (SQLite) ==="
if [ -f "logs/trades.db" ]; then
  sqlite3 logs/trades.db "SELECT ts, level, source, message FROM system_events ORDER BY ts DESC LIMIT 10;" 2>/dev/null
else
  echo "No trades.db found"
fi

echo ""
echo "=== BOT PROCESS STATUS ==="
pgrep -fl "main.py\|streamlit" || echo "No bot/dashboard processes found"
```

## Step 9: Code vs Docs Consistency

Quick check for obvious CLAUDE.md vs code drift.

```bash
echo "=== CLAUDE.md vs CODE DRIFT CHECK ==="

# Check current version string
echo "Version in CLAUDE.md:"
grep "^## Current Version\|^| Version \| v13\| v1[0-9]" CLAUDE.md | head -3

echo ""
echo "Scanner volume floor in code:"
grep "_MIN_VOLUME_24H_USD" scanner.py | head -1

echo ""
echo "CLAUDE.md says scanner floor is:"
grep "MIN_VOLUME_24H_USD\|scanner.*vol.*floor\|500K\|2\.5M\|2,500" CLAUDE.md | head -3

echo ""
echo "Trailing stop in code:"
grep "4\.5.*atr\|atr.*4\.5" position_manager.py | head -2
echo "CLAUDE.md says trailing stop:"
grep -i "trail.*4\|4.*trail\|4\.5.*ATR" CLAUDE.md | head -2
```

## Step 10: Session Command Log

What Bash commands did Claude run in this session?

```bash
echo "=== RECENT CLAUDE SESSION COMMANDS ==="
tail -20 .claude/logs/commands.log 2>/dev/null || echo "No command log found"
```

---

## Required Output Format

After gathering all evidence above, produce a structured audit report with EXACTLY these 7 sections.
Do not skip sections. Do not write "N/A" without explanation.

---

### 1. CURRENT TRUTH

State the verifiable facts gathered from the evidence above:
- What branch you are on
- What the last commit says
- Whether the bot is running
- What the last scan cycle reported (candidates, entries, funnel)
- Which tests pass, which fail

### 2. WHAT CHANGED

List every changed file with a one-sentence summary of what the change does.
Flag any changed HIGH-RISK trading-path files.
Flag any changed files that are in the behavior freeze list.
If nothing changed, say so explicitly.

### 3. WHAT MIGHT HAVE BROKEN

For each high-risk file change, reason through the failure modes:
- What could this change break in paper trading?
- What could this change break in live trading if enabled?
- Could this change affect contamination tagging, cooldowns, position restore, or learning?
- Is there any path where this change could trigger a live order? (Assume paper mode, but check.)

If nothing might be broken, prove it — don't assume.

### 4. WHAT WAS NOT TESTED

List specifically:
- What test files were NOT run
- What behaviors were assumed rather than verified
- Whether any touched high-risk file has meaningful test coverage
- Whether the smoke tests are sufficient for the changes made
- Whether any copy of runtime DB/state was used in testing

### 5. WHAT STILL LOOKS WEIRD

Report honestly on any anomalies found:
- Economics veto rate and top symbols (is MOODENG/ZETA still showing up?)
- Duplicate close patterns (resolved or still present?)
- Candle sanity failures (ETH at $19 vs $2130?)
- Funnel numbers that don't add up
- Code/docs drift
- Any log line that does not make sense

Do NOT dismiss anomalies without evidence. "Probably fine" is not an answer here.

### 6. ASSUMPTIONS

List every assumption you made in this audit that was NOT verified from code or log evidence:
- Format: "ASSUMPTION: [what you assumed] — RISK: [why this matters]"
- Be specific. Be honest.

### 7. NEXT SAFE MOVE

One concrete, verifiable action to take next.
Must be:
- Paper-safe (will not place a live order)
- Non-destructive (will not mutate runtime DB/state)
- Specific (not "run more tests" — say exactly which test and why)

If there is nothing to do, say: "No action needed — audit found no issues."
If you are uncertain about safety, say: "BLOCKER: [describe it]."

---

## Behavior Freeze Reference (v13 — do not change without explicit authorization)

| Component | File | Frozen Value |
|---|---|---|
| Scanner vol floor | scanner.py | `_MIN_VOLUME_24H_USD = 2_500_000` |
| Economics vol floor | economics_gate.py | `_MIN_VOLUME_USD = 3_000_000` |
| EV threshold A+ | economics_gate.py | `_TIER_APLUS_EV = 0.016` (1.6%) |
| EV threshold A | economics_gate.py | `_TIER_A_EV = 0.008` (0.8%) |
| EV threshold B | economics_gate.py | `_TIER_B_EV = 0.003` (0.3%) |
| Stop multiplier | v10_runner.py | `stop_multiplier=3.0` |
| Tier 1 composite floor | v10_runner.py | `_TIER1_COMPOSITE_FLOOR = 50.0` |
| Tier 2 composite threshold | v10_runner.py | `composite >= 58` |
| Trailing stop ATR | position_manager.py | `4.5x ATR from peak` |
| WAE MACD gate | signal_engine.py | requires BOTH fast AND slow MACD agreement |
| Duplicate close window | perps_engine.py | `_IDEMPOTENCY_WINDOW = 60.0` |
| Entry cooldown (thesis) | v10_runner.py | `_COOLDOWN_THESIS_SEC = 7200` (2h) |
| Veto log cooldown | v10_runner.py | `_VETO_LOG_COOLDOWN_SEC = 1800` (30min) |
| Close note format | perps_engine.py | includes `score/tier/setup/regime` |
| Contamination tag | trades DB | source NOT IN ('backtest','pre_v10_contaminated','bybit_paper') |

Any deviation from these values is a behavior freeze violation and must be flagged in section 2.
