"""
dashboard/app.py — v10.1 single-page dashboard
One page, no tabs, minimal styling.
All data read from live system: SQLite DB, bot.log, config/signal_engine imports.
"""
import sys, os, re
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from datetime import datetime

import streamlit as st

# ── paths ──────────────────────────────────────────────────────────────────────
_ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH     = os.path.join(_ROOT, "logs", "trades.db")
LOG_PATH    = os.path.join(_ROOT, "logs", "bot.log")
LAUNCH_DATE = "2026-04-02"

st.set_page_config(
    page_title="Algo Trading v10.1",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Minimal CSS — hide chrome, dark background, nothing else
st.markdown("""
<style>
section[data-testid="stSidebar"] { display: none; }
#MainMenu, footer, header, .stDeployButton, [data-testid="stToolbar"] { visibility: hidden; }
.block-container { padding: 20px 24px 60px 24px !important; max-width: 100% !important; }
</style>
""", unsafe_allow_html=True)


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
# DATA FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def get_account():
    try:
        from config import ACCOUNT_SIZE, PAPER_TRADING
        base = float(ACCOUNT_SIZE)
        paper = bool(PAPER_TRADING)
    except Exception:
        base, paper = 5000.0, True
    # Actual balance = base + net PnL (gross PnL minus ALL fees, both entry and exit)
    r = _q1("""
        SELECT SUM(pnl_usd) - SUM(fee_usd) AS net_pnl
        FROM trades
        WHERE ts >= ? AND paper=1
          AND source NOT IN ('backtest','pre_v10_contaminated','bybit_paper')
    """, (LAUNCH_DATE,))
    cum_pnl = r.get("net_pnl") or 0.0
    return base + cum_pnl, paper, base

def get_performance_stats():
    # Use the `won` field (set only on real closes) — avoids counting entry rows
    # or mis-fired pnl_usd!=0 entries. Net PnL = pnl_usd - fee_usd per close.
    r = _q1("""
        SELECT
            COUNT(CASE WHEN won IS NOT NULL THEN 1 END)      AS closes,
            SUM(CASE WHEN won=1 THEN 1 ELSE 0 END)           AS wins,
            SUM(CASE WHEN won=0 THEN 1 ELSE 0 END)           AS losses,
            SUM(pnl_usd - fee_usd)                           AS total_net_pnl,
            SUM(CASE WHEN won=1 THEN pnl_usd - fee_usd ELSE 0 END) AS net_wins_sum,
            SUM(CASE WHEN won=0 THEN ABS(pnl_usd - fee_usd) ELSE 0 END) AS net_losses_sum,
            SUM(fee_usd)                                     AS total_fees
        FROM trades
        WHERE ts >= ? AND paper=1 AND broker NOT LIKE '%bybit%'
          AND source NOT IN ('backtest','pre_v10_contaminated','bybit_paper')
    """, (LAUNCH_DATE,))
    closes = r.get("closes") or 0
    wins   = r.get("wins") or 0
    gw     = r.get("net_wins_sum") or 0.0
    gl     = r.get("net_losses_sum") or 0.0
    return {
        "closes":        closes,
        "wins":          wins,
        "losses":        r.get("losses") or 0,
        "win_rate":      wins / closes * 100 if closes else 0.0,
        "total_pnl":     r.get("total_net_pnl") or 0.0,
        "profit_factor": gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0),
        "gross_wins":    gw,
        "gross_losses":  gl,
        "total_fees":    r.get("total_fees") or 0.0,
    }

def get_today_pnl():
    today = datetime.now().strftime("%Y-%m-%d")
    r = _q1("""
        SELECT SUM(pnl_usd) v FROM trades
        WHERE ts >= ? AND paper=1 AND broker NOT LIKE '%bybit%' AND pnl_usd != 0
          AND source NOT IN ('backtest','pre_v10_contaminated','bybit_paper')
    """, (today,))
    return r.get("v") or 0.0

def get_open_positions():
    return _q("SELECT * FROM open_positions WHERE paper=1 ORDER BY ts_entry DESC")

def get_equity_curve():
    return _q("""
        SELECT ts, SUM(pnl_usd) OVER (ORDER BY ts) AS cum_pnl
        FROM trades
        WHERE ts >= ? AND paper=1 AND broker NOT LIKE '%bybit%'
          AND pnl_usd != 0
          AND source NOT IN ('backtest','pre_v10_contaminated','bybit_paper')
        ORDER BY ts
    """, (LAUNCH_DATE,))

def get_trade_log(limit=50):
    return _q("""
        SELECT ts, symbol, action, qty, price, pnl_usd, fee_usd, notes
        FROM trades
        WHERE ts >= ? AND paper=1 AND broker NOT LIKE '%bybit%'
          AND pnl_usd != 0
          AND source NOT IN ('backtest','pre_v10_contaminated','bybit_paper')
        ORDER BY ts DESC LIMIT ?
    """, (LAUNCH_DATE, limit))

def get_per_symbol_stats():
    return _q("""
        SELECT symbol,
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
          AND source NOT IN ('backtest','pre_v10_contaminated','bybit_paper')
        GROUP BY symbol ORDER BY total_pnl DESC
    """, (LAUNCH_DATE,))

def get_signal_bayesian_stats():
    return _q("""
        SELECT signal_name, regime, fires, wins,
               ROUND(win_rate * 100, 1) AS win_rate_pct,
               ROUND(bayesian_pts, 2) AS bayesian_pts,
               ROUND(prior_pts, 2) AS prior_pts,
               ROUND(bayesian_pts - prior_pts, 2) AS pts_drift,
               ROUND(avg_pnl, 2) AS avg_pnl,
               last_updated
        FROM signal_stats
        WHERE regime = 'any'
        ORDER BY fires DESC, bayesian_pts DESC
    """)

def get_ml_status():
    r = _q1("SELECT COUNT(*) AS n FROM trade_features")
    return {"snapshots": r.get("n") or 0, "min_needed": 30}

def get_recent_events(limit=20):
    return _q("""
        SELECT ts, level, source, message FROM system_events
        WHERE source NOT IN ('IBKRBroker')
        ORDER BY rowid DESC LIMIT ?
    """, (limit,))


# ── MES Futures data ──────────────────────────────────────────────────────────

def get_mes_state() -> dict:
    """Latest MES state snapshot written by the runner each cycle."""
    row = _q1("""
        SELECT ts, message FROM system_events
        WHERE source = 'mes_state'
        ORDER BY rowid DESC LIMIT 1
    """)
    if not row:
        return {}
    try:
        import json
        state = json.loads(row.get('message', '{}'))
        state['ts'] = row.get('ts', '')
        return state
    except Exception:
        return {}

def get_mes_trades_today() -> list:
    today = datetime.now().strftime("%Y-%m-%d")
    return _q("""
        SELECT ts, action, qty, price, pnl_usd, notes
        FROM trades
        WHERE ts >= ? AND symbol = 'MES'
        ORDER BY ts DESC
    """, (today,))

def get_mes_daily_pnl() -> float:
    today = datetime.now().strftime("%Y-%m-%d")
    r = _q1("""
        SELECT SUM(pnl_usd) v FROM trades
        WHERE ts >= ? AND symbol = 'MES' AND pnl_usd != 0
    """, (today,))
    return r.get('v') or 0.0

