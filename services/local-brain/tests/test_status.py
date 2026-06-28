from __future__ import annotations

import asyncio
import os
from datetime import datetime

from fritz_local_brain import status as status_module
from fritz_local_brain.api import routes
from fritz_local_brain.captures import mark_captures_processed
from fritz_local_brain.models import CompileRunResult
from fritz_local_brain.run_history import clear_recent_runs_for_tests, record_compile


class _Settings:
    def __init__(self, tmp_path, *, scheduler_enabled: bool, scheduler_dry_run: bool = False, autostart: bool = False) -> None:
        self.brain_home = tmp_path
        self.skills_dir = tmp_path / "skills"
        self.scheduler_enabled = scheduler_enabled
        self.scheduler_dry_run = scheduler_dry_run
        self.local_brain_autostart_installed = autostart
        self.interval_minutes = 30
        self.allow_first_external_sync = False


class _State:
    scheduler_task = None


class _App:
    state = _State()


class _Request:
    app = _App()


async def _status_with_current_task():
    request = _Request()
    request.app.state.scheduler_task = asyncio.current_task()
    return await routes.status(request)


def test_status_reports_processing_state_backlog_and_last_compile(tmp_path, monkeypatch) -> None:
    older = tmp_path / "capture" / "daily" / "older.md"
    newer = tmp_path / "capture" / "inbox" / "newer.md"
    session = tmp_path / "capture" / "sessions" / "session.md"
    for capture in (older, newer, session):
        capture.parent.mkdir(parents=True, exist_ok=True)
        capture.write_text(capture.name, encoding="utf-8")
    os.utime(older, (100, 100))
    os.utime(newer, (200, 200))
    os.utime(session, (300, 300))

    started = datetime(2026, 5, 27, 10, 0, 0)
    finished = datetime(2026, 5, 27, 10, 5, 0)
    clear_recent_runs_for_tests()
    record_compile(
        CompileRunResult(
            run_id="run-1",
            started_at=started,
            finished_at=finished,
            dry_run=False,
            captures_considered=1,
        )
    )
    monkeypatch.setattr(
        routes,
        "get_settings",
        lambda: _Settings(tmp_path, scheduler_enabled=True, scheduler_dry_run=False, autostart=True),
    )

    result = asyncio.run(_status_with_current_task())

    assert result.service_running is True
    assert result.scheduler_enabled is True
    assert result.scheduler_dry_run is False
    assert result.processing_mode == "apply"
    assert result.processing_active is True
    assert result.last_successful_compile_at == finished
    assert result.pending_captures_by_source == {"inbox": 1, "daily": 1, "sessions": 1}
    assert result.oldest_pending_capture_path == str(older)
    assert result.oldest_pending_capture_at == datetime.fromtimestamp(100)
    assert "autostart" not in result.processing_note.lower()


def test_status_marks_enabled_scheduler_inactive_without_running_task(tmp_path) -> None:
    result = status_module.build_status(
        _Settings(tmp_path, scheduler_enabled=True, scheduler_dry_run=True),
        scheduler_task_running=False,
    )

    assert result.service_running is True
    assert result.scheduler_enabled is True
    assert result.processing_mode == "dry-run"
    assert result.processing_active is False
    assert "no scheduler task is running" in result.processing_note
    assert "not active" in result.processing_note


def test_status_explains_disabled_scheduler_and_no_autostart(tmp_path, monkeypatch) -> None:
    clear_recent_runs_for_tests()
    monkeypatch.setattr(
        routes,
        "get_settings",
        lambda: _Settings(tmp_path, scheduler_enabled=False),
    )

    result = asyncio.run(routes.status(_Request()))

    assert result.processing_active is False
    assert result.processing_mode == "apply"
    assert result.last_successful_compile_at is None
    assert result.oldest_pending_capture_path is None
    assert "disabled" in result.processing_note.lower()
    assert "service/agent trigger" in result.processing_note


def test_status_backlog_includes_processed_capture_when_content_changes(tmp_path) -> None:
    capture = tmp_path / "capture" / "inbox" / "changed.md"
    capture.parent.mkdir(parents=True, exist_ok=True)
    capture.write_text("original", encoding="utf-8")
    mark_captures_processed(tmp_path, [capture])

    assert status_module._best_effort_status_backlog(tmp_path).by_source["inbox"] == 0

    capture.write_text("updated", encoding="utf-8")

    backlog = status_module._best_effort_status_backlog(tmp_path)

    assert backlog.by_source["inbox"] == 1
    assert backlog.oldest_path == capture


