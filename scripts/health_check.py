"""
scripts/health_check.py — Trade DB integrity checker + security audit.

Run anytime to surface silent errors:
  python3 scripts/health_check.py
  python3 scripts/health_check.py --days 7
  python3 scripts/health_check.py --no-sec    # skip security checks
  python3 scripts/health_check.py --sec-only  # security audit only

─── TRADE INTEGRITY CHECKS ───────────────────────────────────────────────────
  1. Duplicate close events (same symbol+strategy+qty closed within 90s)
  2. Orphaned open positions (already have a close trade in DB)
  3. Duplicate BUY entries for same symbol within 60s
  4. P&L reconciliation (gross vs net vs fee totals)
  5. Outlier trades (P&L > 10× average — possible calculation error)
  6. Timestamp ordering (IDs should increase monotonically with time)
  7. Unknown strategies (not in approved list — possible injection)

─── SECURITY CHECKS ──────────────────────────────────────────────────────────
  S1. File permissions  — .env and DB must not be world-readable
  S2. Secrets in code   — scan all .py files for exposed API keys / tokens
  S3. DB tampering      — non-monotonic IDs, timestamps going backward
  S4. Off-hours entries — trades logged outside any known bot-active window
  S5. Log anomalies     — auth failures, unexpected errors in bot.log
  S6. Order injection   — trades with unknown strategy or unusual field values
  S7. Credential backup — .env backed up outside repo (data-loss protection)
"""
import sys, os, re, stat, argparse, sqlite3, glob
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH, PAPER_TRADING

PAPER      = int(PAPER_TRADING)
PROJECT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH   = os.path.join(PROJECT, '.env')
LOG_PATH   = os.path.join(PROJECT, 'logs', 'service', 'bot.log')
BACKUP_DIR = os.path.expanduser('~/.algo_backup')

# Strategies the bot is authorised to create trades under.
# Update this list when adding a new strategy.
KNOWN_STRATEGIES = {
    'crypto_ai_debate',
    'crypto_macd_consensus',
    'crypto_mean_reversion',
    'crypto_perp_strategy',
    'crypto_perp',           # short alias used by bybit_broker
    'equity_ai_debate',
    'equity_momentum',
    'futures_scalper',
    'manual',                # allow one-off manual override entries
}

# ── helpers ───────────────────────────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _section(title: str) -> None:
    print(f"\n{'─'*55}")
    print(f"  {title}")
    print(f"{'─'*55}")


# ══════════════════════════════════════════════════════════════════════════════
#  TRADE INTEGRITY
# ══════════════════════════════════════════════════════════════════════════════

def check_duplicate_closes(conn, days):
    """Same symbol+strategy+qty sold twice within 90 seconds."""
    cur = conn.cursor()
    cur.execute(f"""
        SELECT a.id, a.ts, a.symbol, a.strategy, a.qty, a.pnl_usd,
               b.id as dup_id, b.ts as dup_ts
        FROM trades a
        JOIN trades b ON a.symbol=b.symbol AND a.strategy=b.strategy
            AND a.action=b.action AND ABS(a.qty-b.qty)<0.000001
            AND a.paper=b.paper AND b.id > a.id
            AND (julianday(b.ts) - julianday(a.ts)) * 86400 < 90
        WHERE a.paper=? AND a.pnl_usd != 0
          AND a.ts >= datetime('now', '-{days} days')
        ORDER BY a.ts DESC
    """, (PAPER,))
    rows = cur.fetchall()
    if rows:
        print(f"\n⚠️  DUPLICATE CLOSES ({len(rows)} pairs):")
        for r in rows[:20]:
            print(f"   {r['ts'][:16]} {r['symbol']:12} qty={r['qty']:.4f}  "
                  f"P&L={r['pnl_usd']:+.4f}  dup_id={r['dup_id']} ({r['dup_ts'][:16]})")
        if len(rows) > 20:
            print(f"   ... and {len(rows)-20} more")
    else:
        print("✅  No duplicate close events")
    return len(rows)


