"""
dashboard/app.py — v10.1 War Room
Built from actual system state: DB tables, bot.log, live config imports.
Nothing is hardcoded that can be read from the system.
"""
import sys, os, re, json
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import time
from datetime import datetime

import streamlit as st

# ── constants ──────────────────────────────────────────────────────────────────
LAUNCH_DATE = "2026-04-02"
DB_PATH     = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "trades.db")
LOG_PATH    = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "bot.log")

# ── palette ────────────────────────────────────────────────────────────────────
BG      = "#07080d"
SURFACE = "#0d0e18"
CARD    = "#111220"
CARD2   = "#161829"
BORDER  = "#1c1f35"
BORDER2 = "#252843"
GOLD    = "#f5a623"
GREEN   = "#00d9a3"
GREEN2  = "#00b88a"
RED     = "#f03e5e"
AMBER   = "#f59e0b"
BLUE    = "#4c8bf5"
BLUE2   = "#3a6fd4"
PURPLE  = "#a78bfa"
CYAN    = "#22d3ee"
TEXT    = "#e8eaf6"
TEXT2   = "#7c85ab"
TEXT3   = "#3d4468"
MONO    = "'JetBrains Mono', 'Fira Code', 'Consolas', monospace"

st.set_page_config(
    page_title="v10.1 War Room",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

html, body, .stApp, .main, [data-testid="stAppViewContainer"] {{
    background: {BG} !important;
    font-family: 'Inter', sans-serif !important;
    color: {TEXT} !important;
}}
section[data-testid="stSidebar"] {{ display: none; }}
#MainMenu, footer, header, .stDeployButton, [data-testid="stToolbar"] {{ visibility: hidden; }}
.block-container {{ padding: 14px 18px 60px 18px !important; max-width: 100% !important; }}
div[data-testid="column"] {{ padding: 0 4px !important; }}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {{
    background: {SURFACE} !important; border-radius: 10px !important;
    padding: 4px !important; gap: 2px !important;
    border: 1px solid {BORDER} !important; margin-bottom: 4px;
}}
.stTabs [data-baseweb="tab"] {{
    background: transparent !important; color: {TEXT2} !important;
    border-radius: 7px !important; padding: 7px 18px !important;
    font-weight: 600 !important; font-size: 13px !important; border: none !important;
}}
.stTabs [aria-selected="true"] {{
    background: {CARD2} !important; color: {TEXT} !important;
    box-shadow: 0 0 0 1px {BORDER2} !important;
}}
.stTabs [data-baseweb="tab-panel"] {{ background: transparent !important; padding-top: 16px !important; }}

/* Inputs */
.stSelectbox > div > div, .stTextInput > div > div {{
    background: {CARD} !important; border: 1px solid {BORDER} !important;
    color: {TEXT} !important;
}}

/* Expander */
.stExpander {{ border: 1px solid {BORDER} !important; border-radius: 10px !important;
    background: {CARD} !important; }}
div[data-testid="stExpander"] summary {{
    color: {TEXT2} !important; font-size: 12px !important; font-weight: 600 !important;
}}

/* Charts */
[data-testid="stVegaLiteChart"], [data-testid="stArrowVegaLiteChart"] {{
    background: {CARD} !important; border-radius: 10px !important;
    border: 1px solid {BORDER} !important;
}}

/* Animations */
@keyframes pulse {{
    0%,100% {{ opacity:1; box-shadow:0 0 0 0 {GREEN}55; }}
    50%      {{ opacity:.8; box-shadow:0 0 0 5px {GREEN}00; }}
}}
@keyframes sweep {{
    0%   {{ transform:translateX(-100%); opacity:0; }}
    10%  {{ opacity:.9; }}
    90%  {{ opacity:.9; }}
    100% {{ transform:translateX(200%); opacity:0; }}
}}
@keyframes fadein {{ from {{ opacity:0;transform:translateY(-4px); }} to {{ opacity:1;transform:none; }} }}

.live-dot {{
    display:inline-block; width:9px; height:9px; border-radius:50%;
    background:{GREEN}; animation:pulse 2s infinite;
    box-shadow:0 0 8px {GREEN}88;
}}
.sweep-bar {{
    height:2px; border-radius:2px; background:{BORDER}; overflow:hidden; position:relative;
}}
.sweep-bar::after {{
    content:''; position:absolute; top:0; left:0; height:100%; width:35%;
    background:linear-gradient(90deg,transparent,{BLUE},{GREEN},transparent);
    animation:sweep 2.6s ease-in-out infinite;
}}
.fadein {{ animation:fadein .25s ease; }}
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# DB / LOG HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def _q(sql, params=()):
    try:
        with _conn() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]
    except Exception:
        return []

def _q1(sql, params=()):
    rows = _q(sql, params)
    return rows[0] if rows else {}


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LAYER — all queries are real, no placeholders
# ═══════════════════════════════════════════════════════════════════════════════

def get_account():
    try:
        from config import ACCOUNT_SIZE, PAPER_TRADING
        return float(ACCOUNT_SIZE), bool(PAPER_TRADING)
    except Exception:
        return 5000.0, True

def get_performance_stats():
    r = _q1("""
        SELECT
            COUNT(*) AS total_rows,
            SUM(CASE WHEN action IN ('SELL','BUY') AND pnl_usd != 0 THEN 1 END) AS closes,
            SUM(CASE WHEN action IN ('SELL','BUY') AND pnl_usd > 0 THEN 1 END) AS wins,
            SUM(CASE WHEN action IN ('SELL','BUY') AND pnl_usd < 0 THEN 1 END) AS losses,
            SUM(CASE WHEN action IN ('SELL','BUY') THEN pnl_usd ELSE 0 END) AS total_pnl,
            SUM(CASE WHEN action IN ('SELL','BUY') AND pnl_usd > 0 THEN pnl_usd ELSE 0 END) AS gross_wins,
            SUM(CASE WHEN action IN ('SELL','BUY') AND pnl_usd < 0 THEN ABS(pnl_usd) ELSE 0 END) AS gross_losses,
            COUNT(DISTINCT symbol) AS symbols,
            SUM(ABS(fee_usd)) AS total_fees
        FROM trades
        WHERE ts >= ? AND paper=1 AND broker NOT LIKE '%bybit%'
          AND source NOT IN ('backtest','pre_v10_contaminated','bybit_paper')
    """, (LAUNCH_DATE,))
    closes = r.get("closes") or 0
    wins   = r.get("wins") or 0
    gw     = r.get("gross_wins") or 0
    gl     = r.get("gross_losses") or 0
    return {
        "closes":        closes,
        "wins":          wins,
        "losses":        r.get("losses") or 0,
        "win_rate":      (wins / closes * 100) if closes else 0.0,
        "total_pnl":     r.get("total_pnl") or 0.0,
        "profit_factor": (gw / gl) if gl > 0 else (float("inf") if gw > 0 else 0.0),
        "gross_wins":    gw,
        "gross_losses":  gl,
        "symbols":       r.get("symbols") or 0,
        "total_fees":    r.get("total_fees") or 0.0,
    }

def get_today_pnl():
    today = datetime.now().strftime("%Y-%m-%d")
    r = _q1("""
        SELECT SUM(pnl_usd) v FROM trades
        WHERE ts >= ? AND paper=1 AND broker NOT LIKE '%bybit%'
          AND pnl_usd != 0
    """, (today,))
    return r.get("v") or 0.0

def get_open_positions():
    return _q("SELECT * FROM open_positions WHERE paper=1 ORDER BY ts_entry DESC")

def get_trade_log(limit=60):
    return _q("""
        SELECT ts, symbol, action, pnl_usd, fee_usd, notes, source, qty, price
        FROM trades
        WHERE ts >= ? AND paper=1 AND broker NOT LIKE '%bybit%'
          AND source NOT IN ('backtest','pre_v10_contaminated','bybit_paper')
        ORDER BY ts DESC LIMIT ?
    """, (LAUNCH_DATE, limit))

def get_equity_curve():
    rows = _q("""
        SELECT ts, SUM(pnl_usd) OVER (ORDER BY ts) AS cum_pnl, pnl_usd, symbol
        FROM trades
        WHERE ts >= ? AND paper=1 AND broker NOT LIKE '%bybit%'
          AND pnl_usd != 0
          AND source NOT IN ('backtest','pre_v10_contaminated','bybit_paper')
        ORDER BY ts
    """, (LAUNCH_DATE,))
    return rows

def get_per_symbol_stats():
    return _q("""
        SELECT symbol,
            COUNT(*) AS trades,
            SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END) AS wins,
            SUM(pnl_usd) AS total_pnl,
            AVG(CASE WHEN pnl_usd!=0 THEN pnl_usd END) AS avg_pnl,
            MAX(pnl_usd) AS best,
            MIN(pnl_usd) AS worst
        FROM trades
        WHERE ts >= ? AND paper=1 AND broker NOT LIKE '%bybit%'
          AND pnl_usd != 0
          AND source NOT IN ('backtest','pre_v10_contaminated','bybit_paper')
        GROUP BY symbol ORDER BY total_pnl DESC
    """, (LAUNCH_DATE,))

