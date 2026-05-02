"""
notifications/notification_engine.py — v10 notification system.

Replaces Telegram entirely. All events are written to the `notifications` SQLite
table and consumed by the dashboard's right-side panel.

Eight notification categories:
    TRADE_OPEN    — new position entered
    TRADE_CLOSE   — position closed (with P&L and WHY)
    SIGNAL        — signal fired but no trade (filtered, informational)
    RISK          — risk engine warnings (drawdown, margin, correlation)
    RBI           — incubation events (promoted, graduated, killed)
    ML            — model updates (retrain, calibration, Brier score change)
    KILL_SWITCH   — kill switch trigger/resume
    SYSTEM        — system events (bot start, halt, scanner errors)

Severity levels: INFO, WARNING, CRITICAL

Every notification stores a `why` block (JSON dict) with:
    top_3_reasons: list of str
    features: dict of key feature values
    regime: str
    score: float (composite signal score)

Table: notifications (kept to last 500 rows, older rows pruned on write)
"""

import json
import logging
import time
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Category constants (use these, not raw strings)
CAT_TRADE_OPEN  = 'TRADE_OPEN'
CAT_TRADE_CLOSE = 'TRADE_CLOSE'
CAT_SIGNAL      = 'SIGNAL'
CAT_RISK        = 'RISK'
CAT_RBI         = 'RBI'
CAT_ML          = 'ML'
CAT_KILL_SWITCH = 'KILL_SWITCH'
CAT_SYSTEM      = 'SYSTEM'

ALL_CATEGORIES = [
    CAT_TRADE_OPEN, CAT_TRADE_CLOSE, CAT_SIGNAL,
    CAT_RISK, CAT_RBI, CAT_ML, CAT_KILL_SWITCH, CAT_SYSTEM,
]

SEV_INFO     = 'INFO'
SEV_WARNING  = 'WARNING'
SEV_CRITICAL = 'CRITICAL'

_MAX_NOTIFICATIONS = 500
_PRUNE_TO          = 450  # keep this many after pruning


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class NotificationEvent:
    category:  str
    severity:  str
    title:     str
    message:   str
    why:       Dict        = field(default_factory=dict)
    data:      Dict        = field(default_factory=dict)
    timestamp: float       = field(default_factory=time.time)
    id:        Optional[int] = None
    read:      bool        = False

    def to_dict(self) -> Dict:
        d = asdict(self)
        d['why_json']  = json.dumps(self.why)
        d['data_json'] = json.dumps(self.data)
        return d


def make_why(top_3: List[str],
             features: Optional[Dict] = None,
             regime: str = 'UNKNOWN',
             score: float = 0.0) -> Dict:
    """Helper to build a well-formed `why` block."""
    return {
        'top_3_reasons': top_3[:3],
        'features':      features or {},
        'regime':        regime,
        'score':         round(score, 2),
    }


# ── DB helpers ────────────────────────────────────────────────────────────────

def _conn():
    from logging_db.trade_logger import _conn as _tc
    return _tc()


def _ensure_table():
    """Ensure notifications table exists and has all v10 columns (idempotent)."""
    try:
        conn = _conn()
        # Create table if it doesn't exist (matches Phase 1 migration schema)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id        TEXT    PRIMARY KEY,
                ts        TEXT    NOT NULL,
                category  TEXT    NOT NULL,
                severity  TEXT    NOT NULL DEFAULT 'INFO',
                title     TEXT    NOT NULL,
                message   TEXT    NOT NULL,
                data      TEXT    DEFAULT '{}',
                read      INTEGER DEFAULT 0
            )
        """)
        # Add why_json column if missing (upgrade from Phase 1 schema)
        for col_sql in [
            "ALTER TABLE notifications ADD COLUMN why_json TEXT DEFAULT '{}'",
        ]:
            try:
                conn.execute(col_sql)
            except Exception:
                pass  # column already exists
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notif_ts ON notifications(ts DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notif_cat ON notifications(category)"
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f'[notif] table init error: {e}')


def _prune(conn):
    """Keep at most _MAX_NOTIFICATIONS rows; prune oldest when over limit."""
    try:
        count = conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
        if count > _MAX_NOTIFICATIONS:
            conn.execute("""
                DELETE FROM notifications WHERE id IN (
                    SELECT id FROM notifications
                    ORDER BY CAST(ts AS REAL) ASC
                    LIMIT ?
                )
            """, (count - _PRUNE_TO,))
    except Exception:
        pass


# ── Core write API ────────────────────────────────────────────────────────────

def push(event: NotificationEvent) -> Optional[str]:
    """
    Write a notification to the DB.
    Returns the row id string, or None on failure.
    """
    _ensure_table()
    try:
        import uuid
        row_id = str(uuid.uuid4())
        ts_str = str(event.timestamp)
        conn = _conn()
        _prune(conn)
        conn.execute("""
            INSERT INTO notifications (id, ts, category, severity, title, message, why_json, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row_id,
            ts_str,
            event.category,
            event.severity,
            event.title,
            event.message,
            json.dumps(event.why),
            json.dumps(event.data),
        ))
        event.id = row_id
        conn.commit()
        conn.close()
        logger.debug(f'[notif] {event.severity} {event.category}: {event.title}')

        # Production integration: Send to Telegram if critical or trade event
        if event.severity == SEV_CRITICAL or event.category in (CAT_TRADE_OPEN, CAT_TRADE_CLOSE):
            try:
                from notifications.telegram_bot import send_message as tg_send
                tg_text = f"<b>{event.title}</b>\n{event.message}"
                tg_send(tg_text)
            except Exception as e:
                logger.error(f"Telegram dispatch error: {e}")

        return row_id
    except Exception as e:
        logger.warning(f'[notif] push error: {e}')
        return None