def check_orphaned_open_positions(conn):
    """Positions in open_positions that already have a close trade."""
    cur = conn.cursor()
    cur.execute("SELECT * FROM open_positions WHERE paper=?", (PAPER,))
    positions = cur.fetchall()
    orphans = []
    for p in positions:
        cur.execute(
            "SELECT id, ts FROM trades WHERE symbol=? AND strategy=? AND paper=? "
            "AND pnl_usd != 0 AND ts > ? LIMIT 1",
            (p['symbol'], p['strategy'], PAPER, p['ts_entry'])
        )
        row = cur.fetchone()
        if row:
            orphans.append((p['symbol'], p['strategy'], p['ts_entry'], row['ts']))
    if orphans:
        print(f"\n⚠️  ORPHANED OPEN POSITIONS ({len(orphans)}) — already closed but not deleted:")
        for sym, strat, ts_entry, ts_close in orphans:
            print(f"   {sym:12} {strat:25} entry={ts_entry[:16]}  close={ts_close[:16]}")
        print("   → Run bot restart to auto-clean via _restore_positions")
    else:
        print("✅  No orphaned open positions")
    return len(orphans)


def check_duplicate_buys(conn, days):
    """Same symbol+strategy bought twice within 60 seconds."""
    cur = conn.cursor()
    cur.execute(f"""
        SELECT a.id, a.ts, a.symbol, a.strategy, a.qty
        FROM trades a
        JOIN trades b ON a.symbol=b.symbol AND a.strategy=b.strategy
            AND a.action=b.action AND a.paper=b.paper AND b.id > a.id
            AND (julianday(b.ts) - julianday(a.ts)) * 86400 < 60
        WHERE a.action='BUY' AND a.paper=?
          AND a.ts >= datetime('now', '-{days} days')
        ORDER BY a.ts DESC
    """, (PAPER,))
    rows = cur.fetchall()
    if rows:
        print(f"\n⚠️  DUPLICATE BUY ENTRIES ({len(rows)} pairs):")
        for r in rows[:20]:
            print(f"   {r['ts'][:16]} {r['symbol']:12} {r['strategy']:25} qty={r['qty']:.4f}")
    else:
        print("✅  No duplicate buy entries")
    return len(rows)


def check_pnl_reconciliation(conn, days):
    """Cross-check gross P&L, fees, and net P&L totals."""
    cur = conn.cursor()
    cur.execute(f"""
        SELECT
            COALESCE(SUM(pnl_usd), 0) as gross_pnl,
            COALESCE(SUM(fee_usd), 0) as total_fees,
            COUNT(CASE WHEN pnl_usd > 0 THEN 1 END) as wins,
            COUNT(CASE WHEN pnl_usd < 0 THEN 1 END) as losses,
            COUNT(CASE WHEN pnl_usd != 0 THEN 1 END) as closed_trades,
            COUNT(*) as total_rows
        FROM trades
        WHERE paper=? AND ts >= datetime('now', '-{days} days')
    """, (PAPER,))
    r = cur.fetchone()
    gross = r['gross_pnl']
    fees  = r['total_fees']
    net   = gross - fees
    wr    = r['wins'] / (r['closed_trades'] or 1)
    print(f"\n📊  P&L RECONCILIATION (last {days} days):")
    print(f"   Gross P&L:      ${gross:+.4f}")
    print(f"   Total fees:     ${fees:.4f}")
    print(f"   Net P&L:        ${net:+.4f}")
    print(f"   Closed trades:  {r['closed_trades']}  ({r['wins']}W / {r['losses']}L, {wr:.1%} WR)")
    print(f"   Total DB rows:  {r['total_rows']}")


def check_outlier_trades(conn, days):
    """Flag trades with P&L > 10× the average absolute P&L."""
    cur = conn.cursor()
    cur.execute(f"""
        SELECT id, ts, symbol, strategy, pnl_usd, qty, price
        FROM trades
        WHERE paper=? AND pnl_usd != 0
          AND ts >= datetime('now', '-{days} days')
        ORDER BY ABS(pnl_usd) DESC
        LIMIT 10
    """, (PAPER,))
    rows = cur.fetchall()
    if not rows:
        print("✅  No closed trades to check for outliers")
        return
    avg_abs  = sum(abs(r['pnl_usd']) for r in rows) / len(rows)
    outliers = [r for r in rows if abs(r['pnl_usd']) > avg_abs * 10 and avg_abs > 0.001]
    if outliers:
        print(f"\n⚠️  OUTLIER TRADES (P&L > 10× average ${avg_abs:.4f}):")
        for r in outliers:
            print(f"   id={r['id']} {r['ts'][:16]} {r['symbol']:12} "
                  f"P&L={r['pnl_usd']:+.4f}  qty={r['qty']:.4f} @ ${r['price']:.4f}")
    else:
        print(f"✅  No outlier trades (avg |P&L| = ${avg_abs:.4f}, "
              f"top = ${abs(rows[0]['pnl_usd']):.4f})")


