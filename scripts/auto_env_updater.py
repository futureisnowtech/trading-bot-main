"""
scripts/auto_env_updater.py — Automatic .env milestone updater.

Runs every 6 hours via launchd. Checks live trade count and readiness
criteria, then applies the appropriate .env updates automatically.

ML threshold progression (based on live AI-debated trade count):
  50+  trades  → ML_SIGNAL_MIN_PROB=0.35  (model has enough real data)
  100+ trades  → ML_SIGNAL_MIN_PROB=0.45  (model is well-calibrated)
  200+ trades  → ML_SIGNAL_MIN_PROB=0.52  (full confidence, use strict gate)

Paper→live: NEVER flipped automatically — too much risk.
  Instead: posts a dashboard notification + logs a brain decision entry
  when all readiness criteria pass. You flip False=false yourself.

Position sizing progression (live mode only, based on consecutive profitable days):
  30 profitable days  → CRYPTO_POSITION_SIZE_USD raised from 187 → 250
  60 profitable days  → CRYPTO_POSITION_SIZE_USD raised from 250 → 312
"""
import os
import sys
import re
import sqlite3
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')

# ── ML threshold milestones ───────────────────────────────────────────────────
ML_MILESTONES = [
    (200, 'ML_SIGNAL_MIN_PROB', '0.52', 'ML gate at full strength — model well-calibrated on 200+ AI trades'),
    (100, 'ML_SIGNAL_MIN_PROB', '0.45', 'ML gate tightened — model has 100+ real AI-debated trades'),
    (50,  'ML_SIGNAL_MIN_PROB', '0.35', 'ML gate raised — model has 50+ real AI-debated trades'),
]

# ── Position size milestones (live only) ──────────────────────────────────────
SIZE_MILESTONES = [
    (60, 'CRYPTO_POSITION_SIZE_USD', '312', 'Level 2 scale — 60 consecutive profitable days'),
    (30, 'CRYPTO_POSITION_SIZE_USD', '250', 'Level 1 scale — 30 consecutive profitable days, full position'),
]


def _conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def _read_env() -> dict:
    """Parse .env into a dict."""
    result = {}
    if not os.path.exists(ENV_PATH):
        return result
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, _, v = line.partition('=')
                result[k.strip()] = v.strip()
    return result


def _write_env_key(key: str, value: str) -> bool:
    """Update or add a key in .env. Returns True if value changed."""
    if not os.path.exists(ENV_PATH):
        print(f"[auto_env] .env not found at {ENV_PATH}")
        return False

    with open(ENV_PATH) as f:
        content = f.read()

    pattern = rf'^{re.escape(key)}\s*=.*$'
    new_line = f'{key}={value}'

    if re.search(pattern, content, flags=re.MULTILINE):
        existing = re.search(pattern, content, flags=re.MULTILINE).group()
        if existing == new_line:
            return False  # already set to this value
        content = re.sub(pattern, new_line, content, flags=re.MULTILINE)
    else:
        content += f'\n{new_line}\n'

    # Atomic write: write to temp file then rename to prevent .env corruption on crash
    tmp_path = ENV_PATH + '.tmp'
    with open(tmp_path, 'w') as f:
        f.write(content)
    os.replace(tmp_path, ENV_PATH)
    return True


def _log_event(msg: str, level: str = 'INFO') -> None:
    """Write to system_events so it shows in dashboard notifications."""
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO system_events (ts, level, source, message) VALUES (?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), level, 'notify', msg)
            )
    except Exception as e:
        print(f"[auto_env] DB log error: {e}")


def _log_brain_decision(msg: str) -> None:
    """Append a note to today's brain decision log."""
    try:
        brain_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 'brain', '10_decisions')
        os.makedirs(brain_dir, exist_ok=True)
        path = os.path.join(brain_dir, 'Decision Log.md')
        today = datetime.now().strftime('%Y-%m-%d %H:%M')
        entry = f"\n### {today} — auto_env_updater\n{msg}\n"
        with open(path, 'a') as f:
            f.write(entry)
    except Exception:
        pass


