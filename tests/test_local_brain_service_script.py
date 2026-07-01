from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


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
    # build=True now rebuilds under the shared build lock — keep it off the real ~/.brain.
    monkeypatch.setattr(module, "BUILD_LOCK_PATH", repo / "build.lock")
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


# ---------------------------------------------------------------------------
# Regression test: CLI entry for the provision subcommand
#
# Before the fix in scripts/local-brain-service.py, running
#   python scripts/local-brain-service.py provision --help
# crashed with:
#   ModuleNotFoundError: No module named 'scripts'         (first import path)
#   AttributeError: 'NoneType'... in dataclasses           (spec fallback — module
#       not registered in sys.modules before exec_module)
#
# These tests invoke the REAL CLI entry as a subprocess so they catch regressions
# that unit tests (which import provision_engine directly) cannot detect.
# ---------------------------------------------------------------------------

_SCRIPT = str(Path(__file__).resolve().parents[1] / "scripts" / "local-brain-service.py")


def test_provision_help_exits_0_and_prints_expected_flags() -> None:
    """provision --help must exit 0 and list the LLM/embedding flags including --approval-token."""
    result = subprocess.run(
        [sys.executable, _SCRIPT, "provision", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"provision --help exited {result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    help_text = result.stdout + result.stderr
    for flag in ("--llm-protocol", "--llm-endpoint", "--llm-model", "--api-token", "--approval-token"):
        assert flag in help_text, f"Expected flag {flag!r} missing from provision --help output"


def test_status_help_still_exits_0() -> None:
    """The status subcommand must still load after the provision import fix."""
    result = subprocess.run(
        [sys.executable, _SCRIPT, "status", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"status --help exited {result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_start_help_still_exits_0() -> None:
    """The start subcommand must still load after the provision import fix."""
    result = subprocess.run(
        [sys.executable, _SCRIPT, "start", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"start --help exited {result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# AC3 — build_launch_agent_plist() and build_systemd_unit() builder tests
# ---------------------------------------------------------------------------


def test_build_launch_agent_plist_uses_detached_compose() -> None:
    """ProgramArguments must run `up -d --build` (detached one-shot)."""
    module = load_script()

    plist = module.build_launch_agent_plist()

    args_str = " ".join(plist["ProgramArguments"])
    assert "up -d --build" in args_str, "ProgramArguments must contain 'up -d --build'"
    assert " -d " in args_str or args_str.endswith(" -d") or "up -d" in args_str, (
        "ProgramArguments must include detached flag -d"
    )


def test_build_launch_agent_plist_no_exec_prefix() -> None:
    """ProgramArguments must NOT contain an `exec ` token.

    An `exec ` prefix would replace the shell process so launchd loses its pid
    tracking; the command is already the last thing in the shell snippet so
    `exec` is redundant and was intentionally dropped.
    """
    module = load_script()

    plist = module.build_launch_agent_plist()

    args_str = " ".join(plist["ProgramArguments"])
    assert "exec " not in args_str, "ProgramArguments must not contain 'exec ' prefix"


def test_build_launch_agent_plist_run_at_load_keep_alive_on_failure() -> None:
    """RunAtLoad must be True; KeepAlive must retry only on non-zero exit.

    KeepAlive={"SuccessfulExit": False} means launchd relaunches ONLY when the
    one-shot `up -d --build` exits non-zero (e.g. Docker/Colima not ready yet
    at login).  A successful `up -d` exits 0 and is NOT relaunched, so there is
    no steady-state keepalive loop; Docker's `restart: unless-stopped` then
    supervises the running container.
    """
    module = load_script()

    plist = module.build_launch_agent_plist()

    assert plist["RunAtLoad"] is True, "RunAtLoad must be True"
    assert plist["KeepAlive"] == {"SuccessfulExit": False}, (
        "KeepAlive must be {'SuccessfulExit': False} to retry only on failure"
    )


def test_build_systemd_unit_is_oneshot() -> None:
    """Unit text must declare Type=oneshot and RemainAfterExit=yes."""
    module = load_script()

    unit = module.build_systemd_unit()

    assert "Type=oneshot" in unit, "systemd unit must have Type=oneshot"
    assert "RemainAfterExit=yes" in unit, "systemd unit must have RemainAfterExit=yes"


def test_build_systemd_unit_exec_start_is_detached() -> None:
    """ExecStart must run `up -d --build`."""
    module = load_script()

    unit = module.build_systemd_unit()

    exec_start_lines = [l for l in unit.splitlines() if l.startswith("ExecStart=")]
    assert exec_start_lines, "unit must have an ExecStart line"
    assert "up -d --build" in exec_start_lines[0], (
        f"ExecStart must contain 'up -d --build', got: {exec_start_lines[0]!r}"
    )


def test_build_systemd_unit_has_exec_stop() -> None:
    """ExecStop must contain `down`."""
    module = load_script()

    unit = module.build_systemd_unit()

    exec_stop_lines = [l for l in unit.splitlines() if l.startswith("ExecStop=")]
    assert exec_stop_lines, "unit must have an ExecStop line"
    assert "down" in exec_stop_lines[0], (
        f"ExecStop must contain 'down', got: {exec_stop_lines[0]!r}"
    )


def test_build_systemd_unit_restart_on_failure() -> None:
    """Unit must contain Restart=on-failure and RestartSec=10, NOT Restart=always.

    With Type=oneshot + RemainAfterExit=yes, a successful start ends in
    active(exited) and won't be restarted.  A failed start (e.g. Docker/Colima
    not ready at boot) retries after RestartSec seconds.  Restart=always would
    create a steady-state loop; Docker's `restart: unless-stopped` supervises
    the running container instead.
    """
    module = load_script()

    unit = module.build_systemd_unit()

    assert "Restart=on-failure" in unit, "systemd unit must contain Restart=on-failure"
    assert "RestartSec=10" in unit, "systemd unit must contain RestartSec=10"
    assert "Restart=always" not in unit, "systemd unit must not contain Restart=always"


def test_build_systemd_unit_no_exec_prefix() -> None:
    """Unit text must NOT contain an `exec ` token in ExecStart/ExecStop lines."""
    module = load_script()

    unit = module.build_systemd_unit()

    assert "exec " not in unit, "systemd unit must not contain 'exec ' prefix"


# ---------------------------------------------------------------------------
# AC1 — compose restart policy (previously untested)
# ---------------------------------------------------------------------------


def test_compose_restart_unless_stopped() -> None:
    """docker-compose.example.yml must set restart: unless-stopped on local-brain.

    Docker's own restart policy supervises the running container; the autostart
    unit (launchd/systemd) only needs to fire `up -d` once per boot/login.
    """
    import yaml  # PyYAML — used elsewhere in the repo

    repo_root = Path(__file__).resolve().parents[1]
    compose_path = repo_root / "services" / "local-brain" / "docker-compose.example.yml"
    assert compose_path.exists(), f"compose file not found: {compose_path}"

    data = yaml.safe_load(compose_path.read_text())
    restart = data["services"]["local-brain"]["restart"]
    assert restart == "unless-stopped", (
        f"local-brain service must have restart: unless-stopped, got: {restart!r}"
    )


# ---------------------------------------------------------------------------
# #217 — drift-check subcommand (detect + optional rebuild)
# ---------------------------------------------------------------------------


def _drift_module(monkeypatch, tmp_path, *, running, repo, lock_held=False):
    """Load the script and stub out all IO for drift_check.

    Returns (module, calls) where calls records every `run(...)` invocation
    (git pull / compose up --build) so tests can assert on side effects without
    actually shelling out.
    """
    module = load_script()
    calls: list[list[str]] = []

    fake_bc = SimpleNamespace(
        get_fritz_version=lambda: repo,
        get_local_brain_service_version=lambda: running,
        version_is_behind=_real_version_is_behind,
    )
    monkeypatch.setattr(module, "_import_brain_common", lambda: fake_bc)
    monkeypatch.setattr(module, "run", lambda cmd, check=True: calls.append(cmd))
    monkeypatch.setattr(module, "ensure_runtime_env", lambda: None)

    lock = tmp_path / "build.lock"
    if lock_held:
        # A genuinely-held lock: current (alive) PID + a fresh timestamp so the
        # staleness check treats it as live and the caller skips.
        lock.write_text(f"{os.getpid()}\n{int(__import__('time').time())}\n")
    monkeypatch.setattr(module, "BUILD_LOCK_PATH", lock)
    return module, calls, lock


def _real_version_is_behind(running: str, repo: str) -> bool:
    # Mirror of brain_common.version_is_behind for the stubbed brain_common.
    def parse(value: str) -> list[int]:
        out = []
        for token in value.strip().lstrip("v").split("."):
            digits = "".join(ch for ch in token if ch.isdigit())
            out.append(int(digits) if digits else 0)
        return out
    a, b = parse(running), parse(repo)
    n = max(len(a), len(b))
    a += [0] * (n - len(a))
    b += [0] * (n - len(b))
    return a < b


def test_drift_check_behind_with_rebuild_pulls_and_rebuilds(monkeypatch, tmp_path):
    module, calls, lock = _drift_module(monkeypatch, tmp_path, running="1.3.55", repo="1.3.57")

    with pytest.raises(SystemExit) as exc:
        module.drift_check(SimpleNamespace(rebuild=True))
    assert exc.value.code == 0

    # git pull --ff-only, then compose up -d --build
    assert calls[0] == ["git", "-C", str(module.REPO_ROOT), "pull", "--ff-only"]
    up = calls[1]
    assert up[:2] == ["docker", "compose"]
    assert up[-3:] == ["up", "-d", "--build"]
    # Lock released in finally.
    assert not lock.exists()


def test_drift_check_behind_dry_exits_nonzero_no_rebuild(monkeypatch, tmp_path):
    module, calls, _ = _drift_module(monkeypatch, tmp_path, running="1.3.55", repo="1.3.57")

    with pytest.raises(SystemExit) as exc:
        module.drift_check(SimpleNamespace(rebuild=False))
    assert exc.value.code == 1
    assert calls == []  # no rebuild in dry mode


def test_drift_check_current_is_noop(monkeypatch, tmp_path):
    module, calls, _ = _drift_module(monkeypatch, tmp_path, running="1.3.57", repo="1.3.57")

    with pytest.raises(SystemExit) as exc:
        module.drift_check(SimpleNamespace(rebuild=True))
    assert exc.value.code == 0
    assert calls == []


def test_drift_check_service_down_is_clean_skip(monkeypatch, tmp_path):
    module, calls, _ = _drift_module(monkeypatch, tmp_path, running=None, repo="1.3.57")

    with pytest.raises(SystemExit) as exc:
        module.drift_check(SimpleNamespace(rebuild=True))
    assert exc.value.code == 0
    assert calls == []  # never rebuild when the service is unreachable


def test_drift_check_lock_held_skips(monkeypatch, tmp_path):
    module, calls, _ = _drift_module(
        monkeypatch, tmp_path, running="1.3.55", repo="1.3.57", lock_held=True
    )

    with pytest.raises(SystemExit) as exc:
        module.drift_check(SimpleNamespace(rebuild=True))
    assert exc.value.code == 0
    assert calls == []  # someone else holds the build lock


# ---------------------------------------------------------------------------
# #217 FIX 2 — stale-lock recovery (dead PID / old mtime) must NOT wedge
# ---------------------------------------------------------------------------


def test_drift_check_reclaims_lock_with_dead_pid(monkeypatch, tmp_path):
    """A lock left by a dead process is reclaimed; the rebuild proceeds."""
    module, calls, lock = _drift_module(monkeypatch, tmp_path, running="1.3.55", repo="1.3.57")
    # A PID that is not alive. Force _pid_alive False to be deterministic.
    lock.write_text("424242\n" + str(int(__import__("time").time())) + "\n")
    monkeypatch.setattr(module, "_pid_alive", lambda pid: False)

    with pytest.raises(SystemExit) as exc:
        module.drift_check(SimpleNamespace(rebuild=True))
    assert exc.value.code == 0
    # Rebuild ran: git pull + compose up --build.
    assert calls[0][:2] == ["git", "-C"]
    assert calls[1][-3:] == ["up", "-d", "--build"]
    assert not lock.exists()  # released in finally


def test_drift_check_reclaims_lock_past_ttl(monkeypatch, tmp_path):
    """A lock whose mtime is older than the TTL is reclaimed even if PID looks alive."""
    module, calls, lock = _drift_module(monkeypatch, tmp_path, running="1.3.55", repo="1.3.57")
    import time as _t

    # PID "alive" but the lock is ancient (past TTL) -> stale.
    lock.write_text(f"{os.getpid()}\n0\n")
    old = _t.time() - (module.BUILD_LOCK_TTL_SECONDS + 60)
    os.utime(lock, (old, old))
    monkeypatch.setattr(module, "_pid_alive", lambda pid: True)

    with pytest.raises(SystemExit) as exc:
        module.drift_check(SimpleNamespace(rebuild=True))
    assert exc.value.code == 0
    assert calls[1][-3:] == ["up", "-d", "--build"]
    assert not lock.exists()


def test_drift_check_respects_fresh_live_lock(monkeypatch, tmp_path):
    """A lock held by a live, recent process is respected — skip, no rebuild."""
    module, calls, lock = _drift_module(monkeypatch, tmp_path, running="1.3.55", repo="1.3.57")
    lock.write_text(f"{os.getpid()}\n{int(__import__('time').time())}\n")
    monkeypatch.setattr(module, "_pid_alive", lambda pid: True)

    with pytest.raises(SystemExit) as exc:
        module.drift_check(SimpleNamespace(rebuild=True))
    assert exc.value.code == 0
    assert calls == []  # no rebuild while a live build holds the lock
    assert lock.exists()  # untouched — we did not own it


# ---------------------------------------------------------------------------
# #217 FIX 3 — the build lock covers start --build / restart --build too, and a
# concurrent build attempt while the lock is held skips (no deadlock/hang).
# ---------------------------------------------------------------------------


def test_start_build_acquires_lock_and_builds(monkeypatch, tmp_path):
    module = load_script()
    calls: list[list[str]] = []
    monkeypatch.setattr(module, "run", lambda cmd, check=True: calls.append(cmd))
    monkeypatch.setattr(module, "ensure_runtime_env", lambda: None)
    monkeypatch.setattr(module, "BUILD_LOCK_PATH", tmp_path / "build.lock")

    module.start(SimpleNamespace(build=True))

    # compose up -d --build ran under the lock; lock released.
    assert calls and calls[-1][-3:] == ["up", "-d", "--build"]
    assert not (tmp_path / "build.lock").exists()


def test_start_build_skips_when_live_lock_held(monkeypatch, tmp_path):
    """start --build must SKIP (not hang, not build) when a live build holds the lock."""
    module = load_script()
    calls: list[list[str]] = []
    lock = tmp_path / "build.lock"
    lock.write_text(f"{os.getpid()}\n{int(__import__('time').time())}\n")
    monkeypatch.setattr(module, "run", lambda cmd, check=True: calls.append(cmd))
    monkeypatch.setattr(module, "ensure_runtime_env", lambda: None)
    monkeypatch.setattr(module, "BUILD_LOCK_PATH", lock)
    monkeypatch.setattr(module, "_pid_alive", lambda pid: True)

    module.start(SimpleNamespace(build=True))

    assert calls == []  # no compose build while another live build holds the lock
    assert lock.exists()  # not our lock — left intact


def test_locked_compose_build_returns_false_when_held(monkeypatch, tmp_path):
    module = load_script()
    calls: list[list[str]] = []
    lock = tmp_path / "build.lock"
    lock.write_text(f"{os.getpid()}\n{int(__import__('time').time())}\n")
    monkeypatch.setattr(module, "run", lambda cmd, check=True: calls.append(cmd))
    monkeypatch.setattr(module, "ensure_runtime_env", lambda: None)
    monkeypatch.setattr(module, "BUILD_LOCK_PATH", lock)
    monkeypatch.setattr(module, "_pid_alive", lambda pid: True)

    assert module._locked_compose_build(pull=False) is False
    assert calls == []


def test_start_no_build_does_not_touch_lock(monkeypatch, tmp_path):
    """A plain (non-build) start must not create/consult the build lock."""
    module = load_script()
    calls: list[list[str]] = []
    lock = tmp_path / "build.lock"
    monkeypatch.setattr(module, "run", lambda cmd, check=True: calls.append(cmd))
    monkeypatch.setattr(module, "ensure_runtime_env", lambda: None)
    monkeypatch.setattr(module, "BUILD_LOCK_PATH", lock)

    module.start(SimpleNamespace(build=False))

    assert calls and calls[-1][-2:] == ["up", "-d"]
    assert not lock.exists()


# ---------------------------------------------------------------------------
# #217 FIX 1 — programmatic drift-watcher install must NOT raise SystemExit on
# an unsupported OS (so provision's except-Exception can degrade to a warning).
# ---------------------------------------------------------------------------


def test_install_impl_raises_runtimeerror_not_systemexit_on_non_darwin(monkeypatch):
    module = load_script()
    monkeypatch.setattr(module.platform, "system", lambda: "Linux")

    with pytest.raises(module.DriftWatcherUnsupported):
        module.install_drift_watcher_impl()
    # It is a normal Exception, NOT SystemExit.
    assert issubclass(module.DriftWatcherUnsupported, Exception)
    assert not issubclass(module.DriftWatcherUnsupported, SystemExit)


def test_cli_install_wrapper_exits_nonzero_on_non_darwin(monkeypatch):
    module = load_script()
    monkeypatch.setattr(module.platform, "system", lambda: "Linux")

    with pytest.raises(SystemExit):
        module.install_drift_watcher(SimpleNamespace())


# ---------------------------------------------------------------------------
# #217 — drift-watcher plist builder + install/uninstall
# ---------------------------------------------------------------------------


def test_build_drift_watcher_plist_is_periodic_drift_check():
    module = load_script()
    plist = module.build_drift_watcher_plist()

    assert plist["Label"] == module.DRIFT_WATCHER_LABEL
    assert module.DRIFT_WATCHER_LABEL != module.LAUNCH_AGENT_LABEL
    assert plist["StartInterval"] == module.DRIFT_WATCHER_INTERVAL_SECONDS
    args = plist["ProgramArguments"]
    assert "drift-check" in args
    assert "--rebuild" in args


def test_install_drift_watcher_writes_plist_and_bootstraps(monkeypatch, tmp_path):
    module = load_script()
    plist_path = tmp_path / "LaunchAgents" / f"{module.DRIFT_WATCHER_LABEL}.plist"
    log_path = tmp_path / "logs" / "drift.log"
    commands: list[list[str]] = []

    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(module, "DRIFT_WATCHER_PATH", plist_path)
    monkeypatch.setattr(module, "DRIFT_WATCHER_LOG_PATH", log_path)
    monkeypatch.setattr(module, "run", lambda cmd, check=True: commands.append(cmd))
    monkeypatch.setattr(module.os, "getuid", lambda: 501, raising=False)

    module.install_drift_watcher(SimpleNamespace())

    assert plist_path.exists()
    # launchctl bootstrap + enable were invoked (mocked run).
    joined = [" ".join(c) for c in commands]
    assert any("bootstrap" in j for j in joined)
    assert any("enable" in j for j in joined)


def test_uninstall_drift_watcher_removes_plist(monkeypatch, tmp_path):
    module = load_script()
    plist_path = tmp_path / "LaunchAgents" / f"{module.DRIFT_WATCHER_LABEL}.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text("stub")
    commands: list[list[str]] = []

    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(module, "DRIFT_WATCHER_PATH", plist_path)
    monkeypatch.setattr(module, "run", lambda cmd, check=True: commands.append(cmd))
    monkeypatch.setattr(module.os, "getuid", lambda: 501, raising=False)

    module.uninstall_drift_watcher(SimpleNamespace())

    assert not plist_path.exists()
    assert any("bootout" in " ".join(c) for c in commands)


def test_drift_check_help_exits_0():
    result = subprocess.run(
        [sys.executable, _SCRIPT, "drift-check", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--rebuild" in (result.stdout + result.stderr)
