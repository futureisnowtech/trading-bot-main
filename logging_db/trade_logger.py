"""
logging_db/trade_logger.py
SQLite trade log + position persistence + CSV export.
Positions are written to disk on every open/close so a restart never loses state.
"""
import sqlite3
import csv
import os
import time
import uuid
from datetime import datetime
from typing import Optional
import pytz

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH, CSV_LOG_DIR, MARKET_TIMEZONE, PAPER_TRADING


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    # WAL mode: writes survive crashes without corrupting existing data.
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db() -> None:
    os.makedirs(CSV_LOG_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = _conn()
    cur = conn.cursor()

    cur.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL, strategy TEXT NOT NULL, broker TEXT NOT NULL,
        symbol TEXT NOT NULL, action TEXT NOT NULL, order_type TEXT NOT NULL,
        qty REAL NOT NULL, price REAL NOT NULL, value_usd REAL NOT NULL,
        fee_usd REAL DEFAULT 0, pnl_usd REAL DEFAULT 0,
        paper INTEGER NOT NULL, order_id TEXT, notes TEXT
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS open_positions (
        symbol TEXT NOT NULL, strategy TEXT NOT NULL,
        qty REAL NOT NULL, entry REAL NOT NULL,
        stop REAL NOT NULL, target REAL NOT NULL,
        high_since_entry REAL NOT NULL, ts_entry TEXT NOT NULL,
        paper INTEGER NOT NULL, direction TEXT DEFAULT 'LONG',
        entry_reason TEXT DEFAULT '',
        PRIMARY KEY (symbol, strategy, paper)
    )""")
    for migration in [
        "ALTER TABLE open_positions ADD COLUMN direction TEXT DEFAULT 'LONG'",
        "ALTER TABLE open_positions ADD COLUMN entry_reason TEXT DEFAULT ''",
        # v9.0 Sprint 2: lane tag for 3-lane architecture (lane1=stocks, lane2=crypto, lane3=prediction)
        "ALTER TABLE trades ADD COLUMN lane TEXT DEFAULT 'lane2'",
        "ALTER TABLE open_positions ADD COLUMN lane TEXT DEFAULT 'lane2'",
        # v9.1 audit builds: MAE/MFE tracking, exit classification, ML gate visibility
        "ALTER TABLE open_positions ADD COLUMN low_since_entry REAL",
        "ALTER TABLE trade_attribution ADD COLUMN mae_pct REAL DEFAULT 0",
        "ALTER TABLE trade_attribution ADD COLUMN mfe_pct REAL DEFAULT 0",
        "ALTER TABLE trade_attribution ADD COLUMN exit_type TEXT DEFAULT 'unknown'",
        "ALTER TABLE trade_attribution ADD COLUMN is_fee_trap INTEGER DEFAULT 0",
        "ALTER TABLE trade_attribution ADD COLUMN ml_p_win REAL DEFAULT 0",
        # v9.1 super score: unified 0-100 composite intelligence per trade
        "ALTER TABLE trade_attribution ADD COLUMN super_score REAL DEFAULT 0",
        # v10.1: won flag (1=profitable, 0=loss) and source tag for ML training filters.
        # walk_forward_trainer and position_manager._get_kelly_fraction both query these columns.
        "ALTER TABLE trades ADD COLUMN won INTEGER DEFAULT NULL",
        "ALTER TABLE trades ADD COLUMN source TEXT DEFAULT 'paper'",
        "ALTER TABLE trades ADD COLUMN pnl_pct REAL DEFAULT 0",
        # v10.2: position state persistence — survive restarts without losing exit logic state.
        # These are required to correctly restore trailing stops and scale-out flags.
        "ALTER TABLE open_positions ADD COLUMN atr_at_entry REAL DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN composite_score REAL DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN trailing_active INTEGER DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN trailing_stop_price REAL DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN scale_33_done INTEGER DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN scale_66_done INTEGER DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN leverage INTEGER DEFAULT 3",
    ]:
        try:
            cur.execute(migration)
        except Exception:
            pass

    cur.execute("""CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL, strategy TEXT NOT NULL, symbol TEXT NOT NULL,
        signal TEXT NOT NULL, confidence REAL NOT NULL,
        reason TEXT, acted_on INTEGER DEFAULT 0, price REAL
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS debate_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL, symbol TEXT NOT NULL,
        buy_votes INTEGER, hold_votes INTEGER, sell_votes INTEGER,
        final_signal TEXT, confidence REAL,
        reasoning TEXT, bull_case TEXT, bear_case TEXT, key_risk TEXT,
        agent_details TEXT, regime TEXT
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS system_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL, level TEXT NOT NULL,
        source TEXT NOT NULL, message TEXT NOT NULL
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS api_costs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL, call_type TEXT NOT NULL,
        input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
        cost_usd REAL DEFAULT 0, symbol TEXT
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS edge_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        market TEXT NOT NULL,
        symbol TEXT NOT NULL,
        v_score REAL,
        e_score REAL,
        d_factor REAL,
        t_multiplier REAL,
        k_factor REAL,
        m_score REAL,
        final_size_usd REAL,
        debate_type TEXT,
        notes TEXT
    )""")

    # v10.1: 57-feature snapshots keyed to each trade.
    # Enables walk_forward_trainer to train on real features instead of 3-proxy scores.
    # One row per trade entry (trade_id → BUY trade in trades table).
    cur.execute("""CREATE TABLE IF NOT EXISTS trade_features (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER NOT NULL,
        ts REAL NOT NULL,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        features_json TEXT NOT NULL
    )""")

    conn.commit()
    conn.close()


def _ts() -> str:
    return datetime.now(pytz.timezone(MARKET_TIMEZONE)).isoformat()


def log_trade(strategy, broker, symbol, action, order_type,
              qty, price, fee_usd=0.0, pnl_usd=0.0,
              paper=True, order_id='', notes='',
              won=None, source=None, pnl_pct=0.0) -> int:
    """
    Log a trade to the SQLite trades table.

    Args:
        won:    1 if trade was profitable, 0 if loss, None for open legs.
                Used by walk_forward_trainer and _get_kelly_fraction.
        source: Trade source tag (e.g. 'paper_v10', 'live_v10', 'backtest').
                Used to filter ML training data to prevent contamination.
        pnl_pct: P&L as fraction of position size.
    """
    ts = _ts()
    value_usd = qty * price
    # Infer won from pnl_usd if not supplied explicitly
    if won is None and pnl_usd != 0:
        won = 1 if pnl_usd > 0 else 0
    if source is None:
        source = 'paper_v10' if paper else 'live_v10'
    conn = _conn()
    cur = conn.cursor()

    # Dedup guard: if an identical close (SELL/BUY with P&L) for this symbol+strategy
    # was already logged within the last 90 seconds, skip. Prevents double-logging
    # caused by the kill window between log_trade and delete_position on restart.
    if pnl_usd != 0:
        cur.execute("""
            SELECT id FROM trades
            WHERE symbol=? AND strategy=? AND action=? AND paper=?
              AND ABS(qty - ?) < 0.000001
              AND ts >= datetime('now', '-90 seconds')
            LIMIT 1
        """, (symbol, strategy, action, int(paper), qty))
        if cur.fetchone():
            conn.close()
            return -1  # silently skip duplicate

    cur.execute("""INSERT INTO trades
        (ts,strategy,broker,symbol,action,order_type,qty,price,value_usd,
         fee_usd,pnl_usd,paper,order_id,notes,won,source,pnl_pct)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ts, strategy, broker, symbol, action, order_type, qty, price,
         value_usd, fee_usd, pnl_usd, int(paper),
         order_id or f'PAPER_{uuid.uuid4().hex[:8]}', notes,
         won, source, pnl_pct))
    trade_id = cur.lastrowid
    conn.commit()
    conn.close()
    _csv_append(ts, strategy, broker, symbol, action, order_type,
                qty, price, value_usd, fee_usd, pnl_usd, paper, order_id, notes)
    return trade_id