def get_mes_all_time_stats() -> dict:
    r = _q1("""
        SELECT
            COUNT(*) AS closes,
            SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
            SUM(pnl_usd) AS total_pnl,
            SUM(CASE WHEN pnl_usd > 0 THEN pnl_usd ELSE 0 END) AS gross_wins,
            SUM(CASE WHEN pnl_usd < 0 THEN ABS(pnl_usd) ELSE 0 END) AS gross_losses
        FROM trades
        WHERE symbol = 'MES' AND pnl_usd != 0
    """)
    closes = r.get('closes') or 0
    wins   = r.get('wins') or 0
    gw     = r.get('gross_wins') or 0.0
    gl     = r.get('gross_losses') or 0.0
    return {
        'closes':  closes,
        'wins':    wins,
        'win_rate': wins / closes * 100 if closes else 0.0,
        'total_pnl': r.get('total_pnl') or 0.0,
        'profit_factor': gw / gl if gl > 0 else (float('inf') if gw > 0 else 0.0),
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

def get_scan_status():
    lines = _tail_log(800)
    result = {"age_s": 9999, "count": 0, "candidates": [], "steps": [], "duration_s": 0.0,
              "balance": 0.0, "deployed": 0.0}

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

    cm = re.search(r"Complete:\s*(\d+)\s*candidates\s*in\s*([\d.]+)s", lines[complete_idx])
    if cm:
        result["count"]      = int(cm.group(1))
        result["duration_s"] = float(cm.group(2))

    cand_re = re.compile(
        r"→\s+(\S+)\s+(LONG|SHORT)\s+spike=([\d.]+)\s+adx=([\d.]+)\s+ev=\$([\d.]+)\s+funding=([-\d.]+)%"
    )
    for line in lines[complete_idx + 1: complete_idx + 20]:
        c = cand_re.search(line)
        if c:
            result["candidates"].append({
                "symbol": c.group(1), "direction": c.group(2),
                "vol_spike": float(c.group(3)), "adx": float(c.group(4)),
                "ev_usd": float(c.group(5)), "funding_pct": float(c.group(6)),
            })

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
    for line in lines[complete_idx: complete_idx + 5]:
        sm = scan_re.search(line)
        if sm:
            result["balance"]  = float(sm.group(1))
            result["deployed"] = float(sm.group(2))
            break

    return result

def get_last_scan_age():
    """Scan backwards through bot.log without a line cap — stops at first hit."""
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
                # Keep only the last partial line for next iteration
                buf = buf.split(b"\n")[0]
    except Exception:
        pass
    return 9999

def get_bot_activity(n=40):
    lines = _tail_log(500)
    events = []
    for line in reversed(lines):
        line = line.strip()
        if not any(x in line for x in ("[v10]", "[scanner]", "[perps]", "[risk]", "[wft]", "[learning]")):
            continue
        if any(x in line for x in ("ib_insync", "IBKRBroker", "Connecting to", "Disconnecting")):
            continue

        ts_m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        ts   = ts_m.group(1)[11:19] if ts_m else ""

        if   "ENTERED" in line:               kind = "ENTERED"
        elif "ECONOMICS VETO" in line:         kind = "VETO"
        elif "PAPER CLOSE" in line:            kind = "CLOSE"
        elif "PAPER LONG" in line or "PAPER SHORT" in line: kind = "OPEN"
        elif "TIER 1" in line:                 kind = "TIER1"
        elif "TIER 2" in line:                 kind = "TIER2"
        elif "ENTRY SIGNAL" in line:           kind = "SIGNAL"
        elif "Complete:" in line:              kind = "SCAN"
        elif "Bayesian adj" in line:           kind = "BAYES"
        elif "ERROR" in line.upper():          kind = "ERROR"
        elif "retrain" in line.lower():        kind = "ML"
        else:                                  kind = "INFO"

        if kind == "INFO":
            continue  # skip noise

        msg = re.sub(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+\s+\S+\s+\S+\s+", "", line)
        events.append({"ts": ts, "kind": kind, "msg": msg[:160]})
        if len(events) >= n:
            break
    return events


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _fmt_pnl(v):
    s = "+" if v > 0 else ""
    return f"{s}${v:,.2f}"

def _time_ago(ts_str):
    try:
        ts_str = ts_str.replace("T", " ").split(".")[0].split("+")[0][:19]
        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        secs = int((datetime.now() - dt).total_seconds())
        if secs < 60:    return f"{secs}s ago"
        if secs < 3600:  return f"{secs // 60}m ago"
        if secs < 86400: return f"{secs // 3600}h {(secs % 3600) // 60}m ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return ts_str[:16] if ts_str else "–"

def _parse_notes(notes):
    if not notes:
        return {}
    r = {}
    for pattern, key in [(r"score=([\d.]+)", "score"), (r"regime=(\w+)", "regime"),
                         (r"setup=(\S+)", "setup"), (r"lev=(\d+)x", "lev"),
                         (r"reason=(\S+)", "reason")]:
        m = re.search(pattern, notes)
        if m:
            r[key] = m.group(1)
    return r


# ══════════════════════════════════════════════════════════════════════════════
# RENDER — STATUS BAR (5s)
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=5)
def render_status():
    scan_age          = get_last_scan_age()
    today             = get_today_pnl()
    balance, paper, base = get_account()
    stats             = get_performance_stats()
    open_p            = get_open_positions()

    mode    = "PAPER" if paper else "LIVE"
    bot_ok  = scan_age < 600

    # Human-readable scan age
    if scan_age >= 9999:
        age_str = "–"
    elif scan_age < 60:
        age_str = f"{scan_age}s"
    else:
        age_str = f"{scan_age // 60}m {scan_age % 60}s"

    pnl_since_start = balance - base
    pf = stats['profit_factor']
    pf_str = f"PF {pf:.2f}" if pf != float("inf") else "PF ∞"

    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric("Mode",        mode)
    c2.metric("Bot Status",  "RUNNING" if bot_ok else "STALE",
              delta=f"last scan {age_str} ago")
    c3.metric("Last Scan",   age_str,
              delta=f"{stats['closes']} closes logged")
    c4.metric("Today P&L",   _fmt_pnl(today))
    c5.metric("Win Rate",    f"{stats['win_rate']:.1f}%",
              delta=f"{stats['wins']}W / {stats['losses']}L · {pf_str}")
    c6.metric("Balance",     f"${balance:,.2f}",
              delta=f"{_fmt_pnl(pnl_since_start)} since {LAUNCH_DATE}")
    c7.metric("Open Pos",    str(len(open_p)))

    st.caption(f"Updated {datetime.now().strftime('%H:%M:%S')} · Paper trades since {LAUNCH_DATE} · Bybit/backtest/contaminated data excluded")


# ══════════════════════════════════════════════════════════════════════════════
# RENDER — PORTFOLIO (30s)
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=30)
def render_portfolio():
    import pandas as pd
    stats    = get_performance_stats()
    acct, _, _base = get_account()
    eq       = get_equity_curve()

    st.subheader("Portfolio Performance")

    c1, c2, c3, c4, c5 = st.columns(5)
    pf = stats["profit_factor"]
    pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"

    c1.metric("Net P&L",        _fmt_pnl(stats["total_pnl"]),
              delta=f"{stats['total_pnl']/acct*100:+.2f}% of account" if acct else None)
    c2.metric("Win Rate",       f"{stats['win_rate']:.1f}%",
              delta=f"{stats['closes']} closed trades")
    c3.metric("Profit Factor",  pf_str,
              delta="need ≥1.35 for live")
    c4.metric("Total Fees",     _fmt_pnl(-stats["total_fees"]))
    c5.metric("Clean Trades",   f"{stats['closes']} / 30",
              delta="minimum for ML gate")

    if eq:
        df = pd.DataFrame(eq)
        df["ts"] = pd.to_datetime(df["ts"].str[:19])
        df = df.rename(columns={"cum_pnl": "Cumulative P&L ($)"})
        st.line_chart(df.set_index("ts")[["Cumulative P&L ($)"]], height=200, use_container_width=True)
    else:
        st.info("No closed trades yet — equity curve will appear after first close.")