# ── Convenience factories ─────────────────────────────────────────────────────

def notify_trade_open(symbol: str, direction: str, size_usd: float,
                      entry_price: float, score: float,
                      top_3: List[str], features: Dict,
                      regime: str = 'UNKNOWN') -> None:
    """Notify when a new position is opened."""
    push(NotificationEvent(
        category=CAT_TRADE_OPEN,
        severity=SEV_INFO,
        title=f'{direction} {symbol} @ ${entry_price:,.2f}',
        message=(f'Size: ${size_usd:.0f}  Score: {score:.1f}  '
                 f'Regime: {regime}'),
        why=make_why(top_3, features, regime, score),
        data={'symbol': symbol, 'direction': direction,
              'entry': entry_price, 'size_usd': size_usd},
    ))


def notify_trade_close(symbol: str, direction: str, pnl_usd: float,
                       pnl_pct: float, exit_type: str,
                       top_3: List[str], features: Dict,
                       regime: str = 'UNKNOWN', score: float = 0.0) -> None:
    """Notify when a position is closed, including WHY it was closed."""
    sev = SEV_CRITICAL if pnl_usd < -50 else (SEV_WARNING if pnl_usd < 0 else SEV_INFO)
    push(NotificationEvent(
        category=CAT_TRADE_CLOSE,
        severity=sev,
        title=f'CLOSED {direction} {symbol}  P&L ${pnl_usd:+.2f} ({pnl_pct:+.1%})',
        message=f'Exit: {exit_type}  Regime: {regime}',
        why=make_why(top_3, features, regime, score),
        data={'symbol': symbol, 'pnl_usd': pnl_usd, 'pnl_pct': pnl_pct,
              'exit_type': exit_type},
    ))


def notify_signal(symbol: str, direction: str, score: float,
                  threshold: float, reason: str,
                  regime: str = 'UNKNOWN') -> None:
    """Notify when a strong signal fired but no trade was opened (e.g. position limit)."""
    push(NotificationEvent(
        category=CAT_SIGNAL,
        severity=SEV_INFO,
        title=f'SIGNAL {direction} {symbol}  score={score:.1f}/{threshold:.0f}',
        message=reason,
        why=make_why([reason], {}, regime, score),
        data={'symbol': symbol, 'direction': direction, 'score': score},
    ))


def notify_risk(title: str, detail: str, severity: str = SEV_WARNING,
                data: Optional[Dict] = None) -> None:
    """Notify risk engine events (drawdown levels, margin warnings, correlation)."""
    push(NotificationEvent(
        category=CAT_RISK,
        severity=severity,
        title=title,
        message=detail,
        data=data or {},
    ))


def notify_rbi(event_type: str, symbol: str, combo: List[str],
               detail: str, wr: float = 0.0, pf: float = 0.0) -> None:
    """Notify RBI lifecycle events (promoted, graduated, killed, demoted)."""
    sev = SEV_WARNING if event_type in ('killed', 'demoted') else SEV_INFO
    push(NotificationEvent(
        category=CAT_RBI,
        severity=sev,
        title=f'RBI {event_type.upper()} — {symbol}',
        message=(f'{", ".join(combo[:3])}{"…" if len(combo) > 3 else ""}  '
                 f'WR={wr:.0%} PF={pf:.2f}  {detail}'),
        data={'symbol': symbol, 'event': event_type,
              'combo': combo, 'wr': wr, 'pf': pf},
    ))