def test_status_backlog_skips_hashing_large_processed_captures(tmp_path) -> None:
    capture = tmp_path / "capture" / "inbox" / "large.md"
    capture.parent.mkdir(parents=True, exist_ok=True)
    capture.write_text("original", encoding="utf-8")
    mark_captures_processed(tmp_path, [capture])
    capture.write_text("updated-large-content", encoding="utf-8")

    backlog = status_module._best_effort_status_backlog(tmp_path, hash_byte_limit=1)

    assert backlog.by_source["inbox"] == 0
    assert any("hash skipped" in warning for warning in backlog.warnings)


def test_status_backlog_rechecks_size_after_opening_processed_capture(tmp_path, monkeypatch) -> None:
    capture = tmp_path / "capture" / "inbox" / "racing.md"
    capture.parent.mkdir(parents=True, exist_ok=True)
    capture.write_text("ok", encoding="utf-8")
    mark_captures_processed(tmp_path, [capture])

    real_open = status_module.os.open

    def grow_before_open(path, flags, mode=0o777, *, dir_fd=None):
        capture.write_text("x" * 20, encoding="utf-8")
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(status_module.os, "open", grow_before_open)

    backlog = status_module._best_effort_status_backlog(tmp_path, hash_byte_limit=10)

    assert backlog.by_source["inbox"] == 0
    assert any("hash skipped" in warning for warning in backlog.warnings)


def test_status_backlog_scan_is_best_effort_when_capture_scan_fails(tmp_path, monkeypatch) -> None:
    capture_dir = tmp_path / "capture" / "inbox"
    capture_dir.mkdir(parents=True)

    def fail_scandir(path):
        raise OSError("filesystem unavailable")

    monkeypatch.setattr(status_module.os, "scandir", fail_scandir)

    result = status_module.build_status(_Settings(tmp_path, scheduler_enabled=True))

    assert result.service_running is True
    assert result.pending_captures_by_source == {"inbox": 0, "daily": 0, "sessions": 0}
    assert result.oldest_pending_capture_path is None
    assert any("Unable to scan capture/inbox" in warning for warning in result.status_warnings)


def test_status_backlog_scan_is_bounded(tmp_path) -> None:
    capture_dir = tmp_path / "capture" / "inbox"
    capture_dir.mkdir(parents=True)
    for index in range(3):
        (capture_dir / f"capture-{index}.md").write_text("capture", encoding="utf-8")

    backlog = status_module._best_effort_status_backlog(tmp_path, scan_limit=2)

    assert backlog.by_source["inbox"] == 2
    assert any("capped at 2 files" in warning for warning in backlog.warnings)


def test_status_reports_baked_version(tmp_path, monkeypatch) -> None:
    version_file = tmp_path / "VERSION"
    version_file.write_text("1.3.58\n", encoding="utf-8")
    monkeypatch.setattr(status_module, "_SERVICE_VERSION_PATH", version_file)

    result = status_module.build_status(_Settings(tmp_path, scheduler_enabled=True))

    assert result.version == "1.3.58"


def test_status_version_none_when_unavailable(tmp_path, monkeypatch) -> None:
    missing = tmp_path / "does-not-exist" / "VERSION"
    monkeypatch.setattr(status_module, "_SERVICE_VERSION_PATH", missing)

    result = status_module.build_status(_Settings(tmp_path, scheduler_enabled=True))

    assert result.version is None


def test_status_version_none_on_non_utf8_file(tmp_path, monkeypatch) -> None:
    version_file = tmp_path / "VERSION"
    version_file.write_bytes(b"\xff\xfe\x00bad")
    monkeypatch.setattr(status_module, "_SERVICE_VERSION_PATH", version_file)

    result = status_module.build_status(_Settings(tmp_path, scheduler_enabled=True))

    assert result.version is None


def test_status_backlog_warns_when_scan_limit_reached_between_sources(tmp_path) -> None:
    inbox_dir = tmp_path / "capture" / "inbox"
    daily_dir = tmp_path / "capture" / "daily"
    inbox_dir.mkdir(parents=True)
    daily_dir.mkdir(parents=True)
    for index in range(2):
        (inbox_dir / f"capture-{index}.md").write_text("capture", encoding="utf-8")
    (daily_dir / "unscanned.md").write_text("capture", encoding="utf-8")

    backlog = status_module._best_effort_status_backlog(tmp_path, scan_limit=2)

    assert backlog.by_source == {"inbox": 2, "daily": 0, "sessions": 0}
    assert any("capped at 2 files" in warning for warning in backlog.warnings)