def log_signal(strategy, symbol, signal, confidence,
               reason='', acted_on=False, price=0.0) -> None:
    conn = _conn()
    conn.cursor().execute(
        "INSERT INTO signals (ts,strategy,symbol,signal,confidence,reason,acted_on,price) VALUES (?,?,?,?,?,?,?,?)",
        (_ts(), strategy, symbol, signal, confidence, reason, int(acted_on), price))
    conn.commit()
    conn.close()


def log_debate(symbol, buy_votes, hold_votes, sell_votes,
               final_signal, confidence, reasoning, bull_case,
               bear_case, key_risk, agent_details, regime='') -> None:
    import json
    conn = _conn()
    conn.cursor().execute("""INSERT INTO debate_results
        (ts,symbol,buy_votes,hold_votes,sell_votes,final_signal,confidence,
         reasoning,bull_case,bear_case,key_risk,agent_details,regime)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (_ts(), symbol, buy_votes, hold_votes, sell_votes, final_signal,
         confidence, reasoning, bull_case, bear_case, key_risk,
         json.dumps(agent_details) if not isinstance(agent_details, str) else agent_details, regime))
    conn.commit()
    conn.close()


def log_event(level, source, message) -> None:
    conn = _conn()
    conn.cursor().execute(
        "INSERT INTO system_events (ts,level,source,message) VALUES (?,?,?,?)",
        (_ts(), level, source, message))
    conn.commit()
    conn.close()


def log_api_cost(call_type, input_tokens, output_tokens, cost_usd, symbol='') -> None:
    conn = _conn()
    conn.cursor().execute(
        "INSERT INTO api_costs (ts,call_type,input_tokens,output_tokens,cost_usd,symbol) VALUES (?,?,?,?,?,?)",
        (_ts(), call_type, input_tokens, output_tokens, cost_usd, symbol))
    conn.commit()
    conn.close()


def log_edge_snapshot(
    market: str,
    symbol: str,
    v_score: float = 0.0,
    e_score: float = 0.0,
    d_factor: float = 1.0,
    t_multiplier: float = 1.0,
    k_factor: float = 1.0,
    m_score: float = 0.0,
    final_size_usd: float = 0.0,
    debate_type: str = 'agents',
    notes: str = '',
) -> None:
    """Log a sizing edge snapshot for post-trade attribution and reporting."""
    conn = _conn()
    conn.cursor().execute(
        "INSERT INTO edge_snapshots "
        "(ts,market,symbol,v_score,e_score,d_factor,t_multiplier,k_factor,m_score,final_size_usd,debate_type,notes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (_ts(), market, symbol, v_score, e_score, d_factor, t_multiplier,
         k_factor, m_score, final_size_usd, debate_type, notes),
    )
    conn.commit()
    conn.close()


def log_trade_features(trade_id: int, symbol: str, direction: str,
                        features: dict) -> None:
    """
    Persist a 57-feature snapshot for a trade entry.

    Called immediately after a successful perps_engine.open_long/open_short so
    walk_forward_trainer._load_training_data() can join features to outcomes and
    train on the real 57-column feature matrix instead of 3-proxy scores.

    Args:
        trade_id:  The id returned by log_trade() for the BUY/SELL entry leg.
        symbol:    Trading pair (e.g. 'BTCUSDT').
        direction: 'LONG' or 'SHORT'.
        features:  Dict produced by feature_builder.build_features() + injections
                   in v10_runner._attempt_entry.  All 57 FEATURE_NAMES keys should
                   be present; extras are serialised and ignored at training time.
    """
    import json
    if not trade_id or trade_id <= 0:
        return
    try:
        conn = _conn()
        conn.cursor().execute(
            "INSERT INTO trade_features (trade_id, ts, symbol, direction, features_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (trade_id, time.time(), symbol, direction, json.dumps(features)),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # feature snapshot is best-effort; never block trade execution


# ─── Position persistence ─────────────────────────────────────────────────────

def persist_position(symbol, strategy, qty, entry, stop, target,
                     high_since_entry, ts_entry, paper=True,
                     direction='LONG', entry_reason='', low_since_entry=None,
                     atr_at_entry=0.0, composite_score=0.0,
                     trailing_active=False, trailing_stop_price=0.0,
                     scale_33_done=False, scale_66_done=False,
                     leverage=3) -> None:
    """Write open position to DB so restarts can recover it (including exit state)."""
    _low = low_since_entry if low_since_entry is not None else entry
    conn = _conn()
    conn.cursor().execute("""INSERT OR REPLACE INTO open_positions
        (symbol,strategy,qty,entry,stop,target,high_since_entry,low_since_entry,ts_entry,paper,
         direction,entry_reason,atr_at_entry,composite_score,
         trailing_active,trailing_stop_price,scale_33_done,scale_66_done,leverage)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (symbol, strategy, qty, entry, stop, target, high_since_entry, _low, ts_entry,
         int(paper), direction, entry_reason or '',
         float(atr_at_entry), float(composite_score),
         int(trailing_active), float(trailing_stop_price),
         int(scale_33_done), int(scale_66_done), int(leverage)))
    conn.commit()
    conn.close()