def get_signal_bayesian_stats():
    """Signal stats from live Bayesian learning loop."""
    return _q("""
        SELECT signal_name, regime, fires, wins, losses,
               win_rate, bayesian_pts, prior_pts, total_pnl, avg_pnl, last_updated
        FROM signal_stats
        WHERE regime = 'any'
        ORDER BY fires DESC, bayesian_pts DESC
    """)

def get_signal_stats_by_regime():
    return _q("""
        SELECT signal_name, regime, fires, wins, win_rate, bayesian_pts, prior_pts
        FROM signal_stats
        WHERE regime != 'any'
        ORDER BY fires DESC
    """)

def get_ml_status():
    r = _q1("""
        SELECT COUNT(*) AS snap_count FROM trade_features
    """)
    return {
        "snapshots": r.get("snap_count") or 0,
        "min_needed": 30,
    }

def get_retrain_queue():
    return _q("""
        SELECT pair_key, direction, trade_count, requested_at, status
        FROM ml_retrain_queue ORDER BY requested_at DESC LIMIT 10
    """)

def get_recent_events(limit=25):
    return _q("""
        SELECT ts, level, source, message FROM system_events
        WHERE source NOT IN ('IBKRBroker')
        ORDER BY rowid DESC LIMIT ?
    """, (limit,))

def get_system_events_all(limit=50):
    return _q("SELECT ts, level, source, message FROM system_events ORDER BY rowid DESC LIMIT ?", (limit,))


# ═══════════════════════════════════════════════════════════════════════════════
# BOT LOG PARSING
# ═══════════════════════════════════════════════════════════════════════════════

def _tail_log(n=600):
    try:
        with open(LOG_PATH, "r") as f:
            return f.readlines()[-n:]
    except Exception:
        return []

def get_scan_status():
    lines = _tail_log(800)
    result = {"age_s": 9999, "count": 0, "candidates": [], "steps": [],
              "duration_s": 0.0, "balance": 0.0, "deployed": 0.0}

    # Find last Complete line
    complete_idx = None
    for i in range(len(lines)-1, -1, -1):
        if "[scanner] Complete:" in lines[i]:
            complete_idx = i
            break
    if complete_idx is None:
        return result

    # Age
    m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", lines[complete_idx])
    if m:
        try:
            dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            result["age_s"] = int((datetime.now() - dt).total_seconds())
        except Exception:
            pass

    # Candidate count + duration
    cm = re.search(r"Complete:\s*(\d+)\s*candidates\s*in\s*([\d.]+)s", lines[complete_idx])
    if cm:
        result["count"]      = int(cm.group(1))
        result["duration_s"] = float(cm.group(2))

    # Candidates
    cand_re = re.compile(
        r"→\s+(\S+)\s+(LONG|SHORT)\s+spike=([\d.]+)\s+adx=([\d.]+)\s+ev=\$([\d.]+)\s+funding=([-\d.]+)%"
    )
    for line in lines[complete_idx+1 : complete_idx+20]:
        c = cand_re.search(line)
        if c:
            result["candidates"].append({
                "symbol": c.group(1), "direction": c.group(2),
                "vol_spike": float(c.group(3)), "adx": float(c.group(4)),
                "ev": float(c.group(5)), "funding_pct": float(c.group(6)),
            })

    # Pipeline steps (look backwards from complete_idx for Step lines)
    step_re = re.compile(r"\[scanner\] Step (\d+)[^:]*:\s*(\d+)\s*→\s*(\d+)")
    start_re = re.compile(r"\[scanner\] Starting")
    steps = {}
    for i in range(complete_idx, max(0, complete_idx-30), -1):
        s = step_re.search(lines[i])
        if s:
            steps[int(s.group(1))] = {"in": int(s.group(2)), "out": int(s.group(3)),
                                       "raw": lines[i].split("[scanner]")[-1].strip()}
    result["steps"] = [steps[k] for k in sorted(steps.keys())]

    # Balance + deployed from v10 scan line
    scan_re = re.compile(r"\[v10\] scan:.*balance=\$([\d.]+)\s+deployed=\$([\d.]+)")
    for line in lines[complete_idx : complete_idx+5]:
        sm = scan_re.search(line)
        if sm:
            result["balance"]  = float(sm.group(1))
            result["deployed"] = float(sm.group(2))
            break

    return result