# ══════════════════════════════════════════════════════════════════════════════
# RENDER — OPEN POSITIONS (10s)
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=10)
def render_open_positions():
    import pandas as pd
    open_p = get_open_positions()
    st.subheader(f"Open Positions ({len(open_p)})")

    if not open_p:
        st.info("No open positions. Scanner runs every 5 min.")
        return

    rows = []
    for p in open_p:
        entry  = float(p.get("entry") or 0)
        stop   = float(p.get("stop") or 0)
        target = float(p.get("target") or 0)
        stop_pct = abs(entry - stop) / entry * 100 if entry else 0
        tp_pct   = abs(target - entry) / entry * 100 if entry else 0
        rr       = tp_pct / stop_pct if stop_pct > 0 else 0
        rows.append({
            "Symbol":    p.get("symbol", ""),
            "Direction": p.get("direction", ""),
            "Entry":     f"{entry:.5g}",
            "Stop":      f"{stop:.5g}  (−{stop_pct:.2f}%)",
            "Target":    f"{target:.5g}  (+{tp_pct:.2f}%)",
            "R:R":       f"{rr:.1f}×",
            "Age":       _time_ago(p.get("ts_entry", "")),
            "Setup":     p.get("entry_reason", ""),
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# RENDER — SCANNER (15s)
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=15)
def render_scanner():
    import pandas as pd
    scan = get_scan_status()
    age  = scan["age_s"]

    st.subheader("Scanner — Last Output")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Last Scan",     f"{age}s ago" if age < 9999 else "No data")
    c2.metric("Candidates",    str(scan["count"]))
    c3.metric("Runtime",       f"{scan['duration_s']:.1f}s")
    c4.metric("Balance",       f"${scan['balance']:,.0f}")
    c5.metric("Deployed",      f"${scan['deployed']:,.0f}")

    # Pipeline filter steps
    if scan["steps"]:
        st.caption("Filter pipeline — step-by-step (last scan)")
        step_rows = [{"Step": s["step"], "Filter": s["label"], "In": s["in"],
                      "Out": s["out"], "Dropped": s["dropped"]} for s in scan["steps"]]
        st.dataframe(pd.DataFrame(step_rows), use_container_width=True, hide_index=True)
    else:
        st.caption("No pipeline step data — waiting for next scan.")

    # Candidates table
    if scan["candidates"]:
        st.caption("Candidates passed to signal engine")
        st.dataframe(pd.DataFrame(scan["candidates"]), use_container_width=True, hide_index=True)
    else:
        st.caption("No candidates this scan cycle — all tickers filtered out.")

    # Scanner config from live imports
    with st.expander("Scanner config (live values from scanner.py)"):
        try:
            from scanner import (
                _MIN_VOLUME_24H_USD, _MIN_VOL_SPIKE, _MIN_PRICE_MOVE_1H,
                _MIN_ADX_15M, _MIN_OB_DEPTH_USD, _MAX_SPREAD_PCT,
                _MIN_EXPECTED_PROFIT, _ROUND_TRIP_FEE_PCT,
            )
            cfg = {
                "Min 24h volume (USD)":    f"${_MIN_VOLUME_24H_USD/1e6:.0f}M",
                "Min vol spike":           f"≥ {_MIN_VOL_SPIKE}×",
                "Min price move 1h":       f"≥ {_MIN_PRICE_MOVE_1H:.1f}%",
                "Min ADX 15m":             f"≥ {_MIN_ADX_15M}",
                "Min OB depth each side":  f"≥ ${_MIN_OB_DEPTH_USD/1e3:.0f}K",
                "Max spread":              f"< {_MAX_SPREAD_PCT:.2f}%",
                "Min expected profit":     f"≥ ${_MIN_EXPECTED_PROFIT:.2f}",
                "Round-trip fee modeled":  f"{_ROUND_TRIP_FEE_PCT*100:.3f}%",
                "Max candidates returned": "15",
                "Source":                  "Kraken Futures public REST (no auth)",
            }
            for k, v in cfg.items():
                st.text(f"  {k}: {v}")
        except Exception as e:
            st.error(f"scanner import: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# RENDER — SIGNAL BRAIN (60s)
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=60)
def render_signal_brain():
    import pandas as pd
    bay_stats = get_signal_bayesian_stats()
    ml_status = get_ml_status()

    st.subheader("Signal Engine")

    # ── Entry thresholds ──────────────────────────────────────────────────────
    st.caption("**Tier 2 entry thresholds by regime** (composite score must meet these; Tier 1 bypasses)")
    try:
        from signal_engine import _ENTRY_THRESHOLDS
        thresh_rows = [{"Regime": r, "Min Score": f"≥ {t} / 100"} for r, t in sorted(_ENTRY_THRESHOLDS.items())]
        thresh_rows.append({"Regime": "Tier 2 size mult", "Min Score": "0.75× base"})
        thresh_rows.append({"Regime": "Tier 1 size mult", "Min Score": "1.0× base (full)"})
        st.dataframe(pd.DataFrame(thresh_rows), use_container_width=False, hide_index=True)
    except Exception as e:
        st.error(f"signal_engine._ENTRY_THRESHOLDS: {e}")

    # ── Tier 1 setups ─────────────────────────────────────────────────────────
    st.caption("**Tier 1 setups** — these fire regardless of composite score")
    try:
        from signal_engine import _LONG_SETUPS, _SHORT_SETUPS
        t1_rows = []
        for direction, setups in [("LONG", _LONG_SETUPS), ("SHORT", _SHORT_SETUPS)]:
            for s in setups:
                t1_rows.append({"Direction": direction, "Name": s.get("name", ""), "Label": s.get("label", "")})
        st.dataframe(pd.DataFrame(t1_rows), use_container_width=True, hide_index=True)
    except Exception as e:
        st.error(f"signal_engine._LONG/SHORT_SETUPS: {e}")

    # ── Bayesian signal learning ───────────────────────────────────────────────
    st.caption(f"**Bayesian signal learning** — {len(bay_stats)} signals tracked · updates after every closed trade · PRIOR_N=20 · live after 10 fires")
    if bay_stats:
        st.dataframe(pd.DataFrame(bay_stats), use_container_width=True, hide_index=True)
    else:
        st.info("No signal data yet — accumulates with live trades.")

    # ── Technical tower signal list ────────────────────────────────────────────
    with st.expander("Technical tower — all scoring conditions (LONG side)"):
        long_signals = [
            ("CVD bullish divergence",          "+25"),
            ("MACD all variants aligned long",  "+20"),
            ("TradingView webhook confirmed",   "+20"),
            ("RSI bullish divergence",          "+15"),
            ("Funding squeeze (< −0.3 norm)",   "+15"),
            ("VWAP reclaim on volume",          "+15"),
            ("Liquidation cascade → long magnet", "+15"),
            ("WaveTrend oversold cross",        "+12"),
            ("SuperTrend bullish (ATR10 ×3)",   "+12"),
            ("WAE Bullish + Exploding",         "+10"),
            ("OB L5 imbalance > 0.60",          "+10"),
            ("Williams %R < −80",               "+10"),
            ("Whale accumulation signal",       "+10"),
            ("Options skew bullish",            "+10"),
            ("MACD fast histogram positive",    " +8"),
            ("Funding favorable (−0.1 to −0.3)"," +8"),
            ("KST above signal line",           " +8"),
            ("Fisher Transform cross up",       " +8"),
            ("Ichimoku cloud bullish",          " +8"),
            ("Laguerre RSI < 0.15 (deep OS)",   " +8"),
            ("OB L5 imbalance 0.55–0.60",       " +5"),
            ("Williams %R −80 to −70",          " +5"),
            ("Vol spike > 1.5×",                " +5"),
            ("RSI not overbought (< 60)",       " +5"),
            ("Choppiness trending (< 38.2)",    " +5"),
            ("WAE Bullish only (no explosion)", " +5"),
            ("Laguerre RSI < 0.25",             " +4"),
            ("Price > 2σ VWAP",                 "−25"),
            ("CVD bearish divergence",          "−20"),
            ("Extreme positive funding (> 0.5)","−20"),
            ("RSI bearish divergence",          "−15"),
            ("Whale distributing",              "−15"),
            ("Cascade risk > 0.70",             "−15"),
            ("OB L5 < 0.40 (bear pressure)",    "−10"),
            ("Price 1–2σ above VWAP",           "−10"),
            ("High funding 0.3–0.5",            "−10"),
            ("Fear & Greed euphoria (> 85)",    "−10"),
        ]
        st.caption("Raw range ~−115 to +150 · normalized 0–100 · same set mirrored for SHORT side")
        import pandas as pd
        st.dataframe(pd.DataFrame(long_signals, columns=["Condition", "Points"]),
                     use_container_width=False, hide_index=True)

    # ── CHOP gating ───────────────────────────────────────────────────────────
    with st.expander("CHOP index gating"):
        st.text("CHOP < 38.2  →  TRENDING  →  all momentum Tier 1 setups active")
        st.text("CHOP > 61.8  →  RANGING   →  momentum setups blocked; ranging_mr_long/short active")
        st.text("Ranging MR threshold:  VWAP distance ≥ ±0.30%")
        st.text("Ranging min hold before thesis check:  15 min")
        st.text("Trending min hold before thesis check: 30 min")

    # ── ML tower status ────────────────────────────────────────────────────────
    snaps  = ml_status["snapshots"]
    needed = ml_status["min_needed"]
    status = "ACTIVE — models being trained" if snaps >= needed else f"ACCUMULATING — {snaps}/{needed} snapshots"
    st.metric("ML Tower — 57-feature snapshots", f"{snaps} / {needed}", delta=status)
    st.caption("XGBoost 60% + LightGBM 40% · walk-forward 60d train / 10d val · WR≥54%, PF≥1.35, Sharpe≥0.8 to pass")


# ══════════════════════════════════════════════════════════════════════════════
# RENDER — TRADE LOG + PER-SYMBOL (30s)
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=30)
def render_trades():
    import pandas as pd

    sym    = get_per_symbol_stats()
    trades = get_trade_log(50)

    st.subheader("Per-Symbol Breakdown")
    if sym:
        st.dataframe(pd.DataFrame(sym), use_container_width=True, hide_index=True)
    else:
        st.info("No closed trades yet.")

    st.subheader("Trade Log (last 50 closed, since launch)")
    if not trades:
        st.info("No closed trades yet.")
        return

    rows = []
    for t in trades:
        notes = _parse_notes(t.get("notes", ""))
        action = t.get("action", "")
        direction = "LONG" if action == "SELL" else ("SHORT" if action == "BUY" else action)
        pnl = t.get("pnl_usd") or 0
        rows.append({
            "Time":      _time_ago(t.get("ts", "")),
            "Symbol":    t.get("symbol", ""),
            "Direction": direction,
            "Score":     notes.get("score", ""),
            "Regime":    notes.get("regime", ""),
            "Setup":     notes.get("setup", notes.get("reason", "")),
            "Price":     t.get("price") or 0,
            "P&L":       _fmt_pnl(pnl),
            "Result":    "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "FLAT"),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# RENDER — SYSTEM CONFIG (120s)
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=120)
def render_system_config():
    st.subheader("System Configuration")

    col_left, col_right = st.columns(2)

    with col_left:
        # Economics gate
        st.caption("**Economics gate** — pre-trade EV veto (risk/economics_gate.py)")
        try:
            from risk.economics_gate import (
                TAKER_FEE_PCT, ROUND_TRIP_COST,
                _TIER_APLUS_EV, _TIER_A_EV, _TIER_B_EV,
                TIER_MULTIPLIERS, _MIN_NET_RR,
            )
            st.text(f"  Taker fee (per side):    {TAKER_FEE_PCT*100:.3f}%  (Kraken Futures)")
            st.text(f"  Round-trip cost:         {ROUND_TRIP_COST*100:.3f}%")
            st.text(f"  Min net R:R:             ≥ {_MIN_NET_RR}:1 after fees")
            st.text(f"  Ranging R:R floor:       ≥ {_MIN_NET_RR * 1.25:.2f}:1 (CHOP > 61.8, 1.25× tighter)")
            st.text(f"  Tier A+  (EV ≥ {_TIER_APLUS_EV*100:.2f}%): {TIER_MULTIPLIERS.get('A+',1.0)}× size")
            st.text(f"  Tier A   (EV ≥ {_TIER_A_EV*100:.2f}%):  {TIER_MULTIPLIERS.get('A', 1.0)}× size")
            st.text(f"  Tier B   (EV ≥ {_TIER_B_EV*100:.2f}%):  {TIER_MULTIPLIERS.get('B', 0.75)}× size")
            st.text(f"  Below B:                 VETO — trade blocked")
            st.text(f"  Ranging EV floor:        ≥ {_TIER_B_EV*1.67*100:.3f}% (1.67× tighter)")
        except Exception as e:
            st.error(f"economics_gate: {e}")

        st.divider()

        # Position sizer
        st.caption("**Position sizer** — risk/unified_sizer.py")
        try:
            from risk.unified_sizer import BASE_RISK_PCT, MAX_HEAT_PCT, MAX_SINGLE_NOTIONAL_PCT, _QUALITY_MULT
            from config import ACCOUNT_SIZE
            acct = float(ACCOUNT_SIZE)
            st.text(f"  Formula:  size = (acct × {BASE_RISK_PCT*100:.1f}% × quality_mult) / stop_pct")
            st.text(f"  Account:  ${acct:,.0f}")
            st.text(f"  Base risk per trade:     {BASE_RISK_PCT*100:.1f}%  = ${acct * BASE_RISK_PCT:.0f}")
            st.text(f"  Portfolio heat cap:      {MAX_HEAT_PCT*100:.0f}%  = ${acct * MAX_HEAT_PCT:.0f} max deployed")
            st.text(f"  Hard position cap:       {MAX_SINGLE_NOTIONAL_PCT*100:.0f}% per symbol")
            st.text(f"  Default leverage:        3× ISOLATED margin")
            st.text(f"  Max leverage:            10× (strict gates)")
            for tier, mult in sorted(_QUALITY_MULT.items(), key=lambda x: -x[1]):
                st.text(f"  Quality {tier}:  {mult}× size")
            st.text(f"  Regime TRENDING:  1.00×   RANGING: 0.85×   HIGH_VOL: 0.70×")
        except Exception as e:
            st.error(f"unified_sizer: {e}")

    with col_right:
        # Exit stack
        st.caption("**6-priority exit stack** — position_manager.py (highest number = highest priority)")
        exits = [
            ("6", "Kill Switch",         "Balance < 75% of account / API errors / latency"),
            ("5", "Risk Forced Exit",    "Margin breach / portfolio VaR breach / correlation limit"),
            ("4", "Hard Stop",           "STOP_MARKET at entry − ATR×1.5 · NEVER widened"),
            ("3", "Thesis Invalidated",  "composite < entry_score × 0.45 → close · 10 min hold gate"),
            ("2", "TP Scale-Out",        "2R → close 33% · 3.5R → close 33% · remainder trails"),
            ("1", "Trailing Stop",       "Activates after 1× ATR in favor · trails 1.5× ATR from peak"),
        ]
        for num, title, detail in exits:
            st.text(f"  [{num}] {title}: {detail}")

        st.divider()

        # Kill switch + risk rules
        st.caption("**Kill switch & risk rules** — hardcoded, no override")
        try:
            from config import ACCOUNT_SIZE, MAX_DAILY_LOSS_PCT
            acct = float(ACCOUNT_SIZE)
            st.text(f"  Kill switch:             Balance < 75%  = ${acct * 0.75:,.0f}")
            st.text(f"  Max daily loss:          {MAX_DAILY_LOSS_PCT*100:.0f}% → halt all trading")
            st.text(f"  Max deployed capital:    90%")
            st.text(f"  Max risk per trade:      1% of account")
            st.text(f"  Margin type:             ISOLATED — never CROSS")
            st.text(f"  Kraken taker fee:        0.065%")
            st.text(f"  No double-entry:         one position per symbol, ever")
            st.text(f"  No chase:                skip if price moved > 3% since signal")
            st.text(f"  Stop sacred:             never moved wider after entry")
        except Exception as e:
            st.error(f"config: {e}")

        st.divider()

        # Learning loop
        st.caption("**Learning loop** — fires on every closed trade")
        ml = get_ml_status()
        st.text(f"  57-feature snapshots stored:  {ml['snapshots']} / {ml['min_needed']} needed")
        st.text(f"  Bayesian overlay blend:       15% of composite after scoring")
        st.text(f"  Bayesian PRIOR_N:             20 phantom trades")
        st.text(f"  Bayesian min fires to learn:  10")
        st.text(f"  Bayesian weight cap:          2.5× original prior points")
        st.text(f"  Retrain trigger:              ml_retrain_queue · checked every 6h")
        st.text(f"  Post-trade analyzer:          fires on every full close")
        st.text(f"  Dynamic weights TTL:          5 min cache")
        st.text(f"  RBI nightly:                  02:00 ET · 575 combo research")

    # System events
    st.divider()
    st.caption("**System events log** (last 20, IBKR noise excluded)")
    events = get_recent_events(20)
    if events:
        import pandas as pd
        rows = [{"Time": _time_ago(e.get("ts","")), "Level": e.get("level",""),
                 "Source": e.get("source","")[:30], "Message": e.get("message","")[:120]}
                for e in events]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No events.")

    with st.expander("Full config.py constants"):
        try:
            import config as _cfg
            import pandas as pd
            items = sorted({k: v for k, v in vars(_cfg).items()
                            if not k.startswith("_") and isinstance(v, (int, float, str, bool))}.items())
            st.dataframe(pd.DataFrame(items, columns=["Key", "Value"]), use_container_width=True, hide_index=True)
        except Exception as e:
            st.error(str(e))


# ══════════════════════════════════════════════════════════════════════════════
# RENDER — MANUAL SCAN + TRADE APPROVAL
# ══════════════════════════════════════════════════════════════════════════════

# Setup descriptions shown in the ℹ detail card
_SETUP_DESC = {
    'momentum':   'Price closed above VWAP with a volume spike. Trend is accelerating — ride the move.',
    'ranging_mr': 'ADX < 20 (no trend). Price stretched from VWAP. Mean-reversion back toward center expected.',
    'kst_cross':  'KST momentum oscillator crossed its signal line. Indicates a turning point in medium-term momentum.',
    'supertrend': 'SuperTrend indicator flipped direction. Trailing stop-based trend-following entry.',
    'ichimoku':   'Price broke through the Ichimoku cloud. Cloud acts as dynamic support/resistance.',
}

_SETUP_PRIORITY = ['momentum', 'ranging_mr', 'kst_cross', 'supertrend', 'ichimoku']


def _win_prob(c: dict) -> float:
    """
    Estimate win probability from scanner fields.
    Base = 52% (scanner EV formula assumption).
    Each confirmed signal adds points; capped at 84%.
    """
    prob  = 52.0
    dirn  = c.get('direction', 'LONG')
    vs    = c.get('vol_spike', 1.0)
    adx   = c.get('adx_15m', 20.0)
    setup = c.get('primary_setup', '')
    vwap_d = abs(c.get('vwap_disp_pct', 0.0))
    kst_v  = c.get('kst_value',  0.0)
    kst_s  = c.get('kst_signal', 0.0)
    st_dir = c.get('supertrend_dir', 0)
    fund   = abs(c.get('funding_rate', 0.0))
    pm1h   = c.get('price_move_1h_pct', 0.0)

    # Volume conviction (strongest signal)
    if vs >= 3.0:   prob += 9
    elif vs >= 2.0: prob += 6
    elif vs >= 1.5: prob += 3

    # ADX — setup dependent
    if 'momentum' in setup and adx >= 25:  prob += 7
    elif 'ranging' in setup and adx < 20:  prob += 7
    elif 'kst' in setup and adx < 30:      prob += 4
    else:                                   prob += 2  # some ADX, some trend

    # KST direction confirmation
    if (dirn == 'LONG'  and kst_v > kst_s) or \
       (dirn == 'SHORT' and kst_v < kst_s):
        prob += 5

    # SuperTrend alignment
    if (dirn == 'LONG'  and st_dir > 0) or \
       (dirn == 'SHORT' and st_dir < 0):
        prob += 5

    # VWAP displacement (mean-reversion edge)
    if 'ranging' in setup:
        if vwap_d >= 2.0:  prob += 5
        elif vwap_d >= 1.0: prob += 3

    # Funding squeeze pressure
    if fund > 0.002:   prob += 3   # >0.2% annualized, elevated
    elif fund > 0.0005: prob += 1

    # Short-term momentum alignment
    if dirn == 'LONG'  and pm1h > 0.3:  prob += 2
    elif dirn == 'SHORT' and pm1h < -0.3: prob += 2

    return min(round(prob, 1), 84.0)


def _win_prob_breakdown(c: dict) -> list:
    """Returns list of (factor, value_str, points) tuples for the detail card."""
    rows = []
    dirn  = c.get('direction', 'LONG')
    vs    = c.get('vol_spike', 1.0)
    adx   = c.get('adx_15m', 20.0)
    setup = c.get('primary_setup', '')
    vwap_d = abs(c.get('vwap_disp_pct', 0.0))
    kst_v  = c.get('kst_value',  0.0)
    kst_s  = c.get('kst_signal', 0.0)
    st_dir = c.get('supertrend_dir', 0)
    fund   = abs(c.get('funding_rate', 0.0))
    pm1h   = c.get('price_move_1h_pct', 0.0)

    rows.append(("Base rate (scanner EV model)", "52% assumed win rate", "+52%"))

    # Volume
    if vs >= 3.0:    rows.append(("Volume spike", f"{vs:.2f}× avg  (strong conviction)", "+9%"))
    elif vs >= 2.0:  rows.append(("Volume spike", f"{vs:.2f}× avg  (moderate conviction)", "+6%"))
    elif vs >= 1.5:  rows.append(("Volume spike", f"{vs:.2f}× avg  (mild conviction)", "+3%"))
    else:            rows.append(("Volume spike", f"{vs:.2f}× avg  (weak — minimal edge)", "+0%"))

    # ADX
    if 'momentum' in setup and adx >= 25:
        rows.append(("ADX trend strength", f"{adx:.1f}  (strong trend confirms momentum entry)", "+7%"))
    elif 'ranging' in setup and adx < 20:
        rows.append(("ADX trend strength", f"{adx:.1f}  (low ADX confirms ranging/mean-rev setup)", "+7%"))
    elif 'kst' in setup and adx < 30:
        rows.append(("ADX trend strength", f"{adx:.1f}  (moderate trend, KST cross valid)", "+4%"))
    else:
        rows.append(("ADX trend strength", f"{adx:.1f}  (partial alignment)", "+2%"))

    # KST
    kst_ok = (dirn == 'LONG' and kst_v > kst_s) or (dirn == 'SHORT' and kst_v < kst_s)
    kst_str = f"KST={kst_v:.4f} vs Signal={kst_s:.4f}"
    if kst_ok:
        rows.append(("KST momentum cross", f"{kst_str}  ✓ aligned with {dirn}", "+5%"))
    else:
        rows.append(("KST momentum cross", f"{kst_str}  ✗ not aligned", "+0%"))

    # SuperTrend
    st_ok = (dirn == 'LONG' and st_dir > 0) or (dirn == 'SHORT' and st_dir < 0)
    st_label = "bullish" if st_dir > 0 else ("bearish" if st_dir < 0 else "flat")
    if st_ok:
        rows.append(("SuperTrend direction", f"{st_label}  ✓ aligned with {dirn}", "+5%"))
    else:
        rows.append(("SuperTrend direction", f"{st_label}  ✗ not aligned", "+0%"))

    # VWAP
    if 'ranging' in setup:
        if vwap_d >= 2.0:
            rows.append(("VWAP displacement", f"{vwap_d:.2f}%  (strongly extended → snap-back likely)", "+5%"))
        elif vwap_d >= 1.0:
            rows.append(("VWAP displacement", f"{vwap_d:.2f}%  (moderately extended)", "+3%"))
        else:
            rows.append(("VWAP displacement", f"{vwap_d:.2f}%  (close to VWAP — weak MR edge)", "+0%"))

    # Funding
    if fund > 0.002:
        rows.append(("Funding rate", f"{fund*100:.4f}% ann  (elevated — squeeze pressure adds edge)", "+3%"))
    elif fund > 0.0005:
        rows.append(("Funding rate", f"{fund*100:.4f}% ann  (mild)", "+1%"))
    else:
        rows.append(("Funding rate", f"{fund*100:.4f}% ann  (neutral)", "+0%"))

    # Price momentum
    if dirn == 'LONG' and pm1h > 0.3:
        rows.append(("1h price momentum", f"+{pm1h:.2f}%  ✓ aligned with LONG", "+2%"))
    elif dirn == 'SHORT' and pm1h < -0.3:
        rows.append(("1h price momentum", f"{pm1h:.2f}%  ✓ aligned with SHORT", "+2%"))
    else:
        rows.append(("1h price momentum", f"{pm1h:.2f}%  (neutral or against direction)", "+0%"))

    return rows


def _render_trade_details(c: dict, prob: float):
    """Renders the full detail card shown inside the ℹ expander."""
    import pandas as pd

    sym    = c.get('symbol', '')
    dirn   = c.get('direction', '')
    exch   = c.get('exchange', 'kraken').upper()
    setup  = c.get('primary_setup', '')
    price  = c.get('price', 0)
    atr    = c.get('atr_15m', 0)
    stop_p = c.get('stop_pct', 0)
    tgt_p  = c.get('target_pct', 0)
    ev     = c.get('expected_profit', 0)
    fund_ann = c.get('funding_rate', 0.0)
    fund_cost = c.get('funding_cost_pct', 0.0)
    pm4h   = c.get('price_move_4h_pct', 0.0)
    vwap   = c.get('vwap', 0)
    vwap_d = c.get('vwap_disp_pct', 0.0)
    all_setups = c.get('scan_setups', [setup])

    # ── Setup explanation ──────────────────────────────────────────────────
    desc = _SETUP_DESC.get(setup, 'Composite signal — multiple filters triggered.')
    st.markdown(f"**Setup: `{setup}`** — {desc}")
    if len(all_setups) > 1:
        others = [s for s in all_setups if s != setup]
        st.caption(f"Also triggered: {', '.join(others)}")

    st.divider()

    # ── Win probability breakdown ──────────────────────────────────────────
    st.markdown("**Win probability breakdown**")
    breakdown = _win_prob_breakdown(c)
    df_bp = pd.DataFrame(breakdown, columns=["Factor", "Reading", "Points"])
    st.dataframe(df_bp, use_container_width=True, hide_index=True)
    st.markdown(f"**→ Total estimated win probability: {prob:.1f}%**")

    st.divider()

    # ── EV math ───────────────────────────────────────────────────────────
    st.markdown("**Expected value (EV) calculation**")
    risk_usd  = 5000.0 * 0.015
    pos_usd   = risk_usd / (stop_p / 100) if stop_p > 0 else 0
    fee_pct   = 0.13   # round-trip Kraken taker
    net_win   = tgt_p / 100 - fee_pct / 100 - fund_cost / 100
    net_loss  = stop_p / 100 + fee_pct / 100
    st.text(f"  Position size:      ${pos_usd:,.0f}  (1.5% account risk / stop%)")
    st.text(f"  Stop loss:          {stop_p:.3f}%  (1.5× ATR = {atr:.6g})")
    st.text(f"  Take profit:        {tgt_p:.3f}%  (3× ATR)")
    st.text(f"  Round-trip fee:     {fee_pct:.2f}%  (0.065% × 2)")
    st.text(f"  Funding cost (est): {fund_cost:.4f}%  ({abs(fund_ann)*100:.4f}% ann ÷ 365×3 × hold)")
    st.text(f"  Net win if TP hit:  {net_win*100:.3f}%  →  ${net_win*pos_usd:+.2f}")
    st.text(f"  Net loss if SL hit: {net_loss*100:.3f}%  →  ${-net_loss*pos_usd:.2f}")
    st.text(f"  EV = 52%×${net_win*pos_usd:.2f} − 48%×${net_loss*pos_usd:.2f}  =  ${ev:+.2f}")

    st.divider()

    # ── Raw indicator readings ─────────────────────────────────────────────
    st.markdown("**Raw indicator readings**")
    c1, c2 = st.columns(2)
    c1.text(f"  Price:           {price:.6g}")
    c1.text(f"  VWAP:            {vwap:.6g}")
    c1.text(f"  VWAP disp:       {vwap_d:+.3f}%")
    c1.text(f"  1h price move:   {c.get('price_move_1h_pct',0):+.3f}%")
    c1.text(f"  4h price move:   {pm4h:+.3f}%")
    c2.text(f"  ADX (15m):       {c.get('adx_15m',0):.1f}")
    c2.text(f"  Vol spike:       {c.get('vol_spike',0):.3f}×")
    c2.text(f"  KST:             {c.get('kst_value',0):.4f}")
    c2.text(f"  KST signal:      {c.get('kst_signal',0):.4f}")
    c2.text(f"  SuperTrend dir:  {'↑ bullish' if c.get('supertrend_dir',0)>0 else ('↓ bearish' if c.get('supertrend_dir',0)<0 else 'flat')}")
    st.text(f"  Exchange: {exch}   |   Funding (ann): {fund_ann*100:.4f}%")


def render_manual_scan():
    st.subheader("Manual Scan & Trade Approval")
    st.caption("Runs a fresh scan (bypasses the 5-min cache). You pick which trades execute.")

    col_btn, col_info = st.columns([1, 4])
    with col_btn:
        run_scan = st.button("Run Scan Now", type="primary", key="manual_scan_btn")
    with col_info:
        last_ts = st.session_state.get('manual_scan_time')
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
                st.session_state['manual_candidates'] = candidates
                st.session_state['manual_scan_time'] = datetime.now().strftime('%H:%M:%S')
                # Clear old row selections
                for k in list(st.session_state.keys()):
                    if k.startswith('ms_sel_'):
                        del st.session_state[k]
            except Exception as e:
                st.error(f"Scan failed: {e}")
                return
        n = len(st.session_state.get('manual_candidates', []))
        st.success(f"Found {n} candidates.")

    candidates = st.session_state.get('manual_candidates', [])
    if not candidates:
        st.info("No scan results yet — click **Run Scan Now** above.")
        return

    # ── Column headers ─────────────────────────────────────────────────────
    hc1, hc2, hc3, hc4 = st.columns([0.4, 3.2, 2.8, 0.6])
    hc1.caption("Trade?")
    hc2.caption("Signal")
    hc3.caption("Win Probability")
    hc4.caption("Why")
    st.divider()

    # ── One row per candidate ──────────────────────────────────────────────
    for i, c in enumerate(candidates):
        prob  = _win_prob(c)
        sym   = c.get('symbol', '')
        dirn  = c.get('direction', '')
        exch  = c.get('exchange', 'kraken')
        setup = c.get('primary_setup', '')
        badge = "🔵" if exch == 'hyperliquid' else "🟠"

        col1, col2, col3, col4 = st.columns([0.4, 3.2, 2.8, 0.6])

        with col1:
            st.checkbox("", key=f"ms_sel_{i}", label_visibility="collapsed")

        with col2:
            st.markdown(f"**{sym}** `{dirn}` {badge} `{exch[:5].upper()}` · *{setup}*")

        with col3:
            bar_color = "normal" if prob >= 65 else "off"
            label = f"{prob:.0f}% — {'High edge' if prob>=68 else ('Moderate edge' if prob>=60 else 'Lower edge')}"
            st.progress(prob / 100.0, text=label)

        with col4:
            with st.expander("ℹ️"):
                _render_trade_details(c, prob)

    st.divider()

    # ── Execute block ──────────────────────────────────────────────────────
    selected_idx = [i for i in range(len(candidates))
                    if st.session_state.get(f"ms_sel_{i}", False)]
    n_sel = len(selected_idx)

    if n_sel == 0:
        st.caption("Check the **Trade?** box on rows you want to execute, then click Execute.")
        return

    if st.button(f"Execute {n_sel} Trade(s)", type="primary", key="manual_execute_btn"):
        from data.historical_data import get_candles
        import perps_engine as perps

        results = []
        for idx in selected_idx:
            cand  = candidates[idx]
            sym   = cand['symbol']
            dirn  = cand['direction']
            setup = cand.get('primary_setup', 'manual')

            try:
                df_c = get_candles(sym, '1h', 100)
                if df_c is None or len(df_c) < 10:
                    results.append((sym, dirn, False, "insufficient candle data"))
                    continue

                price  = float(df_c['close'].iloc[-1])
                atr_7  = float(df_c['high'].sub(df_c['low']).tail(7).mean())
                if atr_7 <= 0:
                    atr_7 = price * 0.015

                stop_dist  = max(atr_7 * 1.5, price * 0.008)
                target_dist = stop_dist * 3.0
                composite  = cand.get('composite_score', 50.0)

                # Use the real position sizer — same logic the bot uses
                from position_manager import compute_position_size
                balance, _, _b = get_account()
                sizing = compute_position_size(
                    account_balance=balance,
                    current_price=price,
                    atr_7=atr_7,
                    stop_multiplier=1.5,
                    ml_score=composite,
                    composite_score=composite,
                    paper=True,
                )
                pos_usd  = sizing['position_usd']
                leverage = sizing['leverage']

                if dirn == 'LONG':
                    stop_p   = round(price - stop_dist, 6)
                    target_p = round(price + target_dist, 6)
                    pos = perps.open_long(
                        symbol=sym, position_usd=pos_usd, entry_price=price,
                        stop_price=stop_p, take_profit_price=target_p, leverage=leverage,
                        composite_score=composite, atr_at_entry=atr_7,
                        regime='UNKNOWN', entry_setup=f'manual_{setup}', paper=True,
                    )
                else:
                    stop_p   = round(price + stop_dist, 6)
                    target_p = round(price - target_dist, 6)
                    pos = perps.open_short(
                        symbol=sym, position_usd=pos_usd, entry_price=price,
                        stop_price=stop_p, take_profit_price=target_p, leverage=leverage,
                        composite_score=composite, atr_at_entry=atr_7,
                        regime='UNKNOWN', entry_setup=f'manual_{setup}', paper=True,
                    )

                if pos:
                    results.append((sym, dirn, True,
                        f"entered @ {price:.6g}  stop={stop_p:.6g}  target={target_p:.6g}"
                        f"  size=${pos_usd:.0f}  lev={leverage}x  kelly={sizing['kelly_fraction']:.3f}"))
                else:
                    results.append((sym, dirn, False, "open_long/short returned None"))

            except Exception as e:
                results.append((sym, dirn, False, str(e)[:120]))

        for sym, dirn, ok, msg in results:
            st.write(f"{'✅' if ok else '❌'} **{sym} {dirn}** — {msg}")

        # Clear so next scan starts clean
        st.session_state.pop('manual_candidates', None)
        st.session_state.pop('manual_scan_time', None)


# ══════════════════════════════════════════════════════════════════════════════
# RENDER — LIVE ACTIVITY LOG (5s)
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=5)
def render_activity():
    activity = get_bot_activity(40)
    st.subheader("Live Bot Activity")

    if not activity:
        st.info("Waiting for bot.log…")
        return

    # Color map for event kinds
    KIND_COLOR = {
        "ENTERED": "🟢", "CLOSE": "🔵", "OPEN": "🟩",
        "TIER1": "🟡", "TIER2": "🟦", "SIGNAL": "⚪",
        "VETO": "🟠", "SCAN": "⬜", "BAYES": "🔷",
        "ERROR": "🔴", "ML": "🟣",
    }

    for ev in activity:
        icon = KIND_COLOR.get(ev["kind"], "·")
        st.text(f"  {ev['ts']}  {icon} [{ev['kind']:<8}]  {ev['msg']}")