def delete_position(symbol, strategy, paper=True) -> None:
    conn = _conn()
    conn.cursor().execute(
        "DELETE FROM open_positions WHERE symbol=? AND strategy=? AND paper=?",
        (symbol, strategy, int(paper)))
    conn.commit()
    conn.close()


def load_open_positions(paper=True) -> list:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM open_positions WHERE paper=?", (int(paper),))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ─── Query helpers ────────────────────────────────────────────────────────────

def get_todays_trades(paper=True) -> list:
    today = datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime('%Y-%m-%d')
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM trades WHERE ts LIKE ? AND paper=? ORDER BY ts DESC",
                (f'{today}%', int(paper)))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_todays_signals() -> list:
    today = datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime('%Y-%m-%d')
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM signals WHERE ts LIKE ? ORDER BY ts DESC LIMIT 50",
                (f'{today}%',))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_recent_debates(limit=10) -> list:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM debate_results ORDER BY ts DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_todays_pnl(paper=True) -> float:
    today = datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime('%Y-%m-%d')
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(pnl_usd),0) FROM trades WHERE ts LIKE ? AND paper=?",
                (f'{today}%', int(paper)))
    val = cur.fetchone()[0]
    conn.close()
    return float(val)


def get_todays_fees(paper=True) -> float:
    """Returns total cost today: trading fees + Claude API costs."""
    today = datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime('%Y-%m-%d')
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(fee_usd),0) FROM trades WHERE ts LIKE ? AND paper=?",
                (f'{today}%', int(paper)))
    trade_fees = float(cur.fetchone()[0])
    cur.execute("SELECT COALESCE(SUM(cost_usd),0) FROM api_costs WHERE ts LIKE ?",
                (f'{today}%',))
    api_fees = float(cur.fetchone()[0])
    conn.close()
    return trade_fees + api_fees