def notify_ml(event_type: str, pair_key: str, direction: str,
              brier: float = 0.0, n_samples: int = 0, detail: str = '') -> None:
    """Notify ML model events (retrain, calibration, Brier score change)."""
    sev = SEV_WARNING if brier > 0.22 else SEV_INFO
    push(NotificationEvent(
        category=CAT_ML,
        severity=sev,
        title=f'ML {event_type} — {pair_key}/{direction}',
        message=f'Brier={brier:.3f}  n={n_samples}  {detail}',
        data={'pair': pair_key, 'direction': direction,
              'brier': brier, 'n_samples': n_samples},
    ))


def notify_kill_switch(trigger_type: str, detail: str,
                       is_resume: bool = False) -> None:
    """Notify kill switch trigger or manual resume."""
    sev = SEV_INFO if is_resume else SEV_CRITICAL
    action = 'RESUME' if is_resume else 'TRIGGERED'
    push(NotificationEvent(
        category=CAT_KILL_SWITCH,
        severity=sev,
        title=f'KILL SWITCH {action} — {trigger_type}',
        message=detail,
        data={'trigger': trigger_type, 'resume': is_resume},
    ))


def notify_system(title: str, detail: str,
                  severity: str = SEV_INFO) -> None:
    """Generic system event notification."""
    push(NotificationEvent(
        category=CAT_SYSTEM,
        severity=severity,
        title=title,
        message=detail,
    ))


# ── Read API (used by dashboard) ──────────────────────────────────────────────

def get_notifications(limit: int = 50,
                      category_filter: Optional[str] = None,
                      unread_only: bool = False) -> List[Dict]:
    """
    Fetch latest notifications for the dashboard.

    Args:
        limit:           Max rows to return
        category_filter: One of ALL_CATEGORIES, or None for all
        unread_only:     Only return unread notifications

    Returns:
        List of dicts with: id, ts, category, severity, title, message, why, data, read
    """
    _ensure_table()
    try:
        conn = _conn()
        where_clauses = []
        params: List[Any] = []

        if category_filter and category_filter != 'ALL':
            where_clauses.append('category = ?')
            params.append(category_filter)
        if unread_only:
            where_clauses.append('read = 0')

        where = ('WHERE ' + ' AND '.join(where_clauses)) if where_clauses else ''
        params.append(limit)

        rows = conn.execute(f"""
            SELECT id, ts, category, severity, title, message,
                   why_json, data, read
            FROM notifications
            {where}
            ORDER BY ts DESC
            LIMIT ?
        """, params).fetchall()
        conn.close()

        result = []
        for r in rows:
            why_parsed = {}
            data_parsed = {}
            try:
                why_parsed = json.loads(r[6] or '{}')
            except Exception:
                pass
            try:
                data_parsed = json.loads(r[7] or '{}')
            except Exception:
                pass
            # ts may be a float string or ISO string
            ts_raw = r[1]
            try:
                ts_float = float(ts_raw)
            except Exception:
                ts_float = 0.0
            result.append({
                'id':       r[0],
                'ts':       ts_float,
                'category': r[2],
                'severity': r[3],
                'title':    r[4],
                'message':  r[5],
                'why':      why_parsed,
                'data':     data_parsed,
                'read':     bool(r[8]),
            })
        return result
    except Exception as e:
        logger.debug(f'[notif] get error: {e}')
        return []


def mark_read(notification_id) -> None:
    """Mark a single notification as read."""
    try:
        conn = _conn()
        conn.execute("UPDATE notifications SET read=1 WHERE id=?", (str(notification_id),))
        conn.commit()
        conn.close()
    except Exception:
        pass


def mark_all_read(category: Optional[str] = None) -> None:
    """Mark all (optionally filtered) notifications as read."""
    try:
        conn = _conn()
        if category and category != 'ALL':
            conn.execute(
                "UPDATE notifications SET read=1 WHERE category=?", (category,)
            )
        else:
            conn.execute("UPDATE notifications SET read=1")
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_unread_count(category: Optional[str] = None) -> int:
    """Count unread notifications (optionally for one category)."""
    _ensure_table()
    try:
        conn = _conn()
        if category and category != 'ALL':
            n = conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE read=0 AND category=?",
                (category,)
            ).fetchone()[0]
        else:
            n = conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE read=0"
            ).fetchone()[0]
        conn.close()
        return n
    except Exception:
        return 0


def get_category_counts() -> Dict[str, int]:
    """Return unread count per category (for filter bar badges)."""
    _ensure_table()
    try:
        conn = _conn()
        rows = conn.execute("""
            SELECT category, COUNT(*) FROM notifications
            WHERE read=0
            GROUP BY category
        """).fetchall()
        conn.close()
        counts = {cat: 0 for cat in ALL_CATEGORIES}
        for r in rows:
            counts[r[0]] = r[1]
        return counts
    except Exception:
        return {cat: 0 for cat in ALL_CATEGORIES}