def check_id_ordering(conn, days):
    """IDs must increase monotonically with timestamp — any reversal signals a manual insert."""
    cur = conn.cursor()
    cur.execute(f"""
        SELECT id, ts FROM trades
        WHERE paper=? AND ts >= datetime('now', '-{days} days')
        ORDER BY id ASC
    """, (PAPER,))
    rows = cur.fetchall()
    issues = []
    for i in range(1, len(rows)):
        if rows[i]['ts'] < rows[i-1]['ts']:
            issues.append((rows[i-1]['id'], rows[i-1]['ts'], rows[i]['id'], rows[i]['ts']))
    if issues:
        print(f"\n⚠️  TIMESTAMP REVERSALS ({len(issues)}) — ID order and time order don't match:")
        for prev_id, prev_ts, cur_id, cur_ts in issues[:10]:
            print(f"   id={prev_id} ts={prev_ts[:19]}  →  id={cur_id} ts={cur_ts[:19]}")
        print("   → Could indicate manual DB edits or clock skew")
    else:
        print("✅  Trade IDs and timestamps are monotonically ordered")
    return len(issues)


def check_unknown_strategies(conn, days):
    """Trades with strategy names not in the approved list."""
    cur = conn.cursor()
    cur.execute(f"""
        SELECT DISTINCT strategy, COUNT(*) as cnt
        FROM trades
        WHERE paper=? AND ts >= datetime('now', '-{days} days')
        GROUP BY strategy
    """, (PAPER,))
    rows = cur.fetchall()
    unknown = [(r['strategy'], r['cnt']) for r in rows
               if r['strategy'] not in KNOWN_STRATEGIES]
    if unknown:
        print(f"\n🚨  UNKNOWN STRATEGIES ({len(unknown)}) — not in approved list:")
        for strat, cnt in unknown:
            print(f"   '{strat}'  ({cnt} trade(s))")
        print("   → Add to KNOWN_STRATEGIES if legitimate, investigate if not")
    else:
        print("✅  All strategies are in the approved list")
    return len(unknown)


# ══════════════════════════════════════════════════════════════════════════════
#  SECURITY CHECKS
# ══════════════════════════════════════════════════════════════════════════════

# Regex patterns that suggest an exposed credential value.
# Intentionally avoids matching placeholder / example strings.
_SECRET_PATTERNS = [
    # Generic long hex keys (32+ chars, no spaces)
    (r'(?<![#\s])["\']?[0-9a-fA-F]{32,64}["\']?', 'hex-key'),
    # Base64-like tokens (40+ chars of base64 alphabet with mixed case)
    (r'["\'][A-Za-z0-9+/]{40,}={0,2}["\']', 'base64-token'),
    # Coinbase / Alpaca / Bybit key prefixes
    (r'(?i)(api[_-]?key|api[_-]?secret|access[_-]?token|private[_-]?key)\s*=\s*["\'][^"\']{16,}["\']',
     'key-assignment'),
    # Hard-coded Bearer tokens
    (r'(?i)bearer\s+[A-Za-z0-9\-._~+/]{20,}', 'bearer-token'),
]
_SECRET_RE = [(re.compile(p), label) for p, label in _SECRET_PATTERNS]

# Lines that look like examples / placeholders — skip them
_PLACEHOLDER_RE = re.compile(
    r'(?i)(your[_\-]?key|placeholder|example|changeme|xxx|<.*?>|'
    r'sk-[a-z]{2,4}-[a-z]{2,4}|test|demo|fake|dummy|todo|fixme)'
)


def check_file_permissions():
    """
    .env and trades.db must not be world-readable (mode should be 600 or 640).
    World-readable means any process or user on the machine can read API keys.
    """
    issues = 0
    targets = [
        (ENV_PATH,  '600', 'API keys / secrets'),
        (DB_PATH,   '640', 'trade history'),
    ]
    cred_backup = os.path.join(BACKUP_DIR, 'credentials')
    if os.path.isdir(cred_backup):
        for f in glob.glob(os.path.join(cred_backup, '.env.*')):
            targets.append((f, '600', 'credential backup'))

    for path, recommended, desc in targets:
        if not os.path.exists(path):
            continue
        mode = os.stat(path).st_mode
        world_read  = bool(mode & stat.S_IROTH)
        world_write = bool(mode & stat.S_IWOTH)
        group_write = bool(mode & stat.S_IWGRP)
        octal       = oct(mode)[-3:]

        flags = []
        if world_read:  flags.append('world-readable')
        if world_write: flags.append('world-writable')
        if group_write: flags.append('group-writable')

        if flags:
            print(f"\n🚨  INSECURE PERMISSIONS on {os.path.relpath(path, PROJECT)}:")
            print(f"   Current mode: {octal}  ({', '.join(flags)})")
            print(f"   Contains:     {desc}")
            print(f"   Fix:          chmod {recommended} {path}")
            issues += 1
        else:
            short = os.path.basename(path)
            print(f"✅  {short:30s} permissions OK ({octal})")
    return issues


