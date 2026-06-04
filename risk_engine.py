"""Minimal risk engine state for proof-runtime resets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskState:
    account_balance: float = 0.0
    peak_balance: float = 0.0
    daily_start_balance: float = 0.0


_state = RiskState()
