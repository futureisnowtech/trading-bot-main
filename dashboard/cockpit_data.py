"""Read-only data layer for the Streamlit Kalshi cockpit."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from VERSION import VERSION
from config import (
    ACCOUNT_SIZE,
    BOT_LOG_PATH,
    DB_PATH,
    FORECAST_LOG_PATH,
    GEMINI_MODEL,
    KALSHI_DATA_FRESHNESS_MINUTES,
    KALSHI_EXIT_MODEL_INVALIDATION_DELTA,
    KALSHI_EXIT_REDEPLOY_EDGE,
    KALSHI_EXIT_TIME_DECAY_BID_FLOOR,
    KALSHI_EXIT_TIME_DECAY_HOURS,
    KALSHI_FEE_BUFFER,
    KALSHI_FEE_PER_CONTRACT,
    KALSHI_KELLY_CAP,
    KALSHI_MAX_CONCURRENT_POSITIONS,
    KALSHI_MAX_DEPLOYED_PCT,
    KALSHI_MAX_RISK_PER_EVENT_PCT,
    KALSHI_MAX_SIGMA,
    KALSHI_MAX_SPREAD_RATIO,
    KALSHI_MAX_USD_PER_POSITION,
    KALSHI_MIN_PRICE,
    KALSHI_SAME_EVENT_FAMILY_CAP,
)
from forecast.strategy_engine import EV_THRESHOLD, _get_city_hub, _get_macro_context
from notifications.notification_engine import get_notifications
from runtime.operator_truth import get_live_kalshi_status, get_recent_veto_summary
from runtime.storage_guard import runtime_storage_status

_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME_DIR = Path(DB_PATH).resolve().parent


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None

    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None

    text = str(value).strip()
    if not text:
        return None

    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    try:
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    except Exception:
        return None


def _dt_text(value: Any) -> str:
    dt = _parse_ts(value)
    if dt is None:
        return str(value or "")
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _file_size_mb(path: str) -> float:
    try:
        return round(os.path.getsize(path) / (1024 * 1024), 2)
    except OSError:
        return 0.0


def _load_deploy_metadata() -> dict[str, Any]:
    manifest_candidates = [
        _RUNTIME_DIR / "deploy_manifest.json",
        _ROOT / "deploy_manifest.json",
    ]
    version_candidates = [
        _RUNTIME_DIR / "version.txt",
        _ROOT / "version.txt",
    ]
    payload: dict[str, Any] = {}

    for manifest_path in manifest_candidates:
        if manifest_path.exists():
            try:
                payload.update(json.loads(manifest_path.read_text(encoding="utf-8")))
                break
            except Exception:
                continue

    for version_path in version_candidates:
        if version_path.exists():
            try:
                for line in version_path.read_text(encoding="utf-8").splitlines():
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    payload.setdefault(key.strip(), value.strip())
                break
            except Exception:
                continue

    return payload


def _load_contract_metadata(symbols: list[str]) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}

    placeholders = ",".join("?" for _ in symbols)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT c.local_symbol, c.contract_name, c.right, c.strike,
                   c.last_trade_at, c.resolution_at, c.market_id,
                   m.market_name
            FROM forecast_contracts c
            LEFT JOIN forecast_markets m ON c.market_id = m.id
            WHERE c.local_symbol IN ({placeholders})
            ORDER BY c.active DESC, c.last_seen_at DESC, c.id DESC
            """,
            tuple(symbols),
        ).fetchall()

    metadata: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row["local_symbol"] or "")
        metadata.setdefault(
            ticker,
            {
                "contract_name": row["contract_name"] or row["market_name"] or ticker,
                "strike": row["strike"],
                "right": row["right"],
                "last_trade_at": row["last_trade_at"],
                "resolution_at": row["resolution_at"],
            },
        )
    return metadata


