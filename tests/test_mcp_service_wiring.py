"""Tests for the session-start Claude MCP registration self-healing warning (#278).

The session-start hook no longer checks whether ``LOCAL_BRAIN_API_TOKEN`` is in
the process environment. Claude MCP auth is user-scope registration owned by the
installer, so the hook compares ``~/.claude.json`` against the registry token.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import brain_session_start  # noqa: E402
import brain_common  # noqa: E402


def _write_registry(brain: Path, token: str = "registry-token") -> None:
    brain.mkdir(parents=True, exist_ok=True)
    (brain / "registry.yaml").write_text(
        yaml.safe_dump(
            {
                "settings": {
                    "local_brain_service": {
                        "enabled": True,
                        "base_url": "http://127.0.0.1:8765",
                        "api_token": token,
                        "api_token_env": "LOCAL_BRAIN_API_TOKEN",
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def _write_claude_config(home: Path, token: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fritz-brain": {
                        "type": "http",
                        "url": "http://127.0.0.1:8765/mcp/",
                        "headers": {"Authorization": f"Bearer {token}"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def _setup(monkeypatch, tmp_path: Path, *, registry_token: str = "registry-token") -> Path:
    home = tmp_path / "home"
    brain = home / ".brain"
    _write_registry(brain, registry_token)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BRAIN_HOME", str(brain))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(ROOT / "bindings" / "claude"))
    monkeypatch.delenv("CLAUDE_CONFIG_PATH", raising=False)
    monkeypatch.delenv("FRITZ_AGENT", raising=False)
    monkeypatch.setattr(brain_common, "BRAIN_HOME", brain)
    monkeypatch.setattr(brain_common, "REGISTRY_PATH", brain / "registry.yaml")
    monkeypatch.setattr(brain_session_start, "BRAIN_HOME", brain)
    return home


def _run() -> str:
    parts: list[str] = []
    brain_session_start.check_mcp_token_wiring(parts)
    return "\n".join(parts)


def test_missing_claude_user_registration_emits_update_warning(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    out = _run()

    assert "Brain MCP registration drift" in out
    assert "registration is missing" in out
    assert "/fritz-brain:update" in out
    assert "use the HTTP API immediately" in out
    assert "LOCAL_BRAIN_API_TOKEN" not in out


def test_token_mismatch_emits_update_warning(monkeypatch, tmp_path):
    home = _setup(monkeypatch, tmp_path)
    _write_claude_config(home, "old-token")

    out = _run()

    assert "Brain MCP registration drift" in out
    assert "registration is token mismatch" in out
    assert "/fritz-brain:update" in out


def test_missing_registration_is_repaired_when_service_is_usable(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(brain_session_start, "repair_claude_mcp_registration", lambda: True)
    parts: list[str] = []

    brain_session_start.check_mcp_token_wiring(parts, repair=True)
    out = "\n".join(parts)

    assert "Brain MCP registration repaired" in out
    assert "use the HTTP API immediately" in out
    assert "restart" in out
    assert "/fritz-brain:update" not in out


def test_matching_registration_no_warning_even_without_env(monkeypatch, tmp_path):
    home = _setup(monkeypatch, tmp_path)
    _write_claude_config(home, "registry-token")
    monkeypatch.delenv("LOCAL_BRAIN_API_TOKEN", raising=False)

    assert _run() == ""


def test_non_claude_agent_does_not_check_claude_registration(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.setenv("FRITZ_AGENT", "codex")

    assert _run() == ""


def test_malformed_claude_config_counts_as_missing(monkeypatch, tmp_path):
    home = _setup(monkeypatch, tmp_path)
    (home / ".claude.json").write_text("{not-json", encoding="utf-8")

    out = _run()

    assert "registration is missing" in out
