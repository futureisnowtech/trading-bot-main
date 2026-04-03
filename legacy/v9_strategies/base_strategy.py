"""
strategies/base_strategy.py
Abstract base class all strategies inherit from.
Defines the interface and shared utilities.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


@dataclass
class Signal:
    """Standardized signal output from any strategy."""
    action: str          # 'BUY' | 'SELL' | 'HOLD'
    symbol: str
    strategy: str
    confidence: float    # 0.0 to 1.0
    reason: str
    price: float
    suggested_size_usd: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    metadata: dict = field(default_factory=dict)

    def __repr__(self):
        emoji = {'BUY': '🟢', 'SELL': '🔴', 'HOLD': '⚪'}.get(self.action, '?')
        return (f"{emoji} {self.action} {self.symbol} @ ${self.price:.4f} "
                f"| conf={self.confidence:.0%} | {self.reason}")


class BaseStrategy(ABC):
    """
    Abstract base strategy.
    All strategies must implement generate_signal().
    """

    def __init__(self, name: str):
        self.name = name
        self._last_signal: Optional[Signal] = None

    @abstractmethod
    def generate_signal(self, symbol: str, df: pd.DataFrame) -> Signal:
        """
        Analyze the dataframe and return a Signal.
        df must already have indicators added (via data/indicators.py).
        """
        pass

    def on_fill(self, symbol: str, action: str, qty: float, price: float) -> None:
        """Called by execution layer when order is filled. Override if needed."""
        pass

    def on_stop_hit(self, symbol: str, price: float) -> None:
        """Called when stop loss is triggered."""
        pass

    def on_target_hit(self, symbol: str, price: float) -> None:
        """Called when take profit is triggered."""
        pass

    @property
    def last_signal(self) -> Optional[Signal]:
        return self._last_signal

    def _hold(self, symbol: str, price: float, reason: str = 'No signal') -> Signal:
        return Signal(
            action='HOLD',
            symbol=symbol,
            strategy=self.name,
            confidence=0.0,
            reason=reason,
            price=price
        )
