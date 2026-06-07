from __future__ import annotations

from pathlib import Path


def test_rotate_log_file_rotates_when_above_cap(tmp_path):
    from runtime.storage_maintenance import rotate_log_file

    log_path = tmp_path / "bot.log"
    log_path.write_text("x" * (2 * 1024 * 1024), encoding="utf-8")

    result = rotate_log_file(log_path, max_mb=1, backups=2)

    assert result["rotated"] is True
    assert log_path.exists()
    assert log_path.read_text(encoding="utf-8") == ""
    assert (tmp_path / "bot.log.1").exists()


def test_maintain_runtime_storage_reports_all_sections(tmp_path):
    from runtime.storage_maintenance import maintain_runtime_storage

    db_path = tmp_path / "trades.db"
    bot_log = tmp_path / "bot.log"
    forecast_log = tmp_path / "forecast.log"

    bot_log.write_text("ok", encoding="utf-8")
    forecast_log.write_text("ok", encoding="utf-8")
    db_path.write_text("", encoding="utf-8")

    result = maintain_runtime_storage(
        bot_log_path=str(bot_log),
        forecast_log_path=str(forecast_log),
        db_path=str(db_path),
        log_max_mb=8,
        wal_threshold_mb=16,
    )

    assert set(result.keys()) == {"bot_log", "forecast_log", "wal"}
    assert result["bot_log"]["rotated"] is False
    assert result["forecast_log"]["rotated"] is False
    assert result["wal"]["checkpointed"] is True
    assert result["wal"]["forced_daily"] is True
    assert result["wal"]["wal_bytes_before"] == 0
