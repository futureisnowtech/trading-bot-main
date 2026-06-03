import os
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
        self._proc = psutil.Process(os.getpid())
        try:
            self._cpu_count = max(1, psutil.cpu_count() or 1)
        except Exception:
            self._cpu_count = 1
        try:
            self._proc.cpu_percent(interval=None)
        except Exception:
            pass
        self.state = {
            "mode": "LIVE",
            "kalshi": {
                "connected": False,
                "balance": 0.0,
                "active_markets": 0,
            },
            "strategy": {
                "active_positions": [],
            },
            "system": {
                "cpu_percent": 0.0,
                "ram_percent": 0.0,
                "uptime_seconds": 0,
            }
        }
        self.lock = threading.Lock()

    def set_mode(self, mode: str):
        """Mandatory LIVE enforcement."""
        with self.lock:
            self.state["mode"] = "LIVE"

    def update_kalshi(self, connected: bool = None, balance: float = None, active_markets: int = None):
        with self.lock:
            if connected is not None:
                self.state["kalshi"]["connected"] = connected
            if balance is not None:
                self.state["kalshi"]["balance"] = balance
            if active_markets is not None:
                self.state["kalshi"]["active_markets"] = active_markets

    def update_strategy(self, positions: List[Dict] = None):
        with self.lock:
            if positions is not None:
                self.state["strategy"]["active_positions"] = positions

    def update_prometheus(self):
        """Push internal state to Prometheus gauges."""
        self.refresh_system_metrics()
        try:
            from monitoring import metrics
            with self.lock:
                s = self.state
                metrics.BUYING_POWER_GAUGE.set(s["kalshi"]["balance"])
                metrics.OPEN_TRADES_GAUGE.set(len(s["strategy"]["active_positions"]))
                
                # Total equity estimation
                total_pos_val = sum(float(p.get("qty", 0)) * float(p.get("entry_price", 0)) for p in s["strategy"]["active_positions"])
                metrics.EQUITY_GAUGE.set(s["kalshi"]["balance"] + total_pos_val)
                
                # System Metrics
                metrics.CPU_PERCENT_GAUGE.set(s["system"]["cpu_percent"])
                metrics.RAM_PERCENT_GAUGE.set(s["system"]["ram_percent"])
        except Exception:
            pass

    def refresh_system_metrics(self):
        now = time.time()
        with self.lock:
            if now - self.last_metrics_refresh < 5.0:
                return
            self.last_metrics_refresh = now
            try:
                raw = self._proc.cpu_percent(interval=None)
            except Exception:
                raw = 0.0
            self.state["system"]["cpu_percent"] = round(raw / self._cpu_count, 1)
            self.state["system"]["ram_percent"] = psutil.virtual_memory().percent
            self.state["system"]["uptime_seconds"] = int(now - self.start_time)

    def get_state(self) -> Dict[str, Any]:
        self.refresh_system_metrics()
        with self.lock:
            return self.state.copy()

# Global singleton instance
state = SystemState()