def get_todays_trade_fees(paper=True) -> float:
    """Trading exchange fees only (excludes API costs)."""
    today = datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime('%Y-%m-%d')
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(fee_usd),0) FROM trades WHERE ts LIKE ? AND paper=?",
                (f'{today}%', int(paper)))
    val = cur.fetchone()[0]
    conn.close()
    return float(val)


def get_todays_api_cost() -> float:
    """Claude API cost today only."""
    today = datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime('%Y-%m-%d')
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(cost_usd),0) FROM api_costs WHERE ts LIKE ?",
                (f'{today}%',))
    val = cur.fetchone()[0]
    conn.close()
    return float(val)


def get_daily_trade_count(strategy, paper=True) -> int:
    today = datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime('%Y-%m-%d')
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM trades WHERE ts LIKE ? AND strategy=? AND paper=? AND action='BUY'",
                (f'{today}%', strategy, int(paper)))
    count = cur.fetchone()[0]
    conn.close()
    return count


def get_win_rate(strategy=None, lookback_days=14, paper=True) -> float:
    # Use pnl_usd != 0 so SHORT exits (logged as action='BUY') are counted.
    conn = _conn()
    cur = conn.cursor()
    if strategy:
        cur.execute("SELECT pnl_usd FROM trades WHERE strategy=? AND paper=? AND pnl_usd != 0 ORDER BY ts DESC LIMIT ?",
                    (strategy, int(paper), lookback_days * 5))
    else:
        cur.execute("SELECT pnl_usd FROM trades WHERE paper=? AND pnl_usd != 0 ORDER BY ts DESC LIMIT ?",
                    (int(paper), lookback_days * 5))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return 0.0
    wins = sum(1 for r in rows if r[0] > 0)
    return wins / len(rows)


def get_monthly_api_cost() -> float:
    today = datetime.now(pytz.timezone(MARKET_TIMEZONE))
    month_start = today.strftime('%Y-%m-01')
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(cost_usd),0) FROM api_costs WHERE ts >= ?",
                (month_start,))
    val = cur.fetchone()[0]
    conn.close()
    return float(val)


