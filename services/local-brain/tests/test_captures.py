from __future__ import annotations

import pytest

from fritz_local_brain.captures import list_daily_captures, read_capture


def test_list_daily_captures_skips_symlinks(tmp_path) -> None:
    capture_dir = tmp_path / "capture" / "daily"
    capture_dir.mkdir(parents=True)
    safe_capture = capture_dir / "2026-05-13.md"
    safe_capture.write_text("safe", encoding="utf-8")
    outside = tmp_path / "secret.md"
    outside.write_text("secret", encoding="utf-8")
    (capture_dir / "2026-05-14.md").symlink_to(outside)

    assert list_daily_captures(tmp_path) == [safe_capture]


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
