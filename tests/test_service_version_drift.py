"""Tests for issue #172: session-start nudge when the running Local Brain
service container is behind the local repo VERSION.

This nudge is independent of the origin/main "update available" nudge: it
compares the RUNNING CONTAINER's reported version (from /v1/status) to the
local repo VERSION and points at /fritz:brain-service-setup. It honors the
update_check toggle, throttles to ~once/24h via a dedicated check file, and
must be safe (no crash, no false nudge) when the service is unreachable.

GUARDRAIL: every test monkeypatches brain_session_start.BRAIN_HOME onto
tmp_path so the live ~/.brain is never touched.
"""

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import brain_session_start  # noqa: E402


def _patch(
    monkeypatch,
    tmp_path: Path,
    *,
    running: str | None,
    repo: str | None,
    available: bool = True,
    update_check: bool = True,
):
    monkeypatch.setattr(brain_session_start, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_session_start, "local_brain_service_available", lambda: available)
    monkeypatch.setattr(brain_session_start, "get_local_brain_service_version", lambda: running)
    monkeypatch.setattr(brain_session_start, "get_fritz_version", lambda: repo)
    monkeypatch.setattr(brain_session_start, "get_setting", lambda key, default=None: update_check)


def _run(monkeypatch, tmp_path, **kw) -> str:
    _patch(monkeypatch, tmp_path, **kw)
    parts: list[str] = []
    brain_session_start.check_service_version_drift(parts)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Behavioral acceptance cases
# ---------------------------------------------------------------------------

def test_behind_emits_nudge(monkeypatch, tmp_path):
    out = _run(monkeypatch, tmp_path, running="1.3.55", repo="1.3.57")
    assert "Local Brain service is behind" in out
    assert "/fritz:brain-service-setup" in out


def test_equal_no_nudge(monkeypatch, tmp_path):
    out = _run(monkeypatch, tmp_path, running="1.3.57", repo="1.3.57")
    assert "behind" not in out


def test_running_newer_no_nudge(monkeypatch, tmp_path):
    out = _run(monkeypatch, tmp_path, running="1.3.58", repo="1.3.57")
    assert "behind" not in out


def test_service_unreachable_no_nudge_no_crash(monkeypatch, tmp_path):
    out = _run(monkeypatch, tmp_path, running=None, repo="1.3.57", available=False)
    assert out == ""


def test_version_unknown_no_nudge(monkeypatch, tmp_path):
    out = _run(monkeypatch, tmp_path, running=None, repo="1.3.57", available=True)
    assert out == ""
    # Throttle must NOT be recorded when a version is unknown — retry next session.
    assert not (tmp_path / ".service-version-check").exists()


def test_update_check_false_suppresses_nudge(monkeypatch, tmp_path):
    out = _run(monkeypatch, tmp_path, running="1.3.55", repo="1.3.57", update_check=False)
    assert out == ""


def test_throttle_recent_suppresses_nudge(monkeypatch, tmp_path):
    check_file = tmp_path / ".service-version-check"
    check_file.write_text(str(time.time()))
    out = _run(monkeypatch, tmp_path, running="1.3.55", repo="1.3.57")
    assert out == ""


def test_throttle_old_allows_nudge(monkeypatch, tmp_path):
    check_file = tmp_path / ".service-version-check"
    check_file.write_text(str(time.time() - 90000))  # > 24h ago
    out = _run(monkeypatch, tmp_path, running="1.3.55", repo="1.3.57")
    assert "Local Brain service is behind" in out


def test_throttle_recorded_after_nudge(monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, running="1.3.55", repo="1.3.57")
    assert (tmp_path / ".service-version-check").exists()


# ---------------------------------------------------------------------------
# Unit: _version_is_behind
# ---------------------------------------------------------------------------

def test_version_is_behind_numeric_not_lexical():
    assert brain_session_start._version_is_behind("1.3.5", "1.3.10") is True


def test_version_is_behind_v_prefix_tolerated():
    assert brain_session_start._version_is_behind("v1.3.0", "1.3.1") is True


def test_version_is_behind_equal_false():
    assert brain_session_start._version_is_behind("1.3.7", "1.3.7") is False


def test_version_is_behind_newer_false():
    assert brain_session_start._version_is_behind("1.3.8", "1.3.7") is False


# ---------------------------------------------------------------------------
# #217: the comparison now lives in brain_common (shared with the drift-watcher)
# and brain_session_start reuses it — no behavior change.
# ---------------------------------------------------------------------------

import brain_common  # noqa: E402


def test_helper_moved_to_brain_common():
    assert hasattr(brain_common, "version_is_behind")


def test_session_start_reuses_brain_common_helper():
    # brain_session_start._version_is_behind must BE the brain_common function,
    # not a local re-implementation.
    assert brain_session_start._version_is_behind is brain_common.version_is_behind


def test_brain_common_version_is_behind_cases():
    assert brain_common.version_is_behind("1.3.5", "1.3.10") is True
    assert brain_common.version_is_behind("v1.3.0", "1.3.1") is True
    assert brain_common.version_is_behind("1.3.7", "1.3.7") is False
    assert brain_common.version_is_behind("1.3.8", "1.3.7") is False
    # Malformed tokens degrade to 0 rather than crashing.
    assert brain_common.version_is_behind("1.x", "1.3") is True
    assert brain_common.version_is_behind("", "1.0.0") is True
