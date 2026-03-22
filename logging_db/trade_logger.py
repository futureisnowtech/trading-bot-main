"""
logging_db/trade_logger.py
SQLite trade log + position persistence + CSV export.
Positions are written to disk on every open/close so a restart never loses state.
"""
import sqlite3
import csv
import os
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
        PRIMARY KEY (symbol, strategy, paper)
    )""")
    try:
        cur.execute("ALTER TABLE open_positions ADD COLUMN direction TEXT DEFAULT 'LONG'")
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

    conn.commit()
    conn.close()


def _ts() -> str:
    return datetime.now(pytz.timezone(MARKET_TIMEZONE)).isoformat()


def log_trade(strategy, broker, symbol, action, order_type,
              qty, price, fee_usd=0.0, pnl_usd=0.0,
              paper=True, order_id='', notes='') -> int:
    ts = _ts()
    value_usd = qty * price
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""INSERT INTO trades
        (ts,strategy,broker,symbol,action,order_type,qty,price,value_usd,
         fee_usd,pnl_usd,paper,order_id,notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ts, strategy, broker, symbol, action, order_type, qty, price,
         value_usd, fee_usd, pnl_usd, int(paper), order_id or f'PAPER_{uuid.uuid4().hex[:8]}', notes))
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


# ─── Position persistence ─────────────────────────────────────────────────────

def persist_position(symbol, strategy, qty, entry, stop, target,
                     high_since_entry, ts_entry, paper=True, direction='LONG') -> None:
    """Write open position to DB so restarts can recover it."""
    conn = _conn()
    conn.cursor().execute("""INSERT OR REPLACE INTO open_positions
        (symbol,strategy,qty,entry,stop,target,high_since_entry,ts_entry,paper,direction)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (symbol, strategy, qty, entry, stop, target, high_since_entry, ts_entry, int(paper), direction))
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
    today = datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime('%Y-%m-%d')
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(fee_usd),0) FROM trades WHERE ts LIKE ? AND paper=?",
                (f'{today}%', int(paper)))
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
    conn = _conn()
    cur = conn.cursor()
    if strategy:
        cur.execute("SELECT pnl_usd FROM trades WHERE strategy=? AND paper=? AND action='SELL' ORDER BY ts DESC LIMIT ?",
                    (strategy, int(paper), lookback_days * 5))
    else:
        cur.execute("SELECT pnl_usd FROM trades WHERE paper=? AND action='SELL' ORDER BY ts DESC LIMIT ?",
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
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""SELECT COUNT(*) as total,
        SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN pnl_usd<0 THEN 1 ELSE 0 END) as losses,
        SUM(pnl_usd) as total_pnl,
        MAX(pnl_usd) as best_trade,
        MIN(pnl_usd) as worst_trade
        FROM trades WHERE paper=? AND action='SELL'""", (int(paper),))
    row = cur.fetchone()
    conn.close()
    if not row or not row[0]:
        return {'total': 0, 'wins': 0, 'losses': 0, 'total_pnl': 0,
                'best_trade': 0, 'worst_trade': 0, 'win_rate': 0}
    total = row[0] or 0
    wins = row[1] or 0
    return {
        'total': total, 'wins': wins, 'losses': row[2] or 0,
        'total_pnl': row[3] or 0, 'best_trade': row[4] or 0,
        'worst_trade': row[5] or 0,
        'win_rate': wins / total if total > 0 else 0,
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
