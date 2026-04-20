"""
runtime/crypto_tradeability.py — Single source of truth for crypto tradeability routing.

Determines whether a symbol is executable and via which lane (spot or perp).
Called by both v10_runner._attempt_entry() and manual_scan.py execute path.

Public API:
    get_crypto_tradeability(symbol, direction, candidate, *, live, manual) -> dict
    get_recommended_crypto_lane(symbol, direction, candidate, *, live) -> str

Lanes:
    "spot"    — Coinbase spot BTC-USD/ETH-USD, LONG only, no leverage
    "perp"    — Coinbase nano perp futures (BTC/ETH/SOL/XRP), long or short
    "blocked" — not executable via any lane

Version: v16.14 (2026-04-18)
"""

from __future__ import annotations

import logging
import sqlite3
import os

logger = logging.getLogger(__name__)

# ── Canonical blocked/size/source reason strings ─────────────────────────────
# These must never be changed without updating CLAUDE.md and all callers.

_BLOCKED_REASONS = frozenset(
    {
        "none",
        "unknown_symbol_mapping",
        "spot_symbol_not_allowed",
        "spot_lane_disabled",
        "spot_direction_not_allowed",
        "spot_position_already_open",
        "spot_deployment_cap_exceeded",
        "spot_balance_unavailable",
        "perp_symbol_not_supported",
        "perp_not_autonomous_eligible",
        "perp_position_limit_reached",
        "perp_opposite_side_block",
        "perp_deployment_cap_exceeded",
        "perp_contract_min_exceeds_policy",
        "perp_source_untrusted",
        "execution_policy_unavailable",
    }
)

_SIZE_BLOCK_REASONS = frozenset(
    {
        "none",
        "spot_min_order_not_met",
        "spot_deployment_cap_exceeded",
        "perp_contract_min_exceeds_policy",
        "perp_deployment_cap_exceeded",
    }
)

_SOURCE_REASONS = frozenset(
    {
        "not_applicable",
        "trusted_source",
        "untrusted_source",
        "uncertain_mapping",
    }
)

# ── DB path helper ────────────────────────────────────────────────────────────


def _db_path() -> str:
    try:
        from config import DB_PATH

        if os.path.isabs(DB_PATH):
            return DB_PATH
        # Relative to repo root
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(_root, DB_PATH)
    except Exception:
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(_root, "logs", "trades.db")


def _blocked_result(
    symbol: str,
    underlying: str,
    reason: str,
    *,
    size_block: str = "none",
    source_reason: str = "not_applicable",
) -> dict:
    """Build a fully populated blocked tradeability dict."""
    return {
        "symbol": symbol,
        "underlying": underlying,
        "lane": "blocked",
        "recommended_lane": "blocked",
        "status": "blocked",
        "auto_executable": 0,
        "manual_executable": 0,
        "blocked_reason": reason,
        "size_block_reason": size_block,
        "source_reason": source_reason,
        "display_label": "BLOCKED",
    }


def _executable_result(
    symbol: str,
    underlying: str,
    lane: str,
    *,
    recommended_lane: str | None = None,
    auto_executable: int = 1,
    manual_executable: int = 1,
    source_reason: str = "trusted_source",
) -> dict:
    """Build a fully populated executable tradeability dict."""
    return {
        "symbol": symbol,
        "underlying": underlying,
        "lane": lane,
        "recommended_lane": recommended_lane or lane,
        "status": "executable",
        "auto_executable": auto_executable,
        "manual_executable": manual_executable,
        "blocked_reason": "none",
        "size_block_reason": "none",
        "source_reason": source_reason,
        "display_label": "SPOT EXECUTABLE" if lane == "spot" else "PERP EXECUTABLE",
    }


# ── Symbol normalisation ──────────────────────────────────────────────────────


def _normalise_underlying(symbol: str) -> str:
    """Return normalised base asset (ETH, BTC, SOL, XRP).  Empty string on failure."""
    try:
        from runtime.execution_universe import get_underlying

        return get_underlying(symbol)
    except Exception:
        # Inline fallback so we don't fail completely on import errors
        s = symbol.upper().strip()
        for pfx in ("PF_", "PI_"):
            if s.startswith(pfx):
                s = s[len(pfx) :]
                break
        if "-" in s:
            return s.split("-")[0]
        for q in ("USDT", "USDC", "BUSD", "USD"):
            if s.endswith(q) and len(s) > len(q) + 1:
                s = s[: -len(q)]
                break
        return s


# ── DB helpers ───────────────────────────────────────────────────────────────


