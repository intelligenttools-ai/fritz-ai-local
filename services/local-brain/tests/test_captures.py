from __future__ import annotations

import os

import pytest

from fritz_local_brain.captures import list_all_captures, list_daily_captures, mark_captures_processed, read_capture


def test_list_daily_captures_skips_symlinks(tmp_path) -> None:
    capture_dir = tmp_path / "capture" / "daily"
    capture_dir.mkdir(parents=True)
    safe_capture = capture_dir / "2026-05-13.md"
    safe_capture.write_text("safe", encoding="utf-8")
    outside = tmp_path / "secret.md"
    outside.write_text("secret", encoding="utf-8")
    (capture_dir / "2026-05-14.md").symlink_to(outside)

    assert list_daily_captures(tmp_path) == [safe_capture]


def test_list_all_captures_includes_inbox_daily_and_sessions(tmp_path) -> None:
    inbox = tmp_path / "capture" / "inbox" / "fact.md"
    daily = tmp_path / "capture" / "daily" / "2026-05-13.md"
    session = tmp_path / "capture" / "sessions" / "session.md"
    for capture in (inbox, daily, session):
        capture.parent.mkdir(parents=True, exist_ok=True)
        capture.write_text(capture.name, encoding="utf-8")

    result = list_all_captures(tmp_path)

    assert result.by_source == {"inbox": 1, "daily": 1, "sessions": 1}
    assert set(result.paths) == {inbox, daily, session}


def test_list_all_captures_orders_oldest_first_and_default_includes_all(tmp_path) -> None:
    older = tmp_path / "capture" / "daily" / "older.md"
    newer = tmp_path / "capture" / "inbox" / "newer.md"
    for capture in (newer, older):
        capture.parent.mkdir(parents=True, exist_ok=True)
        capture.write_text(capture.name, encoding="utf-8")
    os.utime(older, (100, 100))
    os.utime(newer, (200, 200))

    result = list_all_captures(tmp_path)

    assert result.paths == [older, newer]
    assert result.by_source == {"inbox": 1, "daily": 1, "sessions": 0}


def test_list_all_captures_skips_symlinked_source_directories(tmp_path) -> None:
    capture_parent = tmp_path / "capture"
    capture_parent.mkdir()
    sessions = capture_parent / "sessions"
    sessions.mkdir()
    safe_session = sessions / "safe.md"
    safe_session.write_text("safe session", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    (capture_parent / "inbox").symlink_to(outside, target_is_directory=True)
    (capture_parent / "daily").symlink_to(outside, target_is_directory=True)

    result = list_all_captures(tmp_path)

    assert result.by_source == {"inbox": 0, "daily": 0, "sessions": 1}
    assert result.paths == [safe_session]


def test_list_all_captures_skips_symlinked_files_in_all_sources(tmp_path) -> None:
    capture_parent = tmp_path / "capture"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    safe_paths = []
    for source in ("inbox", "daily", "sessions"):
        source_dir = capture_parent / source
        source_dir.mkdir(parents=True, exist_ok=True)
        safe_capture = source_dir / f"safe-{source}.md"
        safe_capture.write_text(f"safe {source}", encoding="utf-8")
        safe_paths.append(safe_capture)
        (source_dir / "linked.md").symlink_to(outside / "secret.md")

    result = list_all_captures(tmp_path)

    assert result.by_source == {"inbox": 1, "daily": 1, "sessions": 1}
    assert set(result.paths) == set(safe_paths)


def test_list_all_captures_rejects_symlinked_capture_directory(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    (tmp_path / "capture").symlink_to(outside, target_is_directory=True)

    result = list_all_captures(tmp_path)

    assert result.paths == []
    assert result.by_source == {"inbox": 0, "daily": 0, "sessions": 0}


def test_list_all_captures_skips_processed_captures_until_content_changes(tmp_path) -> None:
    capture = tmp_path / "capture" / "inbox" / "fact.md"
    capture.parent.mkdir(parents=True)
    capture.write_text("old", encoding="utf-8")

    mark_captures_processed(tmp_path, [capture])

    assert list_all_captures(tmp_path).paths == []

    capture.write_text("new", encoding="utf-8")

    assert list_all_captures(tmp_path).paths == [capture]


def test_read_capture_rejects_symlink(tmp_path) -> None:
    outside = tmp_path / "secret.md"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "capture.md"
    link.symlink_to(outside)

    with pytest.raises(ValueError, match="Unsafe capture path"):
        read_capture(link)


def test_list_daily_captures_rejects_symlinked_capture_directory(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    capture_parent = tmp_path / "capture"
    capture_parent.mkdir()
    (capture_parent / "daily").symlink_to(outside, target_is_directory=True)

    assert list_daily_captures(tmp_path) == []
