"""
strategies/ai_agents/risk_synthesizer.py
The last brain before any order hits a broker.
Hard rules that NO AI can override + AI sanity check + auto debate depth tuning.
"""
import json
import os
import sys
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from strategies.ai_agents.analyst_agents import call_claude_structured
from strategies.ai_agents.debate_engine import DebateResult
from config import (
    ACCOUNT_SIZE, MAX_RISK_PER_TRADE_PCT, MAX_DAILY_LOSS_PCT,
    PAPER_TRADING, EQUITY_STOP_LOSS_PCT, CRYPTO_STOP_LOSS_PCT,
    AUTO_TUNE_FULL_DEBATE_THRESHOLD, AUTO_TUNE_WIN_RATE_THRESHOLD
)

# THE AMYGDALA REMOVAL CONSTANTS — quoted intentionally, they are principles
NO_EMOTION_RULES = [
    "RULE 1: Never chase. If price moved >3% since signal, SKIP.",
    "RULE 2: Never average down. One position per symbol, ever.",
    "RULE 3: Stop losses are sacred. Never moved wider after entry.",
    "RULE 4: Wins don't justify ignoring rules on the next trade.",
    "RULE 5: Losses don't justify revenge trading or larger size.",
    "RULE 6: FOMO is not a signal. Watching a stock go up without you is fine.",
    "RULE 7: When in doubt, HOLD. A skipped trade loses nothing.",
    "RULE 8: The goal is being in business next month, not winning today.",
]

SANITY_SCHEMA = {
    "type": "object",
    "properties": {
        "veto": {"type": "boolean"},
        "veto_reason": {"type": "string"}
    },
    "required": ["veto", "veto_reason"]
}


class FinalDecision:
    def __init__(self, action, symbol, size_usd, entry_price, stop_loss,
                 take_profit, confidence, reasoning, veto_reason='',
                 debate_result=None):
        self.action = action
        self.symbol = symbol
        self.size_usd = size_usd
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.confidence = confidence
        self.reasoning = reasoning
        self.veto_reason = veto_reason
        self.debate_result = debate_result

    def __repr__(self):
        e = {'BUY': '🟢', 'SELL': '🔴', 'HOLD': '⚪', 'VETO': '🚫'}.get(self.action, '?')
        lines = [f"\n{'═'*60}", f"  FINAL DECISION: {self.symbol}", f"{'═'*60}",
                 f"  {e} {self.action}"]
        if self.action == 'BUY':
            lines += [f"  Size: ${self.size_usd:.2f}",
                      f"  Entry: ${self.entry_price:.4f}",
                      f"  Stop: ${self.stop_loss:.4f}",
                      f"  Target: ${self.take_profit:.4f}",
                      f"  Confidence: {self.confidence:.0%}"]
        if self.veto_reason:
            lines.append(f"  VETO: {self.veto_reason}")
        lines += [f"  Reason: {self.reasoning}", f"{'═'*60}"]
        return '\n'.join(lines)


