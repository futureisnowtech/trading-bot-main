"""
learning/tax_tracker.py — Tax lot tracking and estimated liability.

Handles all three tax treatments this system encounters:
  - Section 1256 (MES/ES futures, Bybit perps): 60% long-term / 40% short-term
    regardless of hold period. Blended rate ~17% vs 22-37% for short-term.
    THIS IS THE TAX-PREFERRED VEHICLE. Futures trading has a genuine tax edge.
  - Short-term gains (< 1 year): ordinary income rates (~22-37%)
  - Long-term gains (≥ 1 year): preferential rates (0%, 15%, or 20%)

Disclaimer: For awareness only. Actual rates depend on total income and state taxes.
Consult a CPA for tax advice.

Usage:
    from learning.tax_tracker import record_tax_lot, get_ytd_summary, get_estimated_liability
"""
import sqlite3, os, sys
from datetime import datetime
from typing import Optional, List
import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH, MARKET_TIMEZONE

# ── Tax rate estimates (mid-bracket federal; actual depends on income) ────────
TAX_RATE_SHORT_TERM:   float = 0.32    # 32% federal ordinary income (conservative estimate)
TAX_RATE_LONG_TERM:    float = 0.15    # 15% federal LTCG (most common bracket)
# Section 1256: 60% × 15% LTCG + 40% × 32% ST = 9% + 12.8% = 21.8% blended
TAX_RATE_1256_BLENDED: float = 0.60 * TAX_RATE_LONG_TERM + 0.40 * TAX_RATE_SHORT_TERM

# Asset classes eligible for Section 1256 treatment
SECTION_1256_ASSET_CLASSES = {'futures', 'mes', 'es'}


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_tax_tables() -> None:
    """Create tax_lots table if it doesn't exist. Safe to call multiple times."""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tax_lots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol          TEXT    NOT NULL,
                strategy        TEXT    NOT NULL,
                asset_class     TEXT    NOT NULL,
                paper           INTEGER NOT NULL DEFAULT 1,
                entry_ts        TEXT    NOT NULL,
                exit_ts         TEXT    NOT NULL,
                entry_price     REAL    NOT NULL,
                exit_price      REAL    NOT NULL,
                qty             REAL    NOT NULL,
                gross_pnl       REAL    NOT NULL,
                fees_usd        REAL    NOT NULL DEFAULT 0,
                net_pnl         REAL    NOT NULL,
                hold_seconds    INTEGER NOT NULL DEFAULT 0,
                hold_days       REAL    NOT NULL DEFAULT 0,
                is_section_1256 INTEGER NOT NULL DEFAULT 0,
                tax_treatment   TEXT    NOT NULL,
                rate_pct        REAL    NOT NULL,
                estimated_tax   REAL    NOT NULL DEFAULT 0,
                ytd_year        INTEGER NOT NULL,
                created_ts      TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tax_year ON tax_lots(ytd_year, paper)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tax_symbol ON tax_lots(symbol, exit_ts)"
        )
        conn.commit()


def record_tax_lot(
    symbol: str,
    strategy: str,
    asset_class: str,       # 'crypto' | 'equity' | 'futures' | 'perp'
    entry_ts: str,          # ISO format timestamp
    exit_ts: str,           # ISO format timestamp
    entry_price: float,
    exit_price: float,
    qty: float,
    fees_usd: float = 0.0,
    paper: bool = True,
) -> dict:
    """
    Record a closed trade as a tax lot. Called from post_trade_analyzer or
    directly from the exit path in job_runner.py.

    Automatically classifies:
      - Hold period (seconds → days)
      - Tax treatment (section_1256 | long_term | short_term)
      - Estimated tax liability

    Returns the lot dict for logging.
    """
    init_tax_tables()

    # Parse timestamps — handle timezone-aware and naive
    def _parse_ts(ts_str):
        ts_str = ts_str.replace('Z', '+00:00') if ts_str else ''
        try:
            return datetime.fromisoformat(ts_str)
        except Exception:
            return datetime.now(pytz.utc)

    entry_dt = _parse_ts(entry_ts)
    exit_dt  = _parse_ts(exit_ts)

    hold_seconds = max(0, int((exit_dt - entry_dt).total_seconds()))
    hold_days    = hold_seconds / 86400.0

    gross_pnl = round((exit_price - entry_price) * qty, 6)
    net_pnl   = round(gross_pnl - fees_usd, 6)

    # Tax treatment
    is_section_1256 = asset_class.lower() in SECTION_1256_ASSET_CLASSES

    if is_section_1256:
        tax_treatment = 'section_1256'
        rate = TAX_RATE_1256_BLENDED
    elif hold_days >= 365.0:
        tax_treatment = 'long_term'
        rate = TAX_RATE_LONG_TERM
    else:
        tax_treatment = 'short_term'
        rate = TAX_RATE_SHORT_TERM

    # Tax only on gains; losses reduce other gains (tracked as negative net_pnl)
    estimated_tax = round(max(0.0, net_pnl * rate), 4)

    ytd_year = exit_dt.year

    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO tax_lots
                (symbol, strategy, asset_class, paper,
                 entry_ts, exit_ts, entry_price, exit_price, qty,
                 gross_pnl, fees_usd, net_pnl,
                 hold_seconds, hold_days,
                 is_section_1256, tax_treatment, rate_pct, estimated_tax, ytd_year)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            symbol, strategy, asset_class, int(paper),
            entry_dt.isoformat(), exit_dt.isoformat(),
            entry_price, exit_price, qty,
            round(gross_pnl, 4), round(fees_usd, 4), round(net_pnl, 4),
            hold_seconds, round(hold_days, 4),
            int(is_section_1256), tax_treatment, round(rate * 100, 2),
            estimated_tax, ytd_year,
        ))
        conn.commit()

    return {
        'symbol':         symbol,
        'strategy':       strategy,
        'asset_class':    asset_class,
        'hold_days':      round(hold_days, 2),
        'gross_pnl':      round(gross_pnl, 4),
        'fees_usd':       round(fees_usd, 4),
        'net_pnl':        round(net_pnl, 4),
        'tax_treatment':  tax_treatment,
        'rate_pct':       round(rate * 100, 1),
        'estimated_tax':  estimated_tax,
        'is_section_1256': is_section_1256,
        'year':           ytd_year,
    }