def _count_open_spot_positions(underlying: str, paper_int: int) -> int:
    """Return number of open spot positions for underlying in open_positions table."""
    try:
        with sqlite3.connect(_db_path(), timeout=3, check_same_thread=False) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM open_positions "
                "WHERE strategy LIKE 'spot_%' AND symbol=? AND paper=?",
                (underlying, paper_int),
            ).fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


def _count_open_perp_positions(paper_int: int) -> int:
    """Return count of open perp positions (non-spot)."""
    try:
        with sqlite3.connect(_db_path(), timeout=3, check_same_thread=False) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM open_positions "
                "WHERE strategy NOT LIKE 'spot_%' AND paper=?",
                (paper_int,),
            ).fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


def _get_open_perp_directions(underlying: str, paper_int: int) -> list[str]:
    """Return list of directions for open perp positions for underlying."""
    try:
        with sqlite3.connect(_db_path(), timeout=3, check_same_thread=False) as conn:
            rows = conn.execute(
                "SELECT COALESCE(direction,'LONG') FROM open_positions "
                "WHERE symbol=? AND strategy NOT LIKE 'spot_%' AND paper=?",
                (underlying, paper_int),
            ).fetchall()
            return [r[0] for r in rows]
    except Exception:
        return []


def _get_spot_deployed_usd(paper_int: int) -> float:
    """Return total deployed USD in spot positions (qty * entry)."""
    try:
        with sqlite3.connect(_db_path(), timeout=3, check_same_thread=False) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(qty * entry), 0.0) FROM open_positions "
                "WHERE strategy LIKE 'spot_%' AND paper=?",
                (paper_int,),
            ).fetchone()
            return float(row[0]) if row else 0.0
    except Exception:
        return 0.0


def _get_perp_deployed_usd(paper_int: int) -> float:
    """Return total deployed USD across perp positions."""
    try:
        with sqlite3.connect(_db_path(), timeout=3, check_same_thread=False) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(qty * entry), 0.0) FROM open_positions "
                "WHERE strategy NOT LIKE 'spot_%' AND paper=?",
                (paper_int,),
            ).fetchone()
            return float(row[0]) if row else 0.0
    except Exception:
        return 0.0


# ── Spot balance helper ───────────────────────────────────────────────────────


def _get_spot_balance_usd() -> tuple[float, bool]:
    """
    Return (usd_available, ok) for spot balance.
    ok=False when the API call failed (triggers spot_balance_unavailable).
    """
    try:
        from execution.coinbase_spot_broker import get_spot_broker

        broker = get_spot_broker()
        bal = broker.get_spot_balance()
        usd = float(bal.get("usd_available", 0) or 0)
        return usd, True
    except Exception as e:
        logger.debug(f"[tradeability] spot balance fetch error: {e}")
        return 0.0, False


# ── Main tradeability function ────────────────────────────────────────────────


def get_crypto_tradeability(
    symbol: str,
    direction: str,
    candidate: dict | None = None,
    *,
    live: bool,
    manual: bool,
) -> dict:
    """
    Determine whether symbol+direction is executable and via which lane.

    Parameters
    ----------
    symbol    : raw scanner symbol (e.g. "PF_ETHUSD", "ETHUSDT", "ETH")
    direction : "LONG" or "SHORT"
    candidate : optional scanner candidate dict (for future price/size checks)
    live      : True = live mode, False = paper
    manual    : True = human initiated (relaxes some autonomous-only gates)

    Returns
    -------
    dict with exactly these keys:
        symbol, underlying, lane, recommended_lane, status,
        auto_executable, manual_executable, blocked_reason,
        size_block_reason, source_reason, display_label
    """
    try:
        return _evaluate_tradeability(
            symbol, direction, candidate, live=live, manual=manual
        )
    except Exception as e:
        logger.error(f"[tradeability] unhandled exception for {symbol}: {e}")
        return _blocked_result(symbol, "", "execution_policy_unavailable")


