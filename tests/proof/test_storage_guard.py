from __future__ import annotations

import importlib
import os
from collections import namedtuple


def test_config_runtime_root_overrides_paths(monkeypatch, tmp_path):
    import config as cfg

    keys = [
        "ALGO_RUNTIME_DIR",
        "DB_PATH",
        "CSV_LOG_DIR",
        "BOT_LOG_PATH",
        "FORECAST_LOG_PATH",
        "MACRO_CACHE_FILE",
    ]
    original = {key: os.environ.get(key) for key in keys}

    runtime_dir = tmp_path / "external-runtime"
    monkeypatch.setenv("ALGO_RUNTIME_DIR", str(runtime_dir))
    for key in keys[1:]:
        monkeypatch.delenv(key, raising=False)

    reloaded = importlib.reload(cfg)
    try:
        assert reloaded.RUNTIME_ROOT == str(runtime_dir)
        assert reloaded.DB_PATH == str(runtime_dir / "trades.db")
        assert reloaded.CSV_LOG_DIR == str(runtime_dir / "csv")
        assert reloaded.BOT_LOG_PATH == str(runtime_dir / "bot.log")
        assert reloaded.FORECAST_LOG_PATH == str(runtime_dir / "forecast.log")
        assert reloaded.MACRO_CACHE_FILE == str(runtime_dir / "cached_macro_regime.json")
    finally:
        for key, value in original.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)
        importlib.reload(cfg)


def test_runtime_storage_status_flags_low_disk(monkeypatch, tmp_path):
    import runtime.storage_guard as sg

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(sg.shutil, "disk_usage", lambda path: usage(1000, 950, 25 * 1024 * 1024))

    status = sg.runtime_storage_status(path=str(tmp_path), min_free_mb=128)

    assert status["ok"] is False
    assert status["threshold_mb"] == 128.0
    assert status["free_mb"] < status["threshold_mb"]


def test_health_check_degrades_on_low_disk(proof_runtime, monkeypatch):
    import monitoring.health_check as hc

    monkeypatch.setattr(hc, "DB_PATH", str(proof_runtime.db_path), raising=False)
    monkeypatch.setattr(
        hc,
        "runtime_storage_status",
        lambda **kwargs: {
            "ok": False,
            "free_mb": 512.0,
            "threshold_mb": 2048.0,
            "path": str(proof_runtime.db_path.parent),
        },
    )

    result = hc.run_health_check(force=True)
    checks = {check["name"]: check for check in result["checks"]}

    assert result["healthy"] is False
    assert checks["disk_headroom"]["ok"] is False


def test_sniper_cron_skips_cycle_on_low_disk(monkeypatch):
    import sniper_cron as sc

    called = {"cycle": 0}

    monkeypatch.setattr(sc, "KALSHI_ENABLED", True, raising=False)
    monkeypatch.setattr(sc, "FORECAST_LANE_ACTIVE", True, raising=False)
    monkeypatch.setattr(sc, "FORECAST_AUTONOMOUS_ENABLED", True, raising=False)
    monkeypatch.setattr(
        sc,
        "runtime_storage_status",
        lambda: {
            "ok": False,
            "free_mb": 256.0,
            "threshold_mb": 2048.0,
            "path": "/tmp/runtime",
        },
        raising=False,
    )
    monkeypatch.setattr(
        sc,
        "run_execution_cycle",
        lambda bankroll: called.__setitem__("cycle", called["cycle"] + 1),
        raising=False,
    )

    assert sc.main() == 0
    assert called["cycle"] == 0


def test_execution_daemon_starts_weather_monitor_after_first_cycle(monkeypatch):
    import execution_daemon as ed

    order: list[str] = []

    monkeypatch.setattr(ed, "KALSHI_ENABLED", True, raising=False)
    monkeypatch.setattr(ed, "FORECAST_LANE_ACTIVE", True, raising=False)
    monkeypatch.setattr(ed, "FORECAST_AUTONOMOUS_ENABLED", True, raising=False)
    monkeypatch.setattr(ed, "run_reconciliation", lambda: None, raising=False)
    monkeypatch.setattr(ed, "sync_incidents_and_notify", lambda: None, raising=False)
    monkeypatch.setattr(ed, "maintain_runtime_storage", lambda: None, raising=False)
    monkeypatch.setattr(
        "notifications.telegram_bot.start_bot_thread",
        lambda: order.append("telegram"),
    )
    monkeypatch.setattr(
        ed,
        "runtime_storage_status",
        lambda: {
            "ok": True,
            "free_mb": 4096.0,
            "threshold_mb": 2048.0,
            "path": "/tmp/runtime",
        },
        raising=False,
    )
    monkeypatch.setattr(
        ed,
        "run_execution_cycle",
        lambda bankroll, run_rbi=True: order.append("cycle") or {"entries": 0},
        raising=False,
    )
    monkeypatch.setattr(
        ed,
        "start_weather_monitor",
        lambda: order.append("monitor"),
        raising=False,
    )
    monkeypatch.setattr(ed.time, "sleep", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()))

    assert ed.main() == 0
    assert order == ["telegram", "cycle", "monitor"]
