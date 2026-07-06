"""Tests for the session-start MCP-token self-healing warning (#259, part C).

When the Local Brain service is reachable but the token env var the plugin's
MCP header expands (``LOCAL_BRAIN_API_TOKEN``) is missing from THIS session's
environment, every brain MCP call fails auth silently. The session-start hook
must surface a one-line fix pointing at the update skill.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import brain_session_start  # noqa: E402


def _patch(monkeypatch, *, available: bool, token_env_set: bool):
    monkeypatch.setattr(
        brain_session_start,
        "local_brain_service_reachable_for_mcp_token_warning",
        lambda: available,
    )
    if token_env_set:
        monkeypatch.setenv("LOCAL_BRAIN_API_TOKEN", "some-token")
    else:
        monkeypatch.delenv("LOCAL_BRAIN_API_TOKEN", raising=False)


def _run(monkeypatch, **kw) -> str:
    _patch(monkeypatch, **kw)
    parts: list[str] = []
    brain_session_start.check_mcp_token_wiring(parts)
    return "\n".join(parts)


def _clear_claude_markers(monkeypatch):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("FRITZ_AGENT", raising=False)


def test_reachable_and_token_missing_emits_warning(monkeypatch):
    _clear_claude_markers(monkeypatch)
    out = _run(monkeypatch, available=True, token_env_set=False)
    assert "Brain MCP token not exported" in out
    assert "LOCAL_BRAIN_API_TOKEN" in out
    assert "/fritz:update" in out  # non-Claude rendering names the fix
    assert "quit and restart the agent application" in out
    assert "Starting a new session in the same app can inherit the old environment" in out
    assert "restart the session" not in out


def test_reachable_and_token_missing_claude_uses_plugin_skill_name(monkeypatch):
    _clear_claude_markers(monkeypatch)
    monkeypatch.setenv("CLAUDECODE", "1")
    out = _run(monkeypatch, available=True, token_env_set=False)
    assert "Brain MCP token not exported" in out
    assert "/fritz-brain:update" in out
    assert "quit and restart the agent application" in out
    assert "Starting a new session in the same app can inherit the old environment" in out
    assert "restart the session" not in out


def test_reachable_and_token_present_no_warning(monkeypatch):
    out = _run(monkeypatch, available=True, token_env_set=True)
    assert out == ""


def test_custom_api_token_env_does_not_suppress_claude_mcp_warning(monkeypatch):
    monkeypatch.setattr(
        brain_session_start,
        "local_brain_service_reachable_for_mcp_token_warning",
        lambda: True,
    )
    monkeypatch.setenv("BRAIN_TOKEN", "configured-custom-env")
    monkeypatch.delenv("LOCAL_BRAIN_API_TOKEN", raising=False)

    parts: list[str] = []
    brain_session_start.check_mcp_token_wiring(parts)
    out = "\n".join(parts)

    assert "Brain MCP token not exported" in out
    assert "LOCAL_BRAIN_API_TOKEN" in out


def test_unreachable_no_warning(monkeypatch):
    out = _run(monkeypatch, available=False, token_env_set=False)
    assert out == ""


def test_status_401_still_counts_as_reachable_for_token_warning(monkeypatch):
    calls: list[str] = []

    def fake_urlopen(req, timeout):
        calls.append(req.full_url)
        if req.full_url.endswith("/health"):
            raise brain_session_start.error.URLError("connection reset")
        raise brain_session_start.error.HTTPError(
            req.full_url,
            401,
            "Unauthorized",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(brain_session_start, "get_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_session_start.request, "urlopen", fake_urlopen)

    assert brain_session_start.local_brain_service_reachable_for_mcp_token_warning() is True
    assert calls == ["http://127.0.0.1:8765/health", "http://127.0.0.1:8765/v1/status"]