def get_bot_activity(n=35):
    lines = _tail_log(500)
    events = []
    for line in reversed(lines):
        line = line.strip()
        # Include v10, scanner, perps, risk lines; skip ib_insync noise
        if not any(x in line for x in ("[v10]", "[scanner]", "[perps]", "[risk]", "[wft]", "[learning]")):
            continue
        if any(x in line for x in ("ib_insync", "IBKRBroker", "Connecting to", "Disconnecting")):
            continue

        ts_m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        ts   = ts_m.group(1) if ts_m else ""

        # Extract module tag
        mod_m = re.search(r"\[(v10|scanner|perps|risk|wft|learning)\]", line)
        mod   = mod_m.group(1) if mod_m else "sys"

        # Classify
        if "ENTERED" in line:
            kind = "ENTERED"
        elif "ENTRY SIGNAL" in line:
            kind = "SIGNAL"
        elif "ECONOMICS VETO" in line or "VETO" in line.upper():
            kind = "VETO"
        elif "PAPER LONG" in line or "PAPER SHORT" in line:
            kind = "OPEN"
        elif "PAPER CLOSE" in line or "close partial" in line.lower():
            kind = "CLOSE"
        elif "Complete:" in line and "candidates" in line:
            kind = "SCAN"
        elif "TIER 1" in line:
            kind = "TIER1"
        elif "TIER 2" in line:
            kind = "TIER2"
        elif "Bayesian adj" in line:
            kind = "BAYES"
        elif "score=" in line and ("< 50" in line or "< threshold" in line or "skip" in line.lower()):
            kind = "SKIP"
        elif "ERROR" in line.upper() or "fatal" in line.lower():
            kind = "ERROR"
        elif "training on" in line.lower() or "retrain" in line.lower():
            kind = "ML"
        else:
            kind = "INFO"

        # Extract symbol (PF_ or USDT suffix)
        sym_m = re.search(r"\b(PF_\w+|\w+USDT|\w+USD)\b", line.split("]",2)[-1])
        symbol = sym_m.group(1) if sym_m else ""

        # Clean message
        msg = re.sub(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+\s+\S+\s+\S+\s+", "", line)
        msg = msg[:150]

        events.append({"ts": ts, "kind": kind, "mod": mod, "symbol": symbol, "msg": msg})
        if len(events) >= n:
            break
    return events

def get_last_scan_age():
    for line in reversed(_tail_log(400)):
        if "[v10] scan:" in line:
            m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
            if m:
                try:
                    dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    return int((datetime.now() - dt).total_seconds())
                except Exception:
                    pass
    return 9999


# ═══════════════════════════════════════════════════════════════════════════════
# HTML PRIMITIVES
# ═══════════════════════════════════════════════════════════════════════════════

def card(content, padding="18px 20px", border_left="", extra_style=""):
    bl = f"border-left:3px solid {border_left};" if border_left else ""
    return (
        f'<div style="background:{CARD};border:1px solid {BORDER};{bl}'
        f'border-radius:12px;padding:{padding};margin-bottom:8px;{extra_style}">'
        f'{content}</div>'
    )

def label(txt):
    return (
        f'<div style="font-size:10px;font-weight:700;letter-spacing:2.5px;'
        f'text-transform:uppercase;color:{TEXT3};margin-bottom:5px;">{txt}</div>'
    )

def big_num(val, color=TEXT, size=34):
    return (
        f'<div style="font-size:{size}px;font-weight:900;line-height:1;'
        f'font-family:{MONO};color:{color};">{val}</div>'
    )

def sub(txt, color=TEXT2):
    return f'<div style="font-size:11px;color:{color};margin-top:5px;">{txt}</div>'

def badge(txt, color=BLUE, bg=None):
    bg = bg or color + "22"
    return (
        f'<span style="background:{bg};color:{color};border-radius:5px;'
        f'padding:2px 8px;font-size:10px;font-weight:800;letter-spacing:.3px;">{txt}</span>'
    )

def row_kv(k, v, v_color=TEXT2, mono=False):
    fc = f"font-family:{MONO};" if mono else ""
    return (
        f'<div style="display:flex;justify-content:space-between;align-items:center;'
        f'padding:7px 0;border-bottom:1px solid {BORDER}22;">'
        f'<span style="font-size:12px;color:{TEXT2};">{k}</span>'
        f'<span style="font-size:12px;font-weight:700;color:{v_color};{fc}">{v}</span></div>'
    )

def section_header(txt):
    st.markdown(
        f'<div style="font-size:10px;font-weight:700;letter-spacing:2.5px;'
        f'text-transform:uppercase;color:{TEXT3};padding:16px 0 8px 0;">{txt}</div>',
        unsafe_allow_html=True,
    )

def pnl_color(v):
    return GREEN if v > 0 else (RED if v < 0 else TEXT3)

def fmt_pnl(v):
    s = "+" if v > 0 else ""
    return f"{s}${v:,.2f}"

def time_ago(ts_str):
    try:
        ts_str = ts_str.replace("T"," ").split(".")[0].split("+")[0]
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(ts_str[:19], fmt)
                break
            except Exception:
                continue
        secs = int((datetime.now() - dt).total_seconds())
        if secs < 60:   return f"{secs}s ago"
        if secs < 3600: return f"{secs//60}m ago"
        if secs < 86400:return f"{secs//3600}h {(secs%3600)//60}m ago"
        return f"{secs//86400}d ago"
    except Exception:
        return ts_str[:16] if ts_str else "–"

def parse_notes(notes):
    """Extract score, regime, setup from trade notes field."""
    if not notes:
        return {}
    result = {}
    m = re.search(r"score=([\d.]+)", notes)
    if m: result["score"] = float(m.group(1))
    m = re.search(r"regime=(\w+)", notes)
    if m: result["regime"] = m.group(1)
    m = re.search(r"setup=(\S+)", notes)
    if m and m.group(1): result["setup"] = m.group(1)
    m = re.search(r"lev=(\d+)x", notes)
    if m: result["lev"] = int(m.group(1))
    m = re.search(r"reason=(\S+)", notes)
    if m: result["reason"] = m.group(1)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# TOP STATUS BAR
# ═══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=5)
def render_topbar():
    scan_age = get_last_scan_age()
    today    = get_today_pnl()
    acct, paper = get_account()
    stats    = get_performance_stats()
    mode_badge = f'<span style="background:{GREEN}22;color:{GREEN};border-radius:5px;padding:2px 8px;font-size:10px;font-weight:800;">PAPER</span>'
    scan_col = GREEN if scan_age < 360 else (AMBER if scan_age < 600 else RED)
    tc       = pnl_color(today)

    st.markdown(
        f'<div style="display:flex;align-items:center;justify-content:space-between;'
        f'background:{SURFACE};border:1px solid {BORDER};border-radius:10px;'
        f'padding:9px 18px;margin-bottom:14px;">'

        f'<div style="display:flex;align-items:center;gap:10px;">'
        f'<div class="live-dot"></div>'
        f'<span style="font-weight:800;font-size:14px;color:{TEXT};letter-spacing:.5px;">WAR ROOM</span>'
        f'<span style="color:{TEXT3};">·</span>'
        f'{mode_badge}'
        f'<span style="color:{TEXT3};font-size:12px;">v10.1</span>'
        f'</div>'

        f'<div style="display:flex;gap:28px;font-size:12px;align-items:center;">'

        f'<div style="text-align:center;">'
        f'<div style="color:{TEXT3};font-size:10px;letter-spacing:1.5px;text-transform:uppercase;">Scanner</div>'
        f'<div style="color:{scan_col};font-weight:700;font-family:{MONO};">'
        f'{"LIVE" if scan_age < 30 else f"{scan_age}s ago"}</div>'
        f'</div>'

        f'<div style="text-align:center;">'
        f'<div style="color:{TEXT3};font-size:10px;letter-spacing:1.5px;text-transform:uppercase;">Today P&L</div>'
        f'<div style="color:{tc};font-weight:700;font-family:{MONO};">{fmt_pnl(today)}</div>'
        f'</div>'

        f'<div style="text-align:center;">'
        f'<div style="color:{TEXT3};font-size:10px;letter-spacing:1.5px;text-transform:uppercase;">Win Rate</div>'
        f'<div style="color:{GREEN if stats["win_rate"]>=52 else (AMBER if stats["win_rate"]>=45 else RED)};'
        f'font-weight:700;font-family:{MONO};">{stats["win_rate"]:.1f}%</div>'
        f'</div>'

        f'<div style="text-align:center;">'
        f'<div style="color:{TEXT3};font-size:10px;letter-spacing:1.5px;text-transform:uppercase;">Account</div>'
        f'<div style="color:{TEXT};font-weight:700;font-family:{MONO};">${acct:,.0f}</div>'
        f'</div>'

        f'<div style="color:{TEXT3};font-family:{MONO};font-size:11px;">'
        f'{datetime.now().strftime("%H:%M:%S")}</div>'

        f'</div></div>',
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — WAR ROOM
# ═══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=10)
def render_war_room():
    acct, paper = get_account()
    stats  = get_performance_stats()
    open_p = get_open_positions()
    scan   = get_scan_status()
    pnl    = stats["total_pnl"]

    # ── Hero metrics ──────────────────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)
    metrics = [
        (c1, "Total P&L", fmt_pnl(pnl), pnl_color(pnl), f"since {LAUNCH_DATE}"),
        (c2, "Win Rate", f"{stats['win_rate']:.1f}%",
         GREEN if stats['win_rate']>=52 else (AMBER if stats['win_rate']>=45 else RED),
         f"{stats['wins']}W · {stats['losses']}L · {stats['closes']} closed"),
        (c3, "Profit Factor",
         f"{stats['profit_factor']:.2f}" if stats['profit_factor'] != float("inf") else "∞",
         GREEN if stats['profit_factor']>=1.35 else (AMBER if stats['profit_factor']>=1.0 else RED),
         f"+${stats['gross_wins']:.2f} / −${stats['gross_losses']:.2f}"),
        (c4, "Open Positions", str(len(open_p)), BLUE if open_p else TEXT3,
         f"deployed ${scan.get('deployed', 0):.0f}"),
        (c5, "Scan Age",
         f"{scan['age_s']}s" if scan['age_s'] < 9999 else "–",
         GREEN if scan['age_s']<360 else (AMBER if scan['age_s']<600 else RED),
         f"{scan['count']} candidates · {scan['duration_s']:.1f}s"),
    ]
    for col, lbl, val, col_v, sub_txt in metrics:
        with col:
            st.markdown(card(label(lbl) + big_num(val, col_v) + sub(sub_txt)), unsafe_allow_html=True)

    # ── Open positions ────────────────────────────────────────────────────────
    section_header(f"OPEN POSITIONS  ({len(open_p)})")
    if not open_p:
        st.markdown(
            card(f'<div style="text-align:center;padding:18px;color:{TEXT3};">'
                 f'No open positions — scanner running every 5 min</div>'),
            unsafe_allow_html=True,
        )
    else:
        cols = st.columns(min(len(open_p), 3))
        for i, p in enumerate(open_p):
            entry  = float(p.get("entry") or 0)
            stop   = float(p.get("stop") or 0)
            target = float(p.get("target") or 0)
            dirn   = p.get("direction", "LONG")
            symbol = p.get("symbol", "")
            reason = p.get("entry_reason", "")
            age_s  = time_ago(p.get("ts_entry", ""))
            dir_col  = GREEN if dirn == "LONG" else RED
            stop_pct = abs(entry - stop) / entry * 100 if entry else 0
            tp_pct   = abs(target - entry) / entry * 100 if entry else 0
            rr       = tp_pct / stop_pct if stop_pct > 0 else 0

            with cols[i % 3]:
                st.markdown(
                    card(
                        f'<div style="display:flex;justify-content:space-between;margin-bottom:10px;">'
                        f'<div style="font-size:16px;font-weight:900;color:{TEXT};font-family:{MONO};">{symbol}</div>'
                        f'<div style="display:flex;gap:6px;align-items:center;">'
                        f'{badge("▲ " + dirn if dirn=="LONG" else "▼ "+dirn, dir_col)}'
                        f'<span style="font-size:10px;color:{TEXT3};">{age_s}</span>'
                        f'</div></div>'

                        f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:10px;">'
                        f'<div>{label("Entry")}<div style="font-size:13px;font-weight:700;color:{TEXT};font-family:{MONO};">{entry:.5g}</div></div>'
                        f'<div>{label("Stop")}<div style="font-size:13px;font-weight:700;color:{RED};font-family:{MONO};">{stop:.5g}<span style="font-size:9px;"> −{stop_pct:.2f}%</span></div></div>'
                        f'<div>{label("Target")}<div style="font-size:13px;font-weight:700;color:{GREEN};font-family:{MONO};">{target:.5g}<span style="font-size:9px;"> +{tp_pct:.2f}%</span></div></div>'
                        f'</div>'

                        f'<div style="display:flex;justify-content:space-between;'
                        f'border-top:1px solid {BORDER};padding-top:8px;font-size:11px;">'
                        f'<span style="color:{TEXT3};">R:R <span style="color:{TEXT};font-weight:700;font-family:{MONO};">{rr:.1f}×</span></span>'
                        f'<span style="color:{TEXT3};">{reason[:40] if reason else "–"}</span>'
                        f'</div>',
                        border_left=dir_col,
                    ),
                    unsafe_allow_html=True,
                )

    # ── Live activity feed ────────────────────────────────────────────────────
    section_header("LIVE BRAIN ACTIVITY")

    scan_interval = 300
    age = scan["age_s"] if scan["age_s"] < 9999 else 0
    bar_pct = min(100, int(age / scan_interval * 100))
    next_s  = max(0, scan_interval - age)
    status_col  = GREEN if age < 30 else (GREEN if age < scan_interval + 30 else RED)
    status_txt  = "SCANNING NOW" if age < 30 else (f"NEXT SCAN ~{next_s}s" if age < scan_interval+30 else f"STALE {age}s")

    st.markdown(
        f'<div style="background:{CARD};border:1px solid {BORDER};border-radius:12px;'
        f'padding:14px 18px;margin-bottom:10px;">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;">'
        f'<div style="display:flex;align-items:center;gap:10px;">'
        f'<div class="live-dot" style="background:{status_col};box-shadow:0 0 8px {status_col}88;"></div>'
        f'<div>'
        f'<div style="font-size:12px;font-weight:800;letter-spacing:2px;color:{status_col};">{status_txt}</div>'
        f'<div style="font-size:10px;color:{TEXT3};margin-top:2px;">Kraken Futures · 7-filter pipeline · 5 min cycle</div>'
        f'</div></div>'
        f'<div style="display:flex;gap:20px;text-align:right;">'
        f'<div><div style="font-size:9px;color:{TEXT3};text-transform:uppercase;letter-spacing:1px;">Balance</div>'
        f'<div style="font-size:16px;font-weight:900;color:{TEXT};font-family:{MONO};">${scan.get("balance",0):,.0f}</div></div>'
        f'<div><div style="font-size:9px;color:{TEXT3};text-transform:uppercase;letter-spacing:1px;">Deployed</div>'
        f'<div style="font-size:16px;font-weight:900;color:{AMBER};font-family:{MONO};">${scan.get("deployed",0):,.0f}</div></div>'
        f'<div><div style="font-size:9px;color:{TEXT3};text-transform:uppercase;letter-spacing:1px;">Candidates</div>'
        f'<div style="font-size:16px;font-weight:900;color:{BLUE};font-family:{MONO};">{scan["count"]}</div></div>'
        f'</div></div>'
        f'<div class="sweep-bar" style="margin-top:10px;">'
        f'<div style="height:100%;width:{bar_pct}%;background:linear-gradient(90deg,{BLUE2},{GREEN});border-radius:2px;"></div>'
        f'</div>'
        f'<div style="display:flex;justify-content:space-between;font-size:9px;color:{TEXT3};margin-top:3px;">'
        f'<span>last scan {age}s ago</span><span>next scan in {next_s}s</span>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    activity = get_bot_activity(30)
    KIND_CFG = {
        "ENTERED": (GREEN,  "⬆ ENTERED"),
        "SIGNAL":  (BLUE,   "◆ SIGNAL"),
        "TIER1":   (GOLD,   "★ TIER 1"),
        "TIER2":   (CYAN,   "◈ TIER 2"),
        "OPEN":    (GREEN2, "↗ OPEN"),
        "CLOSE":   (PURPLE, "↙ CLOSE"),
        "VETO":    (AMBER,  "✕ VETO"),
        "SKIP":    (TEXT3,  "· SKIP"),
        "SCAN":    (TEXT2,  "⟳ SCAN"),
        "BAYES":   (CYAN,   "∿ BAYES"),
        "ERROR":   (RED,    "! ERROR"),
        "ML":      (PURPLE, "⊕ ML"),
        "INFO":    (TEXT3,  "  INFO"),
    }

    if not activity:
        st.markdown(card(f'<div style="color:{TEXT3};text-align:center;padding:16px;">Waiting for bot.log…</div>'), unsafe_allow_html=True)
    else:
        rows = ""
        for ev in activity:
            kind  = ev["kind"]
            color, lbl = KIND_CFG.get(kind, (TEXT3, "  ..."))
            ts    = ev["ts"][11:19] if len(ev["ts"]) >= 19 else ev["ts"]
            msg   = ev["msg"]
            # Skip pure INFO noise
            if kind == "INFO" and rows:
                continue
            bg    = color + "15" if kind in ("ENTERED","TIER1","ERROR","CLOSE","OPEN") else "transparent"
            bl    = f"border-left:2px solid {color};" if kind in ("ENTERED","TIER1","ERROR","SIGNAL","CLOSE") else ""

            rows += (
                f'<div class="fadein" style="display:grid;grid-template-columns:65px 88px 1fr;'
                f'gap:8px;padding:8px 12px;border-bottom:1px solid {BORDER}22;'
                f'background:{bg};{bl}align-items:center;">'
                f'<div style="font-size:10px;color:{TEXT3};font-family:{MONO};">{ts}</div>'
                f'<div style="background:{color}22;color:{color};border-radius:4px;'
                f'padding:2px 6px;font-size:9px;font-weight:800;white-space:nowrap;">{lbl}</div>'
                f'<div style="font-size:11px;color:{TEXT if kind in ("ENTERED","TIER1","SIGNAL","CLOSE") else TEXT2};'
                f'font-family:{MONO};overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{msg}</div>'
                f'</div>'
            )
        st.markdown(
            f'<div style="background:{CARD};border:1px solid {BORDER};border-radius:12px;'
            f'overflow:hidden;max-height:420px;overflow-y:auto;">{rows}</div>',
            unsafe_allow_html=True,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — PERFORMANCE
# ═══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=30)
def render_performance():
    stats   = get_performance_stats()
    acct, _ = get_account()
    trades  = get_trade_log(100)
    eq      = get_equity_curve()
    sym     = get_per_symbol_stats()

    # ── Stats row ─────────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        pnl = stats["total_pnl"]
        pct = pnl / acct * 100 if acct > 0 else 0
        st.markdown(card(
            label("Net P&L") +
            big_num(fmt_pnl(pnl), pnl_color(pnl)) +
            sub(f"{pct:+.2f}% of account · {fmt_pnl(-stats['total_fees'])} fees")
        ), unsafe_allow_html=True)

    with c2:
        wr = stats["win_rate"]
        st.markdown(card(
            label("Win Rate") +
            big_num(f"{wr:.1f}%", GREEN if wr>=52 else (AMBER if wr>=45 else RED)) +
            sub(f"{stats['wins']}W · {stats['losses']}L · {stats['closes']} closed trades")
        ), unsafe_allow_html=True)

    with c3:
        pf = stats["profit_factor"]
        pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"
        st.markdown(card(
            label("Profit Factor") +
            big_num(pf_str, GREEN if pf>=1.35 else (AMBER if pf>=1.0 else RED)) +
            sub(f"need ≥1.35 to go live")
        ), unsafe_allow_html=True)

    with c4:
        needed = 30
        clean  = stats["closes"]
        pct_done = min(100, clean / needed * 100)
        st.markdown(card(
            label("Clean Trades (Paper v10)") +
            big_num(f"{clean} / {needed}", BLUE if clean>=needed else AMBER) +
            sub(f"{pct_done:.0f}% of {needed}-trade minimum for ML · {stats['symbols']} symbols")
        ), unsafe_allow_html=True)

    # ── Equity curve ──────────────────────────────────────────────────────────
    if eq:
        import pandas as pd
        section_header("EQUITY CURVE — CUMULATIVE P&L")
        df = pd.DataFrame(eq)
        df["ts"] = pd.to_datetime(df["ts"].str[:19])
        df = df.rename(columns={"cum_pnl": "Cumulative P&L ($)"})
        st.line_chart(df.set_index("ts")[["Cumulative P&L ($)"]], height=220, use_container_width=True)
    else:
        section_header("EQUITY CURVE")
        st.markdown(card(f'<div style="color:{TEXT3};text-align:center;padding:20px;">No closed trades yet</div>'), unsafe_allow_html=True)

    # ── Per-symbol breakdown ──────────────────────────────────────────────────
    if sym:
        section_header(f"PER-SYMBOL BREAKDOWN  ({len(sym)} symbols)")
        hdr = (
            f'<div style="display:grid;grid-template-columns:130px 60px 60px 90px 90px 90px 90px;'
            f'gap:8px;padding:7px 14px;font-size:10px;font-weight:700;letter-spacing:1.5px;'
            f'text-transform:uppercase;color:{TEXT3};border-bottom:1px solid {BORDER};">'
            f'<div>Symbol</div><div>Trades</div><div>WR</div>'
            f'<div style="text-align:right">Total P&L</div>'
            f'<div style="text-align:right">Avg P&L</div>'
            f'<div style="text-align:right">Best</div>'
            f'<div style="text-align:right">Worst</div>'
            f'</div>'
        )
        rows = ""
        for s in sym:
            n    = s.get("trades") or 0
            wins = s.get("wins") or 0
            wr   = wins / n * 100 if n > 0 else 0
            pnl  = s.get("total_pnl") or 0
            avg  = s.get("avg_pnl") or 0
            best = s.get("best") or 0
            worst = s.get("worst") or 0
            rows += (
                f'<div style="display:grid;grid-template-columns:130px 60px 60px 90px 90px 90px 90px;'
                f'gap:8px;padding:9px 14px;border-bottom:1px solid {BORDER}22;'
                f'font-size:12px;align-items:center;">'
                f'<div style="font-weight:700;color:{TEXT};font-family:{MONO};">{s["symbol"]}</div>'
                f'<div style="color:{TEXT2};">{n}</div>'
                f'<div style="color:{GREEN if wr>=52 else (AMBER if wr>=45 else RED)};font-weight:700;">{wr:.0f}%</div>'
                f'<div style="text-align:right;font-weight:700;color:{pnl_color(pnl)};font-family:{MONO};">{fmt_pnl(pnl)}</div>'
                f'<div style="text-align:right;color:{pnl_color(avg)};font-family:{MONO};">{fmt_pnl(avg)}</div>'
                f'<div style="text-align:right;color:{GREEN};font-family:{MONO};">{fmt_pnl(best)}</div>'
                f'<div style="text-align:right;color:{RED};font-family:{MONO};">{fmt_pnl(worst)}</div>'
                f'</div>'
            )
        st.markdown(
            f'<div style="background:{CARD};border:1px solid {BORDER};border-radius:12px;overflow:hidden;">'
            f'{hdr}{rows}</div>',
            unsafe_allow_html=True,
        )

    # ── Trade log ─────────────────────────────────────────────────────────────
    section_header(f"TRADE LOG — SINCE {LAUNCH_DATE}")
    closes = [t for t in trades if t.get("pnl_usd") != 0]

    if not closes:
        st.markdown(card(f'<div style="color:{TEXT3};text-align:center;padding:20px;">No closed trades yet</div>'), unsafe_allow_html=True)
        return

    hdr = (
        f'<div style="display:grid;grid-template-columns:90px 130px 70px 70px 90px 100px 80px 1fr;'
        f'gap:8px;padding:7px 14px;font-size:10px;font-weight:700;letter-spacing:1.5px;'
        f'text-transform:uppercase;color:{TEXT3};border-bottom:1px solid {BORDER};">'
        f'<div>Time</div><div>Symbol</div><div>Dir</div><div>Score</div>'
        f'<div style="text-align:right">Price</div>'
        f'<div style="text-align:right">P&L</div>'
        f'<div style="text-align:right">Result</div>'
        f'<div>Setup / Reason</div>'
        f'</div>'
    )
    rows = ""
    for t in closes:
        pnl   = t.get("pnl_usd") or 0
        price = t.get("price") or 0
        notes = parse_notes(t.get("notes", ""))
        score = notes.get("score", "–")
        setup = notes.get("setup", notes.get("reason", "–"))
        action = t.get("action", "")
        dirn  = "LONG" if action == "SELL" else ("SHORT" if action == "BUY" else action)
        dc    = GREEN if dirn == "LONG" else (RED if dirn == "SHORT" else TEXT3)
        rc    = pnl_color(pnl)
        result = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "FLAT")

        rows += (
            f'<div style="display:grid;grid-template-columns:90px 130px 70px 70px 90px 100px 80px 1fr;'
            f'gap:8px;padding:9px 14px;border-bottom:1px solid {BORDER}22;'
            f'font-size:12px;align-items:center;'
            f'background:{pnl_color(pnl)}08 if pnl!=0 else transparent">'
            f'<div style="color:{TEXT3};font-size:10px;">{time_ago(t["ts"])}</div>'
            f'<div style="font-weight:700;color:{TEXT};font-family:{MONO};">{t.get("symbol","")}</div>'
            f'<div>{badge(dirn, dc)}</div>'
            f'<div style="color:{TEXT2};font-family:{MONO};">{score}</div>'
            f'<div style="text-align:right;color:{TEXT2};font-family:{MONO};">${price:.4g}</div>'
            f'<div style="text-align:right;font-weight:700;color:{rc};font-family:{MONO};">{fmt_pnl(pnl)}</div>'
            f'<div style="text-align:right;">{badge(result, rc)}</div>'
            f'<div style="color:{TEXT3};font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{setup}</div>'
            f'</div>'
        )
    st.markdown(
        f'<div style="background:{CARD};border:1px solid {BORDER};border-radius:12px;overflow:hidden;">'
        f'{hdr}{rows}</div>',
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — SIGNAL BRAIN
# ═══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=60)
def render_signal_brain():
    bay_stats = get_signal_bayesian_stats()
    ml_status = get_ml_status()

    col_left, col_right = st.columns([3, 2])

    # ── Left: Bayesian Signal Learning Table ──────────────────────────────────
    with col_left:
        section_header(f"BAYESIAN SIGNAL LEARNING  ({len(bay_stats)} signals tracked)")
        st.markdown(
            f'<div style="font-size:11px;color:{TEXT3};margin-bottom:10px;">'
            f'Posterior win rates update after every closed trade. '
            f'PRIOR_N=20 phantom trades. Weights apply after MIN_FIRES=10. '
            f'Bayesian pts = prior_pts × (posterior_wr / prior_wr). Cap 2.5× prior.</div>',
            unsafe_allow_html=True,
        )

        if not bay_stats:
            st.markdown(card(f'<div style="color:{TEXT3};text-align:center;padding:16px;">No signal data yet — accumulates with live trades</div>'), unsafe_allow_html=True)
        else:
            hdr = (
                f'<div style="display:grid;grid-template-columns:160px 55px 55px 65px 80px 80px 80px;'
                f'gap:6px;padding:7px 14px;font-size:10px;font-weight:700;letter-spacing:1.5px;'
                f'text-transform:uppercase;color:{TEXT3};border-bottom:1px solid {BORDER};">'
                f'<div>Signal</div><div>Fires</div><div>WR</div><div>Trend</div>'
                f'<div style="text-align:right">Prior Pts</div>'
                f'<div style="text-align:right">Bayes Pts</div>'
                f'<div style="text-align:right">Avg P&L</div>'
                f'</div>'
            )
            rows = ""
            for s in bay_stats:
                fires  = s.get("fires") or 0
                wr     = (s.get("win_rate") or 0) * 100
                bpts   = s.get("bayesian_pts") or 0
                ppts   = s.get("prior_pts") or 0
                avg_p  = s.get("avg_pnl") or 0
                drift  = bpts - ppts
                drift_c = GREEN if drift > 0 else (RED if drift < -0.5 else TEXT3)
                drift_s = f"+{drift:.1f}" if drift > 0 else f"{drift:.1f}"
                wr_c   = GREEN if wr >= 55 else (AMBER if wr >= 40 else RED)
                live   = fires >= 10
                live_c = GREEN if live else TEXT3
                live_s = "LIVE" if live else f"({fires}/10)"
                trend_s = "↑ UP" if drift > 0.5 else ("↓ DOWN" if drift < -0.5 else "→ FLAT")
                trend_c = GREEN if drift > 0.5 else (RED if drift < -0.5 else TEXT3)

                rows += (
                    f'<div style="display:grid;grid-template-columns:160px 55px 55px 65px 80px 80px 80px;'
                    f'gap:6px;padding:8px 14px;border-bottom:1px solid {BORDER}22;'
                    f'font-size:12px;align-items:center;">'
                    f'<div style="font-weight:600;color:{TEXT};font-family:{MONO};font-size:11px;">{s["signal_name"]}</div>'
                    f'<div><span style="color:{live_c};font-size:10px;font-weight:700;">{live_s}</span></div>'
                    f'<div style="color:{wr_c};font-weight:700;">{wr:.0f}%</div>'
                    f'<div style="color:{trend_c};font-size:11px;font-weight:700;">{trend_s}</div>'
                    f'<div style="text-align:right;color:{TEXT2};font-family:{MONO};">{ppts:.1f}</div>'
                    f'<div style="text-align:right;color:{drift_c};font-weight:700;font-family:{MONO};">'
                    f'{bpts:.1f} <span style="font-size:9px;">({drift_s})</span></div>'
                    f'<div style="text-align:right;color:{pnl_color(avg_p)};font-family:{MONO};">{fmt_pnl(avg_p)}</div>'
                    f'</div>'
                )
            st.markdown(
                f'<div style="background:{CARD};border:1px solid {BORDER};border-radius:12px;overflow:hidden;">'
                f'{hdr}{rows}</div>',
                unsafe_allow_html=True,
            )

        # ── Technical Scoring — Long Tower ────────────────────────────────────
        section_header("TECHNICAL TOWER — LONG SCORING (live values)")
        long_signals = [
            ("CVD bullish divergence",         "+25", BLUE,   True),
            ("MACD all variants aligned long",  "+20", BLUE,   True),
            ("TradingView signal confirmed",    "+20", GOLD,   False),
            ("RSI bullish divergence",          "+15", GREEN,  True),
            ("Funding squeeze (<−0.3 norm)",    "+15", GREEN,  True),
            ("VWAP reclaim on volume",          "+15", GREEN,  True),
            ("Liq cascade → long magnet",       "+15", GREEN,  False),
            ("WaveTrend oversold cross",        "+12", CYAN,   True),
            ("SuperTrend bullish (ATR10 ×3)",   "+12", CYAN,   True),
            ("WAE Bullish + Exploding",         "+10", CYAN,   True),
            ("OB L5 imbalance > 0.60",          "+10", TEXT2,  True),
            ("Williams %R < −80",               "+10", TEXT2,  True),
            ("Whale accumulation",              "+10", TEXT2,  False),
            ("Options skew bullish",            "+10", TEXT2,  False),
            ("MACD fast histogram positive",    " +8", TEXT2,  False),
            ("Funding favorable (−0.1 to −0.3)"," +8", TEXT2,  True),
            ("KST above signal line",           " +8", CYAN,   True),
            ("Fisher Transform cross up",       " +8", CYAN,   True),
            ("Ichimoku cloud bullish",          " +8", CYAN,   True),
            ("Laguerre RSI < 0.15 (deep)",      " +8", TEXT2,  True),
            ("OB L5 imbalance 0.55–0.60",       " +5", TEXT3,  False),
            ("Williams %R −80 to −70",          " +5", TEXT3,  False),
            ("Vol spike > 1.5×",                " +5", TEXT3,  True),
            ("RSI not overbought (<60)",         " +5", TEXT3,  True),
            ("Choppiness trending (< 38.2)",    " +5", TEXT3,  True),
            ("WAE Bullish only (no explosion)", " +5", TEXT3,  True),
            ("Laguerre RSI < 0.25",             " +4", TEXT3,  True),
            ("Price > 2σ VWAP",                 "−25", RED,    False),
            ("CVD bearish divergence",          "−20", RED,    False),
            ("Extreme positive funding (>0.5)", "−20", RED,    False),
            ("RSI bearish divergence",          "−15", RED,    False),
            ("Whale distributing",              "−15", RED,    False),
            ("Cascade risk > 0.70",             "−15", RED,    False),
            ("OB L5 < 0.40 (bear pressure)",    "−10", AMBER,  False),
            ("Price 1–2σ above VWAP",           "−10", AMBER,  False),
            ("High funding 0.3–0.5",            "−10", AMBER,  False),
            ("Fear & Greed euphoria (>85)",     "−10", AMBER,  False),
        ]
        rows = "".join(
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:6px 0;border-bottom:1px solid {BORDER}22;">'
            f'<span style="font-size:11px;color:{TEXT2 if c!=RED and c!=AMBER else c};">'
            f'{"★ " if indicator else "  "}{sig}</span>'
            f'<span style="font-size:12px;font-weight:800;color:{c};font-family:{MONO};">{pts}</span>'
            f'</div>'
            for sig, pts, c, indicator in long_signals
        )
        st.markdown(
            card(f'<div style="font-size:9px;color:{TEXT3};margin-bottom:10px;">Raw range: ~−115 to +150 · normalized 0-100 · ★ = uses live indicators</div>' + rows),
            unsafe_allow_html=True,
        )

    # ── Right: Tier 1 Setups + ML + Regime ───────────────────────────────────
    with col_right:
        section_header("TIER 1 SETUPS — AUTO ENTRY (any regime)")
        try:
            from signal_engine import _LONG_SETUPS, _SHORT_SETUPS, _ENTRY_THRESHOLDS
            for direction, setups, dc in [("LONG", _LONG_SETUPS, GREEN), ("SHORT", _SHORT_SETUPS, RED)]:
                st.markdown(
                    f'<div style="font-size:10px;font-weight:700;letter-spacing:1.5px;'
                    f'text-transform:uppercase;color:{dc};margin:8px 0 4px 0;">{direction}</div>',
                    unsafe_allow_html=True,
                )
                for setup in setups:
                    label_txt = setup.get("label", setup.get("name", "–"))
                    conditions = []
                    # Inspect setup condition lambda variables if available
                    cond_txt = setup.get("description", "")
                    st.markdown(
                        card(
                            f'<div style="font-size:12px;font-weight:700;color:{TEXT};margin-bottom:4px;">{label_txt}</div>'
                            f'<div style="font-size:10px;color:{TEXT3};">Tier 1 · full size · enter regardless of composite</div>'
                        ),
                        unsafe_allow_html=True,
                    )
        except Exception as e:
            st.error(f"signal_engine: {e}")

        section_header("ENTRY THRESHOLDS BY REGIME (Tier 2)")
        try:
            from signal_engine import _ENTRY_THRESHOLDS
            rows = "".join(
                row_kv(regime, f"≥ {thresh} / 100",
                       GREEN if regime in ("RANGING","TRENDING_UP") else TEXT2, True)
                for regime, thresh in sorted(_ENTRY_THRESHOLDS.items(), key=lambda x: x[1])
            )
            rows += row_kv("Tier 2 size mult", "0.75× position", AMBER, False)
            rows += row_kv("Tier 1 size mult", "1.0× position", GREEN, False)
            st.markdown(card(rows), unsafe_allow_html=True)
        except Exception as e:
            st.error(f"thresholds: {e}")

        section_header("ML TOWER STATUS")
        snaps   = ml_status["snapshots"]
        needed  = ml_status["min_needed"]
        active  = snaps >= needed
        ml_col  = GREEN if active else AMBER
        ml_msg  = "ACTIVE — models being trained" if active else f"ACCUMULATING — {snaps}/{needed} snapshots"
        st.markdown(
            card(
                label("57-Feature Snapshots") +
                big_num(str(snaps), ml_col, size=28) +
                sub(ml_msg, ml_col) +
                f'<div style="height:8px;background:{BORDER};border-radius:4px;margin-top:10px;overflow:hidden;">'
                f'<div style="height:100%;width:{min(100,snaps/needed*100):.0f}%;'
                f'background:linear-gradient(90deg,{BLUE},{GREEN});border-radius:4px;"></div>'
                f'</div>'
                f'<div style="font-size:10px;color:{TEXT3};margin-top:4px;">'
                f'Stored per entry · joined at retrain · 57 features across 11 groups</div>'
            ),
            unsafe_allow_html=True,
        )
        st.markdown(
            card(
                row_kv("ML tower weight", "20% of composite", TEXT2) +
                row_kv("Technical tower weight", "80% of composite", TEXT2) +
                row_kv("Bayesian overlay", "15% blend post-score", CYAN) +
                row_kv("XGBoost weight", "60% of ML ensemble", TEXT2) +
                row_kv("LightGBM weight", "40% of ML ensemble", TEXT2) +
                row_kv("Training protocol", "Walk-forward 60d train / 10d val", TEXT2) +
                row_kv("Pass criteria", "WR≥54% · PF≥1.35 · Sharpe≥0.8", TEXT2) +
                row_kv("Features", "57 across 11 groups (price/vol/CVD/...)", TEXT2)
            ),
            unsafe_allow_html=True,
        )

        section_header("CHOP INDEX GATING")
        st.markdown(
            card(
                row_kv("CHOP < 38.2", "Trending → all momentum setups ON", GREEN) +
                row_kv("CHOP > 61.8", "Ranging → momentum setups BLOCKED", AMBER) +
                row_kv("CHOP > 61.8", "ranging_mr_long/short setup ACTIVE", CYAN) +
                row_kv("Ranging MR threshold", "VWAP dist ≥ ±0.30%", TEXT2) +
                row_kv("WAE/Squeeze min hold", "30 min before thesis check", TEXT2) +
                row_kv("Ranging MR min hold", "15 min before thesis check", TEXT2)
            ),
            unsafe_allow_html=True,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — SCANNER
# ═══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=15)
def render_scanner():
    scan = get_scan_status()
    age  = scan["age_s"]
    candidates = scan["candidates"]
    steps = scan["steps"]

    # ── Scan header ───────────────────────────────────────────────────────────
    sc = GREEN if age < 360 else (AMBER if age < 600 else RED)
    st.markdown(
        f'<div style="background:{CARD};border:1px solid {BORDER};border-radius:12px;'
        f'padding:16px 20px;margin-bottom:12px;">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;">'
        f'<div>'
        f'<div style="font-size:10px;color:{TEXT3};text-transform:uppercase;letter-spacing:2px;margin-bottom:6px;">KRAKEN FUTURES SCANNER</div>'
        f'<div style="font-size:26px;font-weight:900;color:{sc};font-family:{MONO};">'
        f'{"SCANNING NOW" if age<15 else f"{age}s since last scan"}</div>'
        f'<div style="font-size:11px;color:{TEXT2};margin-top:4px;">'
        f'7-filter pipeline · runs every 5 min · {scan["duration_s"]:.1f}s runtime · top 15 returned</div>'
        f'</div>'
        f'<div style="display:flex;gap:20px;text-align:right;">'
        f'<div>{label("Candidates")}<div style="font-size:40px;font-weight:900;color:{BLUE if candidates else TEXT3};font-family:{MONO};line-height:1;">{scan["count"]}</div></div>'
        f'<div>{label("Balance")}<div style="font-size:22px;font-weight:900;color:{TEXT};font-family:{MONO};line-height:1;">${scan.get("balance",0):,.0f}</div></div>'
        f'<div>{label("Deployed")}<div style="font-size:22px;font-weight:900;color:{AMBER};font-family:{MONO};line-height:1;">${scan.get("deployed",0):,.0f}</div></div>'
        f'</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # ── Last scan candidates ──────────────────────────────────────────────────
    if candidates:
        section_header(f"CANDIDATES FROM LAST SCAN  ({len(candidates)})")
        for c in candidates:
            dirn   = c["direction"]
            dc     = GREEN if dirn == "LONG" else RED
            spike  = c["vol_spike"]
            adx    = c["adx"]
            ev     = c["ev"]
            fund   = c["funding_pct"]
            fund_c = RED if fund > 0.05 else (GREEN if fund < -0.05 else TEXT2)

            spike_c = GREEN if spike >= 2.0 else (AMBER if spike >= 1.2 else TEXT2)
            adx_c   = GREEN if adx >= 40  else (AMBER if adx >= 20 else TEXT2)
            ev_c    = GREEN if ev >= 5.0  else (AMBER if ev >= 0.5 else RED)

            st.markdown(
                card(
                    f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                    f'<div style="display:flex;align-items:center;gap:12px;">'
                    f'<div style="font-size:18px;font-weight:900;color:{TEXT};font-family:{MONO};">{c["symbol"]}</div>'
                    f'{badge(dirn, dc)}'
                    f'</div>'
                    f'<div style="display:flex;gap:28px;">'
                    f'<div style="text-align:center;">{label("Vol Spike")}<div style="font-size:18px;font-weight:900;color:{spike_c};font-family:{MONO};">{spike:.2f}×</div></div>'
                    f'<div style="text-align:center;">{label("ADX (15m)")}<div style="font-size:18px;font-weight:900;color:{adx_c};font-family:{MONO};">{adx:.0f}</div></div>'
                    f'<div style="text-align:center;">{label("EV / Trade")}<div style="font-size:18px;font-weight:900;color:{ev_c};font-family:{MONO};">${ev:.2f}</div></div>'
                    f'<div style="text-align:center;">{label("Funding /8h")}<div style="font-size:18px;font-weight:900;color:{fund_c};font-family:{MONO};">{fund:.4f}%</div></div>'
                    f'</div></div>',
                    border_left=dc,
                ),
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            card(f'<div style="text-align:center;padding:20px;color:{TEXT3};">'
                 f'No candidates this cycle — all tickers filtered out by pipeline<br>'
                 f'<span style="font-size:11px;">Common: vol spike < 1.2×, ADX < 20, OB depth < $10K, EV too low</span>'
                 f'</div>'),
            unsafe_allow_html=True,
        )

    # ── Pipeline steps ────────────────────────────────────────────────────────
    section_header("FILTER PIPELINE — LAST SCAN (live step-by-step output)")
    if steps:
        for i, step in enumerate(steps):
            drop = step["in"] - step["out"]
            keep_pct = step["out"] / step["in"] * 100 if step["in"] > 0 else 100
            dc   = GREEN if drop == 0 else (AMBER if drop <= 3 else RED)
            st.markdown(
                f'<div style="display:flex;gap:12px;align-items:center;padding:10px 14px;'
                f'background:{CARD};border:1px solid {BORDER};border-radius:10px;margin-bottom:6px;">'
                f'<div style="min-width:28px;height:28px;border-radius:50%;'
                f'background:{BLUE}22;border:1px solid {BLUE};display:flex;align-items:center;'
                f'justify-content:center;font-size:12px;font-weight:800;color:{BLUE};">{i+1}</div>'
                f'<div style="flex:1;">'
                f'<div style="font-size:12px;font-weight:600;color:{TEXT};">{step["raw"]}</div>'
                f'</div>'
                f'<div style="display:flex;gap:16px;text-align:right;">'
                f'<div>{label("In")}<div style="font-size:16px;font-weight:900;color:{TEXT2};font-family:{MONO};">{step["in"]}</div></div>'
                f'<div>{label("Out")}<div style="font-size:16px;font-weight:900;color:{GREEN};font-family:{MONO};">{step["out"]}</div></div>'
                f'<div>{label("Dropped")}<div style="font-size:16px;font-weight:900;color:{dc};font-family:{MONO};">{drop}</div></div>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
    else:
        st.markdown(card(f'<div style="color:{TEXT3};text-align:center;padding:16px;">No scan step data yet — waiting for next scan</div>'), unsafe_allow_html=True)

    # ── Pipeline config from live scanner ─────────────────────────────────────
    section_header("SCANNER CONFIG — LIVE VALUES")
    try:
        from scanner import (
            _MIN_VOLUME_24H_USD, _MIN_VOL_SPIKE, _MIN_PRICE_MOVE_1H,
            _MIN_ADX_15M, _MIN_OB_DEPTH_USD, _MAX_SPREAD_PCT,
            _MIN_EXPECTED_PROFIT, _ROUND_TRIP_FEE_PCT,
        )
        _MAX_CANDIDATES = 15  # hardcoded in scanner Step 7
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(card(
                row_kv("Step 1 — min volume 24h", f"${_MIN_VOLUME_24H_USD/1e6:.0f}M USD", TEXT2, True) +
                row_kv("Step 2 — vol spike minimum", f"≥ {_MIN_VOL_SPIKE}×", TEXT2, True) +
                row_kv("Step 2 — price move 1h", f"≥ {_MIN_PRICE_MOVE_1H:.1f}%", TEXT2, True) +
                row_kv("Step 2 — ADX 15m", f"≥ {_MIN_ADX_15M}", TEXT2, True) +
                row_kv("Max candidates returned", str(_MAX_CANDIDATES), TEXT2, True)
            ), unsafe_allow_html=True)
        with c2:
            st.markdown(card(
                row_kv("Step 3 — OB depth each side", f"≥ ${_MIN_OB_DEPTH_USD/1e3:.0f}K", AMBER, True) +
                row_kv("Step 3 — max spread", f"< {_MAX_SPREAD_PCT:.2f}%", TEXT2, True) +
                row_kv("Step 4 — min expected profit", f"≥ ${_MIN_EXPECTED_PROFIT:.2f}", TEXT2, True) +
                row_kv("Step 4 — round-trip fee", f"{_ROUND_TRIP_FEE_PCT*100:.3f}%", TEXT2, True) +
                row_kv("Source", "Kraken Futures public REST · no auth · US-accessible", TEXT2)
            ), unsafe_allow_html=True)
    except Exception as e:
        st.error(f"scanner import: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 — SYSTEM CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

def render_system():
    section_header("ECONOMICS GATE — PRE-TRADE EV VETO  (Kraken Futures)")
    try:
        from risk.economics_gate import (
            TAKER_FEE_PCT, ROUND_TRIP_COST,
            _TIER_APLUS_EV, _TIER_A_EV, _TIER_B_EV,
            TIER_MULTIPLIERS, _MIN_NET_RR
        )
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(card(
                row_kv("Taker fee (per side)", f"{TAKER_FEE_PCT*100:.3f}%  (Kraken Futures)", AMBER, True) +
                row_kv("Round-trip cost", f"{ROUND_TRIP_COST*100:.3f}%", AMBER, True) +
                row_kv("Min net R:R", f"≥ {_MIN_NET_RR}:1 after fees", TEXT2, True) +
                row_kv("Ranging R:R floor (1.25×)", f"≥ {_MIN_NET_RR * 1.25:.2f}:1 (CHOP > 61.8)", AMBER, True)
            ), unsafe_allow_html=True)
        with c2:
            tier_rows = ""
            for tier in ["A+", "A", "B", "VETO"]:
                if tier == "VETO":
                    tier_rows += row_kv("Below B → VETO", "Trade blocked", RED)
                    continue
                ev_val = {"A+": _TIER_APLUS_EV, "A": _TIER_A_EV, "B": _TIER_B_EV}[tier]
                mult   = TIER_MULTIPLIERS.get(tier, 1.0)
                tc     = GREEN if tier == "A+" else (CYAN if tier == "A" else AMBER)
                tier_rows += row_kv(
                    f"Tier {tier}  (EV ≥ {ev_val*100:.2f}%)",
                    f"{mult}× size",
                    tc, True
                )
            tier_rows += row_kv("Ranging EV floor (1.67×)", f"≥ {_TIER_B_EV*1.67*100:.3f}% (CHOP > 61.8)", AMBER, True)
            st.markdown(card(tier_rows), unsafe_allow_html=True)
    except Exception as e:
        st.error(f"economics_gate: {e}")

    section_header("POSITION SIZER — UNIFIED 3-FACTOR FORMULA")
    try:
        from risk.unified_sizer import BASE_RISK_PCT, MAX_HEAT_PCT, MAX_SINGLE_NOTIONAL_PCT, _QUALITY_MULT
        from config import ACCOUNT_SIZE
        c1, c2 = st.columns(2)
        with c1:
            base_usd = float(ACCOUNT_SIZE) * BASE_RISK_PCT
            st.markdown(card(
                f'<div style="font-size:11px;color:{CYAN};font-family:{MONO};margin-bottom:12px;'
                f'background:{CYAN}11;border-radius:6px;padding:8px 10px;">'
                f'size = (acct × {BASE_RISK_PCT*100:.1f}% × quality_mult) / stop_pct</div>'
                + row_kv("Account size", f"${float(ACCOUNT_SIZE):,.0f}", TEXT, True)
                + row_kv("Base risk per trade", f"{BASE_RISK_PCT*100:.1f}% = ${base_usd:.0f}", GREEN, True)
                + row_kv("Portfolio heat cap", f"{MAX_HEAT_PCT*100:.0f}% = ${float(ACCOUNT_SIZE)*MAX_HEAT_PCT:.0f} max deployed", AMBER, True)
                + row_kv("Hard position cap", f"{MAX_SINGLE_NOTIONAL_PCT*100:.0f}% per symbol", TEXT2, True)
                + row_kv("Default leverage", "3× ISOLATED margin", TEXT2)
                + row_kv("Max leverage", "10× (strict gates)", RED)
            ), unsafe_allow_html=True)
        with c2:
            q_rows = ""
            for tier, mult in sorted(_QUALITY_MULT.items(), key=lambda x: -x[1]):
                tc = GREEN if mult >= 1.0 else (AMBER if mult >= 0.85 else RED)
                q_rows += row_kv(f"Quality {tier}", f"{mult}×  size", tc, True)
            q_rows += row_kv("Regime TRENDING_UP", "1.00× regime mult", GREEN)
            q_rows += row_kv("Regime RANGING", "0.85× regime mult", AMBER)
            q_rows += row_kv("Regime HIGH_VOL", "0.70× regime mult", RED)
            q_rows += row_kv("RBI incubation mult", "0.25× (new strategies)", TEXT2)
            st.markdown(card(q_rows), unsafe_allow_html=True)
    except Exception as e:
        st.error(f"unified_sizer: {e}")

    section_header("6-PRIORITY EXIT STACK  (highest priority wins)")
    exits = [
        (RED,    "6", "Kill Switch",          f"Balance < 75% of account / API errors / latency", RED),
        (RED,    "5", "Risk Forced Exit",      "Margin breach / portfolio VaR breach / correlation limit", RED),
        (AMBER,  "4", "Hard Stop",             "STOP_MARKET on exchange @ entry − ATR×1.5 · NEVER widened", AMBER),
        (AMBER,  "3", "Thesis Invalidated",    "Current composite < entry_score × 0.45 → close all · 10 min hold gate", AMBER),
        (GREEN,  "2", "Take-Profit Scale-Out", "2R → close 33% · 3.5R → close 33% · remainder trails", GREEN),
        (GREEN,  "1", "Trailing Stop",         "Activates after 1× ATR in favor · trails 1.5× ATR from peak", GREEN),
    ]
    for color, num, title, detail, dc in exits:
        st.markdown(
            f'<div style="display:flex;gap:14px;align-items:flex-start;padding:11px 14px;'
            f'background:{CARD};border:1px solid {BORDER};border-left:3px solid {color};'
            f'border-radius:10px;margin-bottom:6px;">'
            f'<div style="min-width:26px;height:26px;border-radius:50%;background:{color}22;'
            f'border:1px solid {color};display:flex;align-items:center;justify-content:center;'
            f'font-size:12px;font-weight:900;color:{color};">{num}</div>'
            f'<div>'
            f'<div style="font-size:13px;font-weight:800;color:{dc};">{title}</div>'
            f'<div style="font-size:11px;color:{TEXT2};margin-top:2px;">{detail}</div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

    section_header("KILL SWITCH  &  RISK RULES")
    try:
        from config import ACCOUNT_SIZE, MAX_DAILY_LOSS_PCT
        MAX_PORTFOLIO_RISK_PCT = 0.90  # 90% max deployed per CLAUDE.md
        MAX_POSITION_RISK_PCT  = 0.01  # 1% per trade per CLAUDE.md
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(card(
                row_kv("Kill switch trigger", f"Balance < 75% = ${float(ACCOUNT_SIZE)*0.75:,.0f}", RED, True) +
                row_kv("Max daily loss", f"{MAX_DAILY_LOSS_PCT*100:.0f}% → halt all trading", RED, True) +
                row_kv("Max portfolio risk", f"{MAX_PORTFOLIO_RISK_PCT*100:.0f}% deployed cap", AMBER, True) +
                row_kv("Max position risk", f"{MAX_POSITION_RISK_PCT*100:.1f}% per trade", AMBER, True)
            ), unsafe_allow_html=True)
        with c2:
            st.markdown(card(
                row_kv("Margin type", "ISOLATED — never CROSS", RED) +
                row_kv("Kraken taker fee", "0.065%", TEXT2, True) +
                row_kv("No double-entry", "One position per symbol — ever", RED) +
                row_kv("No chase", "Skip if price moved > 3% since signal", RED) +
                row_kv("Stop loss sacred", "Never moved wider after entry", RED)
            ), unsafe_allow_html=True)
    except Exception as e:
        st.error(f"config: {e}")

    section_header("LEARNING LOOP")
    ml = get_ml_status()
    st.markdown(card(
        row_kv("57-feature snapshots stored", str(ml["snapshots"]), GREEN if ml["snapshots"]>=ml["min_needed"] else AMBER, True) +
        row_kv("Snapshots needed for ML", str(ml["min_needed"]), TEXT2, True) +
        row_kv("Bayesian signal stats", "18 signals tracked (signal_stats table)", TEXT2) +
        row_kv("Bayesian overlay blend", "15% of composite after scoring", CYAN) +
        row_kv("Retrain trigger", "ml_retrain_queue · checked every 6h", TEXT2) +
        row_kv("Post-trade analyzer", "Fires on every full close", TEXT2) +
        row_kv("Dynamic weights cache", "5-min TTL · updates on next score call", TEXT2) +
        row_kv("RBI nightly", "02:00 ET · 575 combo research", TEXT2)
    ), unsafe_allow_html=True)

    section_header("SYSTEM EVENTS LOG  (last 25, excluding IBKR noise)")
    events = get_recent_events(25)
    if events:
        rows = ""
        for e in events:
            level = e.get("level", "INFO")
            lc = RED if level == "ERROR" else (AMBER if level == "WARNING" else TEXT3)
            src = e.get("source", "")[:20]
            msg = e.get("message", "")[:140]
            rows += (
                f'<div style="display:grid;grid-template-columns:55px 80px 110px 1fr;'
                f'gap:8px;padding:7px 12px;border-bottom:1px solid {BORDER}22;font-size:11px;">'
                f'<span style="color:{lc};font-weight:700;">{level}</span>'
                f'<span style="color:{TEXT3};">{time_ago(e.get("ts",""))}</span>'
                f'<span style="color:{TEXT3};font-family:{MONO};">{src}</span>'
                f'<span style="color:{TEXT2};">{msg}</span>'
                f'</div>'
            )
        st.markdown(
            f'<div style="background:{CARD};border:1px solid {BORDER};border-radius:12px;overflow:hidden;">{rows}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(card(f'<div style="color:{TEXT3};text-align:center;padding:12px;">No events</div>'), unsafe_allow_html=True)

    with st.expander("Full config dump (all constants from config.py)"):
        try:
            import config as _cfg
            items = sorted(
                {k: v for k, v in vars(_cfg).items()
                 if not k.startswith("_") and isinstance(v, (int, float, str, bool))}.items()
            )
            ca, cb = st.columns(2)
            half = len(items) // 2
            for col, chunk in ((ca, items[:half]), (cb, items[half:])):
                with col:
                    for k, v in chunk:
                        st.markdown(
                            f'<div style="display:flex;justify-content:space-between;padding:4px 0;'
                            f'border-bottom:1px solid {BORDER}22;">'
                            f'<span style="font-size:11px;color:{TEXT2};">{k}</span>'
                            f'<span style="font-size:11px;font-weight:600;color:{TEXT};font-family:{MONO};">{v}</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
        except Exception as e:
            st.error(str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    render_topbar()

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "⚡  WAR ROOM",
        "📊  PERFORMANCE",
        "🧠  SIGNAL BRAIN",
        "📡  SCANNER",
        "⚙  SYSTEM",
    ])

    with tab1:
        render_war_room()

    with tab2:
        render_performance()

    with tab3:
        render_signal_brain()

    with tab4:
        render_scanner()

    with tab5:
        render_system()


if __name__ == "__main__":
    main()
else:
    main()