def check_secrets_in_code():
    """
    Walk all .py files in the project and flag lines that look like
    hard-coded API keys or tokens (not placeholder / comment lines).
    """
    py_files = []
    for root, dirs, files in os.walk(PROJECT):
        # Skip __pycache__, .git, venv directories
        dirs[:] = [d for d in dirs if d not in ('__pycache__', '.git', 'venv', '.venv',
                                                  'node_modules', 'site-packages')]
        for f in files:
            if f.endswith('.py'):
                py_files.append(os.path.join(root, f))

    hits = []
    for fpath in py_files:
        try:
            with open(fpath, 'r', errors='replace') as fh:
                for lineno, line in enumerate(fh, 1):
                    stripped = line.strip()
                    # Skip blank lines, pure comments, and placeholder lines
                    if not stripped or stripped.startswith('#'):
                        continue
                    if _PLACEHOLDER_RE.search(stripped):
                        continue
                    for pattern, label in _SECRET_RE:
                        m = pattern.search(stripped)
                        if m:
                            rel = os.path.relpath(fpath, PROJECT)
                            hits.append((rel, lineno, label, stripped[:90]))
                            break   # one hit per line is enough
        except Exception:
            pass

    if hits:
        print(f"\n🚨  POTENTIAL SECRETS IN SOURCE ({len(hits)} hits):")
        for rel, lineno, label, snippet in hits[:20]:
            print(f"   {rel}:{lineno}  [{label}]")
            print(f"      {snippet}")
        if len(hits) > 20:
            print(f"   ... and {len(hits)-20} more")
        print("   → Move credentials to .env and load via os.getenv()")
    else:
        print("✅  No hard-coded credentials detected in source files")
    return len(hits)


def check_db_tampering(conn, days):
    """
    Detect signs that trade records were manually altered or inserted:
      • rows with negative IDs
      • pnl_usd precision too high (> 8 decimal places = calculation anomaly)
      • fee_usd < 0 (refunds don't happen in this system)
      • price <= 0
      • qty <= 0 on a non-zero-pnl trade
    """
    issues = 0
    cur = conn.cursor()

    # Negative IDs
    cur.execute("SELECT COUNT(*) as n FROM trades WHERE id < 0")
    n = cur.fetchone()['n']
    if n:
        print(f"\n🚨  NEGATIVE ROW IDs: {n} rows — DB may have been manually edited")
        issues += n
    else:
        print("✅  No negative row IDs")

    # Impossible fee values
    cur.execute(f"""
        SELECT id, ts, symbol, fee_usd FROM trades
        WHERE fee_usd < 0 AND ts >= datetime('now', '-{days} days')
    """)
    rows = cur.fetchall()
    if rows:
        print(f"\n🚨  NEGATIVE FEES ({len(rows)} rows) — exchange doesn't pay rebates:")
        for r in rows[:10]:
            print(f"   id={r['id']} {r['ts'][:16]} {r['symbol']:12} fee={r['fee_usd']:.6f}")
        issues += len(rows)
    else:
        print("✅  No negative fee entries")

    # Zero or negative prices on closed trades
    cur.execute(f"""
        SELECT id, ts, symbol, price, pnl_usd FROM trades
        WHERE price <= 0 AND pnl_usd != 0
          AND ts >= datetime('now', '-{days} days')
    """)
    rows = cur.fetchall()
    if rows:
        print(f"\n🚨  INVALID PRICES ({len(rows)} rows with price ≤ 0):")
        for r in rows[:10]:
            print(f"   id={r['id']} {r['ts'][:16]} {r['symbol']:12} "
                  f"price={r['price']}  pnl={r['pnl_usd']:+.4f}")
        issues += len(rows)
    else:
        print("✅  All trade prices are positive")

    # P&L magnitude sanity (single trade can't exceed account size)
    from config import ACCOUNT_SIZE
    cur.execute(f"""
        SELECT id, ts, symbol, strategy, pnl_usd FROM trades
        WHERE pnl_usd != 0
          AND ABS(pnl_usd) > {ACCOUNT_SIZE}
          AND ts >= datetime('now', '-{days} days')
    """)
    magnitude_hits = cur.fetchall()
    if magnitude_hits:
        print(f"\n🚨  IMPOSSIBLE P&L ({len(magnitude_hits)} rows exceed account size ${ACCOUNT_SIZE}):")
        for r in magnitude_hits[:10]:
            print(f"   id={r['id']} {r['ts'][:16]} {r['symbol']:12} pnl={r['pnl_usd']:+.4f}")
        print("   → Single trade cannot exceed account size — possible data injection")
        issues += len(magnitude_hits)
    else:
        print(f"✅  All trade P&L values within account size (${ACCOUNT_SIZE})")

    return issues


