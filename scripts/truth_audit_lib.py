#!/usr/bin/env python3
"""
truth_audit_lib.py — Trust-aware performance audit helpers for launch decisions.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
PROJ_ROOT = SCRIPT_DIR.parent

HEADLINE_SOURCES = ("clean_paper_v10", "live_v10")
LOW_TRUST_SOURCES = ("paper_v10",)
DIRTY_SOURCES = ("pre_v10_contaminated", "backtest", "bybit_paper")
SUSPECT_ABS_PNL_USD = 100.0
SUSPECT_ABS_PNL_PCT = 0.50
REPLAY_MARKERS = ("replay", "synthetic")
EXCLUDED_LESSON_PREFIX = "INTEGRITY EXCLUDE:"
TEST_CLOSE_MARKER = "force_test_close"

NOTE_REASON_RE = re.compile(r"reason=([^\s]+)")
NOTE_SETUP_RE = re.compile(r"setup=([^\s]*)")
NOTE_REGIME_RE = re.compile(r"regime=([^\s]+)")


@dataclass(frozen=True)
class Metrics:
    trade_count: int
    wins_net: int
    win_rate_net: float
    gross_pnl: float
    fees: float
    net_pnl: float
    avg_win_gross: float
    avg_loss_gross: float
    avg_win_net: float
    avg_loss_net: float
    profit_factor_gross: float
    profit_factor_net: float
    expectancy_net: float
    fee_drag_pct: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_count": self.trade_count,
            "wins_net": self.wins_net,
            "win_rate_net": round(self.win_rate_net, 4),
            "gross_pnl": round(self.gross_pnl, 4),
            "fees": round(self.fees, 4),
            "net_pnl": round(self.net_pnl, 4),
            "avg_win_gross": round(self.avg_win_gross, 4),
            "avg_loss_gross": round(self.avg_loss_gross, 4),
            "avg_win_net": round(self.avg_win_net, 4),
            "avg_loss_net": round(self.avg_loss_net, 4),
            "profit_factor_gross": round(self.profit_factor_gross, 4),
            "profit_factor_net": round(self.profit_factor_net, 4),
            "expectancy_net": round(self.expectancy_net, 4),
            "fee_drag_pct": round(self.fee_drag_pct, 4),
        }


def default_db_path() -> str:
    try:
        from config import DB_PATH

        return str(DB_PATH)
    except Exception:
        return str(PROJ_ROOT / "logs" / "trades.db")


def _conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or default_db_path()
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _safe_div(numerator: float, denominator: float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _round_money(value: Optional[float]) -> float:
    return round(float(value or 0.0), 4)


def _parse_ts(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    text = str(raw).strip()
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_notes(notes: str) -> dict[str, Optional[str]]:
    text = str(notes or "")

    def _m(pattern: re.Pattern[str]) -> Optional[str]:
        match = pattern.search(text)
        if not match:
            return None
        value = match.group(1)
        return value if value != "" else None

    return {
        "reason": _m(NOTE_REASON_RE),
        "setup": _m(NOTE_SETUP_RE),
        "regime": _m(NOTE_REGIME_RE),
    }


def _looks_replay(source: str, notes: str = "") -> bool:
    haystack = f"{source} {notes}".lower()
    return any(marker in haystack for marker in REPLAY_MARKERS)


def _is_suspect_outlier(pnl_usd: float, pnl_pct: float) -> bool:
    return abs(float(pnl_usd or 0.0)) > SUSPECT_ABS_PNL_USD or abs(
        float(pnl_pct or 0.0)
    ) > SUSPECT_ABS_PNL_PCT


def _direction_from_action(action: str) -> str:
    action_u = str(action or "").upper()
    if action_u == "SELL":
        return "LONG"
    if action_u == "BUY":
        return "SHORT"
    return "OTHER"


def _bucket_hold_minutes(hold_minutes: Optional[float]) -> str:
    minutes = float(hold_minutes or 0.0)
    if minutes < 15:
        return "<15m"
    if minutes < 120:
        return "15m-2h"
    if minutes < 720:
        return "2h-12h"
    return "12h+"


def _active_signals(signals_json: str) -> list[str]:
    if not signals_json:
        return []
    try:
        payload = json.loads(signals_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    return sorted(name for name, value in payload.items() if bool(value))


def _metrics(rows: Iterable[dict[str, Any]]) -> Metrics:
    items = list(rows)
    gross_pnls = [float(r.get("pnl_usd", 0.0) or 0.0) for r in items]
    fees = [float(r.get("fee_usd", 0.0) or 0.0) for r in items]
    net_pnls = [gross - fee for gross, fee in zip(gross_pnls, fees)]
    gross_wins = [p for p in gross_pnls if p > 0]
    gross_losses = [p for p in gross_pnls if p < 0]
    net_wins = [p for p in net_pnls if p > 0]
    net_losses = [p for p in net_pnls if p < 0]
    fee_base = sum(abs(p) for p in gross_pnls)
    return Metrics(
        trade_count=len(items),
        wins_net=len(net_wins),
        win_rate_net=_safe_div(len(net_wins), len(items)),
        gross_pnl=sum(gross_pnls),
        fees=sum(fees),
        net_pnl=sum(net_pnls),
        avg_win_gross=sum(gross_wins) / len(gross_wins) if gross_wins else 0.0,
        avg_loss_gross=sum(gross_losses) / len(gross_losses) if gross_losses else 0.0,
        avg_win_net=sum(net_wins) / len(net_wins) if net_wins else 0.0,
        avg_loss_net=sum(net_losses) / len(net_losses) if net_losses else 0.0,
        profit_factor_gross=_safe_div(sum(gross_wins), abs(sum(gross_losses))),
        profit_factor_net=_safe_div(sum(net_wins), abs(sum(net_losses))),
        expectancy_net=_safe_div(sum(net_pnls), len(items)),
        fee_drag_pct=100.0 * _safe_div(sum(fees), fee_base),
    )


def _summarize_groups(
    rows: Iterable[dict[str, Any]],
    key_name: str,
    *,
    min_count: int = 1,
    sort_key: str = "net_pnl",
    descending: bool = False,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = row.get(key_name)
        if key in (None, ""):
            key = "<unknown>"
        grouped[str(key)].append(row)

    results: list[dict[str, Any]] = []
    for key, items in grouped.items():
        if len(items) < min_count:
            continue
        metrics = _metrics(items).to_dict()
        metrics[key_name] = key
        results.append(metrics)

    return sorted(results, key=lambda item: item.get(sort_key, 0.0), reverse=descending)


def _trade_integrity_index(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    def _table_columns(name: str) -> set[str]:
        try:
            rows = conn.execute(f"PRAGMA table_info({name})").fetchall()
        except sqlite3.OperationalError:
            return set()
        return {str(row["name"]) for row in rows}

    columns = _table_columns("trade_integrity")
    if not columns:
        return {}

    if {"trade_ref", "trust_tier", "reasons_json", "source"}.issubset(columns):
        rows = conn.execute(
            """
            SELECT trade_ref, trust_tier, reasons_json, source
            FROM trade_integrity
            """
        ).fetchall()
    elif {"close_order_id", "tier", "reason", "source_check"}.issubset(columns):
        rows = conn.execute(
            """
            SELECT close_order_id AS trade_ref,
                   tier AS trust_tier,
                   json_array(reason) AS reasons_json,
                   source_check AS source
            FROM trade_integrity
            """
        ).fetchall()
    else:
        return {}

    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        reasons = []
        try:
            reasons = json.loads(row["reasons_json"] or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            reasons = []
        index[str(row["trade_ref"])] = {
            "trust_tier": row["trust_tier"],
            "reasons": reasons,
            "source": row["source"],
        }
    return index


def _load_trade_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            id, ts, symbol, action, fee_usd, pnl_usd, pnl_pct, source, notes
        FROM trades
        WHERE won IS NOT NULL
        ORDER BY ts ASC
        """
    ).fetchall()
    parsed: list[dict[str, Any]] = []
    for row in rows:
        notes = str(row["notes"] or "")
        note_bits = _parse_notes(notes)
        source = str(row["source"] or "")
        replay_like = _looks_replay(source, notes)
        test_close = TEST_CLOSE_MARKER in notes
        contaminated = source in DIRTY_SOURCES
        suspect_outlier = _is_suspect_outlier(row["pnl_usd"], row["pnl_pct"])
        headline_usable = (
            source in HEADLINE_SOURCES
            and not replay_like
            and not test_close
            and not contaminated
            and not suspect_outlier
        )
        comparable = not replay_like and not test_close and not contaminated
        parsed.append(
            {
                "trade_id": int(row["id"]),
                "ts": row["ts"],
                "dt": _parse_ts(row["ts"]),
                "symbol": row["symbol"],
                "direction": _direction_from_action(row["action"]),
                "fee_usd": float(row["fee_usd"] or 0.0),
                "pnl_usd": float(row["pnl_usd"] or 0.0),
                "pnl_pct": float(row["pnl_pct"] or 0.0),
                "source": source,
                "notes": notes,
                "exit_type": note_bits["reason"] or "<unknown>",
                "setup_name": note_bits["setup"] or "<unknown>",
                "regime": note_bits["regime"] or "<unknown>",
                "headline_usable": headline_usable,
                "comparable": comparable,
                "synthetic_replay": replay_like,
                "test_close": test_close,
                "contaminated_source": contaminated,
                "suspect_outlier": suspect_outlier,
            }
        )
    return parsed


