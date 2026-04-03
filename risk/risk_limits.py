"""
risk/risk_limits.py — Entry eligibility checks: position limits, correlation,
market hours, daily trade cap, deployment cap, and crypto fee gate.
Adapted from Polymarket bot's 15-point check pipeline pattern.
Extracted from risk_manager.py (Sprint 1, Task 3).
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    ACCOUNT_SIZE, MAX_DEPLOYED_PCT,
    MAX_POSITIONS_EQUITY, MAX_POSITIONS_CRYPTO, PERP_MAX_POSITIONS,
    MAX_TRADES_PER_DAY_EQUITY, MAX_TRADES_PER_DAY_CRYPTO,
    COINBASE_TAKER_FEE_PCT, CRYPTO_MIN_PROFIT_FEE_MULTIPLE,
)
try:
    from data.market_data import is_market_open, is_in_no_trade_window
except ModuleNotFoundError:
    # data/market_data.py was moved to legacy/v9_data/ in v10 cleanup.
    # v10 trades Kraken Futures 24/7 — equity market hours are not needed.
    # Provide neutral fallbacks so risk_limits can still import cleanly.
    def is_market_open(market: str = 'crypto') -> bool:  # type: ignore[misc]
        return True  # Kraken Futures: always open

    def is_in_no_trade_window() -> bool:  # type: ignore[misc]
        return False  # no equity no-trade window for 24/7 perp system
from logging_db.trade_logger import get_daily_trade_count

# Crypto correlation clusters — never hold two symbols from the same cluster.
# Stored as base-asset sets so Coinbase (BTC-USDC) and Binance (BTCUSDT) both match.
_CORR_GROUPS_BASE = [
    {'BTC', 'LTC', 'BCH'},
    {'ETH', 'LINK', 'UNI', 'ARB', 'OP', 'INJ'},
    {'SOL', 'AVAX', 'ADA', 'NEAR', 'APT', 'SUI', 'DOT'},
    {'PEPE', 'WIF', 'DOGE'},
    {'XRP'},
]


def _normalize_to_base(symbol: str) -> str:
    """Strip quote suffix: BTC-USDC, BTCUSDT, BTC-USD → BTC."""
    return (symbol.replace('-USDC', '').replace('-USDT', '')
                  .replace('-USD', '').replace('USDT', '').replace('USDC', '').upper())


class RiskCheckResult:
    def __init__(self, approved: bool, reason: str = '', adjusted_size: float = None):
        self.approved = approved
        self.reason = reason
        self.adjusted_size = adjusted_size

    def __bool__(self):
        return self.approved

    def __repr__(self):
        s = '✅ APPROVED' if self.approved else '❌ BLOCKED'
        return f"RiskCheck[{s}: {self.reason}]"


def check_market_hours(strategy: str, side: str) -> RiskCheckResult:
    """Block equity entries when market is closed or in the opening no-trade window."""
    is_eq = 'equity' in strategy.lower() or 'futures' in strategy.lower()
    if is_eq:
        if not is_market_open():
            return RiskCheckResult(False, "Market closed")
        if is_in_no_trade_window() and side == 'BUY':
            return RiskCheckResult(False, "No trades 9:30–10:00 ET opening window")
    return RiskCheckResult(True, '')


def check_position_limits(strategy: str, symbol: str, side: str,
                           equity_positions: dict, crypto_positions: dict,
                           perp_positions: dict, paper: bool) -> RiskCheckResult:
    """
    Check max open positions, duplicate-entry guard, correlation block,
    and daily trade count.
    """
    if side not in ('BUY', 'SELL'):
        return RiskCheckResult(True, '')

    is_eq   = 'equity' in strategy.lower() or 'futures' in strategy.lower()
    is_cr   = 'crypto' in strategy.lower() and 'perp' not in strategy.lower()
    is_perp = 'perp' in strategy.lower()

    # Max open positions
    if is_eq and len(equity_positions) >= MAX_POSITIONS_EQUITY:
        return RiskCheckResult(False, f"Max equity positions ({MAX_POSITIONS_EQUITY}) reached")
    if is_cr and len(crypto_positions) >= MAX_POSITIONS_CRYPTO:
        return RiskCheckResult(False, f"Max crypto positions ({MAX_POSITIONS_CRYPTO}) reached")
    if is_perp and len(perp_positions) >= PERP_MAX_POSITIONS:
        return RiskCheckResult(False, f"Max perp positions ({PERP_MAX_POSITIONS}) reached")

    # Duplicate-entry guard — normalized so BTC-USDC and BTCUSDT both match
    sym_base = _normalize_to_base(symbol)
    all_held_bases = {_normalize_to_base(h) for h in
                      list(equity_positions) + list(crypto_positions) + list(perp_positions)}
    if equity_positions.get(symbol) or crypto_positions.get(symbol) or perp_positions.get(symbol) \
            or sym_base in all_held_bases:
        return RiskCheckResult(False, f"Already holding {symbol} — no double-entry")

    # Crypto correlation block — normalized to base asset (catches cross-broker pairs)
    if is_cr or is_perp:
        for group in _CORR_GROUPS_BASE:
            if sym_base in group:
                for held in list(crypto_positions) + list(perp_positions):
                    if _normalize_to_base(held) in group and held != symbol:
                        return RiskCheckResult(
                            False,
                            f"Correlation block: already holding {held} "
                            f"(same cluster as {symbol} — concentrated risk)"
                        )

    # Daily trade count cap
    count = get_daily_trade_count(strategy, paper=paper)
    max_t = MAX_TRADES_PER_DAY_EQUITY if is_eq else MAX_TRADES_PER_DAY_CRYPTO
    if count >= max_t:
        return RiskCheckResult(False, f"Max {max_t} trades/day reached ({strategy})")

    return RiskCheckResult(True, '')


def check_deployment_cap(requested_size_usd: float, deployed_usd: float) -> RiskCheckResult:
    """
    Enforce the max-deployed-capital cap.
    Returns RiskCheckResult with adjusted_size if partially available.
    """
    max_deploy = ACCOUNT_SIZE * MAX_DEPLOYED_PCT
    max_pos = ACCOUNT_SIZE * 0.20
    final_size = min(requested_size_usd, max_pos)

    if deployed_usd + final_size > max_deploy:
        available = max_deploy - deployed_usd
        if available < 10:
            return RiskCheckResult(False,
                                   f"Max capital deployed (${deployed_usd:.0f}/${max_deploy:.0f})")
        final_size = available

    if final_size < 10:
        return RiskCheckResult(False, f"Position size ${final_size:.2f} too small")

    return RiskCheckResult(True, 'Deployment cap OK', adjusted_size=round(final_size, 2))


def check_crypto_fee_gate(strategy: str, current_price: float,
                           stop_price: float, tp_price: float) -> RiskCheckResult:
    """
    Crypto only: reject entry if take-profit can't clear 2× round-trip fees.
    Prevents entering trades where fees eat the entire potential profit.
    """
    is_cr = 'crypto' in strategy.lower() and 'perp' not in strategy.lower()
    if not is_cr or current_price <= 0:
        return RiskCheckResult(True, '')

    potential_pct  = (tp_price - current_price) / current_price
    round_trip_fee = 2 * COINBASE_TAKER_FEE_PCT
    required_pct   = round_trip_fee * CRYPTO_MIN_PROFIT_FEE_MULTIPLE
    if potential_pct < required_pct:
        return RiskCheckResult(
            False,
            f"Fee gate: take-profit only {potential_pct:.2%} away but need "
            f"{required_pct:.2%} to clear {CRYPTO_MIN_PROFIT_FEE_MULTIPLE:.0f}× fees "
            f"(stop=${stop_price:,.4f} tp=${tp_price:,.4f})"
        )
    return RiskCheckResult(True, '')