def synthesize_final_decision(
    debate: DebateResult,
    current_price: float,
    asset_class: str,
    daily_pnl: float,
    open_positions: int,
    trades_today: int,
    account_balance: float = ACCOUNT_SIZE,
    allow_short: bool = False,
) -> FinalDecision:
    """Final decision after debate. Applies hard rules then AI sanity check."""
    symbol = debate.symbol
    signal = debate.synthesized_signal
    confidence = debate.synthesized_confidence

    # ── HARD VETO RULES — no AI overrides ─────────────────────────────────────

    max_daily_loss = account_balance * MAX_DAILY_LOSS_PCT
    if daily_pnl < -max_daily_loss:
        return FinalDecision('VETO', symbol, 0, current_price, 0, 0, 0, '',
                             f"Daily loss limit ${max_daily_loss:.2f} breached. "
                             f"Rule 8: live to play tomorrow.")

    if signal == 'SELL' and allow_short:
        # Route SELL signal as a SHORT entry
        return _synthesize_short(debate, current_price, asset_class,
                                 confidence, account_balance)

    if signal != 'BUY':
        return FinalDecision('HOLD', symbol, 0, current_price, 0, 0, confidence,
                             f"Debate: {signal}. {debate.unified_reasoning}")

    min_conf = 0.50 if asset_class == 'equity' else 0.55
    if confidence < min_conf:
        return FinalDecision('VETO', symbol, 0, current_price, 0, 0, confidence, '',
                             f"Confidence {confidence:.0%} < {min_conf:.0%}. Rule 7: when in doubt, HOLD.")

    buy_votes = debate.vote_breakdown.get('BUY', 0)
    total_agents = sum(debate.vote_breakdown.values())
    if total_agents > 0 and buy_votes / total_agents < 0.60:
        return FinalDecision('VETO', symbol, 0, current_price, 0, 0, confidence, '',
                             f"Only {buy_votes}/{total_agents} agents agree. Need 60%+.")

    # ── Position sizing ────────────────────────────────────────────────────────
    stop_pct = EQUITY_STOP_LOSS_PCT if asset_class == 'equity' else CRYPTO_STOP_LOSS_PCT
    risk_dollars = account_balance * MAX_RISK_PER_TRADE_PCT
    stop_loss = current_price * (1 - stop_pct)
    risk_per_unit = current_price - stop_loss

    if risk_per_unit > 0:
        units = risk_dollars / risk_per_unit
        position_size_usd = units * current_price
    else:
        position_size_usd = account_balance * 0.10

    # Scale with account size — larger account gets larger positions
    scale = min(account_balance / 500.0, 5.0)  # Cap at 5x from initial $500
    position_size_usd = min(position_size_usd * scale, account_balance * 0.20)
    position_size_usd = max(position_size_usd, 10.0)

    risk = current_price - stop_loss
    take_profit = current_price + (risk * 2.0)

    # ── AI sanity check ────────────────────────────────────────────────────────
    rr = (take_profit - current_price) / max(risk, 0.0001)
    sanity = call_claude_structured(
        system_prompt=(
            "You are the final risk manager for a trading account.\n"
            "Your ONLY job is to veto trades that violate principles.\n"
            "THE AMYGDALA IS REMOVED:\n" + '\n'.join(NO_EMOTION_RULES)
        ),
        user_prompt=(
            f"Final check: {symbol} ({asset_class})\n"
            f"Signal: {signal} @ ${current_price:.4f}\n"
            f"Size: ${position_size_usd:.2f} ({position_size_usd/account_balance:.1%} of ${account_balance:.0f})\n"
            f"Stop: ${stop_loss:.4f} | Target: ${take_profit:.4f} | R:R={rr:.1f}:1\n"
            f"Confidence: {confidence:.0%}\n"
            f"Key risk: {debate.key_risk}\n"
            f"Today P&L: ${daily_pnl:+.2f}\n"
            f"Should this be vetoed?"
        ),
        max_tokens=100,
        call_type='sanity_check',
        schema=SANITY_SCHEMA,
    )

    # Map sanity response (may come back as signal dict or veto dict)
    is_vetoed = (sanity.get('veto', False) or
                 sanity.get('signal', 'BUY') == 'SELL')
    veto_reason = sanity.get('veto_reason', sanity.get('reasoning', ''))

    if is_vetoed:
        return FinalDecision('VETO', symbol, 0, current_price, 0, 0, confidence, '',
                             veto_reason or 'AI sanity check veto')

    reasoning = (
        f"[{buy_votes}/{total_agents} agree] {debate.unified_reasoning} | "
        f"Bull: {debate.bull_case} | Risk: {debate.key_risk}"
    )

    return FinalDecision(
        action='BUY', symbol=symbol,
        size_usd=round(position_size_usd, 2),
        entry_price=current_price,
        stop_loss=round(stop_loss, 6),
        take_profit=round(take_profit, 6),
        confidence=confidence,
        reasoning=reasoning,
        debate_result=debate,
    )


