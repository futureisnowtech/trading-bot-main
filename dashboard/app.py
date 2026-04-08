"""
dashboard/app.py — v14 Operator Panel
3-question design: Is the system healthy? Is it profitable? Where is it breaking?
Tabs: OPERATOR | DEEP ANALYSIS | MANUAL CONTROLS | FUTURES | DEV/CONFIG
"""

import sys, os, re, json

_DASH_DIR = os.path.dirname(os.path.abspath(__file__))
if _DASH_DIR not in sys.path:
    sys.path.insert(0, _DASH_DIR)
sys.path.append(os.path.dirname(_DASH_DIR))

try:
    from tooltips import TIPS
except ImportError:
    TIPS = {}

import sqlite3
from datetime import datetime, timedelta

import streamlit as st

# ── paths ──────────────────────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_ROOT, "logs", "trades.db")
LOG_PATH = os.path.join(_ROOT, "logs", "bot.log")
LAUNCH_DATE = "2026-04-02"

st.set_page_config(
    page_title="Algo Trading — Operator",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
section[data-testid="stSidebar"] { display: none; }
#MainMenu, footer, header, .stDeployButton, [data-testid="stToolbar"] { visibility: hidden; }
.block-container { padding: 16px 24px 60px 24px !important; max-width: 100% !important; }
.status-green  { color: #4ade80; font-weight: 700; }
.status-yellow { color: #facc15; font-weight: 700; }
.status-red    { color: #f87171; font-weight: 700; }
.badge-pass    { background: rgba(74,222,128,0.18); color:#4ade80; padding:2px 8px; border-radius:4px; font-size:0.8em; font-weight:700; }
.badge-warn    { background: rgba(250,204,21,0.18);  color:#facc15; padding:2px 8px; border-radius:4px; font-size:0.8em; font-weight:700; }
.badge-fail    { background: rgba(248,113,113,0.18); color:#f87171; padding:2px 8px; border-radius:4px; font-size:0.8em; font-weight:700; }
.panel-title   { font-size:1em; font-weight:700; text-transform:uppercase; letter-spacing:0.06em; color:#94a3b8; margin-bottom:4px; }
</style>
""",
    unsafe_allow_html=True,
)


# ══════════════════════════════════════════════════════════════════════════════
# DB HELPERS
# ══════════════════════════════════════════════════════════════════════════════


def _q(sql, params=()):
    try:
        with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute(sql, params).fetchall()]
    except Exception:
        return []


def _q1(sql, params=()):
    rows = _q(sql, params)
    return rows[0] if rows else {}


# ══════════════════════════════════════════════════════════════════════════════
# CORE DATA FUNCTIONS (ported verbatim, keep signature-stable)
# ══════════════════════════════════════════════════════════════════════════════


def get_account():
    try:
        from config import ACCOUNT_SIZE, PAPER_TRADING

        base = float(ACCOUNT_SIZE)
        paper = bool(PAPER_TRADING)
    except Exception:
        base, paper = 10000.0, True

    r = _q1(
        """SELECT SUM(pnl_usd) - SUM(fee_usd) AS net_pnl FROM trades
           WHERE ts >= ? AND paper=1
             AND (source IS NULL OR source NOT IN ('backtest','pre_v10_contaminated','bybit_paper'))""",
        (LAUNCH_DATE,),
    )
    realized = r.get("net_pnl") or 0.0
    unrealized = 0.0
    try:
        open_pos = _q(
            "SELECT symbol, direction, qty, entry FROM open_positions WHERE paper=1"
        )
        if open_pos:
            syms = [p["symbol"] for p in open_pos]
            prices = get_live_prices(syms)
            for p in open_pos:
                now = prices.get(p["symbol"], 0)
                if now <= 0:
                    continue
                qty = float(p["qty"] or 0)
                entry = float(p["entry"] or 0)
                if p["direction"] == "LONG":
                    unrealized += (now - entry) * qty
                else:
                    unrealized += (entry - now) * qty
    except Exception:
        pass
    return base + realized + unrealized, paper, base


def get_performance_stats():
    r = _q1(
        """SELECT
            COUNT(CASE WHEN won IS NOT NULL THEN 1 END)      AS closes,
            SUM(CASE WHEN won=1 THEN 1 ELSE 0 END)           AS wins,
            SUM(CASE WHEN won=0 THEN 1 ELSE 0 END)           AS losses,
            SUM(pnl_usd - fee_usd)                           AS total_net_pnl,
            SUM(CASE WHEN won=1 THEN pnl_usd - fee_usd ELSE 0 END) AS net_wins_sum,
            SUM(CASE WHEN won=0 THEN ABS(pnl_usd - fee_usd) ELSE 0 END) AS net_losses_sum,
            SUM(fee_usd)                                     AS total_fees,
            AVG(CASE WHEN won=1 THEN pnl_usd - fee_usd END)  AS avg_win,
            AVG(CASE WHEN won=0 THEN ABS(pnl_usd - fee_usd) END) AS avg_loss
        FROM trades
        WHERE ts >= ? AND paper=1 AND broker NOT LIKE '%bybit%'
          AND (source IS NULL OR source NOT IN ('backtest','pre_v10_contaminated','bybit_paper'))
          AND (notes IS NULL OR notes NOT LIKE '%force_test_close%')""",
        (LAUNCH_DATE,),
    )
    closes = r.get("closes") or 0
    wins = r.get("wins") or 0
    gw = r.get("net_wins_sum") or 0.0
    gl = r.get("net_losses_sum") or 0.0
    avg_win = r.get("avg_win") or 0.0
    avg_loss = r.get("avg_loss") or 0.0
    return {
        "closes": closes,
        "wins": wins,
        "losses": r.get("losses") or 0,
        "win_rate": wins / closes * 100 if closes else 0.0,
        "total_pnl": r.get("total_net_pnl") or 0.0,
        "profit_factor": gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0),
        "gross_wins": gw,
        "gross_losses": gl,
        "total_fees": r.get("total_fees") or 0.0,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "rr_realized": avg_win / avg_loss if avg_loss > 0 else 0.0,
    }


def get_rolling_pf(days=7):
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    r = _q1(
        """SELECT
            SUM(CASE WHEN won=1 THEN pnl_usd - fee_usd ELSE 0 END) AS gw,
            SUM(CASE WHEN won=0 THEN ABS(pnl_usd - fee_usd) ELSE 0 END) AS gl,
            COUNT(CASE WHEN won IS NOT NULL THEN 1 END) AS closes,
            SUM(CASE WHEN won=1 THEN 1 ELSE 0 END) AS wins
        FROM trades
        WHERE ts >= ? AND paper=1 AND broker NOT LIKE '%bybit%'
          AND (source IS NULL OR source NOT IN ('backtest','pre_v10_contaminated','bybit_paper'))
          AND (notes IS NULL OR notes NOT LIKE '%force_test_close%')""",
        (cutoff,),
    )
    gw = r.get("gw") or 0.0
    gl = r.get("gl") or 0.0
    closes = r.get("closes") or 0
    wins = r.get("wins") or 0
    return {
        "profit_factor": gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0),
        "closes": closes,
        "win_rate": wins / closes * 100 if closes else 0.0,
    }


def get_today_pnl():
    today = datetime.now().strftime("%Y-%m-%d")
    r = _q1(
        """SELECT SUM(pnl_usd) v FROM trades
           WHERE ts >= ? AND paper=1 AND broker NOT LIKE '%bybit%' AND pnl_usd != 0
             AND (source IS NULL OR source NOT IN ('backtest','pre_v10_contaminated','bybit_paper'))
             AND (notes IS NULL OR notes NOT LIKE '%force_test_close%')""",
        (today,),
    )
    return r.get("v") or 0.0


def get_open_positions():
    return _q("SELECT * FROM open_positions WHERE paper=1 ORDER BY ts_entry DESC")


def get_live_prices(symbols: list) -> dict:
    import urllib.request

    prices = {}
    if not symbols:
        return prices
    try:
        url = "https://futures.kraken.com/derivatives/api/v3/tickers"
        with urllib.request.urlopen(url, timeout=4) as resp:
            data = json.loads(resp.read())
        for t in data.get("tickers", []):
            sym = t.get("symbol", "")
            price = t.get("markPrice") or t.get("last") or 0
            if sym and price:
                prices[sym] = float(price)
    except Exception:
        pass
    missing = [s for s in symbols if s not in prices]
    if missing:
        try:
            req_data = json.dumps({"type": "allMids"}).encode()
            req = urllib.request.Request(
                "https://api.hyperliquid.xyz/info",
                data=req_data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                mids = json.loads(resp.read())
            for sym in missing:
                if sym in mids:
                    prices[sym] = float(mids[sym])
        except Exception:
            pass
    return prices


def get_equity_curve():
    return _q(
        """SELECT ts, SUM(pnl_usd) OVER (ORDER BY ts) AS cum_pnl
           FROM trades
           WHERE ts >= ? AND paper=1 AND broker NOT LIKE '%bybit%'
             AND pnl_usd != 0
             AND (source IS NULL OR source NOT IN ('backtest','pre_v10_contaminated','bybit_paper'))
             AND (notes IS NULL OR notes NOT LIKE '%force_test_close%')
           ORDER BY ts""",
        (LAUNCH_DATE,),
    )


def get_drawdown():
    """Peak-to-trough max drawdown from equity curve."""
    curve = get_equity_curve()
    if len(curve) < 2:
        return {"max_dd_usd": 0.0, "max_dd_pct": 0.0, "current_dd_usd": 0.0}
    pnls = [r["cum_pnl"] for r in curve if r["cum_pnl"] is not None]
    if not pnls:
        return {"max_dd_usd": 0.0, "max_dd_pct": 0.0, "current_dd_usd": 0.0}
    peak = pnls[0]
    max_dd = 0.0
    for p in pnls:
        if p > peak:
            peak = p
        dd = peak - p
        if dd > max_dd:
            max_dd = dd
    current_peak = max(pnls)
    current_val = pnls[-1]
    current_dd = max(0.0, current_peak - current_val)
    try:
        from config import ACCOUNT_SIZE

        base = float(ACCOUNT_SIZE)
    except Exception:
        base = 10000.0
    return {
        "max_dd_usd": max_dd,
        "max_dd_pct": max_dd / base * 100 if base else 0.0,
        "current_dd_usd": current_dd,
        "current_dd_pct": current_dd / base * 100 if base else 0.0,
    }


def get_trade_log(limit=50):
    return _q(
        """SELECT ts, symbol, action, qty, price, pnl_usd, fee_usd, notes
           FROM trades
           WHERE ts >= ? AND paper=1 AND broker NOT LIKE '%bybit%'
             AND pnl_usd != 0
             AND (source IS NULL OR source NOT IN ('backtest','pre_v10_contaminated','bybit_paper'))
             AND (notes IS NULL OR notes NOT LIKE '%force_test_close%')
           ORDER BY ts DESC LIMIT ?""",
        (LAUNCH_DATE, limit),
    )


def get_per_symbol_stats():
    return _q(
        """SELECT symbol,
            COUNT(*) AS trades,
            SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
            ROUND(100.0 * SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
            ROUND(SUM(pnl_usd), 2) AS total_pnl,
            ROUND(AVG(pnl_usd), 2) AS avg_pnl,
            ROUND(MAX(pnl_usd), 2) AS best,
            ROUND(MIN(pnl_usd), 2) AS worst
        FROM trades
        WHERE ts >= ? AND paper=1 AND broker NOT LIKE '%bybit%'
          AND pnl_usd != 0
          AND (source IS NULL OR source NOT IN ('backtest','pre_v10_contaminated','bybit_paper'))
          AND (notes IS NULL OR notes NOT LIKE '%force_test_close%')
        GROUP BY symbol ORDER BY total_pnl DESC""",
        (LAUNCH_DATE,),
    )


def get_signal_bayesian_stats():
    return _q("""
        SELECT signal_name, regime, fires, wins,
               ROUND(win_rate * 100, 1) AS win_rate_pct,
               ROUND(bayesian_pts, 2) AS bayesian_pts,
               ROUND(prior_pts, 2) AS prior_pts,
               ROUND(bayesian_pts - prior_pts, 2) AS pts_drift,
               ROUND(avg_pnl, 2) AS avg_pnl,
               last_updated
        FROM signal_stats WHERE regime = 'any'
        ORDER BY fires DESC, bayesian_pts DESC
    """)


def get_ml_status():
    r = _q1("SELECT COUNT(*) AS n FROM trade_features")
    return {"snapshots": r.get("n") or 0, "min_needed": 30}


def get_recent_events(limit=20):
    return _q(
        """SELECT ts, level, source, message FROM system_events
           WHERE source NOT IN ('IBKRBroker')
           ORDER BY rowid DESC LIMIT ?""",
        (limit,),
    )


# ── NEW: Health status from system_events ─────────────────────────────────────


def get_health_status() -> dict:
    """Parse the last health_check event from system_events."""
    row = _q1("""
        SELECT ts, level, message FROM system_events
        WHERE source = 'health_check'
        ORDER BY rowid DESC LIMIT 1
    """)
    if not row:
        return {
            "status": "UNKNOWN",
            "score": 0,
            "total": 6,
            "ts": None,
            "message": "No health check data yet",
        }
    msg = row.get("message", "")
    ts = row.get("ts", "")
    # parse "Health 6/6 [HEALTHY]" or similar
    m = re.search(r"(\d+)/(\d+)", msg)
    score = int(m.group(1)) if m else 0
    total = int(m.group(2)) if m else 6
    if "HEALTHY" in msg.upper():
        status = "HEALTHY"
    elif "DEGRADED" in msg.upper():
        status = "DEGRADED"
    else:
        status = "UNHEALTHY"
    return {"status": status, "score": score, "total": total, "ts": ts, "message": msg}


def get_heartbeat_age() -> int:
    """Seconds since last heartbeat write."""
    row = _q1("""
        SELECT ts FROM system_events
        WHERE source = 'heartbeat'
        ORDER BY rowid DESC LIMIT 1
    """)
    if not row or not row.get("ts"):
        return 9999
    return _ts_age_s(row["ts"])


def get_error_rate_1h() -> int:
    """Count of ERROR events in last 60 minutes."""
    cutoff = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    r = _q1(
        "SELECT COUNT(*) AS n FROM system_events WHERE level='ERROR' AND ts >= ?",
        (cutoff,),
    )
    return r.get("n") or 0


def get_restart_count_24h() -> int:
    """Number of bot start events in last 24h."""
    cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    r = _q1(
        "SELECT COUNT(*) AS n FROM system_events WHERE ts >= ? AND message LIKE '%Bot started%'",
        (cutoff,),
    )
    return r.get("n") or 0


# ── NEW: Execution quality from trade_attribution ─────────────────────────────


def get_execution_stats() -> dict:
    """MAE/MFE efficiency, fee trap rate, hold duration from trade_attribution."""
    r = _q1(
        """
        SELECT
            AVG(ABS(COALESCE(mae_pct, 0)))  AS avg_mae,
            AVG(COALESCE(mfe_pct, 0))       AS avg_mfe,
            COUNT(*)                         AS total,
            SUM(CASE WHEN is_fee_trap=1 THEN 1 ELSE 0 END) AS fee_traps,
            AVG(CASE WHEN won=1 THEN hold_minutes END) AS avg_hold_win,
            AVG(CASE WHEN won=0 THEN hold_minutes END) AS avg_hold_loss,
            AVG(CASE WHEN won=1 AND mfe_pct > 0 THEN pnl_pct / mfe_pct END) AS exit_eff
        FROM trade_attribution
        WHERE source != 'backtest' AND ts >= ?
    """,
        (LAUNCH_DATE,),
    )
    total = r.get("total") or 0
    avg_mae = r.get("avg_mae") or 0.0
    avg_mfe = r.get("avg_mfe") or 0.0
    fee_traps = r.get("fee_traps") or 0
    # Entry timing score: 10 * (1 - avg(min(|mae|/0.015, 1.0)))
    # Simplified: scale mae to 0-10 where lower mae = better score
    entry_score = (
        max(0.0, 10.0 * (1.0 - min(avg_mae / 0.015, 1.0))) if avg_mae >= 0 else 5.0
    )
    exit_eff_raw = r.get("exit_eff") or 0.0
    exit_score = min(10.0, max(0.0, exit_eff_raw * 10.0))
    return {
        "total": total,
        "avg_mae_pct": avg_mae * 100,
        "avg_mfe_pct": avg_mfe * 100,
        "entry_score": entry_score,
        "exit_score": exit_score,
        "fee_trap_rate": fee_traps / total * 100 if total else 0.0,
        "fee_traps": fee_traps,
        "avg_hold_win_min": r.get("avg_hold_win") or 0.0,
        "avg_hold_loss_min": r.get("avg_hold_loss") or 0.0,
    }


# ── NEW: Failure mode categorization ─────────────────────────────────────────


def get_failure_counts() -> list:
    """Return categorized failure counts from trade_attribution + system_events."""
    cutoff_7d = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    failures = []

    # Fee traps
    r = _q1(
        "SELECT COUNT(*) AS n, MAX(entry_ts) AS last FROM trade_attribution WHERE is_fee_trap=1 AND entry_ts >= ?",
        (cutoff_7d,),
    )
    failures.append(
        {
            "Category": "Fee Trap",
            "Count (7d)": r.get("n") or 0,
            "Last": _time_ago(r.get("last") or ""),
            "Severity": "WARN",
            "Description": "Fees consumed >50% of gross P&L move",
        }
    )

    # Quick stops (stop hit within 30 min — stop hunt)
    r = _q1(
        """SELECT COUNT(*) AS n, MAX(entry_ts) AS last FROM trade_attribution
           WHERE exit_type='stop_hit' AND COALESCE(hold_minutes,999) < 30 AND entry_ts >= ?""",
        (cutoff_7d,),
    )
    failures.append(
        {
            "Category": "Quick Stop (<30m)",
            "Count (7d)": r.get("n") or 0,
            "Last": _time_ago(r.get("last") or ""),
            "Severity": "WARN",
            "Description": "Stop hit within 30 min of entry (stop hunt / bad timing)",
        }
    )

    # Execution errors from system_events
    r = _q1(
        """SELECT COUNT(*) AS n, MAX(ts) AS last FROM system_events
           WHERE level='ERROR' AND source NOT IN ('IBKRBroker') AND ts >= ?""",
        (cutoff_7d,),
    )
    failures.append(
        {
            "Category": "Execution Error",
            "Count (7d)": r.get("n") or 0,
            "Last": _time_ago(r.get("last") or ""),
            "Severity": "CRIT" if (r.get("n") or 0) > 0 else "OK",
            "Description": "ERROR level events from broker/system",
        }
    )

    # Scan dropout (scanner returned 0 candidates)
    r = _q1(
        """SELECT COUNT(*) AS n, MAX(ts) AS last FROM system_events
           WHERE source='heartbeat' AND message LIKE '%candidates=0%' AND ts >= ?""",
        (cutoff_7d,),
    )
    failures.append(
        {
            "Category": "Scan Dropout (0 cands)",
            "Count (7d)": r.get("n") or 0,
            "Last": _time_ago(r.get("last") or ""),
            "Severity": "WARN",
            "Description": "Scanner returned 0 candidates — possible connectivity issue",
        }
    )

    # Duplicate close warnings
    r = _q1(
        """SELECT COUNT(*) AS n, MAX(ts) AS last FROM system_events
           WHERE message LIKE '%duplicate close%' AND ts >= ?""",
        (cutoff_7d,),
    )
    failures.append(
        {
            "Category": "Duplicate Close",
            "Count (7d)": r.get("n") or 0,
            "Last": _time_ago(r.get("last") or ""),
            "Severity": "WARN",
            "Description": "Idempotency guard triggered — duplicate close attempt",
        }
    )

    # Economics veto clusters (3+ vetoes same symbol in 30 min window — approximated)
    r = _q1(
        """SELECT COUNT(*) AS n, MAX(ts) AS last FROM system_events
           WHERE source='economics_gate' OR message LIKE '%ECONOMICS VETO%' AND ts >= ?""",
        (cutoff_7d,),
    )
    failures.append(
        {
            "Category": "Economics Veto",
            "Count (7d)": r.get("n") or 0,
            "Last": _time_ago(r.get("last") or ""),
            "Severity": "INFO",
            "Description": "Pre-trade EV veto fired (expected; high rate = opportunity cost)",
        }
    )

    # Stagnant positions
    r = _q1(
        """SELECT COUNT(*) AS n, MAX(ts) AS last FROM system_events
           WHERE message LIKE '%stagnant%' AND ts >= ?""",
        (cutoff_7d,),
    )
    failures.append(
        {
            "Category": "Stagnant Position",
            "Count (7d)": r.get("n") or 0,
            "Last": _time_ago(r.get("last") or ""),
            "Severity": "WARN",
            "Description": "Position open >48h with no movement",
        }
    )

    return failures


# ── NEW: Notification feed ────────────────────────────────────────────────────


def get_notification_feed(limit=20) -> list:
    """Read from notifications table (WARNING/CRITICAL only)."""
    return _q(
        """SELECT ts, category, severity, title, message FROM notifications
           WHERE severity IN ('WARNING','CRITICAL')
           ORDER BY rowid DESC LIMIT ?""",
        (limit,),
    )


def get_notification_counts() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    r = _q1(
        """SELECT
            SUM(CASE WHEN severity='CRITICAL' THEN 1 ELSE 0 END) AS critical,
            SUM(CASE WHEN severity='WARNING'  THEN 1 ELSE 0 END) AS warning,
            MAX(ts) AS last_ts
           FROM notifications WHERE ts >= ?""",
        (today,),
    )
    return {
        "critical": r.get("critical") or 0,
        "warning": r.get("warning") or 0,
        "last_ts": r.get("last_ts") or "",
    }


# ── MES Futures ───────────────────────────────────────────────────────────────


def get_mes_state() -> dict:
    row = _q1(
        "SELECT ts, message FROM system_events WHERE source='mes_state' ORDER BY rowid DESC LIMIT 1"
    )
    if not row:
        return {}
    try:
        state = json.loads(row.get("message", "{}"))
        state["ts"] = row.get("ts", "")
        return state
    except Exception:
        return {}


def get_mes_trades_today() -> list:
    today = datetime.now().strftime("%Y-%m-%d")
    return _q(
        "SELECT ts, action, qty, price, pnl_usd, notes FROM trades WHERE ts >= ? AND symbol = 'MES' ORDER BY ts DESC",
        (today,),
    )


def get_mes_daily_pnl() -> float:
    today = datetime.now().strftime("%Y-%m-%d")
    r = _q1(
        "SELECT SUM(pnl_usd) v FROM trades WHERE ts >= ? AND symbol = 'MES' AND pnl_usd != 0",
        (today,),
    )
    return r.get("v") or 0.0


def get_mes_all_time_stats() -> dict:
    r = _q1("""
        SELECT COUNT(*) AS closes,
               SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
               SUM(pnl_usd) AS total_pnl,
               SUM(CASE WHEN pnl_usd > 0 THEN pnl_usd ELSE 0 END) AS gross_wins,
               SUM(CASE WHEN pnl_usd < 0 THEN ABS(pnl_usd) ELSE 0 END) AS gross_losses
        FROM trades WHERE symbol='MES' AND pnl_usd!=0
          AND ts >= '2026-04-02'
          AND source NOT IN ('backtest','pre_v10_contaminated','bybit_paper')
    """)
    closes = r.get("closes") or 0
    wins = r.get("wins") or 0
    gw = r.get("gross_wins") or 0.0
    gl = r.get("gross_losses") or 0.0
    return {
        "closes": closes,
        "wins": wins,
        "win_rate": wins / closes * 100 if closes else 0.0,
        "total_pnl": r.get("total_pnl") or 0.0,
        "profit_factor": gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0),
    }


# ══════════════════════════════════════════════════════════════════════════════
# BOT LOG PARSING
# ══════════════════════════════════════════════════════════════════════════════


def _tail_log(n=800):
    try:
        with open(LOG_PATH, "r") as f:
            return f.readlines()[-n:]
    except Exception:
        return []


def get_last_scan_age():
    try:
        with open(LOG_PATH, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            buf = b""
            pos = file_size
            chunk = 8192
            while pos > 0:
                read_size = min(chunk, pos)
                pos -= read_size
                f.seek(pos)
                buf = f.read(read_size) + buf
                for raw in reversed(buf.split(b"\n")):
                    line = raw.decode("utf-8", errors="replace")
                    if "[v10] scan:" in line:
                        m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                        if m:
                            dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                            return int((datetime.now() - dt).total_seconds())
                        return 9999
                buf = buf.split(b"\n")[0]
    except Exception:
        pass
    return 9999


def get_scan_status():
    lines = _tail_log(800)
    result = {
        "age_s": 9999,
        "count": 0,
        "candidates": [],
        "steps": [],
        "duration_s": 0.0,
        "balance": 0.0,
        "deployed": 0.0,
    }
    complete_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if "[scanner] Complete:" in lines[i]:
            complete_idx = i
            break
    if complete_idx is None:
        return result
    m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", lines[complete_idx])
    if m:
        try:
            dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            result["age_s"] = int((datetime.now() - dt).total_seconds())
        except Exception:
            pass
    cm = re.search(
        r"Complete:\s*(\d+)\s*candidates\s*in\s*([\d.]+)s", lines[complete_idx]
    )
    if cm:
        result["count"] = int(cm.group(1))
        result["duration_s"] = float(cm.group(2))
    cand_re = re.compile(
        r"→\s+(\S+)\s+(LONG|SHORT)\s+spike=([\d.]+)\s+adx=([\d.]+)\s+ev=\$([\d.]+)\s+funding=([-\d.]+)%"
    )
    for line in lines[complete_idx + 1 : complete_idx + 20]:
        c = cand_re.search(line)
        if c:
            result["candidates"].append(
                {
                    "symbol": c.group(1),
                    "direction": c.group(2),
                    "vol_spike": float(c.group(3)),
                    "adx": float(c.group(4)),
                    "ev_usd": float(c.group(5)),
                    "funding_pct": float(c.group(6)),
                }
            )
    step_re = re.compile(r"\[scanner\] Step (\d+)[^:]*:\s*(\d+)\s*→\s*(\d+)")
    steps = {}
    for i in range(complete_idx, max(0, complete_idx - 30), -1):
        s = step_re.search(lines[i])
        if s:
            steps[int(s.group(1))] = {
                "step": int(s.group(1)),
                "in": int(s.group(2)),
                "out": int(s.group(3)),
                "dropped": int(s.group(2)) - int(s.group(3)),
                "label": lines[i].split("[scanner]")[-1].strip(),
            }
    result["steps"] = [steps[k] for k in sorted(steps.keys())]
    scan_re = re.compile(r"\[v10\] scan:.*balance=\$([\d.]+)\s+deployed=\$([\d.]+)")
    for line in lines[complete_idx : complete_idx + 5]:
        sm = scan_re.search(line)
        if sm:
            result["balance"] = float(sm.group(1))
            result["deployed"] = float(sm.group(2))
            break
    return result


def get_smart_log_summary(n=200) -> dict:
    """Parse bot.log into categorized event buckets."""
    lines = _tail_log(n)
    buckets = {
        "ENTERED": [],
        "CLOSE": [],
        "VETO": [],
        "SCAN": [],
        "ERROR": [],
        "ML": [],
        "HEALTH": [],
    }
    for line in reversed(lines):
        line = line.strip()
        if not any(
            x in line
            for x in (
                "[v10]",
                "[scanner]",
                "[perps]",
                "[risk]",
                "[wft]",
                "[learning]",
                "health",
            )
        ):
            continue
        if any(x in line for x in ("ib_insync", "IBKRBroker")):
            continue
        ts_m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        ts = ts_m.group(1)[11:19] if ts_m else ""
        msg = re.sub(
            r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+\s+\S+\s+\S+\s+", "", line
        )[:140]
        if "ENTERED" in line:
            k = "ENTERED"
        elif "PAPER CLOSE" in line or "CLOSE" in line.upper():
            k = "CLOSE"
        elif "ECONOMICS VETO" in line:
            k = "VETO"
        elif "Complete:" in line:
            k = "SCAN"
        elif "ERROR" in line.upper():
            k = "ERROR"
        elif "retrain" in line.lower() or "[wft]" in line:
            k = "ML"
        elif "health" in line.lower():
            k = "HEALTH"
        else:
            continue
        if len(buckets[k]) < 5:
            buckets[k].append({"ts": ts, "msg": msg})
    # Compute 1h counts
    cutoff_1h = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    r = _q1(
        "SELECT COUNT(*) AS n FROM system_events WHERE level='ERROR' AND ts >= ?",
        (cutoff_1h,),
    )
    error_count_1h = r.get("n") or 0
    # Count recent VETO from system_events
    rv = _q1(
        "SELECT COUNT(*) AS n FROM system_events WHERE message LIKE '%VETO%' AND ts >= ?",
        (cutoff_1h,),
    )
    veto_count_1h = rv.get("n") or 0
    # Count ENTERED from trades
    re2 = _q1(
        "SELECT COUNT(*) AS n FROM trades WHERE ts >= ? AND paper=1 AND action IN ('BUY','SELL') AND pnl_usd=0",
        (cutoff_1h,),
    )
    entry_count_1h = re2.get("n") or 0
    return {
        "buckets": buckets,
        "error_count_1h": error_count_1h,
        "veto_count_1h": veto_count_1h,
        "entry_count_1h": entry_count_1h,
    }


# ══════════════════════════════════════════════════════════════════════════════
# FORMAT HELPERS
# ══════════════════════════════════════════════════════════════════════════════


def _fmt_pnl(v):
    s = "+" if v > 0 else ""
    return f"{s}${v:,.2f}"


def _time_ago(ts_str):
    try:
        ts_str = ts_str.replace("T", " ").split(".")[0].split("+")[0][:19]
        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        secs = int((datetime.now() - dt).total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m {secs % 60}s ago"
        if secs < 86400:
            return f"{secs // 3600}h {(secs % 3600) // 60}m ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return ts_str[:16] if ts_str else "–"


def _ts_age_s(ts_str) -> int:
    try:
        ts_str = ts_str.replace("T", " ").split(".")[0].split("+")[0][:19]
        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        return max(0, int((datetime.now() - dt).total_seconds()))
    except Exception:
        return 9999


def _parse_notes(notes):
    if not notes:
        return {}
    r = {}
    for pattern, key in [
        (r"score=([\d.]+)", "score"),
        (r"regime=(\w+)", "regime"),
        (r"setup=(\S+)", "setup"),
        (r"lev=(\d+)x", "lev"),
        (r"reason=(\S+)", "reason"),
    ]:
        m = re.search(pattern, notes)
        if m:
            r[key] = m.group(1)
    return r


def _badge(text, kind="pass"):
    # kind: pass | warn | fail
    return f'<span class="badge-{kind}">{text}</span>'


def _status_dot(color):
    colors = {
        "green": "#4ade80",
        "yellow": "#facc15",
        "red": "#f87171",
        "gray": "#64748b",
    }
    c = colors.get(color, "#94a3b8")
    return f'<span style="color:{c}; font-size:1.1em;">●</span>'


def _age_color(age_s, warn=120, crit=300):
    if age_s >= 9999:
        return "red"
    if age_s > crit:
        return "red"
    if age_s > warn:
        return "yellow"
    return "green"


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1: OPERATOR — TIER 1 ROW (System Health | Edge Quality | Alert Feed)
# ══════════════════════════════════════════════════════════════════════════════


@st.fragment(run_every=10)
def render_system_integrity():
    import pandas as pd

    st.markdown(
        '<div class="panel-title">System Integrity</div>', unsafe_allow_html=True
    )

    health = get_health_status()
    scan_age = get_last_scan_age()
    heartbeat_age = get_heartbeat_age()
    error_rate = get_error_rate_1h()
    restart_count = get_restart_count_24h()
    ml = get_ml_status()

    # Derive overall status
    scan_color = _age_color(scan_age, warn=120, crit=300)
    hb_color = _age_color(heartbeat_age, warn=360, crit=720)
    err_color = "green" if error_rate == 0 else ("yellow" if error_rate <= 5 else "red")
    health_status = health.get("status", "UNKNOWN")
    score = health.get("score", 0)
    total_checks = health.get("total", 6)

    # Determine overall panel status
    if health_status == "UNHEALTHY" or err_color == "red" or scan_color == "red":
        overall_color = "red"
        overall_label = "CRITICAL"
    elif health_status == "DEGRADED" or err_color == "yellow" or scan_color == "yellow":
        overall_color = "yellow"
        overall_label = "DEGRADED"
    else:
        overall_color = "green"
        overall_label = "HEALTHY"

    # Big status display
    st.markdown(
        f'{_status_dot(overall_color)} <span style="font-size:1.2em; font-weight:700; color: {"#4ade80" if overall_color == "green" else "#facc15" if overall_color == "yellow" else "#f87171"}">{overall_label}</span>'
        f' &nbsp; <span style="color:#94a3b8; font-size:0.85em">{score}/{total_checks} checks passing</span>',
        unsafe_allow_html=True,
    )

    # Detail rows
    rows_html = ""
    checks = [
        (
            "Health checks",
            f"{score}/{total_checks}",
            "green"
            if health_status == "HEALTHY"
            else ("yellow" if health_status == "DEGRADED" else "red"),
        ),
        ("Last scan", f"{scan_age}s ago" if scan_age < 9999 else "no data", scan_color),
        (
            "Heartbeat",
            f"{heartbeat_age}s ago" if heartbeat_age < 9999 else "no data",
            hb_color,
        ),
        ("Errors (1h)", str(error_rate), err_color),
        (
            "ML gate",
            "loaded"
            if ml["snapshots"] >= ml["min_needed"]
            else f"{ml['snapshots']}/{ml['min_needed']} snaps",
            "green" if ml["snapshots"] >= ml["min_needed"] else "yellow",
        ),
        (
            "Restarts (24h)",
            str(restart_count),
            "green" if restart_count <= 1 else "yellow",
        ),
    ]
    for label, val, color in checks:
        dot = _status_dot(color)
        rows_html += f'<div style="display:flex; justify-content:space-between; margin:2px 0; font-size:0.82em"><span style="color:#94a3b8">{dot} {label}</span><span style="color:#e2e8f0; font-weight:600">{val}</span></div>'

    st.markdown(rows_html, unsafe_allow_html=True)
    st.caption(f"Updated {datetime.now().strftime('%H:%M:%S')}")


@st.fragment(run_every=30)
def render_edge_quality():
    st.markdown('<div class="panel-title">Edge Quality</div>', unsafe_allow_html=True)

    stats = get_performance_stats()
    rolling_7d = get_rolling_pf(days=7)
    rolling_1d = get_rolling_pf(days=1)
    closes = stats["closes"]

    pf = stats["profit_factor"]
    pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"
    pf_color = "green" if pf >= 1.35 else ("yellow" if pf >= 1.0 else "red")
    badge_kind = "pass" if pf >= 1.35 else ("warn" if pf >= 1.0 else "fail")

    wr = stats["win_rate"]
    ev_per_trade = stats["total_pnl"] / closes if closes else 0.0

    st.markdown(
        f'<div style="font-size:2em; font-weight:800; color: {"#4ade80" if pf_color == "green" else "#facc15" if pf_color == "yellow" else "#f87171"}">'
        f"PF {pf_str}</div>"
        f'<div style="font-size:0.75em; color:#94a3b8; margin-top:-4px">'
        f"{_badge('PASS ≥1.35' if pf >= 1.35 else 'WARN ≥1.0' if pf >= 1.0 else 'FAIL <1.0', badge_kind)}"
        f"</div>",
        unsafe_allow_html=True,
    )

    rows_html = ""
    metrics = [
        ("Win Rate", f"{wr:.1f}%  ({stats['wins']}W/{stats['losses']}L)"),
        ("EV / trade (net)", _fmt_pnl(ev_per_trade)),
        (
            "Avg Win / Avg Loss",
            f"{_fmt_pnl(stats['avg_win'])} / {_fmt_pnl(-stats['avg_loss'])}",
        ),
        ("R:R realized", f"{stats['rr_realized']:.2f}×"),
        ("Total fees", _fmt_pnl(-stats["total_fees"])),
        (
            "7d PF",
            f"{rolling_7d['profit_factor']:.2f}"
            if rolling_7d["profit_factor"] != float("inf")
            else "∞" + f"  ({rolling_7d['closes']} trades)",
        ),
        (
            "24h PF",
            f"{rolling_1d['profit_factor']:.2f}"
            if rolling_1d["profit_factor"] != float("inf")
            else "∞" + f"  ({rolling_1d['closes']} trades)",
        ),
    ]
    for label, val in metrics:
        rows_html += f'<div style="display:flex; justify-content:space-between; margin:2px 0; font-size:0.82em"><span style="color:#94a3b8">{label}</span><span style="color:#e2e8f0; font-weight:600">{val}</span></div>'

    st.markdown(rows_html, unsafe_allow_html=True)
    st.caption(f"{closes} clean trades since {LAUNCH_DATE}")


@st.fragment(run_every=10)
def render_alert_feed():
    st.markdown('<div class="panel-title">Alert Feed</div>', unsafe_allow_html=True)

    counts = get_notification_counts()
    crit = counts["critical"]
    warn = counts["warning"]
    last_ts = counts["last_ts"]

    crit_color = "#f87171" if crit > 0 else "#94a3b8"
    warn_color = "#facc15" if warn > 0 else "#94a3b8"

    st.markdown(
        f'<div style="display:flex; gap:16px; margin-bottom:8px">'
        f'<div style="text-align:center"><div style="font-size:1.6em; font-weight:800; color:{crit_color}">{crit}</div><div style="font-size:0.72em; color:#94a3b8">CRITICAL</div></div>'
        f'<div style="text-align:center"><div style="font-size:1.6em; font-weight:800; color:{warn_color}">{warn}</div><div style="font-size:0.72em; color:#94a3b8">WARNING</div></div>'
        f'<div style="text-align:center"><div style="font-size:0.8em; color:#94a3b8; margin-top:8px">Last:<br>{_time_ago(last_ts) if last_ts else "–"}</div></div>'
        f"</div>",
        unsafe_allow_html=True,
    )

    feed = get_notification_feed(limit=6)
    if not feed:
        # Fall back to system_events if notifications table empty
        events = get_recent_events(5)
        for e in events:
            level = e.get("level", "INFO")
            color = (
                "#f87171"
                if level == "ERROR"
                else ("#facc15" if level == "WARNING" else "#94a3b8")
            )
            msg = e.get("message", "")[:60]
            ts = _time_ago(e.get("ts", ""))
            st.markdown(
                f'<div style="font-size:0.78em; border-left:2px solid {color}; padding-left:6px; margin:3px 0; color:#e2e8f0">'
                f'<span style="color:{color}; font-weight:700">[{level}]</span> {msg}<br>'
                f'<span style="color:#64748b">{ts}</span></div>',
                unsafe_allow_html=True,
            )
    else:
        for n in feed:
            sev = n.get("severity", "INFO")
            color = "#f87171" if sev == "CRITICAL" else "#facc15"
            title = n.get("title", "")[:40]
            ts = _time_ago(n.get("ts", ""))
            st.markdown(
                f'<div style="font-size:0.78em; border-left:2px solid {color}; padding-left:6px; margin:3px 0; color:#e2e8f0">'
                f'<span style="color:{color}; font-weight:700">[{sev}]</span> {title}<br>'
                f'<span style="color:#64748b">{ts}</span></div>',
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1: OPERATOR — TIER 2 ROW (Open Positions | Scanner Funnel | Failures)
# ══════════════════════════════════════════════════════════════════════════════


@st.fragment(run_every=10)
def render_positions_compact():
    st.markdown('<div class="panel-title">Open Positions</div>', unsafe_allow_html=True)

    open_p = get_open_positions()
    n = len(open_p)

    if not open_p:
        st.info("No open positions.")
        return

    symbols = [p.get("symbol", "") for p in open_p]
    live_prices = get_live_prices(symbols)
    total_deployed = 0.0
    total_unrealized = 0.0

    rows_html = ""
    for p in open_p:
        symbol = p.get("symbol", "")
        direction = p.get("direction", "LONG")
        entry = float(p.get("entry") or 0)
        qty = float(p.get("qty") or 0)
        stop = float(p.get("stop") or 0)
        now = live_prices.get(symbol, 0) or entry
        deployed = qty * entry
        if direction == "LONG":
            unreal = (now - entry) * qty
            stop_pct = (entry - stop) / entry * 100 if entry else 0
        else:
            unreal = (entry - now) * qty
            stop_pct = (stop - entry) / entry * 100 if entry else 0
        total_deployed += deployed
        total_unrealized += unreal
        pnl_color = "#4ade80" if unreal >= 0 else "#f87171"
        dir_arrow = "▲" if direction == "LONG" else "▼"
        dir_color = "#4ade80" if direction == "LONG" else "#f87171"
        age = _time_ago(p.get("ts_entry", ""))
        rows_html += (
            f'<div style="display:flex; justify-content:space-between; font-size:0.8em; padding:3px 0; border-bottom:1px solid rgba(255,255,255,0.05)">'
            f'<span><span style="color:{dir_color}; font-weight:700">{dir_arrow}</span> <span style="color:#e2e8f0; font-weight:600">{symbol}</span>'
            f' <span style="color:#64748b">{age}</span></span>'
            f'<span style="color:{pnl_color}; font-weight:700">{_fmt_pnl(unreal)}</span>'
            f"</div>"
        )

    st.markdown(rows_html, unsafe_allow_html=True)
    pnl_color_overall = "#4ade80" if total_unrealized >= 0 else "#f87171"
    st.markdown(
        f'<div style="display:flex; justify-content:space-between; margin-top:6px; font-size:0.82em">'
        f'<span style="color:#94a3b8">{n} positions · ${total_deployed:,.0f} deployed</span>'
        f'<span style="color:{pnl_color_overall}; font-weight:700">Unrealized {_fmt_pnl(total_unrealized)}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )


@st.fragment(run_every=15)
def render_scanner_funnel():
    st.markdown('<div class="panel-title">Scanner Funnel</div>', unsafe_allow_html=True)

    scan = get_scan_status()
    age = scan["age_s"]
    age_str = f"{age}s ago" if age < 9999 else "no data"
    age_color = _age_color(age, warn=120, crit=360)

    st.markdown(
        f'{_status_dot(age_color)} <span style="font-size:0.85em; color:#94a3b8">Last scan: <span style="color:#e2e8f0; font-weight:600">{age_str}</span></span>  '
        f'<span style="font-size:0.85em; color:#94a3b8">&nbsp;·&nbsp; {scan["count"]} candidates &nbsp;·&nbsp; {scan["duration_s"]:.1f}s</span>',
        unsafe_allow_html=True,
    )

    if scan["steps"]:
        # Draw a compact funnel
        steps_html = ""
        for s in scan["steps"]:
            drop_pct = s["dropped"] / s["in"] * 100 if s["in"] else 0
            bar_w = max(4, int((s["out"] / max(s["in"], 1)) * 100))
            steps_html += (
                f'<div style="font-size:0.76em; margin:2px 0; display:flex; align-items:center; gap:6px">'
                f'<span style="color:#64748b; min-width:24px">S{s["step"]}</span>'
                f'<div style="flex:1; background:rgba(255,255,255,0.06); border-radius:3px; height:10px">'
                f'<div style="width:{bar_w}%; background:#3b82f6; border-radius:3px; height:10px"></div></div>'
                f'<span style="color:#e2e8f0; min-width:30px">{s["out"]}</span>'
                f'<span style="color:#f87171; font-size:0.85em">-{s["dropped"]}</span>'
                f"</div>"
            )
        st.markdown(steps_html, unsafe_allow_html=True)
    elif scan["candidates"]:
        st.caption(f"{len(scan['candidates'])} candidates passed all filters")
    else:
        st.caption("Waiting for scan data…")

    if scan["balance"]:
        st.caption(
            f"Balance ${scan['balance']:,.0f} · Deployed ${scan['deployed']:,.0f}"
        )


@st.fragment(run_every=30)
def render_failures_compact():
    st.markdown(
        '<div class="panel-title">Failure Modes (7d)</div>', unsafe_allow_html=True
    )

    failures = get_failure_counts()
    # Show top 5 by count, exclude zero-count OK items unless all zero
    active = sorted(
        [f for f in failures if f["Count (7d)"] > 0], key=lambda x: -x["Count (7d)"]
    )
    show = active[:5] if active else failures[:5]

    if not show:
        st.markdown(
            '<span style="color:#4ade80; font-size:0.85em">✓ No failures detected in last 7 days</span>',
            unsafe_allow_html=True,
        )
        return

    for f in show:
        sev = f["Severity"]
        color = (
            "#f87171" if sev == "CRIT" else ("#facc15" if sev == "WARN" else "#94a3b8")
        )
        count = f["Count (7d)"]
        cat = f["Category"]
        last = f["Last"]
        st.markdown(
            f'<div style="display:flex; justify-content:space-between; font-size:0.8em; padding:2px 0">'
            f'<span><span style="color:{color}; font-weight:700">{count}×</span> <span style="color:#e2e8f0">{cat}</span></span>'
            f'<span style="color:#64748b">{last}</span>'
            f"</div>",
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1: OPERATOR — TIER 2 ROW (Execution Quality | Decision Quality)
# ══════════════════════════════════════════════════════════════════════════════


@st.fragment(run_every=30)
def render_execution_quality():
    st.markdown(
        '<div class="panel-title">Execution Quality</div>', unsafe_allow_html=True
    )

    ex = get_execution_stats()
    total = ex["total"]

    if total == 0:
        st.info("No trade attribution data yet — populates after first closed trade.")
        return

    rows_html = ""
    metrics = [
        (
            "Entry timing score",
            f"{ex['entry_score']:.1f} / 10",
            "green"
            if ex["entry_score"] >= 6
            else "yellow"
            if ex["entry_score"] >= 4
            else "red",
        ),
        (
            "Exit efficiency score",
            f"{ex['exit_score']:.1f} / 10",
            "green"
            if ex["exit_score"] >= 6
            else "yellow"
            if ex["exit_score"] >= 4
            else "red",
        ),
        (
            "Avg MAE (adverse move)",
            f"{ex['avg_mae_pct']:.3f}%",
            "green" if ex["avg_mae_pct"] < 0.5 else "yellow",
        ),
        ("Avg MFE (best possible)", f"{ex['avg_mfe_pct']:.3f}%", "gray"),
        (
            "Fee trap rate",
            f"{ex['fee_trap_rate']:.1f}%  ({ex['fee_traps']}/{total})",
            "green"
            if ex["fee_trap_rate"] < 5
            else "yellow"
            if ex["fee_trap_rate"] < 15
            else "red",
        ),
        (
            "Avg hold — wins",
            f"{ex['avg_hold_win_min']:.0f}m" if ex["avg_hold_win_min"] else "n/a",
            "gray",
        ),
        (
            "Avg hold — losses",
            f"{ex['avg_hold_loss_min']:.0f}m" if ex["avg_hold_loss_min"] else "n/a",
            "gray",
        ),
        ("Slippage", "N/A — not yet instrumented", "gray"),
    ]
    for label, val, color in metrics:
        dot = _status_dot(color)
        rows_html += f'<div style="display:flex; justify-content:space-between; margin:2px 0; font-size:0.82em"><span style="color:#94a3b8">{dot} {label}</span><span style="color:#e2e8f0; font-weight:600">{val}</span></div>'

    st.markdown(rows_html, unsafe_allow_html=True)
    st.caption(f"Based on {total} attributed trades since {LAUNCH_DATE}")


@st.fragment(run_every=30)
def render_decision_quality():
    st.markdown(
        '<div class="panel-title">Decision Quality</div>', unsafe_allow_html=True
    )

    stats = get_performance_stats()
    closes = stats["closes"]
    if closes == 0:
        st.info("No closed trades yet.")
        return

    wins = stats["wins"]
    losses = stats["losses"]
    win_pct = stats["win_rate"]
    loss_pct = 100 - win_pct

    # Good signal / Bad signal breakdown (simplified)
    # From trade_attribution if available
    r = _q1(
        """
        SELECT
            SUM(CASE WHEN won=1 THEN 1 ELSE 0 END) AS good_outcome,
            SUM(CASE WHEN won=0 AND exit_type='stop_hit' THEN 1 ELSE 0 END) AS stopped_out,
            SUM(CASE WHEN won=0 AND exit_type='thesis_exit' THEN 1 ELSE 0 END) AS thesis_failed,
            SUM(CASE WHEN won=1 AND exit_type='target_hit' THEN 1 ELSE 0 END) AS full_target,
            COUNT(*) AS total
        FROM trade_attribution WHERE ts >= ?
    """,
        (LAUNCH_DATE,),
    )
    attr_total = r.get("total") or 0

    rows_html = ""
    if attr_total > 0:
        good = r.get("good_outcome") or 0
        stopped = r.get("stopped_out") or 0
        thesis_fail = r.get("thesis_failed") or 0
        full_target = r.get("full_target") or 0
        metrics = [
            (
                "Good outcome (won)",
                f"{good}/{attr_total} = {good / attr_total * 100:.0f}%",
            ),
            (
                "Stopped out (bad timing/signal)",
                f"{stopped}/{attr_total} = {stopped / attr_total * 100:.0f}%",
            ),
            (
                "Thesis invalidation exit",
                f"{thesis_fail}/{attr_total} = {thesis_fail / attr_total * 100:.0f}%",
            ),
            (
                "Full target hit",
                f"{full_target}/{attr_total} = {full_target / attr_total * 100:.0f}%",
            ),
        ]
    else:
        metrics = [
            ("Overall win rate", f"{win_pct:.1f}%  ({wins}W / {losses}L)"),
            ("Attribution rows", "0 — populates after trade closes"),
        ]

    # Top / worst Bayesian signals
    bay = get_signal_bayesian_stats()
    if bay:
        improving = sorted(
            [b for b in bay if b.get("pts_drift", 0) > 0], key=lambda x: -x["pts_drift"]
        )[:2]
        degrading = sorted(
            [b for b in bay if b.get("pts_drift", 0) < 0], key=lambda x: x["pts_drift"]
        )[:2]
        if improving:
            metrics.append(
                (
                    "Top signal ↑",
                    f"{improving[0]['signal_name']} (+{improving[0]['pts_drift']:.1f}pts, {improving[0]['win_rate_pct']:.0f}%WR)",
                )
            )
        if degrading:
            metrics.append(
                (
                    "Worst signal ↓",
                    f"{degrading[0]['signal_name']} ({degrading[0]['pts_drift']:.1f}pts, {degrading[0]['win_rate_pct']:.0f}%WR)",
                )
            )

    for label, val in metrics:
        rows_html += f'<div style="display:flex; justify-content:space-between; margin:2px 0; font-size:0.82em"><span style="color:#94a3b8">{label}</span><span style="color:#e2e8f0; font-weight:600">{val}</span></div>'

    st.markdown(rows_html, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1: OPERATOR — EQUITY CURVE + SMART LOGS
# ══════════════════════════════════════════════════════════════════════════════


@st.fragment(run_every=30)
def render_equity_curve_compact():
    import pandas as pd

    eq = get_equity_curve()
    dd = get_drawdown()
    today_pnl = get_today_pnl()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric(
        "Today's P&L",
        _fmt_pnl(today_pnl),
        delta_color="normal" if today_pnl >= 0 else "inverse",
    )
    col2.metric(
        "Max Drawdown",
        f"${dd['max_dd_usd']:.2f}",
        delta=f"{dd['max_dd_pct']:.1f}% of account",
        delta_color="inverse" if dd["max_dd_usd"] > 0 else "off",
        help=TIPS.get("max_drawdown"),
    )
    col3.metric(
        "Current DD",
        f"${dd['current_dd_usd']:.2f}",
        delta=f"{dd['current_dd_pct']:.1f}%",
        delta_color="inverse" if dd["current_dd_usd"] > 0 else "off",
        help=TIPS.get("current_dd"),
    )

    # 7d rolling Sharpe (simplified)
    rolling = get_rolling_pf(days=7)
    col4.metric(
        "7d Trades",
        str(rolling["closes"]),
        delta=f"{rolling['win_rate']:.0f}% WR",
        delta_color="normal" if rolling["win_rate"] >= 52 else "inverse",
    )

    if eq:
        df = pd.DataFrame(eq)
        df["ts"] = pd.to_datetime(df["ts"].str[:19])
        df = df.rename(columns={"cum_pnl": "Net P&L ($)"})
        st.line_chart(
            df.set_index("ts")[["Net P&L ($)"]], height=160, use_container_width=True
        )
    else:
        st.info("Equity curve appears after first closed trade.")


@st.fragment(run_every=15)
def render_smart_logs():
    st.markdown(
        '<div class="panel-title">Activity (last 15m)</div>', unsafe_allow_html=True
    )

    summary = get_smart_log_summary(200)
    error_1h = summary["error_count_1h"]
    veto_1h = summary["veto_count_1h"]
    entry_1h = summary["entry_count_1h"]
    buckets = summary["buckets"]

    # Summary line
    err_color = "#f87171" if error_1h > 0 else "#4ade80"
    st.markdown(
        f'<div style="font-size:0.82em; color:#94a3b8; margin-bottom:6px">'
        f"Last 1h: "
        f'<span style="color:#4ade80">{entry_1h} entries</span> · '
        f'<span style="color:#facc15">{veto_1h} vetoes</span> · '
        f'<span style="color:{err_color}; font-weight:700">{error_1h} errors</span>'
        f"</div>",
        unsafe_allow_html=True,
    )

    KIND_COLOR = {
        "ENTERED": "#4ade80",
        "CLOSE": "#60a5fa",
        "VETO": "#f97316",
        "SCAN": "#94a3b8",
        "ERROR": "#f87171",
        "ML": "#a78bfa",
        "HEALTH": "#64748b",
    }
    for kind, events in buckets.items():
        if not events:
            continue
        color = KIND_COLOR.get(kind, "#94a3b8")
        latest = events[0]
        st.markdown(
            f'<div style="font-size:0.78em; border-left:2px solid {color}; padding-left:6px; margin:2px 0">'
            f'<span style="color:{color}; font-weight:700">[{kind}]</span> '
            f'<span style="color:#e2e8f0">{latest["msg"][:100]}</span> '
            f'<span style="color:#64748b">{latest["ts"]}</span>'
            f"</div>",
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2: DEEP ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════


@st.fragment(run_every=30)
def render_deep_analysis():
    import pandas as pd

    # ── Full Edge Quality ──────────────────────────────────────────────────────
    st.subheader("Edge Quality — Full Breakdown")
    stats = get_performance_stats()
    dd = get_drawdown()

    c1, c2, c3, c4, c5 = st.columns(5)
    pf = stats["profit_factor"]
    c1.metric(
        "Profit Factor",
        f"{pf:.2f}" if pf != float("inf") else "∞",
        delta="≥1.35 needed for live",
        delta_color="normal" if pf >= 1.35 else "inverse",
        help=TIPS.get("profit_factor"),
    )
    c2.metric(
        "Win Rate",
        f"{stats['win_rate']:.1f}%",
        delta=f"{stats['wins']}W / {stats['losses']}L",
        help=TIPS.get("win_rate"),
    )
    c3.metric(
        "EV / trade",
        _fmt_pnl(stats["total_pnl"] / stats["closes"]) if stats["closes"] else "$0",
        delta_color="normal",
        help=TIPS.get("ev_per_trade"),
    )
    c4.metric(
        "R:R Realized", f"{stats['rr_realized']:.2f}×", help=TIPS.get("rr_realized")
    )
    c5.metric(
        "Max Drawdown",
        f"${dd['max_dd_usd']:.2f}",
        delta=f"{dd['max_dd_pct']:.1f}%",
        delta_color="inverse",
        help=TIPS.get("max_drawdown"),
    )

    col_left, col_right = st.columns(2)

    with col_left:
        # Performance by regime (from trade_attribution)
        st.caption("**Performance by regime**")
        regime_data = _q(
            """
            SELECT regime,
                COUNT(*) AS trades,
                SUM(CASE WHEN won=1 THEN 1 ELSE 0 END) AS wins,
                ROUND(100.0 * SUM(CASE WHEN won=1 THEN 1 ELSE 0 END) / COUNT(*), 1) AS wr_pct,
                ROUND(AVG(pnl_usd), 2) AS avg_pnl,
                ROUND(SUM(pnl_usd), 2) AS total_pnl
            FROM trade_attribution
            WHERE ts >= ? GROUP BY regime ORDER BY total_pnl DESC
        """,
            (LAUNCH_DATE,),
        )
        if regime_data:
            st.dataframe(
                pd.DataFrame(regime_data), use_container_width=True, hide_index=True
            )
        else:
            st.info("No regime attribution data yet.")

    with col_right:
        # Per-symbol stats
        st.caption("**Performance by symbol**")
        sym = get_per_symbol_stats()
        if sym:
            st.dataframe(pd.DataFrame(sym), use_container_width=True, hide_index=True)
        else:
            st.info("No closed trades yet.")

    # Equity curve
    eq = get_equity_curve()
    if eq:
        df = pd.DataFrame(eq)
        df["ts"] = pd.to_datetime(df["ts"].str[:19])
        df = df.rename(columns={"cum_pnl": "Net P&L ($)"})
        st.line_chart(
            df.set_index("ts")[["Net P&L ($)"]], height=200, use_container_width=True
        )

    st.divider()

    # ── Full Execution Quality ─────────────────────────────────────────────────
    st.subheader("Execution Quality — Full Breakdown")
    ex = get_execution_stats()
    if ex["total"] > 0:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(
            "Entry Timing",
            f"{ex['entry_score']:.1f}/10",
            delta="higher = better entry price",
            help=TIPS.get("entry_score"),
        )
        c2.metric(
            "Exit Efficiency",
            f"{ex['exit_score']:.1f}/10",
            delta="higher = captured more MFE",
            help=TIPS.get("exit_score"),
        )
        c3.metric(
            "Fee Trap Rate",
            f"{ex['fee_trap_rate']:.1f}%",
            delta=f"{ex['fee_traps']} traps / {ex['total']} trades",
            delta_color="inverse" if ex["fee_trap_rate"] > 5 else "off",
            help=TIPS.get("fee_trap"),
        )
        c4.metric(
            "Avg MAE",
            f"{ex['avg_mae_pct']:.3f}%",
            delta="adverse move before recovery",
            help=TIPS.get("mae"),
        )

        # MAE/MFE table from trade_attribution
        attr = _q(
            """
            SELECT symbol, direction, ROUND(mae_pct*100,3) AS mae_pct, ROUND(mfe_pct*100,3) AS mfe_pct,
                   exit_type, hold_minutes, is_fee_trap, won
            FROM trade_attribution WHERE ts >= ?
            ORDER BY entry_ts DESC LIMIT 30
        """,
            (LAUNCH_DATE,),
        )
        if attr:
            st.caption("Last 30 trade attributions")
            st.dataframe(pd.DataFrame(attr), use_container_width=True, hide_index=True)
    else:
        st.info("trade_attribution table is empty — populates as trades close.")

    st.divider()

    # ── Full Decision Quality / Signal Attribution ─────────────────────────────
    st.subheader("Signal Attribution (Bayesian Learning)")
    bay_stats = get_signal_bayesian_stats()
    if bay_stats:
        df_bay = pd.DataFrame(bay_stats)
        st.dataframe(df_bay, use_container_width=True, hide_index=True)
    else:
        st.info("No Bayesian signal data yet — accumulates with live trades.")

    st.divider()

    # ── Learning / Intelligence ────────────────────────────────────────────────
    st.subheader("Learning & Intelligence")

    ml = get_ml_status()
    col1, col2 = st.columns(2)
    with col1:
        snap = ml["snapshots"]
        needed = ml["min_needed"]
        status = (
            "ACTIVE" if snap >= needed else f"ACCUMULATING — {snap}/{needed} snapshots"
        )
        st.metric(
            "ML Snapshots",
            f"{snap} / {needed}",
            delta=status,
            delta_color="normal" if snap >= needed else "off",
            help=TIPS.get("ml_gate"),
        )
        st.progress(
            min(snap / needed, 1.0),
            text=f"{'Ready' if snap >= needed else 'Needs ' + str(needed - snap) + ' more'}",
        )
        st.caption(
            "XGBoost 60% + LightGBM 40% · walk-forward 60d/10d · WR≥54%, PF≥1.35, Sharpe≥0.8"
        )

    with col2:
        try:
            from learning.dynamic_weights import get_learning_summary

            summary = get_learning_summary()
            st.metric("Attributed Trades", str(summary.get("attributed_trades", 0)))
            st.metric("Signals Tracked", str(summary.get("signals_tracked", 0)))
            drift = summary.get("weights_diverged", 0)
            st.metric(
                "Weights Diverged",
                str(drift),
                delta=f"signals with |Δ| > 1.0pts",
                delta_color="off",
            )
        except Exception as e:
            st.info(f"Dynamic weights: {e}")

    st.divider()

    # ── Full Failure Mode ─────────────────────────────────────────────────────
    st.subheader("Failure Mode Analysis (7 days)")
    failures = get_failure_counts()
    df_fail = pd.DataFrame(failures)
    st.dataframe(df_fail, use_container_width=True, hide_index=True)

    st.divider()

    # ── Full Trade Log ────────────────────────────────────────────────────────
    st.subheader("Trade History (last 100)")
    trades = get_trade_log(100)
    if trades:
        rows = []
        for t in trades:
            notes = _parse_notes(t.get("notes", ""))
            action = t.get("action", "")
            direction = (
                "LONG" if action == "SELL" else ("SHORT" if action == "BUY" else action)
            )
            pnl = t.get("pnl_usd") or 0
            fee = t.get("fee_usd") or 0
            rows.append(
                {
                    "Time": _time_ago(t.get("ts", "")),
                    "Symbol": t.get("symbol", ""),
                    "Direction": direction,
                    "Score": notes.get("score", ""),
                    "Regime": notes.get("regime", ""),
                    "Setup": notes.get("setup", notes.get("reason", ""))[:20],
                    "Price": t.get("price") or 0,
                    "P&L": _fmt_pnl(pnl),
                    "Fee": _fmt_pnl(-fee),
                    "Net": _fmt_pnl(pnl - fee),
                    "Result": "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "FLAT"),
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.divider()

    # ── Risk & Exposure ───────────────────────────────────────────────────────
    st.subheader("Risk & Exposure")

    open_p = get_open_positions()
    balance, _, base = get_account()
    today_pnl = get_today_pnl()

    if open_p:
        live_prices = get_live_prices([p.get("symbol", "") for p in open_p])
        total_deployed = sum(
            float(p.get("qty", 0)) * float(p.get("entry", 0)) for p in open_p
        )
        total_unrealized = sum(
            (
                (live_prices.get(p["symbol"], p["entry"]) - p["entry"]) * p["qty"]
                if p["direction"] == "LONG"
                else (p["entry"] - live_prices.get(p["symbol"], p["entry"])) * p["qty"]
            )
            for p in open_p
        )
        deployed_pct = total_deployed / balance * 100 if balance else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric(
            "Deployed Capital",
            f"${total_deployed:,.0f}",
            delta=f"{deployed_pct:.1f}% of account",
        )
        c2.metric(
            "Unrealized P&L",
            _fmt_pnl(total_unrealized),
            delta_color="normal" if total_unrealized >= 0 else "inverse",
        )
        try:
            from config import MAX_DAILY_LOSS_PCT, ACCOUNT_SIZE

            daily_limit = float(ACCOUNT_SIZE) * MAX_DAILY_LOSS_PCT
            c3.metric(
                "Daily P&L",
                _fmt_pnl(today_pnl),
                delta=f"limit: -${daily_limit:.0f}",
                delta_color="normal" if today_pnl >= -daily_limit else "inverse",
            )
        except Exception:
            c3.metric("Daily P&L", _fmt_pnl(today_pnl))
        c4.metric(
            "Kill Switch",
            f"${base * 0.75:,.0f}",
            delta=f"balance < 75% of ${base:,.0f}",
            delta_color="off",
        )

        import pandas as pd

        rows = []
        for p in open_p:
            entry = float(p.get("entry") or 0)
            stop = float(p.get("stop") or 0)
            qty = float(p.get("qty") or 0)
            direction = p.get("direction", "LONG")
            now = live_prices.get(p.get("symbol", ""), entry) or entry
            stop_dist = abs(entry - stop) / entry * 100 if entry else 0
            if direction == "LONG":
                unreal = (now - entry) * qty
            else:
                unreal = (entry - now) * qty
            rows.append(
                {
                    "Symbol": p.get("symbol", ""),
                    "Direction": direction,
                    "Entry $": f"{entry:.5g}",
                    "Now $": f"{now:.5g}" if now != entry else "–",
                    "Unrealized": _fmt_pnl(unreal),
                    "Stop $": f"{stop:.5g}",
                    "Stop %": f"-{stop_dist:.2f}%",
                    "Age": _time_ago(p.get("ts_entry", "")),
                    "Setup": (p.get("entry_reason") or "")[:22],
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No open positions.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3: MANUAL CONTROLS (ported from original)
# ══════════════════════════════════════════════════════════════════════════════

_SETUP_DESC = {
    "momentum": "Price closed above VWAP with a volume spike. Trend is accelerating — ride the move.",
    "ranging_mr": "ADX < 20 (no trend). Price stretched from VWAP. Mean-reversion back toward center expected.",
    "kst_cross": "KST momentum oscillator crossed its signal line. Indicates a turning point in medium-term momentum.",
    "supertrend": "SuperTrend indicator flipped direction. Trailing stop-based trend-following entry.",
    "ichimoku": "Price broke through the Ichimoku cloud. Cloud acts as dynamic support/resistance.",
}


def _win_prob(c: dict) -> float:
    prob = 52.0
    dirn = c.get("direction", "LONG")
    vs = c.get("vol_spike", 1.0)
    adx = c.get("adx_15m", 20.0)
    setup = c.get("primary_setup", "")
    vwap_d = abs(c.get("vwap_disp_pct", 0.0))
    kst_v = c.get("kst_value", 0.0)
    kst_s = c.get("kst_signal", 0.0)
    st_dir = c.get("supertrend_dir", 0)
    fund = abs(c.get("funding_rate", 0.0))
    pm1h = c.get("price_move_1h_pct", 0.0)
    if vs >= 3.0:
        prob += 9
    elif vs >= 2.0:
        prob += 6
    elif vs >= 1.5:
        prob += 3
    if "momentum" in setup and adx >= 25:
        prob += 7
    elif "ranging" in setup and adx < 20:
        prob += 7
    elif "kst" in setup and adx < 30:
        prob += 4
    else:
        prob += 2
    if (dirn == "LONG" and kst_v > kst_s) or (dirn == "SHORT" and kst_v < kst_s):
        prob += 5
    if (dirn == "LONG" and st_dir > 0) or (dirn == "SHORT" and st_dir < 0):
        prob += 5
    if "ranging" in setup:
        if vwap_d >= 2.0:
            prob += 5
        elif vwap_d >= 1.0:
            prob += 3
    if fund > 0.002:
        prob += 3
    elif fund > 0.0005:
        prob += 1
    if dirn == "LONG" and pm1h > 0.3:
        prob += 2
    elif dirn == "SHORT" and pm1h < -0.3:
        prob += 2
    return min(round(prob, 1), 84.0)


def _render_trade_details(c: dict, prob: float):
    import pandas as pd

    sym = c.get("symbol", "")
    dirn = c.get("direction", "")
    exch = c.get("exchange", "kraken").upper()
    setup = c.get("primary_setup", "")
    price = c.get("price", 0)
    atr = c.get("atr_15m", 0)
    stop_p = c.get("stop_pct", 0)
    tgt_p = c.get("target_pct", 0)
    ev = c.get("expected_profit", 0)
    fund_ann = c.get("funding_rate", 0.0)
    fund_cost = c.get("funding_cost_pct", 0.0)
    pm4h = c.get("price_move_4h_pct", 0.0)
    vwap = c.get("vwap", 0)
    vwap_d = c.get("vwap_disp_pct", 0.0)
    all_setups = c.get("scan_setups", [setup])
    desc = _SETUP_DESC.get(setup, "Composite signal — multiple filters triggered.")
    st.markdown(f"**Setup: `{setup}`** — {desc}")
    if len(all_setups) > 1:
        others = [s for s in all_setups if s != setup]
        st.caption(f"Also triggered: {', '.join(others)}")
    st.divider()
    st.markdown(f"**→ Estimated win probability: {prob:.1f}%**")
    st.divider()
    st.markdown("**EV calculation**")
    risk_usd = 5000.0 * 0.015
    pos_usd = risk_usd / (stop_p / 100) if stop_p > 0 else 0
    fee_pct = 0.13
    net_win = tgt_p / 100 - fee_pct / 100 - fund_cost / 100
    net_loss = stop_p / 100 + fee_pct / 100
    st.text(
        f"  Position: ${pos_usd:,.0f}  |  Stop: {stop_p:.3f}%  |  Target: {tgt_p:.3f}%"
    )
    st.text(f"  Net win if TP: {net_win * 100:.3f}% → ${net_win * pos_usd:+.2f}")
    st.text(f"  Net loss if SL: {net_loss * 100:.3f}% → ${-net_loss * pos_usd:.2f}")
    st.text(f"  EV = ${ev:+.2f}")
    st.divider()
    st.markdown("**Indicator readings**")
    c1, c2 = st.columns(2)
    c1.text(f"  Price:      {price:.6g}")
    c1.text(f"  VWAP:       {vwap:.6g}  ({vwap_d:+.3f}%)")
    c1.text(f"  1h move:    {c.get('price_move_1h_pct', 0):+.3f}%")
    c1.text(f"  4h move:    {pm4h:+.3f}%")
    c2.text(f"  ADX (15m):  {c.get('adx_15m', 0):.1f}")
    c2.text(f"  Vol spike:  {c.get('vol_spike', 0):.3f}×")
    c2.text(f"  Exchange:   {exch}")
    c2.text(f"  Funding:    {fund_ann * 100:.4f}% ann")


def render_manual_scan():
    st.subheader("Manual Scan & Trade Approval")
    st.caption(
        "Runs a fresh scan (bypasses the 5-min cache). You pick which trades execute."
    )

    col_btn, col_info = st.columns([1, 4])
    with col_btn:
        run_scan = st.button("Run Scan Now", type="primary", key="manual_scan_btn")
    with col_info:
        last_ts = st.session_state.get("manual_scan_time")
        if last_ts:
            st.caption(f"Last scan: {last_ts}")

    if run_scan:
        with st.spinner("Scanning Kraken + Hyperliquid (~5–10s)…"):
            try:
                import importlib

                sys.path.insert(0, _ROOT)
                import scanner as _scanner_mod

                importlib.reload(_scanner_mod)
                candidates = _scanner_mod.scan(account_balance=5000.0, force=True)
                st.session_state["manual_candidates"] = candidates
                st.session_state["manual_scan_time"] = datetime.now().strftime(
                    "%H:%M:%S"
                )
                for k in list(st.session_state.keys()):
                    if k.startswith("ms_sel_"):
                        del st.session_state[k]
            except Exception as e:
                st.error(f"Scan failed: {e}")
                return
        n = len(st.session_state.get("manual_candidates", []))
        st.success(f"Found {n} candidates.")

    candidates = st.session_state.get("manual_candidates", [])
    if not candidates:
        st.info("No scan results yet — click **Run Scan Now** above.")
        return

    hc1, hc2, hc3, hc4 = st.columns([0.4, 3.2, 2.8, 0.6])
    hc1.caption("Trade?")
    hc2.caption("Signal")
    hc3.caption("Win Probability")
    hc4.caption("Why")
    st.divider()

    for i, c in enumerate(candidates):
        prob = _win_prob(c)
        sym = c.get("symbol", "")
        dirn = c.get("direction", "")
        exch = c.get("exchange", "kraken")
        setup = c.get("primary_setup", "")
        badge = "🔵" if exch == "hyperliquid" else "🟠"

        col1, col2, col3, col4 = st.columns([0.4, 3.2, 2.8, 0.6])
        with col1:
            st.checkbox("", key=f"ms_sel_{i}", label_visibility="collapsed")
        with col2:
            st.markdown(f"**{sym}** `{dirn}` {badge} `{exch[:5].upper()}` · *{setup}*")
        with col3:
            label = f"{prob:.0f}% — {'High edge' if prob >= 68 else ('Moderate edge' if prob >= 60 else 'Lower edge')}"
            st.progress(prob / 100.0, text=label)
        with col4:
            with st.expander("ℹ️"):
                _render_trade_details(c, prob)

    st.divider()

    selected_idx = [
        i for i in range(len(candidates)) if st.session_state.get(f"ms_sel_{i}", False)
    ]
    n_sel = len(selected_idx)

    if n_sel == 0:
        st.caption(
            "Check the **Trade?** box on rows you want to execute, then click Execute."
        )
        return

    if st.button(f"Execute {n_sel} Trade(s)", type="primary", key="manual_execute_btn"):
        from data.historical_data import get_candles
        import perps_engine as perps

        results = []
        for idx in selected_idx:
            cand = candidates[idx]
            sym = cand["symbol"]
            dirn = cand["direction"]
            setup = cand.get("primary_setup", "manual")
            try:
                df_c = get_candles(sym, "1h", 100)
                if df_c is None or len(df_c) < 10:
                    results.append((sym, dirn, False, "insufficient candle data"))
                    continue
                candle_price = float(df_c["close"].iloc[-1])
                live_now = get_live_prices([sym]).get(sym, 0)
                if live_now > 0:
                    ratio = candle_price / live_now
                    if 0.95 <= ratio <= 1.05:
                        price = candle_price
                    else:
                        price = live_now
                        st.warning(
                            f"⚠️ {sym}: candle price ${candle_price:.5g} off by {abs(ratio - 1) * 100:.0f}% — using live ${live_now:.5g}"
                        )
                else:
                    price = candle_price
                if price <= 0:
                    results.append(
                        (sym, dirn, False, "could not determine valid entry price")
                    )
                    continue
                atr_7 = float(df_c["high"].sub(df_c["low"]).tail(7).mean())
                if atr_7 <= 0 or (
                    live_now > 0 and abs(candle_price / live_now - 1) > 0.10
                ):
                    atr_7 = price * 0.015
                stop_dist = max(atr_7 * 1.5, price * 0.008)
                target_dist = stop_dist * 3.0
                composite = cand.get("composite_score", 50.0)
                from position_manager import compute_position_size

                balance, _, _b = get_account()
                _open_pos = get_open_positions()
                _deployed = sum(
                    float(p.get("qty", 0)) * float(p.get("entry", 0)) for p in _open_pos
                )
                sizing = compute_position_size(
                    account_balance=balance,
                    current_price=price,
                    atr_7=atr_7,
                    stop_multiplier=1.5,
                    ml_score=composite,
                    composite_score=composite,
                    deployed_usd=_deployed,
                    paper=True,
                )
                pos_usd = sizing["position_usd"]
                leverage = sizing["leverage"]
                if dirn == "LONG":
                    stop_p = round(price - stop_dist, 6)
                    target_p = round(price + target_dist, 6)
                    pos = perps.open_long(
                        symbol=sym,
                        position_usd=pos_usd,
                        entry_price=price,
                        stop_price=stop_p,
                        take_profit_price=target_p,
                        leverage=leverage,
                        composite_score=composite,
                        atr_at_entry=atr_7,
                        regime="UNKNOWN",
                        entry_setup=f"manual_{setup}",
                        paper=True,
                    )
                else:
                    stop_p = round(price + stop_dist, 6)
                    target_p = round(price - target_dist, 6)
                    pos = perps.open_short(
                        symbol=sym,
                        position_usd=pos_usd,
                        entry_price=price,
                        stop_price=stop_p,
                        take_profit_price=target_p,
                        leverage=leverage,
                        composite_score=composite,
                        atr_at_entry=atr_7,
                        regime="UNKNOWN",
                        entry_setup=f"manual_{setup}",
                        paper=True,
                    )
                if pos:
                    results.append(
                        (
                            sym,
                            dirn,
                            True,
                            f"entered @ {price:.6g}  stop={stop_p:.6g}  target={target_p:.6g}  size=${pos_usd:.0f}  lev={leverage}x",
                        )
                    )
                else:
                    results.append((sym, dirn, False, "open_long/short returned None"))
            except Exception as e:
                results.append((sym, dirn, False, str(e)[:120]))

        for sym, dirn, ok, msg in results:
            st.write(f"{'✅' if ok else '❌'} **{sym} {dirn}** — {msg}")

        st.session_state.pop("manual_candidates", None)
        st.session_state.pop("manual_scan_time", None)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4: FUTURES (ported verbatim)
# ══════════════════════════════════════════════════════════════════════════════


@st.fragment(run_every=10)
def render_futures():
    import pandas as pd

    try:
        import pytz

        et = pytz.timezone("America/New_York")
        now_et = datetime.now(et)
        h, m = now_et.hour, now_et.minute
        is_open = now_et.weekday() < 5 and (
            (h == 9 and m >= 30) or (10 <= h <= 15) or (h == 15 and m <= 45)
        )
        pre_open = now_et.weekday() < 5 and h == 9 and m < 30
        time_str = now_et.strftime("%H:%M ET")
        mkt_status = "OPEN" if is_open else ("PRE-OPEN" if pre_open else "CLOSED")
    except Exception:
        is_open, mkt_status, time_str = False, "UNKNOWN", "--:--"

    mes_state = get_mes_state()
    daily_pnl = get_mes_daily_pnl()
    all_stats = get_mes_all_time_stats()
    trades_today = get_mes_trades_today()

    price = mes_state.get("price")
    or_high = mes_state.get("or_high")
    or_low = mes_state.get("or_low")
    or_locked = mes_state.get("or_locked", False)
    has_pos = mes_state.get("has_pos", False)
    state_time = mes_state.get("time_et", "--")

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Market", mkt_status, delta=time_str)
    c2.metric("MES Price", f"{price:.2f}" if price else "–")
    c3.metric("Today P&L", _fmt_pnl(daily_pnl), help=TIPS.get("mes_pnl"))
    c4.metric("Position", "ACTIVE" if has_pos else "FLAT")
    c5.metric(
        "All-Time W/L",
        f"{all_stats['wins']}W / {all_stats['closes'] - all_stats['wins']}L",
    )
    pf = all_stats["profit_factor"]
    c6.metric(
        "Profit Factor",
        f"{pf:.2f}" if pf != float("inf") else "∞",
        help=TIPS.get("mes_profit_factor"),
    )

    st.divider()

    col_l, col_r = st.columns(2)
    with col_l:
        st.subheader("Opening Range (9:30–10:00 ET)")
        if or_locked and or_high and or_low:
            or_range = or_high - or_low
            long_entry = round(or_high + 0.25, 2)
            short_entry = round(or_low - 0.25, 2)
            r1, r2, r3 = st.columns(3)
            r1.metric("OR High", f"{or_high:.2f}", help=TIPS.get("or_high"))
            r2.metric("OR Low", f"{or_low:.2f}", help=TIPS.get("or_low"))
            r3.metric("Range (pts)", f"{or_range:.2f}", help=TIPS.get("or_range"))
            st.caption(
                f"Long trigger: ≥ {long_entry}  |  Short trigger: ≤ {short_entry}"
            )
            st.caption(f"Last update: {state_time}")
        elif is_open and not or_locked:
            st.info("Building opening range… (9:30–10:00 ET)")
        elif not is_open:
            st.info("Market closed. Opening range resets at 9:30 ET.")
        else:
            st.info("Waiting for runner state (FUTURES_ENABLED must be True in .env)")

    with col_r:
        st.subheader("Strategy Playbook")
        st.caption("**Strategy 1 — Opening Range Breakout**")
        for k, v in [
            (
                "Trigger",
                "Price breaks above OR high (+0.25) → LONG / below OR low (−0.25) → SHORT",
            ),
            ("Stop", "Opposite end of OR ± 0.25 buffer"),
            ("Target", "2× stop distance, min 4 pts ($20/contract)"),
            ("Window", "10:00–15:45 ET; hard EOD close 15:45"),
        ]:
            st.text(f"  {k + ':':<10} {v}")
        st.divider()
        st.caption("**Strategy 2 — VWAP Mean Reversion**")
        for k, v in [
            (
                "Trigger",
                "Price >2 ATR from VWAP AND RSI >68 → SHORT / <2 ATR AND RSI <32 → LONG",
            ),
            ("Stop", "1.5 ATR past entry"),
            ("Target", "VWAP"),
            ("Window", "10:00–14:30 ET"),
        ]:
            st.text(f"  {k + ':':<10} {v}")

    st.divider()

    st.subheader(f"Today's MES Trades ({len(trades_today)})")
    if trades_today:
        rows = []
        for t in trades_today:
            pnl = t.get("pnl_usd") or 0
            rows.append(
                {
                    "Time": _time_ago(t.get("ts", "")),
                    "Action": t.get("action", ""),
                    "Qty": t.get("qty", ""),
                    "Price": t.get("price", ""),
                    "P&L": _fmt_pnl(pnl) if pnl else "–",
                    "Notes": (t.get("notes") or "")[:80],
                    "Result": "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "OPEN"),
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info(
            "No MES trades today."
            if is_open
            else "No MES trades today — market closed."
        )

    st.divider()

    with st.expander("Futures configuration & risk rules"):
        try:
            from config import FUTURES_ENABLED, FUTURES_NUM_CONTRACTS, ACCOUNT_SIZE

            st.text(f"  FUTURES_ENABLED:       {FUTURES_ENABLED}")
            st.text(f"  FUTURES_NUM_CONTRACTS: {FUTURES_NUM_CONTRACTS}")
            st.text(f"  Account size:          ${float(ACCOUNT_SIZE):,.0f}")
        except Exception as e:
            st.error(f"config: {e}")
        st.text("  Contract:    MES (Micro E-mini S&P 500) — CME")
        st.text("  Expiry:      Q2 2026 — 20260619 (update quarterly)")
        st.text("  Point value: $5.00 / full point")
        st.text("  Tick size:   0.25 pts = $1.25 / tick")
        st.text("  Commission:  ~$0.47/side = $0.94 round-trip")
        st.text("  Connection:  IBKR TWS port 7497 (paper) / 7496 (live)")
        st.text("  Daily limit: $150 — no new entries after this")
        st.text("  Hard EOD:    15:45 ET — all positions closed")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5: DEV/CONFIG
# ══════════════════════════════════════════════════════════════════════════════


def render_dev_config():
    st.subheader("Dev / Config")
    st.caption("All system constants, formulas, and thresholds in one place.")

    col_left, col_right = st.columns(2)

    with col_left:
        with st.expander("Economics gate (risk/economics_gate.py)", expanded=True):
            try:
                from risk.economics_gate import (
                    TAKER_FEE_PCT,
                    ROUND_TRIP_COST,
                    _TIER_APLUS_EV,
                    _TIER_A_EV,
                    _TIER_B_EV,
                    TIER_MULTIPLIERS,
                    _MIN_NET_RR,
                )

                st.text(
                    f"  Taker fee (per side):    {TAKER_FEE_PCT * 100:.3f}%  (Kraken Futures)"
                )
                st.text(f"  Round-trip cost:         {ROUND_TRIP_COST * 100:.3f}%")
                st.text(f"  Min net R:R:             ≥ {_MIN_NET_RR}:1 after fees")
                st.text(
                    f"  Tier A+ (EV ≥ {_TIER_APLUS_EV * 100:.2f}%): {TIER_MULTIPLIERS.get('A+', 1.0)}× size"
                )
                st.text(
                    f"  Tier A  (EV ≥ {_TIER_A_EV * 100:.2f}%):  {TIER_MULTIPLIERS.get('A', 1.0)}× size"
                )
                st.text(
                    f"  Tier B  (EV ≥ {_TIER_B_EV * 100:.2f}%):  {TIER_MULTIPLIERS.get('B', 0.75)}× size"
                )
                st.text(f"  Below B:                 VETO — trade blocked")
            except Exception as e:
                st.error(f"economics_gate: {e}")

        with st.expander("Position sizer (risk/unified_sizer.py)"):
            try:
                from risk.unified_sizer import (
                    BASE_RISK_PCT,
                    MAX_HEAT_PCT,
                    MAX_SINGLE_NOTIONAL_PCT,
                    _QUALITY_MULT,
                )
                from config import ACCOUNT_SIZE

                acct = float(ACCOUNT_SIZE)
                st.text(
                    f"  Formula: size = (acct × {BASE_RISK_PCT * 100:.1f}% × quality_mult) / stop_pct"
                )
                st.text(f"  Account: ${acct:,.0f}")
                st.text(
                    f"  Base risk per trade: {BASE_RISK_PCT * 100:.1f}% = ${acct * BASE_RISK_PCT:.0f}"
                )
                st.text(
                    f"  Portfolio heat cap:  {MAX_HEAT_PCT * 100:.0f}% = ${acct * MAX_HEAT_PCT:.0f}"
                )
                st.text(
                    f"  Hard position cap:   {MAX_SINGLE_NOTIONAL_PCT * 100:.0f}% per symbol"
                )
                st.text(f"  Default leverage:    3× ISOLATED margin")
                for tier, mult in sorted(_QUALITY_MULT.items(), key=lambda x: -x[1]):
                    st.text(f"  Quality {tier}: {mult}× size")
            except Exception as e:
                st.error(f"unified_sizer: {e}")

        with st.expander("6-priority exit stack (position_manager.py)"):
            exits = [
                ("6", "Kill Switch", "Balance < 75% of account / API errors / latency"),
                (
                    "5",
                    "Risk Forced Exit",
                    "Margin breach / VaR breach / correlation limit",
                ),
                ("4", "Hard Stop", "STOP_MARKET at entry − ATR×1.5 · NEVER widened"),
                (
                    "3",
                    "Thesis Invalidated",
                    "composite < entry_score × regime_pct → close (TRENDING=30%, RANGING=15%, HIGH_VOL=35%, default=25%)",
                ),
                ("2", "TP Scale-Out", "2R → 33% · 3.5R → 33% · remainder trails"),
                (
                    "1",
                    "Trailing Stop",
                    "Activates after 1× ATR in favor · trails 1.5× ATR from peak",
                ),
            ]
            for num, title, detail in exits:
                st.text(f"  [{num}] {title}: {detail}")

    with col_right:
        with st.expander("Kill switch & risk rules", expanded=True):
            try:
                from config import ACCOUNT_SIZE, MAX_DAILY_LOSS_PCT

                acct = float(ACCOUNT_SIZE)
                st.text(f"  Kill switch:         Balance < 75% = ${acct * 0.75:,.0f}")
                st.text(
                    f"  Max daily loss:      {MAX_DAILY_LOSS_PCT * 100:.0f}% → halt all trading"
                )
                st.text(f"  Max deployed:        90%")
                st.text(f"  Max risk per trade:  1% of account")
                st.text(f"  Margin type:         ISOLATED — never CROSS")
                st.text(f"  Kraken taker fee:    0.065%")
                st.text(f"  No double-entry:     one position per symbol, ever")
                st.text(f"  No chase:            skip if price moved > 3% since signal")
                st.text(f"  Stop sacred:         never moved wider after entry")
            except Exception as e:
                st.error(f"config: {e}")

        with st.expander("Signal engine entry thresholds"):
            try:
                from signal_engine import _ENTRY_THRESHOLDS, _LONG_SETUPS, _SHORT_SETUPS
                import pandas as pd

                thresh_rows = [
                    {"Regime": r, "Min Score": f"≥ {t} / 100"}
                    for r, t in sorted(_ENTRY_THRESHOLDS.items())
                ]
                st.dataframe(
                    pd.DataFrame(thresh_rows),
                    use_container_width=False,
                    hide_index=True,
                )
            except Exception as e:
                st.error(f"signal_engine: {e}")

        with st.expander("Scanner config (live from scanner.py)"):
            try:
                from scanner import (
                    _MIN_VOLUME_24H_USD,
                    _MIN_VOL_SPIKE,
                    _MIN_PRICE_MOVE_1H,
                    _MIN_ADX_MOMENTUM,
                    _MIN_OB_DEPTH_USD,
                    _MAX_SPREAD_PCT,
                    _MIN_EXPECTED_PROFIT,
                    _ROUND_TRIP_FEE_PCT,
                )

                st.text(f"  Min 24h volume:  ${_MIN_VOLUME_24H_USD / 1e6:.1f}M")
                st.text(f"  Min vol spike:   ≥ {_MIN_VOL_SPIKE}×")
                st.text(f"  Min price move:  ≥ {_MIN_PRICE_MOVE_1H:.2f}%")
                st.text(f"  Min ADX:         ≥ {_MIN_ADX_MOMENTUM}")
                st.text(
                    f"  Min OB depth:    ≥ ${_MIN_OB_DEPTH_USD / 1e3:.0f}K each side"
                )
                st.text(f"  Max spread:      < {_MAX_SPREAD_PCT:.2f}%")
                st.text(f"  Min EV:          ≥ ${_MIN_EXPECTED_PROFIT:.2f}")
                st.text(f"  Round-trip fee:  {_ROUND_TRIP_FEE_PCT * 100:.3f}%")
                st.text(
                    f"  Sources:         Kraken Futures + Binance USDM + Hyperliquid"
                )
            except Exception as e:
                st.error(f"scanner: {e}")

    with st.expander("Full config.py constants"):
        try:
            import config as _cfg, pandas as pd

            items = sorted(
                {
                    k: str(v)
                    for k, v in vars(_cfg).items()
                    if not k.startswith("_") and isinstance(v, (int, float, str, bool))
                }.items()
            )
            st.dataframe(
                pd.DataFrame(items, columns=["Key", "Value"]),
                use_container_width=True,
                hide_index=True,
            )
        except Exception as e:
            st.error(str(e))

    with st.expander("Technical tower — all scoring conditions (LONG side)"):
        long_signals = [
            ("CVD bullish divergence", "+25"),
            ("MACD all variants aligned long", "+20"),
            ("TradingView webhook confirmed", "+20"),
            ("RSI bullish divergence", "+15"),
            ("Funding squeeze (< −0.3 norm)", "+15"),
            ("VWAP reclaim on volume", "+15"),
            ("Liquidation cascade → long magnet", "+15"),
            ("WaveTrend oversold cross", "+12"),
            ("SuperTrend bullish (ATR10 ×3)", "+12"),
            ("WAE Bullish + Exploding", "+10"),
            ("OB L5 imbalance > 0.60", "+10"),
            ("Williams %R < −80", "+10"),
            ("Whale accumulation signal", "+10"),
            ("Options skew bullish", "+10"),
            ("MACD fast histogram positive", "+8"),
            ("Funding favorable (−0.1 to −0.3)", "+8"),
            ("KST above signal line", "+8"),
            ("Fisher Transform cross up", "+8"),
            ("Ichimoku cloud bullish", "+8"),
            ("Laguerre RSI < 0.15 (deep OS)", "+8"),
            ("OB L5 imbalance 0.55–0.60", "+5"),
            ("Williams %R −80 to −70", "+5"),
            ("Vol spike > 1.5×", "+5"),
            ("RSI not overbought (< 60)", "+5"),
            ("Choppiness trending (< 38.2)", "+5"),
            ("WAE Bullish only", "+5"),
            ("Price > 2σ VWAP", "−25"),
            ("CVD bearish divergence", "−20"),
            ("Extreme positive funding (> 0.5)", "−20"),
            ("RSI bearish divergence", "−15"),
            ("Cascade risk > 0.70", "−15"),
            ("OB L5 < 0.40 (bear pressure)", "−10"),
            ("Fear & Greed euphoria (> 85)", "−10"),
        ]
        import pandas as pd

        st.caption("Raw range ~−115 to +150 · normalized 0–100 · mirrored for SHORT")
        st.dataframe(
            pd.DataFrame(long_signals, columns=["Condition", "Points"]),
            use_container_width=False,
            hide_index=True,
        )

    st.divider()
    st.caption("**System events log** (last 20)")
    events = get_recent_events(20)
    if events:
        import pandas as pd

        rows = [
            {
                "Time": _time_ago(e.get("ts", "")),
                "Level": e.get("level", ""),
                "Source": e.get("source", "")[:30],
                "Message": e.get("message", "")[:120],
            }
            for e in events
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════


def main():
    st.title("Algo Trading — Operator Panel v14")
    st.caption(
        "Is the system healthy?  ·  Is it profitable after fees?  ·  Where is it breaking?"
    )

    tab_op, tab_deep, tab_manual, tab_futures, tab_dev = st.tabs(
        [
            "OPERATOR",
            "DEEP ANALYSIS",
            "MANUAL CONTROLS",
            "FUTURES (MES)",
            "DEV / CONFIG",
        ]
    )

    # ── Tab 1: OPERATOR ────────────────────────────────────────────────────────
    with tab_op:
        # TIER 1: The Three Answers
        st.markdown("##### System Status")
        col_health, col_edge, col_alerts = st.columns(3)
        with col_health:
            render_system_integrity()
        with col_edge:
            render_edge_quality()
        with col_alerts:
            render_alert_feed()

        st.divider()

        # TIER 2: Operational Detail
        st.markdown("##### Operational Detail")
        col_pos, col_scan, col_fail = st.columns(3)
        with col_pos:
            render_positions_compact()
        with col_scan:
            render_scanner_funnel()
        with col_fail:
            render_failures_compact()

        st.divider()

        # TIER 2: Execution + Decision Quality
        col_exec, col_dec = st.columns(2)
        with col_exec:
            render_execution_quality()
        with col_dec:
            render_decision_quality()

        st.divider()

        # TIER 2: Equity Curve + Smart Logs
        render_equity_curve_compact()
        st.divider()
        render_smart_logs()

    # ── Tab 2: DEEP ANALYSIS ───────────────────────────────────────────────────
    with tab_deep:
        render_deep_analysis()

    # ── Tab 3: MANUAL CONTROLS ─────────────────────────────────────────────────
    with tab_manual:
        render_manual_scan()

    # ── Tab 4: FUTURES ─────────────────────────────────────────────────────────
    with tab_futures:
        render_futures()

    # ── Tab 5: DEV / CONFIG ────────────────────────────────────────────────────
    with tab_dev:
        render_dev_config()


if __name__ == "__main__":
    main()
else:
    main()