def check_off_hours_trades(conn, days):
    """
    Detect trades logged between 02:00 and 05:00 ET — the bot's hard dead zone.
    Any trade in that window means either: the bot ignored the gate, or a record
    was injected manually.
    (Timestamps in DB are UTC; ET = UTC-4 in summer, UTC-5 in winter.
     We use UTC 06:00-09:00 as the conservative dead-zone window.)
    """
    cur = conn.cursor()
    cur.execute(f"""
        SELECT id, ts, symbol, strategy, action, pnl_usd
        FROM trades
        WHERE paper=?
          AND ts >= datetime('now', '-{days} days')
          AND (
              -- UTC 06:00-08:59 ≈ ET 02:00-04:59 (summer offset -4)
              (strftime('%H', ts) >= '06' AND strftime('%H', ts) < '09')
          )
        ORDER BY ts DESC
    """, (PAPER,))
    rows = cur.fetchall()
    if rows:
        print(f"\n⚠️  OFF-HOURS TRADES ({len(rows)}) — logged during 2-5am ET dead zone:")
        for r in rows[:15]:
            print(f"   {r['ts'][:19]} UTC  {r['symbol']:12} {r['action']:8} "
                  f"{r['strategy']:25} pnl={r['pnl_usd']:+.4f}")
        print("   → Investigate: did the bot bypass the time gate, or were these injected?")
    else:
        print("✅  No trades logged during the 2-5am ET dead zone")
    return len(rows)


def check_log_anomalies():
    """
    Scan the last 2000 lines of bot.log for security-relevant patterns:
      • authentication failures
      • repeated API 401/403 errors (credential compromise indicator)
      • unexpected process restarts (could indicate external interference)
      • exception floods (> 50 errors in a short window)
    """
    if not os.path.exists(LOG_PATH):
        print(f"⚠️  bot.log not found at {LOG_PATH} — skipping log audit")
        return 0

    issues = 0

    # Read last 2000 lines efficiently
    try:
        with open(LOG_PATH, 'r', errors='replace') as fh:
            lines = fh.readlines()
        tail = lines[-2000:]
    except Exception as e:
        print(f"⚠️  Could not read bot.log: {e}")
        return 0

    # Pattern → (label, severity)
    log_patterns = [
        (re.compile(r'(?i)(401|unauthorized|invalid.*api.*key|api.*key.*invalid)'), 'AUTH FAILURE', 'CRITICAL'),
        (re.compile(r'(?i)(403|forbidden|permission.*denied)'),                    'FORBIDDEN',    'HIGH'),
        (re.compile(r'(?i)(credential|password|secret).*(error|fail|invalid)'),    'CRED ERROR',   'HIGH'),
        (re.compile(r'(?i)rate.?limit'),                                           'RATE LIMIT',   'MEDIUM'),
        (re.compile(r'(?i)(connection.?refused|timeout|ssl.*error)'),              'CONN ERROR',   'LOW'),
    ]

    counts: dict = {}
    for line in tail:
        for pattern, label, severity in log_patterns:
            if pattern.search(line):
                counts[label] = counts.get(label, {'count': 0, 'severity': severity, 'examples': []})
                counts[label]['count'] += 1
                if len(counts[label]['examples']) < 2:
                    counts[label]['examples'].append(line.strip()[:120])

    # Exception flood: count lines containing 'Traceback' or 'Exception'
    exception_lines = [l for l in tail if 'Traceback' in l or 'Exception' in l or 'Error:' in l]
    if len(exception_lines) > 50:
        counts['EXCEPTION FLOOD'] = {
            'count': len(exception_lines),
            'severity': 'HIGH',
            'examples': [exception_lines[0].strip()[:120]] if exception_lines else []
        }

    if counts:
        crit = [k for k, v in counts.items() if v['severity'] == 'CRITICAL']
        high = [k for k, v in counts.items() if v['severity'] == 'HIGH']

        if crit:
            print(f"\n🚨  CRITICAL LOG ANOMALIES (last 2000 lines):")
            issues += len(crit)
        elif high:
            print(f"\n⚠️  LOG ANOMALIES (last 2000 lines):")
        else:
            print(f"\n⚠️  LOG WARNINGS (last 2000 lines):")

        for label, info in sorted(counts.items(), key=lambda x: x[1]['count'], reverse=True):
            sev_icon = '🚨' if info['severity'] == 'CRITICAL' else '⚠️ ' if info['severity'] == 'HIGH' else 'ℹ️ '
            print(f"   {sev_icon} {label:20s}  ×{info['count']:4d}")
            for ex in info['examples']:
                print(f"       {ex}")
        if high or crit:
            issues += len(high)
    else:
        print("✅  No auth failures or critical errors in recent log (last 2000 lines)")

    return issues