def get_ytd_summary(year: Optional[int] = None, paper: bool = False) -> dict:
    """
    Year-to-date tax summary broken down by treatment type.
    paper=False (default) = live trades only.
    """
    init_tax_tables()
    y = year or datetime.now().year

    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT  tax_treatment,
                    SUM(net_pnl)       AS total_pnl,
                    SUM(estimated_tax) AS total_tax,
                    COUNT(*)           AS trade_count,
                    SUM(CASE WHEN net_pnl > 0 THEN net_pnl ELSE 0 END) AS gains,
                    SUM(CASE WHEN net_pnl < 0 THEN net_pnl ELSE 0 END) AS losses
            FROM  tax_lots
            WHERE ytd_year = ? AND paper = ?
            GROUP BY tax_treatment
        """, (y, int(paper))).fetchall()

    blank = {'pnl': 0.0, 'tax': 0.0, 'trades': 0, 'gains': 0.0, 'losses': 0.0}
    summary = {
        'year':         y,
        'paper':        paper,
        'short_term':   {**blank},
        'long_term':    {**blank},
        'section_1256': {**blank},
    }

    for row in rows:
        t = row['tax_treatment']
        if t in summary:
            summary[t] = {
                'pnl':    round(row['total_pnl']  or 0.0, 2),
                'tax':    round(row['total_tax']  or 0.0, 2),
                'trades': row['trade_count'],
                'gains':  round(row['gains']      or 0.0, 2),
                'losses': round(row['losses']     or 0.0, 2),
            }

    total_pnl = sum(summary[k]['pnl']
                    for k in ('short_term', 'long_term', 'section_1256'))
    total_tax = sum(summary[k]['tax']
                    for k in ('short_term', 'long_term', 'section_1256'))

    summary['total_pnl']           = round(total_pnl, 2)
    summary['total_estimated_tax'] = round(total_tax, 2)
    summary['net_after_tax']       = round(total_pnl - total_tax, 2)
    summary['effective_rate_pct']  = (round(total_tax / total_pnl * 100, 1)
                                      if total_pnl > 0 else 0.0)
    return summary


def get_estimated_liability(year: Optional[int] = None, paper: bool = False) -> dict:
    """
    Quick tax liability snapshot — used by the dashboard and daily brain summaries.
    Highlights futures tax savings vs treating everything as short-term.
    """
    summary = get_ytd_summary(year=year, paper=paper)

    # Calculate how much the 60/40 rule saves vs paying full short-term rate
    s1256 = summary['section_1256']
    s1256_pnl = s1256['gains']   # only gains generate liability
    counterfactual_tax = max(0.0, s1256_pnl * TAX_RATE_SHORT_TERM)
    actual_tax         = s1256['tax']
    futures_tax_savings = round(max(0.0, counterfactual_tax - actual_tax), 2)

    return {
        'year':                summary['year'],
        'total_pnl':           summary['total_pnl'],
        'estimated_tax':       summary['total_estimated_tax'],
        'net_after_tax':       summary['net_after_tax'],
        'effective_rate_pct':  summary['effective_rate_pct'],
        'futures_tax_savings': futures_tax_savings,
        'breakdown':           {
            'short_term':   summary['short_term'],
            'long_term':    summary['long_term'],
            'section_1256': summary['section_1256'],
        },
        'note': (
            f"Section 1256 (futures) saved ~${futures_tax_savings:.2f} vs short-term treatment. "
            f"Effective blended rate: {summary['effective_rate_pct']:.1f}%."
        ) if futures_tax_savings > 0 else '',
    }


def get_harvesting_opportunities(
    open_positions: List[dict],
    year: Optional[int] = None,
    paper: bool = False,
) -> List[dict]:
    """
    Identify open positions with unrealized losses that could be harvested to
    offset YTD gains. Returns sorted list (largest tax benefit first).

    Each position dict should contain: symbol, strategy, asset_class,
    entry_price (or entry), current_price, qty.
    """
    summary = get_ytd_summary(year=year, paper=paper)
    ytd_gains = sum(
        summary[k]['gains']
        for k in ('short_term', 'long_term', 'section_1256')
    )

    opportunities = []
    for pos in open_positions:
        entry   = pos.get('entry_price') or pos.get('entry', 0)
        current = pos.get('current_price', entry)
        qty     = pos.get('qty', 0)
        if entry <= 0 or qty <= 0:
            continue

        unrealized = (current - entry) * qty
        if unrealized >= -1.0:   # Threshold: at least $1 unrealized loss
            continue

        offset_possible = min(abs(unrealized), ytd_gains)
        tax_benefit = round(offset_possible * TAX_RATE_SHORT_TERM, 2)

        opportunities.append({
            'symbol':              pos['symbol'],
            'strategy':            pos.get('strategy', ''),
            'asset_class':         pos.get('asset_class', 'crypto'),
            'unrealized_loss':     round(unrealized, 2),
            'ytd_gains_available': round(ytd_gains, 2),
            'offset_possible':     round(offset_possible, 2),
            'potential_tax_benefit': tax_benefit,
            'note': 'Consider harvesting if trade thesis has changed. Wash-sale rule does not apply to crypto.',
        })

    opportunities.sort(key=lambda x: x['potential_tax_benefit'], reverse=True)
    return opportunities


def get_tax_aware_exit_note(
    symbol: str,
    strategy: str,
    asset_class: str,
    entry_ts: str,
    unrealized_pnl: float,
) -> str:
    """
    Generate a tax-aware note for the exit review agent (Tudor Jones / Soros / Simons).
    Injected into exit review prompts so the AI can factor in tax consequences.
    """
    try:
        def _parse(ts_str):
            ts_str = (ts_str or '').replace('Z', '+00:00')
            try:
                return datetime.fromisoformat(ts_str)
            except Exception:
                return datetime.now(pytz.utc)

        entry_dt  = _parse(entry_ts)
        now_dt    = datetime.now(pytz.utc)
        hold_days = max(0, (now_dt - entry_dt.replace(tzinfo=pytz.utc)).days)
    except Exception:
        hold_days = 0

    is_s1256 = asset_class.lower() in SECTION_1256_ASSET_CLASSES

    if is_s1256:
        return (
            f"TAX NOTE (Section 1256): MES/futures — 60/40 blended rate applies "
            f"(~{TAX_RATE_1256_BLENDED*100:.1f}% effective regardless of hold period). "
            f"No tax-driven reason to delay exit."
        )
    elif hold_days >= 365:
        return (
            f"TAX NOTE: Long-term ({hold_days}d). LTCG rate ~{TAX_RATE_LONG_TERM*100:.0f}% applies. "
            + ("No tax reason to exit early." if unrealized_pnl > 0
               else "Loss qualifies for long-term treatment.")
        )
    elif hold_days >= 350:
        days_left = 365 - hold_days
        savings   = max(0, unrealized_pnl * (TAX_RATE_SHORT_TERM - TAX_RATE_LONG_TERM))
        return (
            f"TAX NOTE: {hold_days}d held — {days_left:.0f}d from LTCG status. "
            f"Waiting saves ~${savings:.2f} in taxes on current P&L=${unrealized_pnl:+.2f}. "
            f"{'Consider holding if thesis intact.' if unrealized_pnl > 0 else 'Harvest now — loss is short-term.'}"
        )
    else:
        return (
            f"TAX NOTE: Short-term ({hold_days}d). "
            f"Gains taxed at ~{TAX_RATE_SHORT_TERM*100:.0f}%. "
            f"No tax benefit to holding longer."
        )


def format_tax_summary_for_brain(year: Optional[int] = None) -> str:
    """
    Format a tax summary for injection into daily brain summaries.
    """
    try:
        lib = get_estimated_liability(year=year, paper=False)
        lines = [
            f"## Tax Snapshot ({lib['year']})",
            f"- Total P&L (live): ${lib['total_pnl']:+.2f}",
            f"- Estimated tax liability: ${lib['estimated_tax']:.2f}",
            f"- Net after tax: ${lib['net_after_tax']:+.2f}",
            f"- Effective rate: {lib['effective_rate_pct']:.1f}%",
        ]
        if lib['futures_tax_savings'] > 0:
            lines.append(
                f"- Futures 60/40 tax savings vs all-short-term: ${lib['futures_tax_savings']:.2f}"
            )
        bd = lib['breakdown']
        for treatment, label in [('short_term', 'Short-term'),
                                  ('section_1256', 'Section 1256 (futures)'),
                                  ('long_term', 'Long-term')]:
            d = bd[treatment]
            if d['trades'] > 0:
                lines.append(
                    f"  {label}: {d['trades']} trades | "
                    f"P&L ${d['pnl']:+.2f} | tax ${d['tax']:.2f}"
                )
        return '\n'.join(lines)
    except Exception as e:
        return f"[tax_tracker unavailable: {e}]"