def get_all_time_stats(paper=True) -> dict:
    # Filter on pnl_usd != 0 (not action='SELL') so SHORT exits logged as
    # action='BUY' with non-zero pnl are counted correctly.
    # Respects TRADE_SESSION_START so pre-overhaul trades don't skew metrics.
    from config import TRADE_SESSION_START
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""SELECT COUNT(*) as total,
        SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN pnl_usd<0 THEN 1 ELSE 0 END) as losses,
        SUM(pnl_usd) as total_pnl,
        MAX(pnl_usd) as best_trade,
        MIN(pnl_usd) as worst_trade
        FROM trades WHERE paper=? AND pnl_usd != 0
        AND ts >= ?""", (int(paper), TRADE_SESSION_START))
    row = cur.fetchone()
    cur.execute("SELECT COALESCE(SUM(fee_usd), 0) FROM trades WHERE paper=? AND ts >= ?",
                (int(paper), TRADE_SESSION_START))
    total_fees = float(cur.fetchone()[0])
    conn.close()
    if not row or not row[0]:
        return {'total': 0, 'wins': 0, 'losses': 0, 'total_pnl': 0,
                'total_fees': 0, 'best_trade': 0, 'worst_trade': 0, 'win_rate': 0}
    total = row[0] or 0
    wins = row[1] or 0
    return {
        'total': total, 'wins': wins, 'losses': row[2] or 0,
        'total_pnl': row[3] or 0, 'total_fees': total_fees,
        'best_trade': row[4] or 0, 'worst_trade': row[5] or 0,
        'win_rate': wins / total if total > 0 else 0,
    }


def get_kelly_stats(strategy: str = None, paper: bool = True, window: int = 50) -> dict:
    """
    Compute rolling Kelly fraction from the last `window` closed trades.

    Returns:
      kelly_full  — f* = p - q/b  (raw Kelly fraction, can be negative)
      kelly_25pct — 25% fractional Kelly (use this for sizing)
      win_rate    — win rate in the window
      avg_win     — avg winning trade $
      avg_loss    — avg losing trade $ (absolute value)
      b_ratio     — avg_win / avg_loss (payoff ratio)
      n_trades    — number of trades in window
    """
    conn = _conn()
    cur = conn.cursor()
    if strategy:
        cur.execute(
            "SELECT pnl_usd FROM trades WHERE paper=? AND strategy=? AND pnl_usd != 0 "
            "ORDER BY ts DESC LIMIT ?",
            (int(paper), strategy, window)
        )
    else:
        cur.execute(
            "SELECT pnl_usd FROM trades WHERE paper=? AND pnl_usd != 0 "
            "ORDER BY ts DESC LIMIT ?",
            (int(paper), window)
        )
    rows = [r[0] for r in cur.fetchall()]
    conn.close()

    _default = {'kelly_full': 0.0, 'kelly_25pct': 0.0, 'win_rate': 0.0,
                'avg_win': 0.0, 'avg_loss': 0.0, 'b_ratio': 1.0, 'n_trades': 0}
    if len(rows) < 10:   # need at least 10 trades for meaningful Kelly
        return _default

    wins   = [r for r in rows if r > 0]
    losses = [r for r in rows if r < 0]
    if not wins or not losses:
        return _default

    p = len(wins) / len(rows)
    q = 1.0 - p
    avg_win  = sum(wins)  / len(wins)
    avg_loss = abs(sum(losses) / len(losses))
    b = avg_win / avg_loss if avg_loss > 0 else 1.0

    kelly_full = p - q / b
    kelly_25pct = max(0.0, kelly_full * 0.25)  # floor at 0 (never negative size)

    return {
        'kelly_full':   round(kelly_full, 4),
        'kelly_25pct':  round(kelly_25pct, 4),
        'win_rate':     round(p, 4),
        'avg_win':      round(avg_win, 4),
        'avg_loss':     round(avg_loss, 4),
        'b_ratio':      round(b, 4),
        'n_trades':     len(rows),
    }


def get_recent_trades(limit=20, paper=True) -> list:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM trades WHERE paper=? ORDER BY ts DESC LIMIT ?",
                (int(paper), limit))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_recent_events(limit=20, level=None) -> list:
    conn = _conn()
    cur = conn.cursor()
    if level:
        cur.execute("SELECT * FROM system_events WHERE level=? ORDER BY ts DESC LIMIT ?",
                    (level, limit))
    else:
        cur.execute("SELECT * FROM system_events ORDER BY ts DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_today_stats(paper=True) -> dict:
    """Today-only stats: closed trades (pnl_usd != 0), wins, losses, fees, net P&L."""
    today = datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime('%Y-%m-%d')
    conn = _conn()
    cur = conn.cursor()
    # Closed trade counts and gross P&L — only rows with actual P&L
    cur.execute("""SELECT
        COUNT(*) as total,
        SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN pnl_usd<0 THEN 1 ELSE 0 END) as losses,
        COALESCE(SUM(pnl_usd), 0) as gross_pnl
        FROM trades WHERE ts LIKE ? AND paper=? AND pnl_usd != 0""",
        (f'{today}%', int(paper)))
    row = cur.fetchone()
    # Fees across ALL trades today (BUY + SELL both charged fees)
    cur.execute(
        "SELECT COALESCE(SUM(fee_usd), 0) FROM trades WHERE ts LIKE ? AND paper=?",
        (f'{today}%', int(paper)))
    fees = float(cur.fetchone()[0])
    conn.close()
    total = row[0] or 0
    wins  = row[1] or 0
    gross = float(row[3] or 0.0)
    return {
        'total': total, 'wins': wins, 'losses': row[2] or 0,
        'win_rate': wins / total if total > 0 else 0.0,
        'gross_pnl': gross, 'fees': fees, 'net_pnl': gross - fees,
    }


def get_tax_summary(paper: bool = False) -> dict:
    """
    Pull all realized P&L data for tax calculations.
    Separates gains from losses, groups by asset class and year.
    Uses paper=False by default — live trades are what matter for taxes.
    """
    conn = _conn()
    cur = conn.cursor()

    # All closed trades with P&L — both gains and losses
    cur.execute("""
        SELECT ts, strategy, symbol, pnl_usd, fee_usd, value_usd
        FROM trades
        WHERE paper=? AND pnl_usd != 0
        ORDER BY ts ASC
    """, (int(paper),))
    rows = [dict(r) for r in cur.fetchall()]

    # Annual breakdown
    annual: dict = {}
    for r in rows:
        year = r['ts'][:4]
        if year not in annual:
            annual[year] = {'gains': 0.0, 'losses': 0.0, 'fees': 0.0,
                            'trades': 0, 'crypto': 0.0, 'equity': 0.0}
        pnl = float(r['pnl_usd'] or 0)
        fee = float(r['fee_usd'] or 0)
        annual[year]['trades'] += 1
        annual[year]['fees'] += fee
        if pnl > 0:
            annual[year]['gains'] += pnl
        else:
            annual[year]['losses'] += pnl
        if 'crypto' in r.get('strategy', ''):
            annual[year]['crypto'] += pnl
        else:
            annual[year]['equity'] += pnl

    total_gains  = sum(v['gains']  for v in annual.values())
    total_losses = sum(v['losses'] for v in annual.values())
    total_fees   = sum(v['fees']   for v in annual.values())
    net_pnl      = total_gains + total_losses  # losses are negative

    conn.close()
    return {
        'rows': rows,
        'annual': annual,
        'total_gains': total_gains,
        'total_losses': total_losses,
        'total_fees': total_fees,
        'net_pnl': net_pnl,
        'total_trades': len(rows),
    }


def get_recent_tv_signal(symbol: str, max_age_seconds: int = 300) -> dict | None:
    """Return the most recent TradingView webhook signal for `symbol` if it arrived
    within `max_age_seconds`.  Returns None if no fresh signal exists.

    The returned dict has keys: symbol, action, price, tf_min, signal, ts
    """
    import json
    from datetime import timezone
    try:
        conn = _conn()
        cur  = conn.cursor()
        # Pull last 20 tradingview events and find a match (small result set, avoids LIKE index miss)
        cur.execute(
            "SELECT message, ts FROM system_events WHERE source='tradingview' ORDER BY ts DESC LIMIT 20"
        )
        rows = cur.fetchall()
        conn.close()
        now = datetime.now(timezone.utc)
        for msg, ts_str in rows:
            try:
                data = json.loads(msg)
            except Exception:
                continue
            # Check symbol match
            if data.get('symbol', '').upper() != symbol.upper():
                continue
            # Check age
            ts_dt = datetime.fromisoformat(data.get('ts', ts_str))
            if not ts_dt.tzinfo:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            age = (now - ts_dt).total_seconds()
            if age <= max_age_seconds:
                return data
        return None
    except Exception:
        return None


def get_recent_notifications(limit=30) -> list:
    """Return notifications written by the alert system (source='notify')."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM system_events WHERE source='notify' ORDER BY ts DESC LIMIT ?",
        (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _parse_scan_msg(msg: str, ts: str) -> dict:
    """Parse a scan_feed log message into structured fields for dashboard display."""
    import re
    out = {'ts': ts, 'symbol': None, 'action': 'HOLD', 'confidence': 0.0,
           'strategy': 'crypto', 'message': (msg or '')[:120]}
    if not msg:
        return out
    # Extract lane and symbol: "[crypto] BTC-USDC ..." or "[perp] BTCUSDT ..."
    m = re.match(r'\[(crypto|perp|equity|futures|deriv)\]\s+(\S+)', msg)
    if m:
        out['strategy'] = m.group(1)
        out['symbol'] = m.group(2).upper()
    # Extract confidence: "conf=75%" pattern
    c = re.search(r'conf=(\d+)%', msg)
    if c:
        out['confidence'] = float(c.group(1)) / 100
    # Determine action from message content
    msg_l = msg.lower()
    if 'calling debate' in msg_l or '✅ buy' in msg or 'near_miss' in msg_l:
        out['action'] = 'BUY'
    # HOLD is the default — skip debate / abort / veto / block all stay HOLD
    return out


def get_scan_feed(limit=40) -> list:
    """Return recent scan activity log entries (source='scan_feed'), newest first.

    Returns structured dicts with symbol/action/confidence/strategy fields
    (parsed from the human-readable log message) so dashboard components can
    use them directly without regex parsing on the caller side.
    """
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT ts, message FROM system_events WHERE source='scan_feed' ORDER BY ts DESC LIMIT ?",
        (limit,))
    rows = [_parse_scan_msg(r[1], r[0]) for r in cur.fetchall()]
    conn.close()
    return rows


