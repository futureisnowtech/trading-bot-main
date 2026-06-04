"""Minimal kill-switch singleton for proof-runtime resets."""

from __future__ import annotations

import threading


class _Switch:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.halted = False
        self.halt_reason = ""
        self.halt_ts = 0.0
        self.live_baseline = 0.0
        self.api_errors: list[str] = []
        self.last_latency_ms = 0.0


_SWITCH = _Switch()


def get_switch() -> _Switch:
    return _SWITCH


def is_halted() -> bool:
    return bool(_SWITCH.halted)


def halt(reason: str = "") -> None:
    with _SWITCH.lock:
        _SWITCH.halted = True
        _SWITCH.halt_reason = reason


def resume() -> None:
    with _SWITCH.lock:
        _SWITCH.halted = False
        _SWITCH.halt_reason = ""
