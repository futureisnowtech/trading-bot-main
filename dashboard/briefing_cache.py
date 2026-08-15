"""Cached answers for the five JARVIS orb briefings.

The orb's suggestion chips each fire one specific read-only tool. Answering them
takes a full tool-calling round trip, which is too slow to do on page load, so
the answers are generated once and cached here. The cockpit renders the cached
text instantly and only pays the model cost on a refresh.

Storage is the shared runtime SQLite database (`/app/logs/trades.db` in
production), which is bind-mounted into both the cockpit and the execution
engine. That is deliberate: execution_daemon's cycle regenerates whatever has
aged past the TTL, and the cockpit reads what it wrote.

Answers are generated on a read-only surface, so neither the timer nor the
refresh button can reach a write-tier tool.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from config import DB_PATH

logger = logging.getLogger(__name__)

# Answers older than this are stale. execution_daemon checks every cycle and
# regenerates only what has aged out, so this TTL alone sets the cadence and in a
# healthy system the cockpit rarely shows a stale one.
BRIEFING_TTL_SECONDS = 4 * 60 * 60

# Generated on this surface, which is deliberately not in brain._WRITE_SURFACES:
# an unattended timer must not be able to invoke a write-tier tool.
BRIEFING_SURFACE = "cockpit_briefing"

# Single source of truth for the orb chips and the cache. Each prompt names one
# verified tool rather than inviting the model to improvise.
BRIEFINGS: list[tuple[str, str]] = [
    (
        "🧭 What Needs Attention?",
        "Call get_operator_brief. In plain English, tell me whether the bot is healthy, "
        "whether it can trade, and the one thing I should pay attention to right now.",
    ),
    (
        "🛑 Why Isn't It Trading?",
        "Call get_trading_readiness_summary. Explain in plain English whether the bot is "
        "allowed to place new trades, and if not, exactly what is stopping it.",
    ),
    (
        "💸 Are Fees Hurting Us?",
        "Call get_fee_drag. Explain in plain English how much of our trading edge fees are "
        "eating and whether that is materially hurting us.",
    ),
    (
        "🎯 Are Entries Working?",
        "Call get_maker_fill_stats. Explain in plain English whether our entry approach is "
        "getting good fills or forcing us to overpay.",
    ),
    (
        "📂 What Bets Are Live?",
        "Call get_open_positions. List our live weather bets in plain English, including "
        "side, size, and entry price.",
    ),
]

BRIEFING_LABELS = [label for label, _ in BRIEFINGS]
PROMPTS_BY_LABEL = dict(BRIEFINGS)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_briefing_table(db_path: str = DB_PATH) -> None:
    try:
        with sqlite3.connect(db_path, timeout=30) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cockpit_briefings (
                    label        TEXT PRIMARY KEY,
                    answer       TEXT NOT NULL DEFAULT '',
                    generated_at TEXT NOT NULL DEFAULT '',
                    ok           INTEGER NOT NULL DEFAULT 0,
                    error        TEXT NOT NULL DEFAULT ''
                )
                """
            )
    except Exception:
        logger.exception("Failed to initialise cockpit_briefings table")


def _age_seconds(generated_at: str) -> float | None:
    if not generated_at:
        return None
    try:
        stamp = datetime.fromisoformat(generated_at)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds())


def get_briefings(db_path: str = DB_PATH) -> dict[str, dict[str, Any]]:
    """Every briefing keyed by label, including ones never generated yet."""
    init_briefing_table(db_path)
    rows: dict[str, Any] = {}
    try:
        with sqlite3.connect(db_path, timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(
                "SELECT label, answer, generated_at, ok, error FROM cockpit_briefings"
            ):
                rows[str(row["label"])] = dict(row)
    except Exception:
        logger.exception("Failed to read cockpit_briefings")

    out: dict[str, dict[str, Any]] = {}
    for label in BRIEFING_LABELS:
        row = rows.get(label) or {}
        generated_at = str(row.get("generated_at") or "")
        age = _age_seconds(generated_at)
        out[label] = {
            "label": label,
            "answer": str(row.get("answer") or ""),
            "generated_at": generated_at,
            "age_seconds": age,
            "ok": bool(row.get("ok")),
            "error": str(row.get("error") or ""),
            "never_generated": not generated_at,
            "stale": age is None or age > BRIEFING_TTL_SECONDS,
        }
    return out


def _store(label: str, answer: str, ok: bool, error: str, db_path: str) -> None:
    try:
        with sqlite3.connect(db_path, timeout=30) as conn:
            conn.execute(
                """
                INSERT INTO cockpit_briefings (label, answer, generated_at, ok, error)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(label) DO UPDATE SET
                    answer=excluded.answer,
                    generated_at=excluded.generated_at,
                    ok=excluded.ok,
                    error=excluded.error
                """,
                (label, answer, _now_iso(), 1 if ok else 0, error),
            )
    except Exception:
        logger.exception("Failed to persist briefing %s", label)


def refresh_briefing(label: str, db_path: str = DB_PATH) -> dict[str, Any]:
    """Regenerate one briefing. Never raises -- a failure is stored as the answer."""
    prompt = PROMPTS_BY_LABEL.get(label)
    if prompt is None:
        return {"label": label, "ok": False, "error": "unknown briefing label"}

    init_briefing_table(db_path)
    try:
        from runtime import brain

        answer = brain.ask([{"role": "user", "content": prompt}], surface=BRIEFING_SURFACE)
        ok = bool(answer) and not answer.startswith("⚠️")
        _store(label, answer, ok, "" if ok else "model returned a degraded answer", db_path)
        return {"label": label, "ok": ok, "answer": answer}
    except Exception as exc:
        logger.exception("Briefing refresh failed for %s", label)
        _store(label, "", False, str(exc), db_path)
        return {"label": label, "ok": False, "error": str(exc)}


def refresh_all_briefings(db_path: str = DB_PATH) -> list[dict[str, Any]]:
    """Regenerate all five. Used by the cockpit button and the engine's timer."""
    results = []
    for label in BRIEFING_LABELS:
        results.append(refresh_briefing(label, db_path=db_path))
    ok_count = sum(1 for r in results if r.get("ok"))
    logger.info("Briefing refresh complete: %d/%d succeeded", ok_count, len(results))
    return results


def refresh_stale_briefings(db_path: str = DB_PATH) -> list[dict[str, Any]]:
    """Regenerate only what has aged out. Cheap enough to call on a short timer."""
    current = get_briefings(db_path)
    results = []
    for label in BRIEFING_LABELS:
        if current[label]["stale"]:
            results.append(refresh_briefing(label, db_path=db_path))
    return results
