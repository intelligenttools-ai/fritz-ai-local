"""Tests for hooks/brain_capture.py — the session/daily rollup writer.

All writes are confined to ``tmp_path`` via monkeypatched ``BRAIN_HOME`` /
``CAPTURE_DIR``, so the live ``~/.brain`` is never touched.
"""

import io
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import brain_capture  # noqa: E402
import brain_common  # noqa: E402
from adapters.base import CaptureEntry  # noqa: E402


def _run_capture(monkeypatch, tmp_path, transcript_path="/fake/transcript.jsonl"):
    capture_dir = tmp_path / "capture" / "daily"
    monkeypatch.setattr(brain_capture, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_capture, "CAPTURE_DIR", capture_dir)
    monkeypatch.setattr(
        brain_capture,
        "parse_transcript",
        lambda hook_input, path: CaptureEntry(
            topics=["did a thing"], agent="claude-code", cwd="/work/proj"
        ),
    )
    hook_input = {
        "hook_event_name": "Stop",
        "transcript_path": transcript_path,
        "cwd": "/work/proj",
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook_input)))

    with pytest.raises(SystemExit):
        brain_capture.main()

    return capture_dir / f"{brain_common.today_str()}.md"


def test_daily_capture_header_stamps_origin_session(monkeypatch, tmp_path):
    daily_file = _run_capture(monkeypatch, tmp_path)
    content = daily_file.read_text(encoding="utf-8")
    assert "origin: session\n" in content


def test_daily_capture_appended_entry_does_not_duplicate_header(monkeypatch, tmp_path):
    daily_file = _run_capture(monkeypatch, tmp_path)
    # A second Stop event on the same day appends to the existing file rather
    # than rewriting the header, so origin: session must still appear exactly
    # once.
    _run_capture(monkeypatch, tmp_path)
    content = daily_file.read_text(encoding="utf-8")
    assert content.count("origin: session\n") == 1