def _load_attribution_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    attr_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(trade_attribution)").fetchall()
    }
    integrity_select = (
        "integrity_tier"
        if "integrity_tier" in attr_columns
        else "'suspect' AS integrity_tier"
    )
    integrity = _trade_integrity_index(conn)
    rows = conn.execute(
        f"""
        SELECT
            id, trade_ref, symbol, source, pnl_usd, pnl_pct, fee_usd,
            exit_type, hold_minutes, lesson, signals_json, created_at,
            {integrity_select}
        FROM trade_attribution
        ORDER BY created_at ASC, id ASC
        """
    ).fetchall()
    parsed: list[dict[str, Any]] = []
    for row in rows:
        trade_ref = str(row["trade_ref"] or "").strip()
        source = str(row["source"] or "")
        lesson = str(row["lesson"] or "")
        active_signals = _active_signals(row["signals_json"] or "")
        integ = integrity.get(trade_ref, {})
        integrity_tier = str(
            row["integrity_tier"] or integ.get("trust_tier") or "suspect"
        ).lower()
        integrity_reasons = list(integ.get("reasons", []))
        contaminated = source in DIRTY_SOURCES or "pre_v10_contaminated" in integrity_reasons
        replay_like = _looks_replay(source) or "synthetic_replay_source" in integrity_reasons
        integrity_excluded = lesson.startswith(EXCLUDED_LESSON_PREFIX) or (
            integrity_tier in {"quarantined", "excluded"}
        )
        suspect_outlier = _is_suspect_outlier(row["pnl_usd"], row["pnl_pct"])
        missing_trade_ref = trade_ref == ""
        missing_signal_truth = len(active_signals) == 0
        strict_usable = (
            source in HEADLINE_SOURCES
            and not contaminated
            and not replay_like
            and not integrity_excluded
            and not suspect_outlier
            and not missing_trade_ref
            and not missing_signal_truth
        )
        relaxed_usable = (
            source in HEADLINE_SOURCES
            and not contaminated
            and not replay_like
            and not integrity_excluded
            and not suspect_outlier
        )
        parsed.append(
            {
                "attribution_id": int(row["id"]),
                "trade_ref": trade_ref,
                "symbol": row["symbol"],
                "source": source,
                "fee_usd": float(row["fee_usd"] or 0.0),
                "pnl_usd": float(row["pnl_usd"] or 0.0),
                "pnl_pct": float(row["pnl_pct"] or 0.0),
                "exit_type": row["exit_type"] or "<unknown>",
                "hold_minutes": float(row["hold_minutes"] or 0.0),
                "hold_bucket": _bucket_hold_minutes(row["hold_minutes"]),
                "lesson": lesson,
                "created_at": row["created_at"],
                "dt": _parse_ts(row["created_at"]),
                "active_signals": active_signals,
                "primary_signal": active_signals[0] if active_signals else "<unknown>",
                "integrity_tier": integrity_tier,
                "integrity_reasons": integrity_reasons,
                "missing_trade_ref": missing_trade_ref,
                "missing_signal_truth": missing_signal_truth,
                "contaminated_source": contaminated,
                "synthetic_replay": replay_like,
                "integrity_excluded": integrity_excluded,
                "suspect_outlier": suspect_outlier,
                "strict_usable": strict_usable,
                "relaxed_usable": relaxed_usable,
            }
        )
    return parsed