def get_performance_attribution(paper=True, lookback_days=30) -> dict:
    """
    Break down P&L, win rate, and trade count by strategy.
    Returns: {strategy_name: {total, wins, losses, win_rate, total_pnl, avg_pnl}}
    """
    from datetime import timedelta
    cutoff = (datetime.now(pytz.timezone(MARKET_TIMEZONE)) -
              timedelta(days=lookback_days)).strftime('%Y-%m-%d')
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT strategy,
               COUNT(*)                                    AS total,
               SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
               COALESCE(SUM(pnl_usd), 0)                  AS total_pnl,
               COALESCE(AVG(pnl_usd), 0)                  AS avg_pnl
        FROM trades
        WHERE paper=? AND pnl_usd != 0 AND ts >= ?
        GROUP BY strategy
        ORDER BY total_pnl DESC
    """, (int(paper), cutoff))
    rows = cur.fetchall()
    conn.close()
    result = {}
    for r in rows:
        total = r[1] or 0
        wins  = r[2] or 0
        result[r[0]] = {
            'total': total,
            'wins':  wins,
            'losses': total - wins,
            'win_rate': wins / total if total > 0 else 0.0,
            'total_pnl': float(r[3]),
            'avg_pnl':   float(r[4]),
        }
    return result


def get_strategy_consecutive_losses(strategy: str, paper=True) -> int:
    """Return the current consecutive loss streak for a strategy (most recent trades first)."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""SELECT pnl_usd FROM trades
        WHERE strategy=? AND paper=? AND pnl_usd != 0
        ORDER BY ts DESC LIMIT 20""", (strategy, int(paper)))
    rows = cur.fetchall()
    conn.close()
    streak = 0
    for r in rows:
        if r[0] < 0:
            streak += 1
        else:
            break
    return streak


def get_trade_quality_stats(lookback: int = 20, paper: bool = True) -> dict:
    """
    Compute Trade Quality scorecard from the last N closed trades in trade_attribution.

    Returns
    -------
    dict with keys:
      entry_timing    : 0-10  (10 = zero adverse excursion before price moved in our favour)
      exit_efficiency : 0-10  (10 = exited at peak MFE)
      thesis_hit_rate : 0-1   (fraction where MFE >= 1.5%, i.e. cleared the crypto stop)
      exit_type_dist  : dict  {exit_type: count}
      avg_super_score : float (avg of non-zero super_score values)
      n               : int   (actual row count used)
    """
    _defaults = {
        'entry_timing':    5.0,
        'exit_efficiency': 5.0,
        'thesis_hit_rate': 0.0,
        'exit_type_dist':  {},
        'avg_super_score': 0.0,
        'n':               0,
    }
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT mae_pct, mfe_pct, pnl_pct, exit_type, won, super_score, ml_p_win
            FROM trade_attribution
            ORDER BY entry_ts DESC
            LIMIT ?
        """, (lookback,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
    except Exception:
        return _defaults

    if not rows:
        return _defaults

    n = len(rows)

    # entry_timing: 10 * (1 - avg(min(|mae_pct| / 0.015, 1.0)))
    mae_vals = [abs(float(r.get('mae_pct') or 0)) for r in rows]
    avg_mae_ratio = sum(min(v / 0.015, 1.0) for v in mae_vals) / n
    entry_timing = round(10.0 * (1.0 - avg_mae_ratio), 2)

    # exit_efficiency: 10 * avg(pnl_pct / mfe_pct) where mfe_pct > 0.001
    eff_pairs = [
        (float(r.get('pnl_pct') or 0), float(r.get('mfe_pct') or 0))
        for r in rows
        if float(r.get('mfe_pct') or 0) > 0.001
    ]
    if eff_pairs:
        ratios = [min(pnl / mfe, 1.0) for pnl, mfe in eff_pairs]  # cap at 1
        exit_efficiency = round(10.0 * (sum(ratios) / len(ratios)), 2)
    else:
        exit_efficiency = 5.0

    # thesis_hit_rate: fraction where mfe_pct >= 0.015
    thesis_hits = sum(1 for r in rows if float(r.get('mfe_pct') or 0) >= 0.015)
    thesis_hit_rate = round(thesis_hits / n, 4)

    # exit_type_dist
    from collections import Counter
    exit_type_dist = dict(Counter(
        r.get('exit_type') or 'unknown' for r in rows
    ))

    # avg_super_score — exclude rows where super_score == 0 (old rows before this column existed)
    scored_rows = [float(r.get('super_score') or 0) for r in rows if float(r.get('super_score') or 0) > 0]
    avg_super_score = round(sum(scored_rows) / len(scored_rows), 2) if scored_rows else 0.0

    return {
        'entry_timing':    max(0.0, min(10.0, entry_timing)),
        'exit_efficiency': max(0.0, min(10.0, exit_efficiency)),
        'thesis_hit_rate': thesis_hit_rate,
        'exit_type_dist':  exit_type_dist,
        'avg_super_score': avg_super_score,
        'n':               n,
    }


def get_open_position_health(paper: bool = True) -> list:
    """
    Return current open positions with full health metadata.

    Returns
    -------
    list of dicts: symbol, strategy, entry, stop, target,
                   high_since_entry, low_since_entry, ts_entry, qty, direction
    """
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT symbol, strategy, qty, entry, stop, target, "
            "high_since_entry, low_since_entry, ts_entry, direction "
            "FROM open_positions WHERE paper=?",
            (int(paper),)
        )
        rows = cur.fetchall()
        conn.close()
        result = []
        for r in rows:
            result.append({
                'symbol':           r[0],
                'strategy':         r[1],
                'qty':              r[2],
                'entry':            r[3],
                'stop':             r[4],
                'target':           r[5],
                'high_since_entry': r[6],
                'low_since_entry':  r[7],
                'ts_entry':         r[8],
                'direction':        r[9] or 'LONG',
            })
        return result
    except Exception:
        return []