# ══════════════════════════════════════════════════════════════════════════════
# RENDER — FUTURES TAB (10s)
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=10)
def render_futures():
    import pandas as pd

    # ── Market hours status ───────────────────────────────────────────────────
    try:
        import pytz
        et       = pytz.timezone('America/New_York')
        now_et   = datetime.now(et)
        h, m     = now_et.hour, now_et.minute
        is_open  = (now_et.weekday() < 5 and
                    ((h == 9 and m >= 30) or (10 <= h <= 15) or (h == 15 and m <= 45)))
        pre_open = (now_et.weekday() < 5 and h == 9 and m < 30)
        time_str = now_et.strftime('%H:%M ET')
        if is_open:
            mkt_status = "OPEN"
        elif pre_open:
            mkt_status = "PRE-OPEN"
        else:
            mkt_status = "CLOSED"
    except Exception:
        is_open, mkt_status, time_str = False, "UNKNOWN", "--:--"

    mes_state  = get_mes_state()
    daily_pnl  = get_mes_daily_pnl()
    all_stats  = get_mes_all_time_stats()
    trades_today = get_mes_trades_today()

    price      = mes_state.get('price')
    or_high    = mes_state.get('or_high')
    or_low     = mes_state.get('or_low')
    or_locked  = mes_state.get('or_locked', False)
    has_pos    = mes_state.get('has_pos', False)
    state_time = mes_state.get('time_et', '--')

    # ── Status row ────────────────────────────────────────────────────────────
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Market",       mkt_status, delta=time_str)
    c2.metric("MES Price",    f"{price:.2f}" if price else "–")
    c3.metric("Today P&L",   _fmt_pnl(daily_pnl))
    c4.metric("Position",    "ACTIVE" if has_pos else "FLAT")
    c5.metric("All-Time W/L", f"{all_stats['wins']}W / {all_stats['closes'] - all_stats['wins']}L")
    pf = all_stats['profit_factor']
    c6.metric("Profit Factor", f"{pf:.2f}" if pf != float('inf') else "∞")

    st.divider()

    # ── Opening Range panel ───────────────────────────────────────────────────
    col_l, col_r = st.columns(2)

    with col_l:
        st.subheader("Opening Range (9:30–10:00 ET)")
        if or_locked and or_high and or_low:
            or_range = or_high - or_low
            or_mid   = (or_high + or_low) / 2
            long_entry  = round(or_high + 0.25, 2)
            short_entry = round(or_low  - 0.25, 2)
            r1, r2, r3 = st.columns(3)
            r1.metric("OR High",       f"{or_high:.2f}")
            r2.metric("OR Low",        f"{or_low:.2f}")
            r3.metric("Range (pts)",   f"{or_range:.2f}")
            st.caption(f"Long breakout trigger: ≥ {long_entry}  |  Short breakdown trigger: ≤ {short_entry}")
            st.caption(f"Last runner update: {state_time}")
        elif is_open and not or_locked:
            st.info("Building opening range… (9:30–10:00 ET)")
        elif not is_open:
            st.info("Market closed. Opening range resets at 9:30 ET.")
        else:
            st.info("Waiting for runner state (FUTURES_ENABLED must be True in .env)")

    with col_r:
        st.subheader("Strategy Playbook")
        st.caption("**Strategy 1 — Opening Range Breakout** (fires once, at OR lock)")
        strats = [
            ("Trigger",  "Price breaks above OR high (+0.25 pt) → LONG"),
            ("",         "Price breaks below OR low  (−0.25 pt) → SHORT"),
            ("Stop",     "Opposite end of OR ± 0.25 pt buffer"),
            ("Target",   "2× stop distance, minimum 4 pts ($20/contract)"),
            ("Contracts","Up to 2 (config: FUTURES_NUM_CONTRACTS)"),
            ("Window",   "10:00–15:45 ET; hard EOD close at 15:45"),
        ]
        for k, v in strats:
            prefix = f"  {k+':':<12}" if k else "               "
            st.text(f"{prefix}{v}")

        st.divider()

        st.caption("**Strategy 2 — VWAP Mean Reversion** (runs all session)")
        strats2 = [
            ("Trigger",  "Price >2 ATR from session VWAP AND RSI >68 → SHORT"),
            ("",         "Price <2 ATR from session VWAP AND RSI <32 → LONG"),
            ("Stop",     "1.5 ATR past entry"),
            ("Target",   "VWAP (mean-reversion)"),
            ("Contracts","1 (conservative)"),
            ("Window",   "10:00–14:30 ET; requires no open position"),
        ]
        for k, v in strats2:
            prefix = f"  {k+':':<12}" if k else "               "
            st.text(f"{prefix}{v}")

    st.divider()

    # ── Today's trades ────────────────────────────────────────────────────────
    st.subheader(f"Today's MES Trades ({len(trades_today)})")
    if trades_today:
        rows = []
        for t in trades_today:
            pnl = t.get('pnl_usd') or 0
            rows.append({
                "Time":    _time_ago(t.get('ts', '')),
                "Action":  t.get('action', ''),
                "Qty":     t.get('qty', ''),
                "Price":   t.get('price', ''),
                "P&L":     _fmt_pnl(pnl) if pnl else '–',
                "Notes":   (t.get('notes') or '')[:80],
                "Result":  "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "OPEN"),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No MES trades today." if is_open else "No MES trades today — market closed.")

    st.divider()

    # ── Config + risk rules ───────────────────────────────────────────────────
    with st.expander("Futures configuration & risk rules"):
        try:
            from config import FUTURES_ENABLED, FUTURES_NUM_CONTRACTS, ACCOUNT_SIZE
            st.text(f"  FUTURES_ENABLED:         {FUTURES_ENABLED}")
            st.text(f"  FUTURES_NUM_CONTRACTS:   {FUTURES_NUM_CONTRACTS}")
            st.text(f"  Account size:            ${float(ACCOUNT_SIZE):,.0f}")
        except Exception as e:
            st.error(f"config: {e}")
        st.text("  Contract:   MES (Micro E-mini S&P 500) — CME")
        st.text("  Expiry:     Q2 2026 — 20260619 (update quarterly)")
        st.text("  Point value: $5.00 / full point")
        st.text("  Tick size:   0.25 pts = $1.25 / tick")
        st.text("  Commission:  ~$0.47/side = $0.94 round-trip per contract")
        st.text("  Connection:  IBKR TWS port 7497 (paper) / 7496 (live)")
        st.text("  Daily loss limit: $150 — no new entries after this")
        st.text("  Hard EOD close:   15:45 ET — all positions closed")
        st.text("  Max simultaneous: 1 position at a time")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    st.title("Algo Trading System — v10.1 Paper")
    st.caption("Two-tower signal engine · 6-priority exit stack · 57-feature ML · Kraken + Hyperliquid perps · MES futures")

    tab_crypto, tab_futures = st.tabs(["CRYPTO PERPS", "FUTURES (MES)"])

    with tab_crypto:
        render_status()
        st.divider()
        render_manual_scan()
        st.divider()
        render_portfolio()
        st.divider()
        render_open_positions()
        st.divider()
        render_scanner()
        st.divider()
        render_signal_brain()
        st.divider()
        render_trades()
        st.divider()
        render_system_config()
        st.divider()
        render_activity()

    with tab_futures:
        render_futures()


if __name__ == "__main__":
    main()
else:
    main()
