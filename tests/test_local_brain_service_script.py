from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "local-brain-service.py"
    spec = importlib.util.spec_from_file_location("local_brain_service_script", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_task_command_runs_generated_wrapper() -> None:
    module = load_script()

    command = module.windows_task_command()

    assert command.startswith("cmd.exe /d /s /c ")
    assert "local-brain-service-autostart.cmd" in command


def test_write_windows_task_wrapper_quotes_paths_with_spaces(monkeypatch, tmp_path) -> None:
    module = load_script()
    repo = tmp_path / "Alice Smith" / "repo & stuff"
    wrapper = tmp_path / "brain dir" / "local-brain-service-autostart.cmd"
    monkeypatch.setattr(module, "REPO_ROOT", repo)
    monkeypatch.setattr(module, "WINDOWS_TASK_WRAPPER_PATH", wrapper)

    module.write_windows_task_wrapper()

    text = wrapper.read_text(encoding="utf-8")
    assert f'cd /d "{repo}"' in text
    assert f'python "{repo / "scripts" / "local-brain-service.py"}" start --build' in text


def test_windows_autostart_create_and_delete_use_schtasks(monkeypatch) -> None:
    module = load_script()
    commands: list[list[str]] = []
    env_values: list[tuple[str, str]] = []

    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(module, "ensure_env", lambda: None)
    monkeypatch.setattr(module, "restart_service", lambda build=False: None)
    monkeypatch.setattr(module, "set_env_value", lambda key, value: env_values.append((key, value)))
    monkeypatch.setattr(module, "run", lambda cmd, check=True: commands.append(cmd))

    module.install_autostart(SimpleNamespace(enable_scheduler=True, apply=True))
    module.uninstall_autostart(SimpleNamespace())

    assert ["SCHEDULER_ENABLED", "true"] in [list(item) for item in env_values]
    assert ["SCHEDULER_DRY_RUN", "false"] in [list(item) for item in env_values]
    assert ["AUTOSTART_INSTALLED", "true"] in [list(item) for item in env_values]
    assert ["AUTOSTART_INSTALLED", "false"] in [list(item) for item in env_values]
    create = commands[0]
    assert create[:4] == ["schtasks", "/Create", "/TN", module.WINDOWS_TASK_NAME]
    assert "/SC" in create and "ONLOGON" in create
    assert "/TR" in create and "local-brain-service-autostart.cmd" in create[create.index("/TR") + 1]
    assert commands[1] == ["schtasks", "/Delete", "/TN", module.WINDOWS_TASK_NAME, "/F"]


def test_dotenv_line_rejects_injection_and_quotes_special_values() -> None:
    module = load_script()

    assert module.dotenv_line("EMBEDDING_ENABLED", "true") == "EMBEDDING_ENABLED=true"
    assert module.dotenv_line("EMBEDDING_MODEL", "model with spaces") == 'EMBEDDING_MODEL="model with spaces"'
    for bad in ["value\nAPI_TOKEN=bad", "value\rAPI_TOKEN=bad", "value\0bad"]:
        try:
            module.dotenv_line("EMBEDDING_MODEL", bad)
        except ValueError:
            pass
        else:  # pragma: no cover - explicit assertion keeps this Python-version neutral
            raise AssertionError("expected unsafe dotenv value to be rejected")
    try:
        module.dotenv_line("bad-key", "true")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected unsafe dotenv key to be rejected")


def test_start_replaces_placeholder_api_token_before_compose_up(monkeypatch, tmp_path) -> None:
    module = load_script()
    env_path = tmp_path / ".env"
    env_path.write_text("API_TOKEN=replace-with-random-token\n", encoding="utf-8")
    commands: list[list[str]] = []

    monkeypatch.setattr(module, "ENV_PATH", env_path)
    monkeypatch.setattr(module, "COMPOSE_FILE", tmp_path / "compose.yml")
    monkeypatch.setattr(module, "run", lambda cmd, check=True: commands.append(cmd))

    module.start(SimpleNamespace(build=False))

    token_line = next(line for line in env_path.read_text(encoding="utf-8").splitlines() if line.startswith("API_TOKEN="))
    token = token_line.split("=", 1)[1]
    assert token != "replace-with-random-token"
    assert len(token) >= 32
    assert commands and commands[0][-2:] == ["up", "-d"]


def test_start_creates_env_with_random_api_token(monkeypatch, tmp_path) -> None:
    module = load_script()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env.example").write_text("API_TOKEN=replace-with-random-token\nAPI_PORT=8765\n", encoding="utf-8")
    env_path = repo / ".env"
    commands: list[list[str]] = []

    monkeypatch.setattr(module, "REPO_ROOT", repo)
    monkeypatch.setattr(module, "ENV_PATH", env_path)
    monkeypatch.setattr(module, "COMPOSE_FILE", repo / "compose.yml")
    monkeypatch.setattr(module, "run", lambda cmd, check=True: commands.append(cmd))

    module.start(SimpleNamespace(build=True))

    text = env_path.read_text(encoding="utf-8")
    assert "API_TOKEN=replace-with-random-token" not in text
    assert "API_PORT=8765" in text
    assert commands and commands[0][-3:] == ["up", "-d", "--build"]


def test_install_autostart_replaces_placeholder_before_install(monkeypatch, tmp_path) -> None:
    module = load_script()
    env_path = tmp_path / ".env"
    env_path.write_text("API_TOKEN=\n", encoding="utf-8")
    env_values: list[tuple[str, str]] = []

    monkeypatch.setattr(module, "ENV_PATH", env_path)
    monkeypatch.setattr(module, "_install_autostart_unit", lambda system: None)
    monkeypatch.setattr(module, "restart_service", lambda build=False: None)
    monkeypatch.setattr(module, "set_env_value", lambda key, value: env_values.append((key, value)))

    module.install_autostart(SimpleNamespace(enable_scheduler=False, apply=False))

    token_line = next(line for line in env_path.read_text(encoding="utf-8").splitlines() if line.startswith("API_TOKEN="))
    assert token_line != "API_TOKEN="
    assert ("AUTOSTART_INSTALLED", "true") in env_values


def test_stop_stops_active_windows_task_before_compose_down(monkeypatch) -> None:
    module = load_script()
    commands: list[list[str]] = []

    monkeypatch.setattr(module, "ensure_env", lambda: None)
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(module, "autostart_active", lambda: True)
    monkeypatch.setattr(module, "run", lambda cmd, check=True: commands.append(cmd))

    module.stop(SimpleNamespace())

    assert commands[0] == ["schtasks", "/End", "/TN", module.WINDOWS_TASK_NAME]
    assert commands[1][:2] == ["docker", "compose"]
    assert commands[1][-1] == "down"


def test_windows_secure_env_permissions_uses_icacls(monkeypatch, tmp_path) -> None:
    module = load_script()
    env_path = tmp_path / ".env"
    env_path.write_text("API_TOKEN=test\n", encoding="utf-8")
    commands: list[list[str]] = []

    monkeypatch.setattr(module, "ENV_PATH", env_path)
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    monkeypatch.setenv("USERDOMAIN", "HOST")
    monkeypatch.setenv("USERNAME", "alice")
    monkeypatch.setattr(module.subprocess, "run", lambda cmd, check=False, **kwargs: commands.append(cmd))

    module.secure_env_permissions()

    assert commands[0] == ["icacls", str(env_path), "/inheritance:r"]
    assert commands[1][:4] == ["icacls", str(env_path), "/remove:g", "Everyone"]
    assert "Users" in commands[1]
    assert "Authenticated Users" in commands[1]
    assert commands[2] == [
        "icacls",
        str(env_path),
        "/grant:r",
        "HOST\\alice:F",
        "SYSTEM:F",
        "Administrators:F",
    ]
