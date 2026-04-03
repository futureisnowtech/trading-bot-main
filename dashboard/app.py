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
        return float(ACCOUNT_SIZE), bool(PAPER_TRADING)
    except Exception:
        return 5000.0, True

def get_performance_stats():
    r = _q1("""
        SELECT
            SUM(CASE WHEN action IN ('SELL','BUY') AND pnl_usd != 0 THEN 1 END) AS closes,
            SUM(CASE WHEN action IN ('SELL','BUY') AND pnl_usd > 0 THEN 1 END) AS wins,
            SUM(CASE WHEN action IN ('SELL','BUY') AND pnl_usd < 0 THEN 1 END) AS losses,
            SUM(CASE WHEN action IN ('SELL','BUY') THEN pnl_usd ELSE 0 END) AS total_pnl,
            SUM(CASE WHEN action IN ('SELL','BUY') AND pnl_usd > 0 THEN pnl_usd ELSE 0 END) AS gross_wins,
            SUM(CASE WHEN action IN ('SELL','BUY') AND pnl_usd < 0 THEN ABS(pnl_usd) ELSE 0 END) AS gross_losses,
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
        "win_rate":      wins / closes * 100 if closes else 0.0,
        "total_pnl":     r.get("total_pnl") or 0.0,
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
    scan_age     = get_last_scan_age()
    today        = get_today_pnl()
    acct, paper  = get_account()
    stats        = get_performance_stats()
    open_p       = get_open_positions()

    mode  = "PAPER" if paper else "LIVE"
    scan_ok = scan_age < 360
    bot_ok  = scan_age < 600

    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric("Mode",         mode)
    c2.metric("Bot Status",   "RUNNING" if bot_ok else "STALE",
              delta=f"{scan_age}s since last scan")
    c3.metric("Scan Age",     f"{scan_age}s" if scan_age < 9999 else "–")
    c4.metric("Today P&L",    _fmt_pnl(today))
    c5.metric("Win Rate",     f"{stats['win_rate']:.1f}%",
              delta=f"{stats['wins']}W {stats['losses']}L")
    c6.metric("Account",      f"${acct:,.0f}")
    c7.metric("Open Pos",     str(len(open_p)))

    st.caption(f"Updated {datetime.now().strftime('%H:%M:%S')} · Paper trades since {LAUNCH_DATE} · Bybit/backtest/contaminated data excluded")


# ══════════════════════════════════════════════════════════════════════════════
# RENDER — PORTFOLIO (30s)
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=30)
def render_portfolio():
    import pandas as pd
    stats    = get_performance_stats()
    acct, _  = get_account()
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
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    st.title("Algo Trading System — v10.1 Paper")
    st.caption("Kraken Futures perps · Two-tower signal engine · 6-priority exit stack · 57-feature ML · All data live from SQLite + bot.log")
    st.divider()

    render_status()
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


if __name__ == "__main__":
    main()
else:
    main()