def _window_rows(
    rows: Iterable[dict[str, Any]], now: datetime, delta: timedelta
) -> list[dict[str, Any]]:
    floor = now - delta
    return [row for row in rows if row.get("dt") and row["dt"] >= floor]


def _recommendations(audit: dict[str, Any]) -> dict[str, Any]:
    headline = audit["headline"]
    overall = headline["overall"]
    recent = headline["recent_windows"]
    direction_map = {row["direction"]: row for row in headline["by_direction"]}
    exit_map = {row["exit_type"]: row for row in headline["by_exit_type"]}
    setup_rows = headline["by_setup"]
    symbol_rows = headline["by_symbol_negative"]
    diagnostic = audit["diagnostics"]

    recommendations: list[dict[str, str]] = []

    status = "GREEN"
    primary = "keep_as_is"
    if overall["net_pnl"] <= -5 or overall["expectancy_net"] <= -0.05:
        status = "RED"
        primary = "continue_paper_only"
    elif overall["net_pnl"] <= 5 or overall["expectancy_net"] <= 0.02:
        status = "AMBER"
        primary = "constrained_live_only"

    if primary != "keep_as_is":
        recommendations.append(
            {
                "code": primary,
                "severity": status,
                "action": (
                    "Do not treat current sample as full-size-live ready."
                    if primary == "constrained_live_only"
                    else "Continue paper only until trustworthy net expectancy turns clearly positive."
                ),
                "evidence": (
                    f"Trustworthy headline net={overall['net_pnl']:+.2f}, "
                    f"expectancy={overall['expectancy_net']:+.3f}, "
                    f"profit_factor={overall['profit_factor_gross']:.2f}."
                ),
            }
        )

    long_row = direction_map.get("LONG")
    short_row = direction_map.get("SHORT")
    if short_row and short_row["trade_count"] >= 5 and short_row["net_pnl"] < 0 and (
        not long_row
        or short_row["expectancy_net"] < long_row["expectancy_net"]
        or short_row["net_pnl"] < long_row["net_pnl"]
    ):
        recommendations.append(
            {
                "code": "suppress_or_deweight_shorts",
                "severity": "HIGH",
                "action": "Disable shorts tonight or cut short size materially below long size.",
                "evidence": (
                    (
                        f"LONG net={long_row['net_pnl']:+.2f} on {long_row['trade_count']} closes vs "
                        if long_row
                        else ""
                    )
                    + f"SHORT net={short_row['net_pnl']:+.2f} on {short_row['trade_count']} closes."
                ),
            }
        )

    hard_stop = exit_map.get("hard_stop")
    thesis_invalidated = exit_map.get("thesis_invalidated")
    trailing_stop = exit_map.get("trailing_stop")
    if hard_stop and hard_stop["trade_count"] >= 10 and hard_stop["net_pnl"] < 0:
        recommendations.append(
            {
                "code": "hard_stop_is_destructive",
                "severity": "HIGH",
                "action": "Keep hard stops for safety, but do not treat hard-stop outcomes as proof of edge tonight.",
                "evidence": (
                    f"hard_stop net={hard_stop['net_pnl']:+.2f} across {hard_stop['trade_count']} close legs."
                ),
            }
        )
    if thesis_invalidated and thesis_invalidated["trade_count"] >= 20 and thesis_invalidated["net_pnl"] < 0:
        recommendations.append(
            {
                "code": "thesis_invalidated_not_trustworthy",
                "severity": "HIGH",
                "action": "Do not promote live confidence or weights from thesis-invalidated results tonight.",
                "evidence": (
                    f"thesis_invalidated net={thesis_invalidated['net_pnl']:+.2f} "
                    f"across {thesis_invalidated['trade_count']} close legs."
                ),
            }
        )
    if trailing_stop and trailing_stop["trade_count"] >= 20 and trailing_stop["net_pnl"] > 0:
        recommendations.append(
            {
                "code": "preserve_trailing_stop",
                "severity": "INFO",
                "action": "Preserve trailing-stop behavior; it is the only clearly positive full-close exit in trustworthy rows.",
                "evidence": (
                    f"trailing_stop net={trailing_stop['net_pnl']:+.2f} "
                    f"across {trailing_stop['trade_count']} close legs."
                ),
            }
        )

    if overall["fee_drag_pct"] >= 25.0:
        recommendations.append(
            {
                "code": "keep_thresholds_unchanged",
                "severity": "HIGH",
                "action": "Keep thresholds unchanged tonight; fee drag is too high to justify loosening entry standards.",
                "evidence": f"Headline fee drag is {overall['fee_drag_pct']:.1f}% of absolute gross move.",
            }
        )

    recent_24h = recent["24h"]
    recent_7d = recent["7d"]
    if recent_24h["trade_count"] == 0:
        recommendations.append(
            {
                "code": "sample_is_stale",
                "severity": "MEDIUM",
                "action": "Treat the sample as stale; do not over-trust tonight's launch to old paper evidence.",
                "evidence": "No trustworthy close legs in the last 24 hours.",
            }
        )
    if recent_7d["trade_count"] < 30:
        recommendations.append(
            {
                "code": "recent_sample_is_thin",
                "severity": "MEDIUM",
                "action": "Use reduced size until a larger recent trustworthy sample is rebuilt.",
                "evidence": f"Only {recent_7d['trade_count']} trustworthy close legs in the last 7 days.",
            }
        )

    strict_coverage = diagnostic["strict_signal_diagnostics"]["coverage_vs_headline_pct"]
    strict_count = diagnostic["strict_signal_diagnostics"]["overall"]["trade_count"]
    if strict_count < 20 or strict_coverage < 25.0:
        recommendations.append(
            {
                "code": "do_not_promote_live_weights",
                "severity": "HIGH",
                "action": "Do not promote ML/Bayesian live weights from current setup-level evidence tonight.",
                "evidence": (
                    f"Strict setup diagnostics only cover {strict_count} rows "
                    f"({strict_coverage:.1f}% of trustworthy close legs)."
                ),
            }
        )

    bad_symbols = [
        row["symbol"]
        for row in symbol_rows
        if row["trade_count"] >= 5 and row["net_pnl"] <= -2.0
    ][:3]
    if bad_symbols:
        recommendations.append(
            {
                "code": "watch_or_suppress_symbols",
                "severity": "MEDIUM",
                "action": f"Watch closely or temporarily suppress: {', '.join(bad_symbols)}.",
                "evidence": "These symbols show repeated negative trustworthy net contribution with meaningful sample.",
            }
        )

    if not any(
        row["trade_count"] >= 3 and row["net_pnl"] > 0 for row in setup_rows if row["setup_name"] != "<unknown>"
    ):
        recommendations.append(
            {
                "code": "setup_specific_sample_too_weak",
                "severity": "MEDIUM",
                "action": "Do not make setup-specific live promotions beyond the obvious short suppression tonight.",
                "evidence": "Named setup sample is too small for confident promotion decisions.",
            }
        )

    exact_tonight = [
        "Launch only in constrained mode if launching tonight.",
        "Keep thresholds unchanged.",
        "Do not promote live ML/Bayesian weights from current sample.",
    ]
    if short_row and short_row["net_pnl"] < 0:
        exact_tonight.append("Disable shorts or cut short size materially below long size.")
    if bad_symbols:
        exact_tonight.append(f"Watch or suppress {', '.join(bad_symbols)}.")

    return {
        "status": status,
        "primary_recommendation": primary,
        "recommendations": recommendations,
        "exact_tonight": exact_tonight,
    }