def _synthesize_short(debate, current_price, asset_class,
                      confidence, account_balance) -> FinalDecision:
    """Build a SHORT entry FinalDecision from a SELL debate signal."""
    symbol = debate.symbol
    stop_pct = EQUITY_STOP_LOSS_PCT if asset_class == 'equity' else CRYPTO_STOP_LOSS_PCT

    min_conf = 0.55
    if confidence < min_conf:
        return FinalDecision('VETO', symbol, 0, current_price, 0, 0, confidence, '',
                             f"Short confidence {confidence:.0%} < {min_conf:.0%}. Rule 7: when in doubt, HOLD.")

    sell_votes = debate.vote_breakdown.get('SELL', 0)
    total_agents = sum(debate.vote_breakdown.values())
    if total_agents > 0 and sell_votes / total_agents < 0.60:
        return FinalDecision('VETO', symbol, 0, current_price, 0, 0, confidence, '',
                             f"Only {sell_votes}/{total_agents} agents agree to short. Need 60%+.")

    risk_dollars = account_balance * MAX_RISK_PER_TRADE_PCT
    stop_loss = current_price * (1 + stop_pct)       # Stop ABOVE entry for short
    risk_per_unit = stop_loss - current_price
    units = risk_dollars / risk_per_unit if risk_per_unit > 0 else account_balance * 0.10 / current_price
    position_size_usd = min(units * current_price, account_balance * 0.20)
    position_size_usd = max(position_size_usd, 10.0)
    take_profit = current_price - (stop_loss - current_price) * 2.0  # 2:1 R:R below entry

    reasoning = (
        f"[SHORT {sell_votes}/{total_agents} agree] {debate.unified_reasoning} | "
        f"Bear: {debate.bear_case} | Risk: {debate.key_risk}"
    )
    return FinalDecision(
        action='SHORT', symbol=symbol,
        size_usd=round(position_size_usd, 2),
        entry_price=current_price,
        stop_loss=round(stop_loss, 6),
        take_profit=round(take_profit, 6),
        confidence=confidence,
        reasoning=reasoning,
        debate_result=debate,
    )


def should_use_full_debate(account_balance: float, win_rate: float) -> bool:
    """
    Auto-tune debate depth based on account size and win rate.
    Returns True = use full 8-agent debate, False = use quick 3-agent.
    """
    if account_balance >= AUTO_TUNE_FULL_DEBATE_THRESHOLD:
        return True
    if win_rate >= AUTO_TUNE_WIN_RATE_THRESHOLD:
        return True
    return False


def get_debate_recommendation(account_balance: float, win_rate: float,
                               monthly_cost: float) -> dict:
    """
    AI recommendation for optimal debate settings based on current performance.
    Used in dashboard cost panel.
    """
    use_full = should_use_full_debate(account_balance, win_rate)
    est_cost_full = 0.12 * 5 * 22 * 30    # ~$40/mo full debate at 5 trades/day
    est_cost_quick = 0.04 * 5 * 22 * 30   # ~$13/mo quick debate

    dollar = '$'
    if use_full:
        rec = 'full_debate'
        reason = (f"Account {dollar}{account_balance:.0f} and win rate {win_rate:.0%} "
                  f"justify full 8-agent debate for maximum signal quality.")
        est_monthly = est_cost_full
    else:
        rec = 'quick_debate'
        reason = (f"Account {dollar}{account_balance:.0f} is building. "
                  f"3-agent quick debate saves ~{dollar}{est_cost_full-est_cost_quick:.0f}/mo "
                  f"while still filtering bad trades effectively.")
        est_monthly = est_cost_quick

    return {
        'recommendation': rec,
        'reason': reason,
        'est_monthly_cost': est_monthly,
        'actual_monthly_cost': monthly_cost,
        'on_track': monthly_cost <= est_monthly * 1.2,
    }