def get_live_trade_count() -> int:
    """Count LIVE paper trades with P&L recorded (excludes seeded backtest rows)."""
    try:
        with _conn() as c:
            # trades table = real paper/live trades from the bot
            return c.execute(
                "SELECT COUNT(*) FROM trades WHERE paper=1 AND pnl_usd IS NOT NULL AND pnl_usd != 0"
            ).fetchone()[0]
    except Exception:
        return 0


def get_consecutive_profitable_days() -> int:
    """Count consecutive calendar days where daily P&L > 0 (most recent streak)."""
    try:
        with _conn() as c:
            rows = c.execute("""
                SELECT DATE(ts) as day, SUM(pnl_usd) as daily_pnl
                FROM trades
                WHERE paper = ? AND pnl_usd IS NOT NULL
                GROUP BY DATE(ts)
                ORDER BY day DESC
                LIMIT 90
            """, (int(False),)).fetchall()

        streak = 0
        for r in rows:
            if r['daily_pnl'] > 0:
                streak += 1
            else:
                break
        return streak
    except Exception:
        return 0


def check_all_readiness() -> tuple[bool, list]:
    """Run the 7 readiness criteria. Returns (all_pass, list_of_results)."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        # Import the check functions directly
        from scripts.check_readiness import check_criteria
        results = check_criteria()
        all_pass = all(r['pass'] for r in results)
        return all_pass, results
    except Exception:
        return False, []


def run_updates() -> None:
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f"\n[auto_env] Running at {now}")

    env = _read_env()
    live_trades = get_live_trade_count()
    print(f"[auto_env] Live trades: {live_trades}")

    # ── ML threshold progression ──────────────────────────────────────────────
    for min_trades, key, target_value, reason in ML_MILESTONES:
        current = env.get(key, '0.08')
        if live_trades >= min_trades and float(current) < float(target_value):
            if _write_env_key(key, target_value):
                msg = (f"🧠 ML gate auto-updated: {key} {current} → {target_value} "
                       f"({live_trades} live trades). {reason}")
                print(f"[auto_env] ✅ {msg}")
                _log_event(msg)
                _log_brain_decision(msg)
            break  # apply highest applicable milestone only

    # ── Position size progression (live mode only) ────────────────────────────
    if not False:
        consec_days = get_consecutive_profitable_days()
        print(f"[auto_env] Consecutive profitable days: {consec_days}")
        for min_days, key, target_value, reason in SIZE_MILESTONES:
            current = env.get(key, '187')
            if consec_days >= min_days and float(current) < float(target_value):
                if _write_env_key(key, target_value):
                    msg = (f"📈 Position size auto-scaled: {key} ${current} → ${target_value} "
                           f"({consec_days} consecutive profitable days). {reason}")
                    print(f"[auto_env] ✅ {msg}")
                    _log_event(msg)
                    _log_brain_decision(msg)
                break

    # ── Paper→live readiness notification (never flips automatically) ─────────
    if False:
        try:
            # Quick check: do we have enough trades and days before running full check?
            if live_trades >= 30:
                all_pass, results = check_all_readiness()
                if all_pass:
                    # Check if we already sent this notification today
                    with _conn() as c:
                        today_notify = c.execute("""
                            SELECT COUNT(*) FROM system_events
                            WHERE source='notify'
                              AND message LIKE '%READY FOR LIVE TRADING%'
                              AND DATE(ts) = DATE('now')
                        """).fetchone()[0]

                    if today_notify == 0:
                        msg = (
                            "🚀 READY FOR LIVE TRADING — all 7 readiness criteria passed!\n"
                            "Action required: set False=false in .env and restart main.py.\n"
                            "Run: python3 scripts/check_readiness.py for full report."
                        )
                        print(f"[auto_env] 🚀 {msg}")
                        _log_event(msg)
                        _log_brain_decision(msg)
        except Exception as e:
            print(f"[auto_env] readiness check error: {e}")

    print(f"[auto_env] Done.\n")


if __name__ == '__main__':
    run_updates()