def check_credential_backup():
    """
    Verify that .env has been backed up outside the repository.
    A missing backup means a single disk failure or accidental `rm` wipes all API keys.
    """
    cred_dir = os.path.join(BACKUP_DIR, 'credentials')
    if not os.path.isdir(cred_dir):
        print(f"\n⚠️  NO CREDENTIAL BACKUP DIR found at {cred_dir}")
        print("   → Run:  bash scripts/backup_credentials.sh")
        return 1

    backups = sorted(glob.glob(os.path.join(cred_dir, '.env.*')), reverse=True)
    if not backups:
        print(f"\n⚠️  NO CREDENTIAL BACKUPS in {cred_dir}")
        print("   → Run:  bash scripts/backup_credentials.sh")
        return 1

    latest = backups[0]
    age_days = (datetime.now(timezone.utc).timestamp() - os.path.getmtime(latest)) / 86400
    if age_days > 7:
        print(f"\n⚠️  STALE CREDENTIAL BACKUP — {age_days:.0f} days old:")
        print(f"   Latest: {os.path.basename(latest)}")
        print("   → Run:  bash scripts/backup_credentials.sh")
        return 1

    print(f"✅  Credential backup exists ({os.path.basename(latest)}, {age_days:.1f}d old, "
          f"{len(backups)} version(s))")
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='Trade DB health check + security audit')
    parser.add_argument('--days',     type=int, default=1,
                        help='Trade lookback window in days (default: 1)')
    parser.add_argument('--no-sec',   action='store_true',
                        help='Skip security checks (integrity checks only)')
    parser.add_argument('--sec-only', action='store_true',
                        help='Run security checks only (skip integrity checks)')
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"❌  DB not found: {DB_PATH}")
        sys.exit(1)

    conn = _conn()
    mode = "PAPER" if PAPER_TRADING else "LIVE"

    print(f"\n{'═'*55}")
    print(f"  HEALTH CHECK + SECURITY AUDIT  |  {mode}  |  last {args.days}d")
    print(f"{'═'*55}")

    total_issues = 0

    # ── Trade integrity ───────────────────────────────────────────────────────
    if not args.sec_only:
        _section("TRADE INTEGRITY")
        total_issues += check_duplicate_closes(conn, args.days)
        total_issues += check_orphaned_open_positions(conn)
        total_issues += check_duplicate_buys(conn, args.days)
        check_pnl_reconciliation(conn, args.days)
        check_outlier_trades(conn, args.days)
        total_issues += check_id_ordering(conn, args.days)
        total_issues += check_unknown_strategies(conn, args.days)

    # ── Security audit ────────────────────────────────────────────────────────
    if not args.no_sec:
        _section("SECURITY AUDIT")
        total_issues += check_file_permissions()
        total_issues += check_secrets_in_code()
        total_issues += check_db_tampering(conn, args.days)
        total_issues += check_off_hours_trades(conn, args.days)
        total_issues += check_log_anomalies()
        total_issues += check_credential_backup()

    conn.close()

    print(f"\n{'═'*55}")
    if total_issues == 0:
        print("✅  All checks passed — system looks clean")
    else:
        sev = "🚨 CRITICAL" if total_issues >= 5 else "⚠️  ISSUES FOUND"
        print(f"{sev}: {total_issues} problem(s) — review above")
    print(f"{'═'*55}\n")
    sys.exit(1 if total_issues > 0 else 0)


if __name__ == '__main__':
    main()
