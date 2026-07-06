"""Tests for migration 006 Claude user-scope MCP registration (#278)."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations" / "006-claude-user-scope-mcp-registration.py"
HELPER_PATH = ROOT / "migrations" / "claude_mcp_registration.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


migration = _load(MIGRATION_PATH, "migration_006")
helper = _load(HELPER_PATH, "claude_mcp_registration_under_test")


def _write_registry(tmp_path: Path, svc: dict) -> Path:
    reg = tmp_path / "home" / ".brain" / "registry.yaml"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(yaml.safe_dump({"settings": {"local_brain_service": svc}}), encoding="utf-8")
    return reg


def _write_config(home: Path, token: str, *, url: str = "http://127.0.0.1:8765/mcp/") -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fritz-brain": {
                        "type": "http",
                        "url": url,
                        "headers": {"Authorization": f"Bearer {token}"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def _runner_writing_config(home: Path, calls: list[list[str]]):
    def runner(cmd: list[str], env: dict[str, str]):
        calls.append(list(cmd))
        assert env["HOME"] == str(home)
        home.mkdir(parents=True, exist_ok=True)
        config_path = home / ".claude.json"
        if cmd[:3] == ["claude", "mcp", "remove"]:
            data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
            servers = data.get("mcpServers")
            if isinstance(servers, dict):
                servers.pop(cmd[3], None)
            config_path.write_text(json.dumps(data), encoding="utf-8")
            return

        assert cmd[:4] == ["claude", "mcp", "add-json", "fritz-brain"]
        data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        servers = data.setdefault("mcpServers", {})
        if "fritz-brain" in servers:
            raise subprocess.CalledProcessError(1, cmd, stderr="MCP server fritz-brain already exists in user config")
        payload = json.loads(cmd[4])
        servers["fritz-brain"] = payload
        (home / ".claude.json").write_text(
            json.dumps(data),
            encoding="utf-8",
        )

    return runner


def test_desired_server_uses_literal_bearer_token_not_env_placeholder():
    server = helper.desired_server("tok-abc", "http://127.0.0.1:8765")
    assert server == {
        "type": "http",
        "url": "http://127.0.0.1:8765/mcp/",
        "headers": {"Authorization": "Bearer tok-abc"},
    }
    assert "${" not in json.dumps(server)


def test_register_missing_user_scope_server_calls_claude_add_json(tmp_path):
    home = tmp_path / "home"
    calls: list[list[str]] = []

    actions = helper.register_user_scope_server(
        "tok-new",
        base_url="http://127.0.0.1:8765",
        home=home,
        runner=_runner_writing_config(home, calls),
    )

    assert calls and calls[0][:4] == ["claude", "mcp", "add-json", "fritz-brain"]
    assert calls[0][-2:] == ["--scope", "user"]
    payload = json.loads(calls[0][4])
    assert payload["headers"]["Authorization"] == "Bearer tok-new"
    assert "token redacted" in actions[0]


def test_register_current_user_scope_server_is_noop(tmp_path):
    home = tmp_path / "home"
    _write_config(home, "tok-current")
    calls: list[list[str]] = []

    actions = helper.register_user_scope_server(
        "tok-current",
        home=home,
        runner=_runner_writing_config(home, calls),
    )

    assert calls == []
    assert actions == ["Claude user-scope MCP server fritz-brain already current"]


def test_register_token_rotation_refreshes_existing_server(tmp_path):
    home = tmp_path / "home"
    _write_config(home, "old-token")
    calls: list[list[str]] = []

    helper.register_user_scope_server(
        "new-token",
        home=home,
        runner=_runner_writing_config(home, calls),
    )

    assert [c[:4] for c in calls] == [
        ["claude", "mcp", "remove", "fritz-brain"],
        ["claude", "mcp", "add-json", "fritz-brain"],
    ]
    server = helper.read_user_mcp_server(home=home)
    assert helper.bearer_token(server) == "new-token"


def test_real_claude_cli_rewrites_existing_server_with_temp_home(tmp_path):
    if not shutil.which("claude"):
        pytest.skip("claude CLI not installed")
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(home)
    subprocess.run(
        [
            "claude",
            "mcp",
            "add-json",
            "fritz-brain",
            '{"type":"http","url":"http://127.0.0.1:8765/mcp/","headers":{"Authorization":"Bearer old-token"}}',
            "--scope",
            "user",
        ],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    helper.register_user_scope_server("new-token", home=home)

    server = helper.read_user_mcp_server(home=home)
    assert helper.bearer_token(server) == "new-token"


def test_register_failure_redacts_token_from_exception(tmp_path):
    home = tmp_path / "home"
    secret = "secret-token-123"

    def failing_runner(cmd: list[str], env: dict[str, str]):
        raise subprocess.CalledProcessError(
            1,
            cmd,
            stderr=f"failed command: {cmd} Authorization: Bearer other-token-456",
        )

    with pytest.raises(helper.ClaudeMcpRegistrationError) as exc:
        helper.register_user_scope_server(secret, home=home, runner=failing_runner)

    message = str(exc.value)
    assert secret not in message
    assert "other-token-456" not in message
    assert "Bearer <redacted>" in message
    assert "add-json" in message


def test_migration_run_registers_and_records_006(tmp_path):
    home = tmp_path / "home"
    root = home / ".brain"
    root.mkdir(parents=True)
    reg = _write_registry(
        tmp_path,
        {"enabled": True, "api_token": "reg-token", "base_url": "http://127.0.0.1:8765"},
    )
    calls: list[list[str]] = []

    actions = migration.run(
        root,
        home=home,
        registry_path=reg,
        runner=_runner_writing_config(home, calls),
    )

    assert calls
    assert (root / ".migrations-run").read_text(encoding="utf-8").splitlines() == ["006"]
    assert any("recorded migration 006" in a for a in actions)


def test_migration_skips_without_token_and_does_not_record(tmp_path):
    home = tmp_path / "home"
    root = home / ".brain"
    root.mkdir(parents=True)
    reg = _write_registry(tmp_path, {"enabled": False})

    actions = migration.run(root, home=home, registry_path=reg, runner=lambda _cmd, _env: None)

    assert any("skipped" in a for a in actions)
    assert not (root / ".migrations-run").exists()


def test_migration_registration_failure_is_sanitized_and_not_recorded(tmp_path):
    home = tmp_path / "home"
    root = home / ".brain"
    root.mkdir(parents=True)
    secret = "reg-secret-token"
    reg = _write_registry(
        tmp_path,
        {"enabled": True, "api_token": secret, "base_url": "http://127.0.0.1:8765"},
    )

    def failing_runner(cmd: list[str], env: dict[str, str]):
        raise subprocess.CalledProcessError(
            1,
            cmd,
            stderr=f"failed command: {cmd} Authorization: Bearer {secret}",
        )

    actions = migration.run(root, home=home, registry_path=reg, runner=failing_runner)
    joined = "\n".join(actions)

    assert "Claude MCP registration failed" in joined
    assert secret not in joined
    assert "Bearer <redacted>" in joined
    assert not (root / ".migrations-run").exists()


def test_refresh_ignores_existing_marker_and_updates_token(tmp_path):
    home = tmp_path / "home"
    root = home / ".brain"
    root.mkdir(parents=True)
    (root / ".migrations-run").write_text("006\n", encoding="utf-8")
    reg = _write_registry(
        tmp_path,
        {"enabled": True, "api_token": "rotated-token", "base_url": "http://127.0.0.1:8765"},
    )
    _write_config(home, "old-token")
    calls: list[list[str]] = []

    migration.run(
        root,
        home=home,
        registry_path=reg,
        refresh=True,
        runner=_runner_writing_config(home, calls),
    )

    assert [c[:4] for c in calls] == [
        ["claude", "mcp", "remove", "fritz-brain"],
        ["claude", "mcp", "add-json", "fritz-brain"],
    ]
    assert helper.bearer_token(helper.read_user_mcp_server(home=home)) == "rotated-token"