def build_net_truth_audit(db_path: Optional[str] = None) -> dict[str, Any]:
    path = os.path.abspath(db_path or default_db_path())
    now = datetime.now(timezone.utc)
    with _conn(path) as conn:
        trade_rows = _load_trade_rows(conn)
        attr_rows = _load_attribution_rows(conn)

    headline_rows = [row for row in trade_rows if row["headline_usable"]]
    raw_comparable_rows = [row for row in trade_rows if row["comparable"]]

    headline_overall = _metrics(headline_rows).to_dict()
    raw_trade_surface = _metrics(raw_comparable_rows).to_dict()

    by_source = _summarize_groups(headline_rows, "source", descending=True)
    by_direction = _summarize_groups(headline_rows, "direction", descending=True)
    by_exit_type = _summarize_groups(headline_rows, "exit_type", descending=False)
    by_setup = _summarize_groups(headline_rows, "setup_name", min_count=2, descending=False)
    by_symbol_negative = _summarize_groups(
        headline_rows, "symbol", min_count=2, descending=False
    )[:10]
    by_symbol_positive = _summarize_groups(
        headline_rows, "symbol", min_count=2, descending=True
    )[:10]

    recent_windows = {
        "24h": _metrics(_window_rows(headline_rows, now, timedelta(days=1))).to_dict(),
        "7d": _metrics(_window_rows(headline_rows, now, timedelta(days=7))).to_dict(),
        "all": headline_overall,
    }

    strict_attr_rows = [row for row in attr_rows if row["strict_usable"]]
    relaxed_attr_rows = [row for row in attr_rows if row["relaxed_usable"]]
    raw_attr_rows = [
        row for row in attr_rows if row["source"] not in DIRTY_SOURCES and not row["synthetic_replay"]
    ]

    trust_counts = Counter()
    for row in trade_rows:
        if row["headline_usable"]:
            trust_counts["headline_usable"] += 1
        elif row["test_close"]:
            trust_counts["synthetic_test"] += 1
        elif row["synthetic_replay"]:
            trust_counts["synthetic_replay"] += 1
        elif row["contaminated_source"]:
            trust_counts["contaminated_source"] += 1
        elif row["source"] in LOW_TRUST_SOURCES:
            trust_counts["low_trust_source"] += 1
        elif row["suspect_outlier"]:
            trust_counts["suspect_outlier"] += 1
        else:
            trust_counts["other"] += 1

    attr_counts = Counter()
    for row in attr_rows:
        if row["strict_usable"]:
            attr_counts["strict_usable"] += 1
        elif row["integrity_excluded"]:
            attr_counts["integrity_excluded"] += 1
        elif row["synthetic_replay"]:
            attr_counts["synthetic_replay"] += 1
        elif row["contaminated_source"]:
            attr_counts["contaminated_source"] += 1
        elif row["missing_trade_ref"]:
            attr_counts["missing_trade_ref"] += 1
        elif row["missing_signal_truth"]:
            attr_counts["missing_signal_truth"] += 1
        elif row["suspect_outlier"]:
            attr_counts["suspect_outlier"] += 1
        elif row["relaxed_usable"]:
            attr_counts["relaxed_usable_only"] += 1
        else:
            attr_counts["other"] += 1

    diagnostics = {
        "raw_attribution_surface": {
            "overall": _metrics(raw_attr_rows).to_dict(),
            "count": len(raw_attr_rows),
        },
        "relaxed_signal_diagnostics": {
            "overall": _metrics(relaxed_attr_rows).to_dict(),
            "count": len(relaxed_attr_rows),
            "coverage_vs_headline_pct": round(
                100.0 * _safe_div(len(relaxed_attr_rows), len(headline_rows)), 2
            ),
            "by_exit_type": _summarize_groups(relaxed_attr_rows, "exit_type", descending=False),
            "by_primary_signal": _summarize_groups(
                relaxed_attr_rows, "primary_signal", min_count=2, descending=False
            ),
            "by_hold_bucket": _summarize_groups(
                relaxed_attr_rows, "hold_bucket", descending=False
            ),
        },
        "strict_signal_diagnostics": {
            "overall": _metrics(strict_attr_rows).to_dict(),
            "count": len(strict_attr_rows),
            "coverage_vs_headline_pct": round(
                100.0 * _safe_div(len(strict_attr_rows), len(headline_rows)), 2
            ),
            "by_exit_type": _summarize_groups(strict_attr_rows, "exit_type", descending=False),
            "by_primary_signal": _summarize_groups(
                strict_attr_rows, "primary_signal", descending=False
            ),
        },
    }

    audit = {
        "generated_at": now.isoformat(),
        "db_path": path,
        "filters": {
            "headline_sources": list(HEADLINE_SOURCES),
            "excluded_sources": list(DIRTY_SOURCES),
            "low_trust_sources": list(LOW_TRUST_SOURCES),
            "excluded_note_markers": [TEST_CLOSE_MARKER],
            "suspect_abs_pnl_usd": SUSPECT_ABS_PNL_USD,
            "suspect_abs_pnl_pct": SUSPECT_ABS_PNL_PCT,
        },
        "headline": {
            "overall": headline_overall,
            "raw_trade_surface": raw_trade_surface,
            "contamination_delta_net": round(
                raw_trade_surface["net_pnl"] - headline_overall["net_pnl"], 4
            ),
            "by_source": by_source,
            "by_direction": by_direction,
            "by_exit_type": by_exit_type,
            "by_setup": by_setup,
            "by_symbol_negative": by_symbol_negative,
            "by_symbol_positive": by_symbol_positive,
            "recent_windows": recent_windows,
        },
        "trust_counts": {
            "closed_trades": dict(trust_counts),
            "trade_attribution": dict(attr_counts),
        },
        "diagnostics": diagnostics,
    }
    audit["go_live"] = _recommendations(audit)
    return audit


def build_go_live_audit(db_path: Optional[str] = None) -> dict[str, Any]:
    audit = build_net_truth_audit(db_path=db_path)
    return {
        "generated_at": audit["generated_at"],
        "db_path": audit["db_path"],
        "headline": audit["headline"]["overall"],
        "recent_windows": audit["headline"]["recent_windows"],
        "go_live": audit["go_live"],
        "diagnostic_coverage": {
            "strict_signal_rows": audit["diagnostics"]["strict_signal_diagnostics"]["count"],
            "strict_signal_coverage_pct": audit["diagnostics"]["strict_signal_diagnostics"][
                "coverage_vs_headline_pct"
            ],
            "relaxed_signal_rows": audit["diagnostics"]["relaxed_signal_diagnostics"]["count"],
            "relaxed_signal_coverage_pct": audit["diagnostics"]["relaxed_signal_diagnostics"][
                "coverage_vs_headline_pct"
            ],
        },
        "worst_symbols": audit["headline"]["by_symbol_negative"][:5],
        "best_symbols": audit["headline"]["by_symbol_positive"][:5],
        "exit_types": audit["headline"]["by_exit_type"],
        "directions": audit["headline"]["by_direction"],
    }