def _load_latest_buy_trades(symbols: list[str]) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}

    results: dict[str, dict[str, Any]] = {}
    with _connect() as conn:
        for symbol in symbols:
            row = conn.execute(
                """
                SELECT ts, qty, price, fee_usd, strategy, contract_side, forecast_yes_prob
                FROM trades
                WHERE symbol = ?
                  AND action = 'BUY'
                  AND broker = 'kalshi'
                ORDER BY ts DESC
                LIMIT 1
                """,
                (symbol,),
            ).fetchone()
            if row:
                results[symbol] = dict(row)
    return results


def _load_recent_trades(limit: int = 20) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT ts, symbol, action, qty, price, fee_usd, pnl_usd, strategy,
                   contract_side, forecast_yes_prob, notes
            FROM trades
            WHERE broker = 'kalshi'
            ORDER BY ts DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def _load_recent_events(limit: int = 20) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT ts, level, source, message
            FROM system_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def _load_market_counts() -> dict[str, int]:
    counts = {
        "active_markets": 0,
        "active_contracts": 0,
        "quote_rows": 0,
        "bar_rows": 0,
    }
    with _connect() as conn:
        counts["active_markets"] = int(
            (conn.execute(
                "SELECT COUNT(*) FROM forecast_markets WHERE COALESCE(active, 1)=1"
            ).fetchone()[0])
            or 0
        )
        counts["active_contracts"] = int(
            (conn.execute(
                "SELECT COUNT(*) FROM forecast_contracts WHERE COALESCE(active, 1)=1"
            ).fetchone()[0])
            or 0
        )
        counts["quote_rows"] = int(
            (conn.execute("SELECT COUNT(*) FROM forecast_quotes").fetchone()[0]) or 0
        )
        counts["bar_rows"] = int(
            (conn.execute("SELECT COUNT(*) FROM forecast_bars").fetchone()[0]) or 0
        )
    return counts


def build_position_row(
    position: dict[str, Any],
    *,
    metadata: dict[str, Any] | None = None,
    quote: dict[str, Any] | None = None,
    entry_trade: dict[str, Any] | None = None,
    state_label: str = "LIVE",
) -> dict[str, Any]:
    metadata = metadata or {}
    quote = quote or {}
    entry_trade = entry_trade or {}

    side = str(position.get("side") or "YES").upper()
    ticker = str(position.get("ticker") or position.get("local_symbol") or "")
    qty = _coerce_float(position.get("qty"))
    entry_price = _coerce_float(position.get("entry_price") or position.get("entry"))

    bid_key = "yes_bid" if side == "YES" else "no_bid"
    ask_key = "yes_ask" if side == "YES" else "no_ask"
    bid = quote.get(bid_key)
    ask = quote.get(ask_key)
    bid = None if bid is None else _coerce_float(bid)
    ask = None if ask is None else _coerce_float(ask)

    if bid is not None and ask is not None:
        mark = round((bid + ask) / 2.0, 4)
    else:
        mark = bid if bid is not None else ask

    gross_mark_pnl = None
    exit_pnl_est = None
    if mark is not None:
        gross_mark_pnl = round((mark - entry_price) * qty, 4)
    if bid is not None:
        entry_fee_usd = _coerce_float(entry_trade.get("fee_usd"))
        exit_fee_usd = qty * KALSHI_FEE_PER_CONTRACT
        exit_pnl_est = round((bid - entry_price) * qty - entry_fee_usd - exit_fee_usd, 4)

    return {
        "ticker": ticker,
        "contract_name": metadata.get("contract_name") or ticker,
        "side": side,
        "qty": qty,
        "entry_price": round(entry_price, 4),
        "bid": bid,
        "ask": ask,
        "mark": mark,
        "gross_mark_pnl": gross_mark_pnl,
        "exit_pnl_est": exit_pnl_est,
        "hub": _get_city_hub(ticker),
        "opened_at": position.get("opened_at") or position.get("entered_at"),
        "resolution_at": metadata.get("resolution_at") or metadata.get("last_trade_at"),
        "strike": metadata.get("strike"),
        "state_label": state_label,
        "entry_strategy": entry_trade.get("strategy"),
        "forecast_yes_prob": entry_trade.get("forecast_yes_prob"),
        "entry_fee_usd": _coerce_float(entry_trade.get("fee_usd")),
    }