def _evaluate_tradeability(
    symbol: str,
    direction: str,
    candidate: dict | None,
    *,
    live: bool,
    manual: bool,
) -> dict:
    """Inner implementation — all exceptions caught by caller."""
    paper_int = 0 if live else 1
    direction = direction.upper().strip()

    # ── 1. Normalise symbol ───────────────────────────────────────────────────
    underlying = _normalise_underlying(symbol)
    if not underlying:
        return _blocked_result(symbol, "", "unknown_symbol_mapping")

    # ── 2. Load config constants ──────────────────────────────────────────────
    try:
        from config import (
            SPOT_LANE_ACTIVE,
            SPOT_MAX_DEPLOYED_PCT,
            SPOT_MIN_ORDER_USD,
            AUTONOMOUS_LIVE_PERP_SYMBOLS,
            CORE_EXECUTION_UNDERLYINGS,
        )

        spot_active = bool(SPOT_LANE_ACTIVE)
        spot_max_pct = float(SPOT_MAX_DEPLOYED_PCT)
        spot_min_usd = float(SPOT_MIN_ORDER_USD)
        auto_perp_syms = [s.upper() for s in AUTONOMOUS_LIVE_PERP_SYMBOLS]
        core_underlyings = {s.upper() for s in CORE_EXECUTION_UNDERLYINGS}
    except Exception as e:
        logger.error(f"[tradeability] config import failed: {e}")
        return _blocked_result(symbol, underlying, "execution_policy_unavailable")

    # ── 3. Check PERP broker support (for preferred-lane computation) ─────────
    perp_supported = False
    try:
        from execution.coinbase_broker import PRODUCT_SPECS

        perp_supported = underlying in PRODUCT_SPECS
    except Exception:
        perp_supported = underlying in core_underlyings

    # ── 4. Determine recommended lane ────────────────────────────────────────
    # BTC/ETH LONG → prefer spot when it's active; otherwise prefer perp.
    # SHORT always perp.
    spot_eligible_symbol = underlying in ("BTC", "ETH") and direction == "LONG"
    recommended_lane = _policy_recommended_lane(
        underlying,
        direction,
        spot_active=spot_active,
        perp_supported=perp_supported,
    )

    # ── 5. Evaluate SPOT lane ─────────────────────────────────────────────────
    spot_blocked_reason = _check_spot_eligibility(
        symbol,
        underlying,
        direction,
        spot_active=spot_active,
        spot_max_pct=spot_max_pct,
        spot_min_usd=spot_min_usd,
        live=live,
        paper_int=paper_int,
        manual=manual,
    )

    # ── 6. Evaluate PERP lane ─────────────────────────────────────────────────
    perp_blocked_reason = _check_perp_eligibility(
        symbol,
        underlying,
        direction,
        perp_supported=perp_supported,
        core_underlyings=core_underlyings,
        auto_perp_syms=auto_perp_syms,
        live=live,
        paper_int=paper_int,
        manual=manual,
        candidate=candidate,
    )

    # ── 7. Route: prefer spot (for eligible symbols), fall back to perp ───────
    if spot_eligible_symbol and spot_blocked_reason == "none":
        # Spot is available
        auto_ex = 1 if (not live or underlying in auto_perp_syms) else 0
        return _executable_result(
            symbol,
            underlying,
            "spot",
            recommended_lane=recommended_lane,
            auto_executable=auto_ex,
            manual_executable=1,
        )

    if perp_blocked_reason == "none":
        # Perp is available (either spot wasn't eligible, or spot was blocked)
        auto_ex = 1 if (not live or underlying in auto_perp_syms) else 0
        return _executable_result(
            symbol,
            underlying,
            "perp",
            recommended_lane=recommended_lane,
            auto_executable=auto_ex,
            manual_executable=1,
        )

    # ── 8. Both blocked — report the most specific reason ────────────────────
    # For symbols that can only use perp, report perp reason.
    # For BTC/ETH LONG, report spot reason (it's the preferred lane).
    if spot_eligible_symbol:
        primary_reason = (
            spot_blocked_reason
            if spot_blocked_reason != "none"
            else perp_blocked_reason
        )
    else:
        primary_reason = perp_blocked_reason

    # Classify size-related block reasons
    size_block = "none"
    if primary_reason in (
        "perp_contract_min_exceeds_policy",
        "perp_deployment_cap_exceeded",
    ):
        size_block = primary_reason
    elif primary_reason in ("spot_deployment_cap_exceeded", "spot_min_order_not_met"):
        size_block = primary_reason

    return _blocked_result(
        symbol,
        underlying,
        primary_reason,
        size_block=size_block,
        source_reason="trusted_source",
    )


def _check_spot_eligibility(
    symbol: str,
    underlying: str,
    direction: str,
    *,
    spot_active: bool,
    spot_max_pct: float,
    spot_min_usd: float,
    live: bool,
    paper_int: int,
    manual: bool,
) -> str:
    """
    Return "none" if spot is eligible, else one of the spot_* blocked reason strings.
    Paper mode: skip balance/deployment checks (symbol/direction routing still enforced).
    """
    # Symbol gate
    if underlying not in ("BTC", "ETH"):
        return "spot_symbol_not_allowed"

    # Direction gate (no shorting spot)
    if direction != "LONG":
        return "spot_direction_not_allowed"

    # Lane active gate
    if not spot_active:
        return "spot_lane_disabled"

    # Paper mode: skip balance/deployment checks
    if not live:
        return "none"

    # Live: balance and deployment checks
    usd_avail, ok = _get_spot_balance_usd()
    if not ok:
        return "spot_balance_unavailable"

    # Min order check
    if spot_min_usd > 0 and usd_avail < spot_min_usd:
        return "spot_min_order_not_met"

    # Deployment cap
    deployed = _get_spot_deployed_usd(paper_int)
    cap = usd_avail * spot_max_pct
    if deployed >= cap and cap > 0:
        return "spot_deployment_cap_exceeded"

    return "none"