def get_intelligence_log(limit: int = 30) -> dict:
    """
    Pull together all self-improvement events for the SELF-LEARNING LOG panel.

    Returns:
        meta_analyses:   list of meta-analysis runs (insight, WR, trades, timestamp)
        recommendations: list of active/recent signal weight recommendations
        agent_accuracy:  list of per-agent accuracy stats
        ml_events:       list of ML retrain events from system_events
        signal_shifts:   top signals ranked by Bayesian pts (current state)
    """
    try:
        conn = _conn()
        cur  = conn.cursor()

        # Meta-analysis runs (what Claude learned from recent trades)
        try:
            cur.execute("""
                SELECT created_at, trades_analyzed, win_rate, key_insight, patterns_found, recs_count
                FROM meta_analysis_log
                ORDER BY created_at DESC LIMIT ?
            """, (limit,))
            meta_analyses = [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]
        except Exception:
            meta_analyses = []

        # Active + recent signal weight recommendations
        try:
            cur.execute("""
                SELECT signal_name, regime, weight_delta, reasoning, pattern, confidence, created_at, applied
                FROM meta_recommendations
                ORDER BY created_at DESC LIMIT ?
            """, (limit,))
            recommendations = [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]
        except Exception:
            recommendations = []

        # Agent accuracy
        try:
            cur.execute("""
                SELECT agent_name, regime, accuracy, total_assessed, votes_buy, votes_hold, last_updated
                FROM agent_stats
                WHERE regime = 'any'
                ORDER BY total_assessed DESC
            """)
            agent_accuracy = [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]
        except Exception:
            agent_accuracy = []

        # ML retrain events
        try:
            cur.execute("""
                SELECT ts, message FROM system_events
                WHERE (source='ml_trainer' OR message LIKE '%retrain%' OR message LIKE '%ml_model%'
                       OR message LIKE '%ML model%' OR message LIKE '%Background retrain%')
                ORDER BY ts DESC LIMIT ?
            """, (limit,))
            ml_events = [{'ts': r[0], 'message': r[1]} for r in cur.fetchall()]
        except Exception:
            ml_events = []

        # Top signals by current Bayesian pts
        try:
            cur.execute("""
                SELECT signal_name, regime, fires, wins, losses, win_rate, bayesian_pts, last_updated
                FROM signal_stats
                WHERE fires >= 5
                ORDER BY bayesian_pts DESC LIMIT 15
            """)
            signal_shifts = [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]
        except Exception:
            signal_shifts = []

        conn.close()
        return {
            'meta_analyses':   meta_analyses,
            'recommendations': recommendations,
            'agent_accuracy':  agent_accuracy,
            'ml_events':       ml_events,
            'signal_shifts':   signal_shifts,
        }
    except Exception:
        return {
            'meta_analyses': [], 'recommendations': [], 'agent_accuracy': [],
            'ml_events': [], 'signal_shifts': [],
        }


def _csv_append(ts, strategy, broker, symbol, action, order_type,
                qty, price, value_usd, fee_usd, pnl_usd, paper, order_id, notes):
    os.makedirs(CSV_LOG_DIR, exist_ok=True)
    date_str = ts[:10]
    path = os.path.join(CSV_LOG_DIR, f'trades_{date_str}.csv')
    write_header = not os.path.exists(path)
    with open(path, 'a', newline='') as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(['ts','strategy','broker','symbol','action','order_type',
                        'qty','price','value_usd','fee_usd','pnl_usd','paper','order_id','notes'])
        w.writerow([ts,strategy,broker,symbol,action,order_type,
                    qty,price,value_usd,fee_usd,pnl_usd,paper,order_id,notes])


init_db()
