import time
import psutil
import threading
from typing import Dict, Any, List

class SystemState:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(SystemState, cls).__new__(cls)
                cls._instance._initialize()
            return cls._instance

    def _initialize(self):
        self.start_time = time.time()
        self.last_metrics_refresh = 0
        self.state = {
            "mode": "PAPER",  # PAPER or LIVE
            "exchange": {
                "connected": False,
                "ws_connected": False,
                "latency_ms": 0,
                "buying_power": 0.0,
            },
            "strategy": {
                "active_symbol": "NONE",
                "current_signal": "NONE",
                "obi": 0.0,
                "microprice": 0.0,
                "active_positions": [],
            },
            "system": {
                "cpu_percent": 0.0,
                "ram_percent": 0.0,
                "uptime_seconds": 0,
            }
        }
        self.lock = threading.Lock()

    def update_exchange(self, connected: bool = None, ws_connected: bool = None, latency: int = None, buying_power: float = None):
        with self.lock:
            if connected is not None:
                self.state["exchange"]["connected"] = connected
            if ws_connected is not None:
                self.state["exchange"]["ws_connected"] = ws_connected
            if latency is not None:
                self.state["exchange"]["latency_ms"] = latency
            if buying_power is not None:
                self.state["exchange"]["buying_power"] = buying_power

    def update_strategy(self, active_symbol: str = None, signal: str = None, obi: float = None, microprice: float = None, positions: List[Dict] = None):
        with self.lock:
            if active_symbol is not None:
                self.state["strategy"]["active_symbol"] = active_symbol
            if signal is not None:
                self.state["strategy"]["current_signal"] = signal
            if obi is not None:
                self.state["strategy"]["obi"] = obi
            if microprice is not None:
                self.state["strategy"]["microprice"] = microprice
            if positions is not None:
                self.state["strategy"]["active_positions"] = positions

    def set_mode(self, mode: str):
        with self.lock:
            self.state["mode"] = mode.upper()

    def refresh_system_metrics(self):
        now = time.time()
        with self.lock:
            if now - self.last_metrics_refresh < 5.0:
                return
            self.last_metrics_refresh = now
            self.state["system"]["cpu_percent"] = psutil.cpu_percent(interval=0.1)
            self.state["system"]["ram_percent"] = psutil.virtual_memory().percent
            self.state["system"]["uptime_seconds"] = int(now - self.start_time)

    def get_state(self) -> Dict[str, Any]:
        self.refresh_system_metrics()
        with self.lock:
            return self.state.copy()

# Global singleton instance
state = SystemState()