def _check_perp_eligibility(
    symbol: str,
    underlying: str,
    direction: str,
    *,
    perp_supported: bool,
    core_underlyings: set,
    auto_perp_syms: list,
    live: bool,
    paper_int: int,
    manual: bool,
    candidate: dict | None,
) -> str:
    """
    Return "none" if perp is eligible, else one of the perp_* blocked reason strings.
    Paper mode: skip live-count, balance, and contract-min checks.
    """
    # Broker support gate
    if not perp_supported:
        return "perp_symbol_not_supported"

    # Autonomous eligibility gate (live + not manual only)
    if live and not manual:
        if underlying not in auto_perp_syms:
            return "perp_not_autonomous_eligible"

    # Paper mode: no further checks
    if not live:
        return "none"

    # Opposite-side block (live perp only) — same direction is allowed (pyramiding)
    open_directions = _get_open_perp_directions(underlying, paper_int)
    for d in open_directions:
        if d.upper() != direction.upper():
            return "perp_opposite_side_block"

    # Live perp count gate
    live_count = _count_open_perp_positions(0)  # live paper_int=0
    max_live_perps = 3
    try:
        import perps_engine as _pe

        max_live_perps = getattr(_pe, "_MAX_LIVE_PERPS", 3)
    except Exception:
        pass
    if live_count >= max_live_perps:
        return "perp_position_limit_reached"

    # Contract minimum vs 15% of balance cap (live, not manual mode)
    if not manual:
        try:
            from execution.coinbase_broker import PRODUCT_SPECS
            from execution.coinbase_broker import get_coinbase_broker

            _spec = PRODUCT_SPECS.get(underlying, {})
            _contract_size = float(_spec.get("contract_size", 0))
            if _contract_size > 0 and candidate:
                _price = float(candidate.get("price", 0))
                if _price > 0:
                    _min_contract = _price * _contract_size
                    # Get live balance to check 15% cap
                    try:
                        _broker = get_coinbase_broker()
                        if not _broker.is_connected():
                            _broker.connect()
                        _balance = _broker.get_wallet_balance()
                        if _balance > 0:
                            _cap = _balance * 0.15
                            if _min_contract > _cap:
                                return "perp_contract_min_exceeds_policy"
                    except Exception:
                        pass
        except Exception:
            pass

    # Deployment cap (live)
    try:
        _deployed = _get_perp_deployed_usd(0)  # live positions
        from execution.coinbase_broker import get_coinbase_broker

        _broker = get_coinbase_broker()
        if not _broker.is_connected():
            _broker.connect()
        _balance = _broker.get_wallet_balance()
        if _balance > 0 and _deployed >= _balance * 0.95:
            return "perp_deployment_cap_exceeded"
    except Exception:
        pass

    return "none"


# ── get_recommended_crypto_lane ───────────────────────────────────────────────


def get_recommended_crypto_lane(
    symbol: str,
    direction: str,
    candidate: dict | None = None,
    *,
    live: bool,
) -> str:
    """
    Return recommended lane string: "spot" | "perp" | "blocked".
    Pure policy only: does not look at positions, balances, deployment, or
    contract-minimum runtime state.
    """
    try:
        clean_direction = direction.upper().strip()
        underlying = _normalise_underlying(symbol)
        if not underlying:
            return "blocked"

        from config import SPOT_LANE_ACTIVE, CORE_EXECUTION_UNDERLYINGS

        spot_active = bool(SPOT_LANE_ACTIVE)
        try:
            from execution.coinbase_broker import PRODUCT_SPECS

            perp_supported = underlying in PRODUCT_SPECS
        except Exception:
            perp_supported = underlying in {
                s.upper() for s in CORE_EXECUTION_UNDERLYINGS
            }

        return _policy_recommended_lane(
            underlying,
            clean_direction,
            spot_active=spot_active,
            perp_supported=perp_supported,
        )
    except Exception:
        return "blocked"


def _policy_recommended_lane(
    underlying: str,
    direction: str,
    *,
    spot_active: bool,
    perp_supported: bool,
) -> str:
    """Pure route ownership policy with no runtime-state checks."""
    if underlying in ("BTC", "ETH") and direction == "LONG" and spot_active:
        return "spot"
    if perp_supported:
        return "perp"
    return "blocked"