def summarize_hub_exposure(position_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_hub: defaultdict[str, float] = defaultdict(float)
    for row in position_rows:
        hub = str(row.get("hub") or "UNKNOWN")
        cost_basis = _coerce_float(row.get("entry_price")) * _coerce_float(row.get("qty"))
        by_hub[hub] += cost_basis
    return [
        {"hub": hub, "cost_basis_usd": round(cost, 4)}
        for hub, cost in sorted(by_hub.items(), key=lambda item: item[1], reverse=True)
    ]


def build_realized_pnl_curve(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    running = 0.0
    points: list[dict[str, Any]] = []
    for row in reversed(trades):
        running += _coerce_float(row.get("pnl_usd"))
        points.append(
            {
                "ts": _dt_text(row.get("ts")),
                "cumulative_pnl": round(running, 4),
            }
        )
    return points


def build_regime_manifest(balance_usd: float | None = None) -> dict[str, Any]:
    balance = _coerce_float(balance_usd, ACCOUNT_SIZE)
    macro = _get_macro_context()
    return {
        "version": VERSION,
        "reasoning_model": GEMINI_MODEL,
        "ensemble_blend": "60% GFS + 40% ECMWF; AI/GraphCast only widens or compresses sigma",
        "entry_math": [
            f"Net EV gate: post-fee EV must exceed {EV_THRESHOLD:.2f}",
            f"Fixed fee drag: ${KALSHI_FEE_PER_CONTRACT:.2f} per contract with {KALSHI_FEE_BUFFER:.2f} taker friction buffer",
            f"Sizing: fractional Kelly capped at {KALSHI_KELLY_CAP:.0%}, max risk/event {KALSHI_MAX_RISK_PER_EVENT_PCT:.2%}, hard per-position cap ${KALSHI_MAX_USD_PER_POSITION:.0f}",
        ],
        "entry_gates": [
            f"Minimum contract price {KALSHI_MIN_PRICE:.2f}",
            f"Maximum sigma {KALSHI_MAX_SIGMA:.1f}F",
            f"Maximum spread ratio {KALSHI_MAX_SPREAD_RATIO:.0%}",
            f"Weather data freshness window {KALSHI_DATA_FRESHNESS_MINUTES} minutes",
            f"Max concurrent positions {KALSHI_MAX_CONCURRENT_POSITIONS}",
            f"Same event family cap {KALSHI_SAME_EVENT_FAMILY_CAP}",
            f"Max deployed capital {KALSHI_MAX_DEPLOYED_PCT:.0%}",
            f"Regional hub cap now ${max(20.0, balance * 0.20):.2f}",
        ],
        "exit_stack": [
            "Take profit when bid reaches 0.85 on weather positions",
            f"Held-model invalidation delta {KALSHI_EXIT_MODEL_INVALIDATION_DELTA:.0%}",
            f"Time-decay redeploy inside {KALSHI_EXIT_TIME_DECAY_HOURS:.0f}h when bid >= {KALSHI_EXIT_TIME_DECAY_BID_FLOOR:.2f} and edge <= {KALSHI_EXIT_REDEPLOY_EDGE:.2f}",
            "Liquidity-checked limit exits only",
        ],
        "macro_context": macro,
    }


def build_metric_explainers(balance_usd: float | None = None) -> dict[str, str]:
    balance = _coerce_float(balance_usd, ACCOUNT_SIZE)
    hub_cap = max(20.0, balance * 0.20)
    return {
        "Live Cash": "This is the cash Kalshi says is available right now. It is the real money the bot can still deploy without guessing from local records.",
        "Open Positions": "These are live Kalshi positions the broker is actually carrying. It keeps us honest by showing what the exchange sees, not what we hoped happened.",
        "Active Markets": "This is the weather market universe the engine can currently scan. A larger number means more opportunities, but the safety gates still decide whether any are worth trading.",
        "Drift": "Drift means the broker and the local SQLite ledger disagree. This matters because we operate broker-first, so drift is a warning that the local story may be stale or incomplete.",
        "Realized P&L": "This is closed-trade profit and loss already locked in. It excludes open-position swings so you can separate booked outcomes from temporary mark changes.",
        "Data Ingestion": "The engine starts by blending the two main weather ensembles. That keeps us from overreacting to one model run and gives the bot a more stable starting forecast.",
        "AI Volatility Adjustment": "GraphCast-style AI does not overrule the forecast direction. It only tells the bot whether confidence should be widened or tightened because the atmosphere looks more or less chaotic.",
        "Safety Gates": "These filters stop trades that look good on paper but fail live economics. Fees, spreads, stale data, and model-vs-market disagreement can all kill a trade here.",
        "Position Sizing": "Even when a trade passes, the bot still sizes it down through bankroll caps. This keeps one good-looking idea from becoming a dangerous oversized bet.",
        "Net EV Gate": f"A trade must still clear at least {EV_THRESHOLD:.0%} edge after the ${KALSHI_FEE_PER_CONTRACT:.2f} fee and the taker friction buffer. This prevents the bot from buying tiny theoretical edges that disappear in real execution.",
        "Fractional Kelly": f"Kelly sizing starts from the math edge, then only uses a fraction of the account. Here it is capped at {KALSHI_KELLY_CAP:.0%}, which keeps conviction from turning into overbetting.",
        "Regional Hub Cap": f"No single weather region is allowed to dominate the book. Right now the live hub cap is {_coerce_float(hub_cap):.2f} dollars, which limits correlated storm or temperature risk.",
        "Max Deployed Capital": f"The engine can only deploy up to {KALSHI_MAX_DEPLOYED_PCT:.0%} of the account at once. That leaves dry powder and prevents the bot from becoming fully invested in mediocre conditions.",
        "Fee Buffer": f"The system assumes extra friction beyond the fixed ${KALSHI_FEE_PER_CONTRACT:.2f} contract fee. That buffer protects against thin books and keeps the entry math closer to what live fills actually cost.",
        "Forecast Freshness": f"Weather data older than {KALSHI_DATA_FRESHNESS_MINUTES} minutes is treated as stale. This stops the engine from making decisions off an old atmosphere.",
        "Recent Edge": "This compares the bot's side probability against the price it paid. A bigger gap means the model believed it was buying more outcome probability than the market was charging for.",
        "Confidence": "Confidence is the bot's probability for the side it actually bought, after converting YES/NO correctly. It is the core number behind whether a trade looked cheap or expensive.",
    }


def build_decision_funnel(balance_usd: float | None = None) -> list[dict[str, Any]]:
    explainers = build_metric_explainers(balance_usd)
    hub_cap = max(20.0, _coerce_float(balance_usd, ACCOUNT_SIZE) * 0.20)
    return [
        {
            "stage": "01",
            "label": "Data Ingestion",
            "headline": "60% GFS + 40% ECMWF",
            "detail": "The engine fuses the two core ensemble families into one base weather probability before anything else happens.",
            "pill": "Base forecast blend",
            "tooltip": explainers["Data Ingestion"],
        },
        {
            "stage": "02",
            "label": "AI Volatility Adjustment",
            "headline": "GraphCast Sigma Scaling",
            "detail": "AI only adjusts how wide or tight uncertainty should be. It shapes confidence, not the raw weather direction.",
            "pill": f"Freshness {KALSHI_DATA_FRESHNESS_MINUTES}m",
            "tooltip": explainers["AI Volatility Adjustment"],
        },
        {
            "stage": "03",
            "label": "Safety Gates",
            "headline": f"Net EV > {EV_THRESHOLD:.0%} After Fees",
            "detail": "Trades get vetoed here if fee drag, spread, stale quotes, or model-vs-market divergence makes the edge unsafe.",
            "pill": f"Fee ${KALSHI_FEE_PER_CONTRACT:.2f} + buffer ${KALSHI_FEE_BUFFER:.2f}",
            "tooltip": explainers["Safety Gates"],
        },
        {
            "stage": "04",
            "label": "Position Sizing",
            "headline": f"Kelly Cap {KALSHI_KELLY_CAP:.0%}",
            "detail": "Approved trades are clipped again by Kelly, event risk, deployment limits, and regional hub exposure.",
            "pill": f"Hub cap ${hub_cap:.2f}",
            "tooltip": explainers["Position Sizing"],
        },
    ]


def build_trade_edge_rows(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trade in trades or []:
        if str(trade.get("action") or "").upper() != "BUY":
            continue

        forecast_yes_prob = trade.get("forecast_yes_prob")
        if forecast_yes_prob in (None, ""):
            continue

        price = _coerce_float(trade.get("price"))
        if price <= 0:
            continue

        side = str(trade.get("contract_side") or "YES").upper()
        yes_prob = _coerce_float(forecast_yes_prob)
        held_confidence = yes_prob if side == "YES" else (1.0 - yes_prob)
        edge = held_confidence - price

        rows.append(
            {
                "symbol": str(trade.get("symbol") or ""),
                "side": side,
                "strategy": str(trade.get("strategy") or ""),
                "ts": _dt_text(trade.get("ts")),
                "model_confidence_pct": round(held_confidence * 100.0, 1),
                "market_price_pct": round(price * 100.0, 1),
                "edge_pct": round(edge * 100.0, 1),
                "edge_label": "Model Edge",
            }
        )

    return rows


def build_ai_insights(
    *,
    truth: dict[str, Any],
    lane: dict[str, Any],
    market_counts: dict[str, Any],
    recent_events: list[dict[str, Any]],
    recent_trades: list[dict[str, Any]],
    recent_vetoes: dict[str, Any],
) -> list[dict[str, str]]:
    insights: list[dict[str, str]] = []

    if truth.get("broker_connected") and str(lane.get("readiness_state") or "") == "OPERATIONAL":
        insights.append(
            {
                "title": "Engine Live",
                "tone": "good",
                "meta": "Broker-first truth",
                "body": "Kalshi is connected, the forecast lane is operational, and the cockpit is reading broker cash and positions directly from the live stack.",
            }
        )

    for event in recent_events:
        source = str(event.get("source") or "")
        message = str(event.get("message") or "")
        if source == "PositionReconciler":
            match = re.search(
                r"holdings=(?P<holdings>\d+).*adopted=(?P<adopted>\d+).*refreshed=(?P<refreshed>\d+).*closed=(?P<closed>\d+)",
                message,
            )
            if match:
                holdings = int(match.group("holdings"))
                refreshed = int(match.group("refreshed"))
                adopted = int(match.group("adopted"))
                closed = int(match.group("closed"))
                insights.append(
                    {
                        "title": "Ledger Reconciled",
                        "tone": "good",
                        "meta": "Broker vs SQLite",
                        "body": f"The runtime checked {holdings} live broker holding(s), refreshed {refreshed} local row(s), adopted {adopted}, and closed {closed} stale remnants. That keeps local state aligned with Kalshi reality.",
                    }
                )
                break

    for event in recent_events:
        message = str(event.get("message") or "")
        if "found=" in message and "active_in_db=" in message:
            match = re.search(
                r"found=(?P<found>\d+).*persisted=(?P<persisted>\d+).*active_in_db=(?P<active>\d+)",
                message,
            )
            if match:
                found = int(match.group("found"))
                persisted = int(match.group("persisted"))
                active = int(match.group("active"))
                live_markets = max(1, active // 2)
                insights.append(
                    {
                        "title": "Universe Refreshed",
                        "tone": "info",
                        "meta": "Discovery sweep",
                        "body": f"The engine discovered {found} raw venues, kept {persisted} weather contracts in focus, and is actively tracking {live_markets} live Kalshi markets ({active} side-specific contract rows).",
                    }
                )
                break

    latest_entry = next(
        (
            trade
            for trade in recent_trades
            if str(trade.get("action") or "").upper() == "BUY"
            and str(trade.get("broker") or "kalshi").lower() == "kalshi"
        ),
        None,
    )
    if latest_entry:
        side = str(latest_entry.get("contract_side") or "YES").upper()
        prob = latest_entry.get("forecast_yes_prob")
        price = _coerce_float(latest_entry.get("price"))
        if prob not in (None, "") and price > 0:
            held_conf = _coerce_float(prob) if side == "YES" else (1.0 - _coerce_float(prob))
            edge = held_conf - price
            insights.append(
                {
                    "title": "Fresh Edge Captured",
                    "tone": "good",
                    "meta": str(latest_entry.get("symbol") or ""),
                    "body": f"The bot opened {side} because its side confidence was {held_conf:.1%} while the paid market price was {price:.1%}, leaving about {edge:.1%} modeled edge before exit risk.",
                }
            )
        else:
            insights.append(
                {
                    "title": "Fresh Entry Logged",
                    "tone": "good",
                    "meta": str(latest_entry.get("symbol") or ""),
                    "body": "The engine found a live opportunity and booked a new Kalshi position after all safety gates and size caps cleared.",
                }
            )
    elif recent_vetoes.get("count"):
        top = (recent_vetoes.get("top_reasons") or [{}])[0]
        insights.append(
            {
                "title": "Holding Cash On Purpose",
                "tone": "warn",
                "meta": "Safety gates active",
                "body": f"No new trade was booked in the latest window because the safety stack kept vetoing candidates. The top blocker was '{top.get('reason', 'unknown')}' ({top.get('count', 0)} hits).",
            }
        )
    else:
        insights.append(
            {
                "title": "No Urgent Action",
                "tone": "info",
                "meta": "Calm state",
                "body": "The engine is live, but nothing recent forced a trade or a risk intervention. Cash is being held until the model finds a cleaner edge.",
            }
        )

    drift = truth.get("position_drift") or {}
    if drift.get("has_drift"):
        insights.append(
            {
                "title": "Truth Drift Alert",
                "tone": "warn",
                "meta": "Operator attention",
                "body": "Broker positions and the local ledger do not perfectly match yet. The cockpit is showing both layers explicitly so you can spot whether the mismatch is stale bookkeeping or an external action.",
            }
        )

    if not insights:
        insights.append(
            {
                "title": "No Insight Available",
                "tone": "info",
                "meta": "Fallback",
                "body": "The cockpit did not detect a strong recent narrative from the live telemetry, so it is defaulting to a neutral read-only state.",
            }
        )

    return insights[:6]


def build_regime_cards(balance_usd: float | None = None) -> list[dict[str, str]]:
    balance = _coerce_float(balance_usd, ACCOUNT_SIZE)
    hub_cap = max(20.0, balance * 0.20)
    explainers = build_metric_explainers(balance_usd)
    return [
        {
            "label": "Net EV Gate",
            "value": f"{EV_THRESHOLD:.0%}",
            "detail": "post-fee minimum edge",
            "tooltip": explainers["Net EV Gate"],
        },
        {
            "label": "Fractional Kelly",
            "value": f"{KALSHI_KELLY_CAP:.0%}",
            "detail": "maximum Kelly slice",
            "tooltip": explainers["Fractional Kelly"],
        },
        {
            "label": "Regional Hub Cap",
            "value": f"${hub_cap:,.0f}",
            "detail": "max correlated regional risk",
            "tooltip": explainers["Regional Hub Cap"],
        },
        {
            "label": "Max Deployed Capital",
            "value": f"{KALSHI_MAX_DEPLOYED_PCT:.0%}",
            "detail": "account-wide live exposure",
            "tooltip": explainers["Max Deployed Capital"],
        },
        {
            "label": "Fee Buffer",
            "value": f"${KALSHI_FEE_BUFFER:.2f}",
            "detail": "extra friction allowance",
            "tooltip": explainers["Fee Buffer"],
        },
        {
            "label": "Forecast Freshness",
            "value": f"{KALSHI_DATA_FRESHNESS_MINUTES}m",
            "detail": "max age before veto",
            "tooltip": explainers["Forecast Freshness"],
        },
    ]


def get_cockpit_payload(*, live_sync: bool = True) -> dict[str, Any]:
    truth = get_live_kalshi_status(connect=live_sync, sync_broker=live_sync)
    lane = truth.get("forecast_lane") or {}
    symbols = sorted(
        {
            str(pos.get("ticker") or pos.get("local_symbol") or "")
            for pos in (truth.get("broker_positions") or []) + (truth.get("db_positions") or [])
            if str(pos.get("ticker") or pos.get("local_symbol") or "")
        }
    )
    metadata = _load_contract_metadata(symbols)
    latest_buys = _load_latest_buy_trades(symbols)

    quote_map: dict[str, dict[str, Any]] = {}
    broker_positions = list(truth.get("broker_positions") or [])
    if broker_positions:
        try:
            from execution.kalshi_broker import get_kalshi_broker

            broker = get_kalshi_broker()
            if broker.is_connected() or broker.connect():
                for position in broker_positions:
                    ticker = str(position.get("ticker") or position.get("local_symbol") or "")
                    if ticker:
                        quote_map[ticker] = broker.get_quote(ticker)
        except Exception:
            pass

    live_rows = [
        build_position_row(
            position,
            metadata=metadata.get(str(position.get("ticker") or position.get("local_symbol") or ""), {}),
            quote=quote_map.get(str(position.get("ticker") or position.get("local_symbol") or ""), {}),
            entry_trade=latest_buys.get(str(position.get("ticker") or position.get("local_symbol") or ""), {}),
            state_label="LIVE",
        )
        for position in broker_positions
    ]

    drift = truth.get("position_drift") or {}
    drift_rows = [
        build_position_row(
            position,
            metadata=metadata.get(str(position.get("ticker") or ""), {}),
            entry_trade=latest_buys.get(str(position.get("ticker") or ""), {}),
            state_label="DB_ONLY_DRIFT",
        )
        for position in drift.get("db_only", [])
    ]

    recent_trades = _load_recent_trades(limit=25)
    recent_events = _load_recent_events(limit=25)
    regime = build_regime_manifest(truth.get("balance_usd"))
    trade_edge_rows = build_trade_edge_rows(recent_trades)
    market_counts = _load_market_counts()
    recent_vetoes = get_recent_veto_summary()
    storage = runtime_storage_status()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "truth": truth,
        "positions_live": live_rows,
        "positions_db_only": drift_rows,
        "hub_exposure": summarize_hub_exposure(live_rows or drift_rows),
        "recent_trades": recent_trades,
        "trade_edge_rows": trade_edge_rows,
        "realized_pnl_curve": build_realized_pnl_curve(recent_trades),
        "recent_events": recent_events,
        "notifications": get_notifications(limit=12),
        "recent_vetoes": recent_vetoes,
        "regime": regime,
        "regime_cards": build_regime_cards(truth.get("balance_usd")),
        "metric_explainers": build_metric_explainers(truth.get("balance_usd")),
        "decision_funnel": build_decision_funnel(truth.get("balance_usd")),
        "market_counts": market_counts,
        "storage": {
            **storage,
            "db_mb": _file_size_mb(DB_PATH),
            "bot_log_mb": _file_size_mb(BOT_LOG_PATH),
            "forecast_log_mb": _file_size_mb(FORECAST_LOG_PATH),
        },
        "deploy": _load_deploy_metadata(),
        "ai_insights": build_ai_insights(
            truth=truth,
            lane=lane,
            market_counts=market_counts,
            recent_events=recent_events,
            recent_trades=recent_trades,
            recent_vetoes=recent_vetoes,
        ),
        "snapshot": _safe_json((truth.get("forecast_lane") or {}).get("snapshot_json"))
        or truth.get("forecast_snapshot")
        or {},
    }
    return payload
