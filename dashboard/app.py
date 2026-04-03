"""
dashboard/app.py — v10 War Room
Clean redesign. Only data that matters: since 2026-04-02.
"""
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import time
from datetime import datetime, timezone, timedelta

import streamlit as st

# ── constants ──────────────────────────────────────────────────────────────────
LAUNCH_DATE = "2026-04-02"          # ignore all data before this
DB_PATH     = "logs/trades.db"

# ── palette ────────────────────────────────────────────────────────────────────
BG      = "#08090e"
SURFACE = "#0f1018"
CARD    = "#13141f"
BORDER  = "#1e2035"
GOLD    = "#f5a623"
GREEN   = "#10c98f"
RED     = "#f03e5e"
AMBER   = "#f59e0b"
BLUE    = "#4f8ef7"
PURPLE  = "#a78bfa"
TEXT    = "#eef0f6"
TEXT2   = "#8892b0"
TEXT3   = "#4a5175"
MONO    = "'JetBrains Mono', 'Fira Code', monospace"

st.set_page_config(
    page_title="War Room v10",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;600;700&display=swap');

html, body, .stApp, .main {{
    background: {BG} !important;
    font-family: 'Inter', sans-serif !important;
    color: {TEXT} !important;
}}
#MainMenu, footer, header, .stDeployButton {{ visibility: hidden; }}
.block-container {{ padding: 16px 20px 60px 20px !important; max-width: 100% !important; }}
div[data-testid="column"] {{ padding: 0 5px !important; }}
.stTabs [data-baseweb="tab-list"] {{
    background: {SURFACE} !important;
    border-radius: 10px !important;
    padding: 4px !important;
    gap: 2px !important;
    border: 1px solid {BORDER} !important;
}}
.stTabs [data-baseweb="tab"] {{
    background: transparent !important;
    color: {TEXT2} !important;
    border-radius: 8px !important;
    padding: 8px 20px !important;
    font-weight: 600 !important;
    font-size: 13px !important;
    border: none !important;
}}
.stTabs [aria-selected="true"] {{
    background: {CARD} !important;
    color: {TEXT} !important;
}}
.stTabs [data-baseweb="tab-panel"] {{ background: transparent !important; padding-top: 20px !important; }}
.stExpander {{ border: 1px solid {BORDER} !important; border-radius: 10px !important; background: {CARD} !important; }}
div[data-testid="stExpander"] summary {{ color: {TEXT2} !important; font-size: 13px !important; }}
@keyframes pulse {{
    0%,100% {{ opacity:1; transform:scale(1); box-shadow:0 0 0 0 {GREEN}66; }}
    50%      {{ opacity:.7; transform:scale(1.25); box-shadow:0 0 0 6px {GREEN}00; }}
}}
@keyframes scanline {{
    0%   {{ transform:translateX(-100%); opacity:0; }}
    10%  {{ opacity:1; }}
    90%  {{ opacity:1; }}
    100% {{ transform:translateX(100%); opacity:0; }}
}}
@keyframes fadein {{
    from {{ opacity:0; transform:translateY(-6px); }}
    to   {{ opacity:1; transform:translateY(0); }}
}}
@keyframes ticker {{
    0%   {{ transform:translateX(0); }}
    100% {{ transform:translateX(-50%); }}
}}
.pulse-dot {{
    width:10px; height:10px; border-radius:50%;
    background:{GREEN};
    animation: pulse 1.6s ease-in-out infinite;
    display:inline-block;
}}
.scanline-bar {{
    height:2px; border-radius:2px; overflow:hidden;
    background:{BORDER}; position:relative; margin-top:6px;
}}
.scanline-bar::after {{
    content:''; position:absolute; top:0; left:0;
    height:100%; width:40%;
    background:linear-gradient(90deg,transparent,{BLUE},{GREEN},transparent);
    animation: scanline 2.4s ease-in-out infinite;
}}
.activity-row {{ animation: fadein .3s ease; }}
</style>
""", unsafe_allow_html=True)


# ── db helpers ────────────────────────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _q(sql: str, params=()) -> list:
    try:
        with _conn() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]
    except Exception:
        return []


def _q1(sql: str, params=()):
    rows = _q(sql, params)
    return rows[0] if rows else {}


# ── data layer ────────────────────────────────────────────────────────────────

def get_stats_since_launch() -> dict:
    row = _q1("""
        SELECT
            COUNT(*) total_trades,
            SUM(CASE WHEN action='SELL' THEN 1 ELSE 0 END) closes,
            SUM(CASE WHEN action='SELL' AND pnl_usd > 0 THEN 1 ELSE 0 END) wins,
            SUM(CASE WHEN action='SELL' THEN pnl_usd ELSE 0 END) total_pnl,
            SUM(CASE WHEN action='SELL' AND pnl_usd > 0 THEN pnl_usd ELSE 0 END) gross_wins,
            SUM(CASE WHEN action='SELL' AND pnl_usd <= 0 THEN ABS(pnl_usd) ELSE 0 END) gross_losses
        FROM trades
        WHERE ts >= ? AND paper = 1 AND broker NOT LIKE '%bybit%'
    """, (LAUNCH_DATE,))
    closes     = row.get("closes") or 0
    wins       = row.get("wins") or 0
    gw         = row.get("gross_wins") or 0
    gl         = row.get("gross_losses") or 0
    win_rate   = (wins / closes * 100) if closes else 0
    profit_fac = (gw / gl) if gl > 0 else (float("inf") if gw > 0 else 0)
    return {
        "closes":      closes,
        "wins":        wins,
        "win_rate":    win_rate,
        "total_pnl":   row.get("total_pnl") or 0,
        "profit_factor": profit_fac,
        "gross_wins":  gw,
        "gross_losses": gl,
    }


def get_open_positions() -> list:
    return _q("SELECT * FROM open_positions WHERE paper = 1 ORDER BY ts_entry DESC")


def get_recent_trades(limit: int = 20) -> list:
    return _q("""
        SELECT ts, symbol, action, pnl_usd, strategy, fee_usd
        FROM trades
        WHERE ts >= ? AND paper = 1 AND broker NOT LIKE '%bybit%'
        ORDER BY ts DESC LIMIT ?
    """, (LAUNCH_DATE, limit))


def get_today_pnl() -> float:
    today = datetime.now().strftime("%Y-%m-%d")
    r = _q1("""
        SELECT SUM(pnl_usd) v FROM trades
        WHERE ts >= ? AND action='SELL' AND paper=1 AND broker NOT LIKE '%bybit%'
    """, (today,))
    return r.get("v") or 0



def get_recent_events(limit: int = 8) -> list:
    return _q("""
        SELECT ts, level, source, message
        FROM system_events
        ORDER BY ts DESC LIMIT ?
    """, (limit,))


LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "bot.log")


def get_scanner_status() -> dict:
    """
    Parse bot.log for the most recent scanner cycle.
    Returns dict: {last_scan_age_s, candidate_count, candidates}
    where candidates is a list of {symbol, direction, vol_spike, adx, ev, funding}.
    """
    import re
    result = {"last_scan_age_s": 9999, "candidate_count": 0, "candidates": []}
    try:
        with open(LOG_PATH, "r") as f:
            lines = f.readlines()[-600:]
    except Exception:
        return result

    # Find the last "Complete: N candidates" line and its index
    complete_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if "[scanner] Complete:" in lines[i]:
            complete_idx = i
            break

    if complete_idx is None:
        return result

    # Parse age from timestamp on that line
    m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", lines[complete_idx])
    if m:
        try:
            dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            result["last_scan_age_s"] = int((datetime.now() - dt).total_seconds())
        except Exception:
            pass

    # Parse candidate count
    cm = re.search(r"Complete:\s*(\d+)\s*candidates", lines[complete_idx])
    if cm:
        result["candidate_count"] = int(cm.group(1))

    # Parse candidate lines that follow (→ SYMBOL DIRECTION spike=X adx=X ev=$X funding=X%)
    cand_re = re.compile(
        r"→\s+(\S+)\s+(LONG|SHORT)\s+spike=([\d.]+)\s+adx=([\d.]+)\s+ev=\$([\d.]+)\s+funding=([-\d.]+)%"
    )
    candidates = []
    for line in lines[complete_idx + 1:complete_idx + 20]:
        cm2 = cand_re.search(line)
        if cm2:
            candidates.append({
                "symbol":    cm2.group(1),
                "direction": cm2.group(2),
                "vol_spike": float(cm2.group(3)),
                "adx":       float(cm2.group(4)),
                "ev":        float(cm2.group(5)),
                "funding":   float(cm2.group(6)) / 100.0,
            })
    result["candidates"] = candidates
    return result


def get_last_scan_age() -> int:
    """
    Read bot.log to find the timestamp of the most recent '[v10] scan:' line.
    Returns seconds since that line, or 9999 if not found.
    """
    import re
    try:
        with open(LOG_PATH, "r") as f:
            lines = f.readlines()[-500:]
    except Exception:
        return 9999
    for line in reversed(lines):
        if "[v10] scan:" in line:
            m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
            if m:
                try:
                    dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    return int((datetime.now() - dt).total_seconds())
                except Exception:
                    pass
    return 9999

def get_bot_activity(n: int = 30) -> list:
    """
    Tail bot.log and parse [v10] lines into structured activity events.
    Returns list of dicts: {ts, kind, symbol, msg, raw}
      kind: ENTERED | VETO | SCAN | SCORE | SIGNAL | ERROR | OTHER
    """
    events = []
    try:
        with open(LOG_PATH, "r") as f:
            lines = f.readlines()[-300:]
    except Exception:
        return []

    import re
    for line in reversed(lines):
        line = line.strip()
        if "[v10]" not in line:
            continue
        # Parse timestamp
        ts_m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        ts   = ts_m.group(1) if ts_m else ""
        msg  = line.split("[v10]", 1)[-1].strip()

        if "ENTERED" in msg:
            kind = "ENTERED"
        elif "ENTRY SIGNAL" in msg:
            kind = "SIGNAL"
        elif "ECONOMICS VETO" in msg or "VETO" in msg.upper():
            kind = "VETO"
        elif "score=" in msg and "< threshold" in msg:
            kind = "SCORE"
        elif "scan:" in msg and "candidates" in msg:
            kind = "SCAN"
        elif "ERROR" in line or "fatal" in line.lower():
            kind = "ERROR"
        elif "exit_monitor" in line.lower() or "EXIT" in msg:
            kind = "EXIT"
        else:
            kind = "OTHER"

        # Extract symbol if present
        sym_m = re.search(r"\bPF_\w+", msg)
        symbol = sym_m.group(0) if sym_m else ""

        events.append({"ts": ts, "kind": kind, "symbol": symbol, "msg": msg})
        if len(events) >= n:
            break

    return events


def get_account_balance() -> float:
    try:
        from config import ACCOUNT_SIZE
        return float(ACCOUNT_SIZE)
    except Exception:
        return 5000.0


# ── html primitives ───────────────────────────────────────────────────────────

def _card(content: str, padding: str = "20px 22px") -> str:
    return (
        f'<div style="background:{CARD};border:1px solid {BORDER};'
        f'border-radius:14px;padding:{padding};margin-bottom:10px;">'
        f'{content}</div>'
    )


def _label(text: str) -> str:
    return (
        f'<div style="font-size:11px;font-weight:700;letter-spacing:2.5px;'
        f'text-transform:uppercase;color:{TEXT3};margin-bottom:6px;">{text}</div>'
    )


def _big(value: str, color: str = TEXT) -> str:
    return (
        f'<div style="font-size:36px;font-weight:800;line-height:1;'
        f'font-family:{MONO};color:{color};">{value}</div>'
    )


def _sub(text: str) -> str:
    return f'<div style="font-size:12px;color:{TEXT2};margin-top:6px;">{text}</div>'


def _divider() -> None:
    st.markdown(
        f'<div style="height:1px;background:{BORDER};margin:12px 0;"></div>',
        unsafe_allow_html=True,
    )


def _pnl_color(v: float) -> str:
    return GREEN if v > 0 else (RED if v < 0 else TEXT2)


def _fmt_pnl(v: float) -> str:
    sign = "+" if v > 0 else ""
    return f"{sign}${v:,.2f}"


def _time_ago(ts_str: str) -> str:
    try:
        ts_str = ts_str.replace("T", " ").split(".")[0].split("+")[0].split("-04")[0].split("-05")[0]
        dt = datetime.fromisoformat(ts_str)
        secs = int((datetime.now() - dt).total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs//60}m ago"
        if secs < 86400:
            return f"{secs//3600}h ago"
        return f"{secs//86400}d ago"
    except Exception:
        return ts_str[:16] if ts_str else ""


# ── status bar ────────────────────────────────────────────────────────────────

def render_status_bar():
    age   = get_last_scan_age()
    today = get_today_pnl()
    tc    = _pnl_color(today)

    st.markdown(
        f'<div style="display:flex;align-items:center;justify-content:space-between;'
        f'background:{SURFACE};border:1px solid {BORDER};border-radius:10px;'
        f'padding:10px 18px;margin-bottom:18px;font-size:12px;">'

        f'<div style="display:flex;align-items:center;gap:8px;">'
        f'<div style="width:8px;height:8px;border-radius:50%;background:{GREEN};'
        f'box-shadow:0 0 6px {GREEN};"></div>'
        f'<span style="font-weight:700;color:{TEXT};letter-spacing:1px;">PAPER</span>'
        f'<span style="color:{TEXT3};">·</span>'
        f'<span style="color:{TEXT2};">v10.1</span>'
        f'</div>'

        f'<div style="color:{TEXT2};">scanner · kraken_futures · '
        f'<span style="color:{GREEN if age < 360 else (AMBER if age < 600 else RED)};">{age}s ago</span></div>'

        f'<div style="color:{TEXT2};">today · '
        f'<span style="font-weight:700;color:{tc};font-family:{MONO};">{_fmt_pnl(today)}</span>'
        f'</div>'

        f'<div style="color:{TEXT3};font-family:{MONO};">'
        f'{datetime.now().strftime("%H:%M:%S")}</div>'

        f'</div>',
        unsafe_allow_html=True,
    )


# ── live activity feed ────────────────────────────────────────────────────────

KIND_STYLE = {
    "ENTERED": ("#10c98f", "⬆ ENTERED",   "#10c98f22"),
    "SIGNAL":  ("#4f8ef7", "◆ SIGNAL",    "#4f8ef722"),
    "VETO":    ("#f59e0b", "✕ VETO",      "#f59e0b22"),
    "SCORE":   ("#4a5175", "· SCORE",     "transparent"),
    "SCAN":    ("#8892b0", "⟳ SCAN",      "transparent"),
    "EXIT":    ("#a78bfa", "⬇ EXIT",      "#a78bfa22"),
    "ERROR":   ("#f03e5e", "! ERROR",     "#f03e5e22"),
    "OTHER":   ("#4a5175", "  ...",       "transparent"),
}


@st.fragment(run_every=5)
def render_live_feed():
    age      = get_last_scan_age()
    activity = get_bot_activity(20)

    # ── pulse header ──────────────────────────────────────────────────────────
    scan_interval = 300   # 5 min
    next_scan_in  = max(0, scan_interval - age)
    bar_pct       = min(100, int(age / scan_interval * 100))

    if age < 30:
        status_text  = "SCANNING NOW"
        status_color = GREEN
    elif age < scan_interval + 60:
        status_text  = f"NEXT SCAN IN {next_scan_in}s"
        status_color = GREEN
    else:
        status_text  = f"STALE — {age}s since last scan"
        status_color = RED

    # Count significant events
    n_entries = sum(1 for e in activity if e["kind"] == "ENTERED")
    n_vetoes  = sum(1 for e in activity if e["kind"] == "VETO")

    st.markdown(
        f'<div style="background:{CARD};border:1px solid {BORDER};border-radius:14px;'
        f'padding:18px 22px;margin-bottom:14px;">'

        # top row: pulse + status + stats
        f'<div style="display:flex;align-items:center;justify-content:space-between;">'

        f'<div style="display:flex;align-items:center;gap:14px;">'
        f'<div class="pulse-dot" style="background:{status_color};'
        f'box-shadow:0 0 8px {status_color}88;"></div>'
        f'<div>'
        f'<div style="font-size:13px;font-weight:800;letter-spacing:2px;'
        f'text-transform:uppercase;color:{status_color};">{status_text}</div>'
        f'<div style="font-size:11px;color:{TEXT3};margin-top:2px;">'
        f'Kraken Futures · scan every 5 min · 7-filter pipeline</div>'
        f'</div>'
        f'</div>'

        f'<div style="display:flex;gap:28px;text-align:right;">'
        f'<div>'
        f'<div style="font-size:10px;color:{TEXT3};text-transform:uppercase;letter-spacing:1.5px;">Entries (recent)</div>'
        f'<div style="font-size:22px;font-weight:900;color:{GREEN};font-family:{MONO};">{n_entries}</div>'
        f'</div>'
        f'<div>'
        f'<div style="font-size:10px;color:{TEXT3};text-transform:uppercase;letter-spacing:1.5px;">Vetoes (recent)</div>'
        f'<div style="font-size:22px;font-weight:900;color:{AMBER};font-family:{MONO};">{n_vetoes}</div>'
        f'</div>'
        f'<div>'
        f'<div style="font-size:10px;color:{TEXT3};text-transform:uppercase;letter-spacing:1.5px;">Last scan</div>'
        f'<div style="font-size:22px;font-weight:900;color:{TEXT2};font-family:{MONO};">{age}s</div>'
        f'</div>'
        f'</div>'

        f'</div>'   # end top row

        # progress bar toward next scan
        f'<div class="scanline-bar" style="margin-top:14px;">'
        f'<div style="height:100%;width:{bar_pct}%;background:linear-gradient(90deg,{BLUE},{GREEN});'
        f'border-radius:2px;transition:width .5s;"></div>'
        f'</div>'
        f'<div style="display:flex;justify-content:space-between;font-size:10px;'
        f'color:{TEXT3};margin-top:4px;">'
        f'<span>last scan</span><span>{next_scan_in}s to next scan</span>'
        f'</div>'

        f'</div>',
        unsafe_allow_html=True,
    )

    # ── activity log ─────────────────────────────────────────────────────────
    st.markdown(
        f'<div style="font-size:11px;font-weight:700;letter-spacing:2.5px;'
        f'text-transform:uppercase;color:{TEXT3};padding:6px 0 10px 0;">'
        f'BRAIN ACTIVITY — LIVE LOG</div>',
        unsafe_allow_html=True,
    )

    if not activity:
        st.markdown(
            _card(f'<div style="text-align:center;padding:20px;color:{TEXT3};">Waiting for bot.log…</div>'),
            unsafe_allow_html=True,
        )
        return

    rows_html = ""
    for ev in activity:
        kind  = ev["kind"]
        color, label, bg = KIND_STYLE.get(kind, KIND_STYLE["OTHER"])
        ts    = ev["ts"][11:19] if len(ev["ts"]) >= 19 else ev["ts"]   # HH:MM:SS
        msg   = ev["msg"][:120]
        sym   = ev["symbol"]

        # Skip pure "OTHER" noise unless it's the most recent
        if kind == "OTHER" and rows_html:
            continue

        # Highlight entry rows with a left accent
        border_left = f"border-left:3px solid {color};" if kind in ("ENTERED","EXIT","ERROR","SIGNAL") else ""

        rows_html += (
            f'<div class="activity-row" style="display:grid;grid-template-columns:70px 90px 1fr;'
            f'gap:8px;padding:9px 14px;border-bottom:1px solid {BORDER}22;'
            f'background:{bg};{border_left}align-items:center;">'

            f'<div style="font-size:11px;color:{TEXT3};font-family:{MONO};">{ts}</div>'

            f'<div><span style="background:{color}22;color:{color};border-radius:4px;'
            f'padding:2px 7px;font-size:10px;font-weight:800;letter-spacing:.5px;">'
            f'{label}</span></div>'

            f'<div style="font-size:12px;color:{"" + TEXT if kind in ("ENTERED","SIGNAL","EXIT") else TEXT2};'
            f'font-family:{MONO if kind in ("ENTERED","SIGNAL","SCORE","VETO") else "inherit"};">'
            f'{msg}</div>'

            f'</div>'
        )

    st.markdown(
        f'<div style="background:{CARD};border:1px solid {BORDER};'
        f'border-radius:12px;overflow:hidden;max-height:380px;overflow-y:auto;">'
        f'{rows_html}</div>',
        unsafe_allow_html=True,
    )


# ── overview tab ──────────────────────────────────────────────────────────────

@st.fragment(run_every=10)
def render_overview():
    stats   = get_stats_since_launch()
    balance = get_account_balance()
    pnl     = stats["total_pnl"]
    closes  = stats["closes"]
    wr      = stats["win_rate"]
    pf      = stats["profit_factor"]
    pnl_col = _pnl_color(pnl)

    # ── hero row ────────────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        st.markdown(
            _card(
                _label("P&L since launch") +
                _big(_fmt_pnl(pnl), pnl_col) +
                _sub(f"account ${balance:,.0f}")
            ),
            unsafe_allow_html=True,
        )

    with c2:
        wr_col = GREEN if wr >= 52 else (AMBER if wr >= 45 else RED)
        st.markdown(
            _card(
                _label("win rate (v10)") +
                _big(f"{wr:.1f}%", wr_col) +
                _sub(f"{stats['wins']}W / {closes - stats['wins']}L · {closes} trades")
            ),
            unsafe_allow_html=True,
        )

    with c3:
        pf_col = GREEN if pf >= 1.4 else (AMBER if pf >= 1.0 else RED)
        pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"
        st.markdown(
            _card(
                _label("profit factor") +
                _big(pf_str, pf_col) +
                _sub(f"+${stats['gross_wins']:.2f} / −${stats['gross_losses']:.2f}")
            ),
            unsafe_allow_html=True,
        )

    with c4:
        pos = get_open_positions()
        st.markdown(
            _card(
                _label("open positions") +
                _big(str(len(pos)), BLUE) +
                _sub("paper · live data")
            ),
            unsafe_allow_html=True,
        )

    st.markdown('<div style="height:4px;"></div>', unsafe_allow_html=True)

    # ── open positions ──────────────────────────────────────────────────────────
    pos = get_open_positions()
    st.markdown(
        f'<div style="font-size:11px;font-weight:700;letter-spacing:2.5px;'
        f'text-transform:uppercase;color:{TEXT3};padding:14px 0 10px 0;">'
        f'OPEN POSITIONS  ({len(pos)})</div>',
        unsafe_allow_html=True,
    )

    if not pos:
        st.markdown(
            _card(
                f'<div style="text-align:center;padding:20px 0;color:{TEXT3};font-size:14px;">'
                f'No open positions — scanner running every 5 min</div>'
            ),
            unsafe_allow_html=True,
        )
    else:
        # 3 columns of position cards
        cols = st.columns(3)
        for i, p in enumerate(pos):
            entry  = float(p.get("entry") or 0)
            stop   = float(p.get("stop") or 0)
            target = float(p.get("target") or 0)
            high   = float(p.get("high_since_entry") or entry)
            symbol = p.get("symbol", "")
            strat  = p.get("strategy", "")
            dirn   = p.get("direction", "LONG")
            age    = _time_ago(p.get("ts_entry", ""))

            # Estimate live P&L from high (best case) — no live price feed
            dist_to_stop   = abs(entry - stop)
            dist_to_target = abs(target - entry)
            r_multiple     = dist_to_target / dist_to_stop if dist_to_stop > 0 else 0

            stop_pct   = (abs(entry - stop)   / entry * 100) if entry else 0
            target_pct = (abs(target - entry) / entry * 100) if entry else 0
            high_pct   = ((high - entry) / entry * 100 * (1 if dirn == "LONG" else -1)) if entry else 0
            high_col   = _pnl_color(high_pct)

            dir_col  = GREEN if dirn == "LONG" else RED
            dir_icon = "▲" if dirn == "LONG" else "▼"

            with cols[i % 3]:
                st.markdown(
                    f'<div style="background:{CARD};border:1px solid {BORDER};'
                    f'border-left:3px solid {dir_col};'
                    f'border-radius:12px;padding:16px 18px;margin-bottom:8px;">'

                    f'<div style="display:flex;justify-content:space-between;'
                    f'align-items:flex-start;margin-bottom:12px;">'
                    f'<div>'
                    f'<div style="font-size:17px;font-weight:800;color:{TEXT};'
                    f'font-family:{MONO};">{symbol}</div>'
                    f'<div style="font-size:11px;color:{TEXT3};margin-top:2px;">{strat}</div>'
                    f'</div>'
                    f'<div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px;">'
                    f'<span style="background:{dir_col}22;color:{dir_col};border-radius:5px;'
                    f'padding:2px 8px;font-size:11px;font-weight:800;">{dir_icon} {dirn}</span>'
                    f'<span style="font-size:11px;color:{TEXT3};">{age}</span>'
                    f'</div>'
                    f'</div>'

                    f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;">'

                    f'<div>'
                    f'<div style="font-size:10px;color:{TEXT3};letter-spacing:1px;text-transform:uppercase;">Entry</div>'
                    f'<div style="font-size:13px;font-weight:700;color:{TEXT};font-family:{MONO};">'
                    f'{entry:.4g}</div>'
                    f'</div>'

                    f'<div>'
                    f'<div style="font-size:10px;color:{TEXT3};letter-spacing:1px;text-transform:uppercase;">Stop</div>'
                    f'<div style="font-size:13px;font-weight:700;color:{RED};font-family:{MONO};">'
                    f'{stop:.4g} <span style="font-size:10px;">−{stop_pct:.2f}%</span></div>'
                    f'</div>'

                    f'<div>'
                    f'<div style="font-size:10px;color:{TEXT3};letter-spacing:1px;text-transform:uppercase;">Target</div>'
                    f'<div style="font-size:13px;font-weight:700;color:{GREEN};font-family:{MONO};">'
                    f'{target:.4g} <span style="font-size:10px;">+{target_pct:.2f}%</span></div>'
                    f'</div>'

                    f'</div>'

                    f'<div style="margin-top:10px;padding-top:10px;border-top:1px solid {BORDER};'
                    f'display:flex;justify-content:space-between;font-size:12px;">'
                    f'<span style="color:{TEXT3};">R:R</span>'
                    f'<span style="font-weight:700;color:{TEXT};font-family:{MONO};">'
                    f'{r_multiple:.1f}×</span>'
                    f'<span style="color:{TEXT3};">best move</span>'
                    f'<span style="font-weight:700;color:{high_col};font-family:{MONO};">'
                    f'{high_pct:+.2f}%</span>'
                    f'</div>'

                    f'</div>',
                    unsafe_allow_html=True,
                )


# ── trades tab ────────────────────────────────────────────────────────────────

@st.fragment(run_every=30)
def render_trades():
    trades = get_recent_trades(50)
    closes = [t for t in trades if t["action"] == "SELL"]
    opens  = [t for t in trades if t["action"] == "BUY"]

    st.markdown(
        f'<div style="font-size:11px;font-weight:700;letter-spacing:2.5px;'
        f'text-transform:uppercase;color:{TEXT3};padding:0 0 12px 0;">'
        f'CLOSED TRADES SINCE 2026-04-02</div>',
        unsafe_allow_html=True,
    )

    if not closes:
        st.markdown(
            _card(
                f'<div style="text-align:center;padding:30px;color:{TEXT3};">'
                f'No closed trades yet since launch</div>'
            ),
            unsafe_allow_html=True,
        )
        return

    # header row
    st.markdown(
        f'<div style="display:grid;grid-template-columns:140px 1fr 80px 90px 100px 80px;'
        f'gap:8px;padding:8px 16px;font-size:11px;font-weight:700;letter-spacing:1.5px;'
        f'text-transform:uppercase;color:{TEXT3};border-bottom:1px solid {BORDER};">'
        f'<div>TIME</div><div>SYMBOL</div><div>STRATEGY</div>'
        f'<div style="text-align:right;">FEE</div>'
        f'<div style="text-align:right;">P&L</div>'
        f'<div style="text-align:right;">RESULT</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    rows_html = ""
    for t in closes:
        pnl    = t.get("pnl_usd") or 0
        fee    = t.get("fee_usd") or 0
        color  = _pnl_color(pnl)
        result = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "FLAT")
        rc     = GREEN if pnl > 0 else (RED if pnl < 0 else TEXT3)
        strat  = (t.get("strategy") or "").replace("crypto_", "").replace("_", " ")[:12]

        rows_html += (
            f'<div style="display:grid;grid-template-columns:140px 1fr 80px 90px 100px 80px;'
            f'gap:8px;padding:10px 16px;border-bottom:1px solid {BORDER}22;'
            f'font-size:13px;align-items:center;">'
            f'<div style="color:{TEXT3};font-size:11px;">{_time_ago(t["ts"])}</div>'
            f'<div style="font-weight:700;color:{TEXT};font-family:{MONO};">'
            f'{t.get("symbol","")}</div>'
            f'<div style="color:{TEXT3};font-size:11px;">{strat}</div>'
            f'<div style="text-align:right;color:{TEXT3};font-family:{MONO};">-${fee:.3f}</div>'
            f'<div style="text-align:right;font-weight:700;color:{color};font-family:{MONO};">'
            f'{_fmt_pnl(pnl)}</div>'
            f'<div style="text-align:right;">'
            f'<span style="background:{rc}22;color:{rc};border-radius:5px;'
            f'padding:2px 8px;font-size:11px;font-weight:700;">{result}</span>'
            f'</div>'
            f'</div>'
        )

    st.markdown(
        f'<div style="background:{CARD};border:1px solid {BORDER};'
        f'border-radius:12px;overflow:hidden;">{rows_html}</div>',
        unsafe_allow_html=True,
    )


# ── scanner tab ───────────────────────────────────────────────────────────────

@st.fragment(run_every=15)
def render_scanner():
    scan = get_scanner_status()
    age  = int(scan.get("last_scan_age_s", 0))
    n    = scan.get("candidate_count", 0)
    candidates = scan.get("candidates", [])

    # Scanner status header
    status_color = GREEN if age < 360 else (AMBER if age < 600 else RED)
    st.markdown(
        f'<div style="background:{CARD};border:1px solid {BORDER};border-radius:12px;'
        f'padding:18px 22px;margin-bottom:14px;display:flex;'
        f'justify-content:space-between;align-items:center;">'

        f'<div>'
        f'<div style="font-size:11px;color:{TEXT3};text-transform:uppercase;'
        f'letter-spacing:2px;margin-bottom:6px;">KRAKEN FUTURES SCANNER</div>'
        f'<div style="font-size:22px;font-weight:800;color:{status_color};'
        f'font-family:{MONO};">{age}s since last scan</div>'
        f'<div style="font-size:12px;color:{TEXT2};margin-top:4px;">'
        f'runs every 5 min · 7-filter pipeline · top 15 candidates</div>'
        f'</div>'

        f'<div style="text-align:right;">'
        f'<div style="font-size:11px;color:{TEXT3};text-transform:uppercase;'
        f'letter-spacing:2px;margin-bottom:6px;">CANDIDATES FOUND</div>'
        f'<div style="font-size:48px;font-weight:900;line-height:1;'
        f'color:{BLUE if n > 0 else TEXT3};font-family:{MONO};">{n}</div>'
        f'</div>'

        f'</div>',
        unsafe_allow_html=True,
    )

    if candidates:
        st.markdown(
            f'<div style="font-size:11px;font-weight:700;letter-spacing:2px;'
            f'text-transform:uppercase;color:{TEXT3};padding:4px 0 10px 0;">'
            f'CURRENT CANDIDATES</div>',
            unsafe_allow_html=True,
        )
        for c in candidates:
            dirn    = c.get("direction", "LONG")
            dir_col = GREEN if dirn == "LONG" else RED
            ev      = c.get("ev") or 0
            spike   = c.get("vol_spike") or 0
            adx     = c.get("adx") or 0
            funding = c.get("funding") or 0

            st.markdown(
                f'<div style="background:{CARD};border:1px solid {BORDER};'
                f'border-left:3px solid {dir_col};border-radius:10px;'
                f'padding:14px 18px;margin-bottom:8px;display:flex;'
                f'justify-content:space-between;align-items:center;">'

                f'<div>'
                f'<span style="font-size:16px;font-weight:800;color:{TEXT};'
                f'font-family:{MONO};">{c.get("symbol")}</span>'
                f'<span style="background:{dir_col}22;color:{dir_col};border-radius:5px;'
                f'padding:2px 8px;font-size:11px;font-weight:700;margin-left:10px;">{dirn}</span>'
                f'</div>'

                f'<div style="display:flex;gap:24px;">'
                f'<div><div style="font-size:10px;color:{TEXT3};text-transform:uppercase;">Vol Spike</div>'
                f'<div style="font-size:14px;font-weight:700;color:{TEXT};font-family:{MONO};">{spike:.2f}×</div></div>'
                f'<div><div style="font-size:10px;color:{TEXT3};text-transform:uppercase;">ADX</div>'
                f'<div style="font-size:14px;font-weight:700;color:{TEXT};font-family:{MONO};">{adx:.0f}</div></div>'
                f'<div><div style="font-size:10px;color:{TEXT3};text-transform:uppercase;">EV</div>'
                f'<div style="font-size:14px;font-weight:700;color:{GREEN};font-family:{MONO};">${ev:.2f}</div></div>'
                f'<div><div style="font-size:10px;color:{TEXT3};text-transform:uppercase;">Funding</div>'
                f'<div style="font-size:14px;font-weight:700;color:{TEXT};font-family:{MONO};">{funding*100:.4f}%</div></div>'
                f'</div>'

                f'</div>',
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            _card(
                f'<div style="text-align:center;padding:24px;color:{TEXT3};font-size:14px;line-height:1.7;">'
                f'No candidates this cycle — all 9 liquid Kraken perps filtered out.<br>'
                f'<span style="font-size:12px;">Typical reasons: vol spike &lt; 1.2×, ADX &lt; 20, OB depth too thin, EV too low.</span>'
                f'</div>'
            ),
            unsafe_allow_html=True,
        )

    # Pipeline steps from last scan
    _divider()
    st.markdown(
        f'<div style="font-size:11px;font-weight:700;letter-spacing:2px;'
        f'text-transform:uppercase;color:{TEXT3};padding:4px 0 10px 0;">'
        f'FILTER PIPELINE</div>',
        unsafe_allow_html=True,
    )

    try:
        from scanner import (
            _MIN_VOLUME_24H_USD, _MIN_VOL_SPIKE, _MIN_PRICE_MOVE_1H,
            _MIN_ADX_15M, _MIN_OB_DEPTH_USD, _MAX_SPREAD_PCT,
            _MIN_EXPECTED_PROFIT, _ROUND_TRIP_FEE_PCT,
        )
        steps = [
            ("1", "Universe", f"PF_ perps · volumeQuote > ${_MIN_VOLUME_24H_USD/1e6:.0f}M USD · not suspended"),
            ("2", "Momentum", f"vol spike ≥ {_MIN_VOL_SPIKE}× · price move ≥ {_MIN_PRICE_MOVE_1H}% (1h) · ADX ≥ {_MIN_ADX_15M} (15m)"),
            ("3", "Liquidity", f"OB depth > ${_MIN_OB_DEPTH_USD/1e3:.0f}K each side · spread < {_MAX_SPREAD_PCT}% · fail-closed"),
            ("4", "EV Gate", f"expected profit > ${_MIN_EXPECTED_PROFIT:.2f} · fees {_ROUND_TRIP_FEE_PCT*100:.3f}% RT · funding modeled"),
            ("5", "Correlation", "flag if correlated with open position (full check in risk engine)"),
            ("6", "Regime", "HIGH_VOL: spike ≥ 1.5× · RANGING: ADX ≤ 30 · TRENDING: penalty for counter-trend"),
            ("7", "Rank", "sort by vol spike · top 15 returned"),
        ]
        cols = st.columns(2)
        for i, (num, title, detail) in enumerate(steps):
            with cols[i % 2]:
                st.markdown(
                    f'<div style="display:flex;gap:12px;padding:10px 14px;'
                    f'background:{CARD};border:1px solid {BORDER};border-radius:10px;'
                    f'margin-bottom:8px;">'
                    f'<div style="min-width:24px;height:24px;border-radius:50%;'
                    f'background:{BLUE}22;border:1px solid {BLUE};display:flex;'
                    f'align-items:center;justify-content:center;font-size:11px;'
                    f'font-weight:800;color:{BLUE};">{num}</div>'
                    f'<div><div style="font-size:13px;font-weight:700;color:{TEXT};">{title}</div>'
                    f'<div style="font-size:11px;color:{TEXT2};margin-top:2px;">{detail}</div>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )
    except Exception:
        pass


# ── debug tab ─────────────────────────────────────────────────────────────────

def render_debug():
    st.markdown(
        f'<div style="font-size:12px;color:{TEXT3};padding:4px 0 18px 0;">'
        f'Live config values read from the running system. Reload to refresh.</div>',
        unsafe_allow_html=True,
    )

    def _row(label, value, hi=False):
        vc = TEXT if hi else TEXT2
        return (
            f'<div style="display:flex;justify-content:space-between;padding:7px 0;'
            f'border-bottom:1px solid {BORDER}22;">'
            f'<span style="font-size:13px;color:{TEXT2};">{label}</span>'
            f'<span style="font-size:13px;font-weight:700;color:{vc};font-family:{MONO};">'
            f'{value}</span></div>'
        )

    def _section(title):
        st.markdown(
            f'<div style="font-size:11px;font-weight:700;letter-spacing:2px;'
            f'text-transform:uppercase;color:{TEXT3};padding:18px 0 8px 0;">'
            f'{title}</div>',
            unsafe_allow_html=True,
        )

    col1, col2 = st.columns(2)

    with col1:
        _section("SIGNAL ENGINE — TECHNICAL TOWER")
        try:
            from signal_engine import _ENTRY_THRESHOLDS
            rows = (
                _row("CVD bull divergence", "+25 pts", True) +
                _row("MACD multi-variant aligned", "+20 pts") +
                _row("RSI bull divergence", "+15 pts") +
                _row("Funding rate squeeze (<−0.3)", "+15 pts") +
                _row("VWAP reclaim on volume", "+15 pts") +
                _row("Liquidation cascade → long setup", "+15 pts") +
                _row("OB imbalance L5 > 0.60", "+10 pts") +
                _row("Williams %R < −80", "+10 pts") +
                _row("Whale accumulation", "+10 pts") +
                _row("Options skew bullish", "+10 pts") +
                _row("Vol spike > 1.5×", "+5 pts") +
                _row("RSI not overbought", "+5 pts") +
                _row("Price ≥ 2σ above VWAP", "−25 pts") +
                _row("Funding extreme (> 0.5)", "−20 pts") +
                _row("CVD bear divergence", "−20 pts") +
                _row("RSI bear divergence", "−15 pts") +
                _row("Whale distributing", "−15 pts") +
                _row("OB bear pressure L5 < 0.40", "−10 pts") +
                _row("Fear & Greed euphoria > 85", "−10 pts")
            )
            rows += f'<div style="height:10px;"></div>'
            rows += "".join(
                _row(f"Threshold: {r}", f"≥ {v} / 100", r == "UNKNOWN")
                for r, v in sorted(_ENTRY_THRESHOLDS.items(), key=lambda x: x[1])
            )
            st.markdown(
                f'<div style="background:{CARD};border:1px solid {BORDER};'
                f'border-radius:12px;padding:16px 18px;">{rows}</div>',
                unsafe_allow_html=True,
            )
        except Exception as e:
            st.error(f"signal_engine: {e}")

    with col2:
        _section("POSITION SIZING")
        try:
            from risk.unified_sizer import BASE_RISK_PCT, MAX_HEAT_PCT, MAX_SINGLE_NOTIONAL_PCT, _QUALITY_MULT
            from config import ACCOUNT_SIZE
            rows = (
                _row("Formula", "(acct × risk% × quality_mult) / stop_pct", True) +
                _row("Base risk per trade", f"{BASE_RISK_PCT*100:.1f}%") +
                _row("Account size", f"${ACCOUNT_SIZE:,.0f}") +
                _row("Quality A+ multiplier", f"{_QUALITY_MULT.get('A+', 1.35)}×") +
                _row("Quality A multiplier", f"{_QUALITY_MULT.get('A', 1.0)}×") +
                _row("Quality B multiplier", f"{_QUALITY_MULT.get('B', 0.75)}×") +
                _row("Portfolio heat cap", f"{MAX_HEAT_PCT*100:.0f}% of account") +
                _row("Hard position cap", f"{MAX_SINGLE_NOTIONAL_PCT*100:.0f}% per position") +
                _row("Minimum notional", "$20") +
                _row("Default leverage", "3×")
            )
            st.markdown(
                f'<div style="background:{CARD};border:1px solid {BORDER};'
                f'border-radius:12px;padding:16px 18px;">{rows}</div>',
                unsafe_allow_html=True,
            )
        except Exception as e:
            st.error(f"sizer: {e}")

        _section("ECONOMICS GATE")
        try:
            from risk.economics_gate import (
                TAKER_FEE_PCT, ROUND_TRIP_COST,
                _TIER_APLUS_EV, _TIER_A_EV, _TIER_B_EV, TIER_MULTIPLIERS,
            )
            rows = (
                _row("Taker fee (per side)", f"{TAKER_FEE_PCT*100:.3f}%") +
                _row("Round-trip fee", f"{ROUND_TRIP_COST*100:.3f}%") +
                _row("A+ floor (EV)", f"≥ {_TIER_APLUS_EV*100:.2f}%  →  {TIER_MULTIPLIERS['A+']}× size") +
                _row("A floor (EV)", f"≥ {_TIER_A_EV*100:.2f}%  →  {TIER_MULTIPLIERS['A']}× size") +
                _row("B floor (EV)", f"≥ {_TIER_B_EV*100:.3f}%  →  {TIER_MULTIPLIERS['B']}× size") +
                _row("VETO", f"below B floor  →  no trade", True)
            )
            st.markdown(
                f'<div style="background:{CARD};border:1px solid {BORDER};'
                f'border-radius:12px;padding:16px 18px;">{rows}</div>',
                unsafe_allow_html=True,
            )
        except Exception as e:
            st.error(f"economics_gate: {e}")

        _section("EXIT STACK  (priority 6 wins)")
        items = [
            (RED,   "6", "Kill switch",          "balance < $7,500 / API failure → close all"),
            (RED,   "5", "Risk forced",           "margin breach / drawdown / correlation limit"),
            (AMBER, "4", "Hard stop",             "ATR × 1.5 below entry, exchange-side, never widened"),
            (AMBER, "3", "Thesis degradation",    "signal score < entry score × 0.45 → close all"),
            (GREEN, "2", "Take profit scale-out", "2R → close 33% · 3.5R → close 33% · rest trails"),
            (GREEN, "1", "Trailing stop",         "activates 1× ATR in favor · trails 1.5× ATR from peak"),
        ]
        rows = ""
        for color, num, title, detail in items:
            rows += (
                f'<div style="display:flex;gap:12px;padding:8px 0;border-bottom:1px solid {BORDER}22;">'
                f'<div style="min-width:22px;height:22px;border-radius:50%;background:{color}22;'
                f'border:1px solid {color};display:flex;align-items:center;justify-content:center;'
                f'font-size:11px;font-weight:800;color:{color};">{num}</div>'
                f'<div><div style="font-size:13px;font-weight:700;color:{TEXT};">{title}</div>'
                f'<div style="font-size:11px;color:{TEXT2};">{detail}</div></div></div>'
            )
        st.markdown(
            f'<div style="background:{CARD};border:1px solid {BORDER};'
            f'border-radius:12px;padding:16px 18px;">{rows}</div>',
            unsafe_allow_html=True,
        )

    # full config dump
    with st.expander("Full config dump"):
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
                            f'<div style="display:flex;justify-content:space-between;'
                            f'padding:4px 0;border-bottom:1px solid {BORDER}22;">'
                            f'<span style="font-size:12px;color:{TEXT2};">{k}</span>'
                            f'<span style="font-size:12px;font-weight:600;color:{TEXT};'
                            f'font-family:{MONO};">{v}</span></div>',
                            unsafe_allow_html=True,
                        )
        except Exception as e:
            st.error(str(e))

    # recent system events
    with st.expander("System events log"):
        events = get_recent_events(30)
        if events:
            for e in events:
                level = e.get("level", "INFO")
                lc = RED if level == "ERROR" else (AMBER if level == "WARNING" else TEXT3)
                st.markdown(
                    f'<div style="display:flex;gap:12px;padding:6px 0;'
                    f'border-bottom:1px solid {BORDER}22;font-size:12px;">'
                    f'<span style="color:{lc};font-weight:700;min-width:60px;">{level}</span>'
                    f'<span style="color:{TEXT3};min-width:100px;">{_time_ago(e.get("ts",""))}</span>'
                    f'<span style="color:{TEXT2};">{e.get("message","")[:120]}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(f'<div style="color:{TEXT3};padding:10px;">No events</div>', unsafe_allow_html=True)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    render_status_bar()

    tab1, tab2, tab3, tab4 = st.tabs([
        "⚡  LIVE",
        "📋  TRADES",
        "📡  SCANNER",
        "🔧  DEBUG",
    ])

    with tab1:
        render_live_feed()
        render_overview()

    with tab2:
        render_trades()

    with tab3:
        render_scanner()

    with tab4:
        render_debug()


if __name__ == "__main__":
    main()
else:
    main()
