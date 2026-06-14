"""Tests for hooks/brain_autocapture.py.

All writes are confined to ``tmp_path`` via the ``root=`` parameter or the
``BRAIN_HOME`` env override, so the live ``~/.brain`` is never touched.
"""

import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import brain_autocapture  # noqa: E402


SIGNAL_AND_INTENT = (
    "The forgejo server is at https://git.example.ai. "
    "Please remember this for future sessions."
)


def _inbox_files(root: Path) -> list[Path]:
    inbox = root / "capture" / "inbox"
    return sorted(inbox.glob("*.md")) if inbox.exists() else []


def _seen_files(root: Path) -> list[Path]:
    auto = root / "capture" / "auto"
    return sorted(auto.glob("*.seen")) if auto.exists() else []


# --- positive --------------------------------------------------------------


def test_signal_plus_intent_writes_one_capture_and_marker(tmp_path):
    result = brain_autocapture.maybe_auto_capture(
        SIGNAL_AND_INTENT, "/work/proj", root=tmp_path
    )
    assert result is not None
    assert result.exists()

    captures = _inbox_files(tmp_path)
    markers = _seen_files(tmp_path)
    assert len(captures) == 1
    assert len(markers) == 1
    assert captures[0] == result


def test_capture_filename_and_title(tmp_path):
    result = brain_autocapture.maybe_auto_capture(
        SIGNAL_AND_INTENT, "/work/proj", root=tmp_path
    )
    assert result.name.endswith("-auto-captured-durable-session-knowledge.md")
    content = result.read_text(encoding="utf-8")
    assert "# Auto-captured durable session knowledge" in content
    assert "cwd: `/work/proj`" in content
    assert "## Relevant transcript excerpt" in content
    assert '  - "pi-agent-end:auto-capture"' in content
    assert content.endswith("Tags: #FritzBrain #AutoCapture #PiAgent\n")


def test_marker_name_is_16_char_sha256(tmp_path):
    import hashlib

    text = SIGNAL_AND_INTENT
    brain_autocapture.maybe_auto_capture(text, "/c", root=tmp_path)
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    markers = _seen_files(tmp_path)
    assert markers[0].name == f"{expected}.seen"
    assert len(expected) == 16


def test_sensitive_flag_set_when_token_present(tmp_path):
    text = "The github pat token location is in ~/.netrc. Please save to brain."
    result = brain_autocapture.maybe_auto_capture(text, "/c", root=tmp_path)
    assert "sensitive: true\n" in result.read_text(encoding="utf-8")


def test_sensitive_flag_false_without_secret_words(tmp_path):
    # Signal via "server is", intent via "remember"; no token/secret words.
    text = "The server is reachable. Please remember the deploy steps."
    result = brain_autocapture.maybe_auto_capture(text, "/c", root=tmp_path)
    assert result is not None
    assert "sensitive: false\n" in result.read_text(encoding="utf-8")


# --- negative --------------------------------------------------------------


def test_no_signal_writes_nothing(tmp_path):
    text = "Please remember to buy milk and save the receipt for the brain."
    result = brain_autocapture.maybe_auto_capture(text, "/c", root=tmp_path)
    assert result is None
    assert _inbox_files(tmp_path) == []
    assert _seen_files(tmp_path) == []


def test_no_intent_writes_nothing(tmp_path):
    text = "The forgejo server is at https://git.example.ai and works fine."
    result = brain_autocapture.maybe_auto_capture(text, "/c", root=tmp_path)
    assert result is None
    assert _inbox_files(tmp_path) == []
    assert _seen_files(tmp_path) == []


def test_empty_text_writes_nothing(tmp_path):
    assert brain_autocapture.maybe_auto_capture("", "/c", root=tmp_path) is None
    assert _inbox_files(tmp_path) == []


# --- dedup -----------------------------------------------------------------


def test_rerun_same_text_is_noop(tmp_path):
    first = brain_autocapture.maybe_auto_capture(
        SIGNAL_AND_INTENT, "/c", root=tmp_path
    )
    assert first is not None
    second = brain_autocapture.maybe_auto_capture(
        SIGNAL_AND_INTENT, "/c", root=tmp_path
    )
    assert second is None
    assert len(_inbox_files(tmp_path)) == 1
    assert len(_seen_files(tmp_path)) == 1


def test_different_text_creates_second_capture(tmp_path):
    brain_autocapture.maybe_auto_capture(SIGNAL_AND_INTENT, "/c", root=tmp_path)
    other = (
        "Different gitlab credential recovery code. Save this for other sessions."
    )
    brain_autocapture.maybe_auto_capture(other, "/c", root=tmp_path)
    assert len(_seen_files(tmp_path)) == 2


# --- regex coverage --------------------------------------------------------


@pytest.mark.parametrize(
    "snippet",
    [
        "https://git.internal/repo",
        "the forgejo instance",
        "our gitea box",
        "gitlab pipeline",
        "github pat here",
        "api-token value",
        "api token value",
        "access token rotated",
        "the server is down",
        "token location noted",
        "credential vault",
        "recovery code list",
    ],
)
def test_durable_signal_patterns_match(tmp_path, snippet):
    text = f"{snippet}. Please remember this."
    assert brain_autocapture.maybe_auto_capture(text, "/c", root=tmp_path) is not None


@pytest.mark.parametrize(
    "snippet",
    [
        "remember this",
        "save this",
        "ingest it",
        "store in the brain",
        "for a future session",
        "for other sessions",
        "so that you know later",
    ],
)
def test_save_intent_patterns_match(tmp_path, snippet):
    text = f"The server is at https://git.x. {snippet}."
    assert brain_autocapture.maybe_auto_capture(text, "/c", root=tmp_path) is not None


# --- CLI -------------------------------------------------------------------


def test_cli_stdin_positive(tmp_path):
    result = subprocess.run(
        [sys.executable, str(HOOKS / "brain_autocapture.py"), "--cwd", "/work"],
        input=SIGNAL_AND_INTENT,
        capture_output=True,
        text=True,
        env={"BRAIN_HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert "Auto-captured to Fritz-Brain:" in result.stdout
    assert len(_inbox_files(tmp_path)) == 1


def test_cli_stdin_negative(tmp_path):
    result = subprocess.run(
        [sys.executable, str(HOOKS / "brain_autocapture.py")],
        input="nothing durable here",
        capture_output=True,
        text=True,
        env={"BRAIN_HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert "No auto-capture" in result.stdout
    assert _inbox_files(tmp_path) == []
