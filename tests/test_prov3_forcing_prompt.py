"""Tests for PROV3: desired-vs-operational brain-service-setup forcing prompt.

Acceptance states:
1. desired==docker + service DOWN  → session-start output CONTAINS the forcing instruction.
2. enabled:false + desired==docker + DOWN → STILL forces (suppression-bug regression).
3. Healthy/operational service (or desired!=docker) → NO forcing instruction injected.

GUARDRAIL: every test monkeypatches brain_common.BRAIN_HOME and REGISTRY_PATH onto
tmp_path.  The live ~/.brain is never touched.
"""

import io
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import brain_common  # noqa: E402
import brain_session_start  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_registry(tmp_path: Path, settings: dict) -> Path:
    """Write a minimal registry.yaml with the given settings block."""
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        yaml.safe_dump({"version": 1, "vaults": {}, "settings": settings}),
        encoding="utf-8",
    )
    return registry_path


def _run_session_start(
    monkeypatch,
    capsys,
    tmp_path: Path,
    settings: dict,
    *,
    operational: bool,
    cwd: str | None = None,
) -> str:
    """Run brain_session_start.main() with a controlled environment.

    - Writes a temp registry with the provided settings.
    - Monkeypatches BRAIN_HOME, REGISTRY_PATH, load_registry, and
      local_brain_service_operational so the live ~/.brain is never touched.
    - ``cwd`` overrides the hook-input cwd (defaults to ``str(ROOT)``).
    - Returns the additionalContext string from the hook output.
    """
    registry_path = _write_registry(tmp_path, settings)

    # Point both brain_common and brain_session_start at the temp home.
    monkeypatch.setattr(brain_common, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_common, "REGISTRY_PATH", registry_path)
    monkeypatch.setattr(brain_session_start, "BRAIN_HOME", tmp_path)

    # Stub load_registry in both modules to use the temp file.
    def _fake_load_registry():
        with open(registry_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {"version": 1, "vaults": {}}

    monkeypatch.setattr(brain_common, "load_registry", _fake_load_registry)
    monkeypatch.setattr(brain_session_start, "load_registry", _fake_load_registry)

    # Stub out the resolve_project_vault so we don't need real vaults.
    monkeypatch.setattr(brain_session_start, "resolve_project_vault", lambda cwd: (None, None, None, None))

    # Stub the operational probe — this is the key control knob for these tests.
    monkeypatch.setattr(brain_session_start, "local_brain_service_operational", lambda **kw: operational)
    monkeypatch.setattr(brain_common, "local_brain_service_operational", lambda **kw: operational)

    # Stub local_brain_service_available (driven by enabled, which varies per test).
    monkeypatch.setattr(brain_session_start, "local_brain_service_available", lambda: False)

    hook_input = {
        "hook_event_name": "SessionStart",
        "cwd": cwd if cwd is not None else str(ROOT),
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook_input)))

    with pytest.raises(SystemExit):
        brain_session_start.main()

    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


# ---------------------------------------------------------------------------
# Acceptance test 1: desired==docker + service DOWN → forcing injected
# ---------------------------------------------------------------------------

def test_desired_docker_service_down_injects_forcing_instruction(monkeypatch, capsys, tmp_path):
    """When desired==docker and the service is not operational, force setup."""
    settings = {
        "local_brain_service": {
            "enabled": True,  # enabled is true here; DOWN is what matters
            "desired": "docker",
        }
    }
    context = _run_session_start(monkeypatch, capsys, tmp_path, settings, operational=False)

    assert "/fritz:brain-service-setup" in context
    assert "REQUIRED ACTION" in context


# ---------------------------------------------------------------------------
# Acceptance test 2: suppression-bug regression — enabled:false + desired:docker + DOWN
# ---------------------------------------------------------------------------

def test_enabled_false_desired_docker_service_down_still_forces(monkeypatch, capsys, tmp_path):
    """enabled:false must NOT suppress the forcing instruction when desired==docker.

    This is the core suppression-bug regression test.  Old code gated the forcing
    on local_brain_service_configured() / local_brain_service_enabled(), which
    caused it to silently suppress when enabled:false was recorded.
    """
    settings = {
        "local_brain_service": {
            "enabled": False,   # THE BUG: this used to suppress the forcing
            "desired": "docker",
        }
    }
    context = _run_session_start(monkeypatch, capsys, tmp_path, settings, operational=False)

    assert "/fritz:brain-service-setup" in context, (
        "Forcing instruction missing: enabled:false must not suppress desired==docker forcing"
    )
    assert "REQUIRED ACTION" in context


# ---------------------------------------------------------------------------
# Acceptance test 3a: operational service → no forcing
# ---------------------------------------------------------------------------

def test_desired_docker_service_operational_no_forcing(monkeypatch, capsys, tmp_path):
    """When desired==docker but the service IS operational, no forcing injected."""
    settings = {
        "local_brain_service": {
            "enabled": True,
            "desired": "docker",
        }
    }
    context = _run_session_start(monkeypatch, capsys, tmp_path, settings, operational=True)

    assert "/fritz:brain-service-setup" not in context
    assert "REQUIRED ACTION" not in context


# ---------------------------------------------------------------------------
# Acceptance test 3b: desired==local → no forcing even when service is down
# ---------------------------------------------------------------------------

def test_desired_local_service_down_no_forcing(monkeypatch, capsys, tmp_path):
    """When desired==local (or absent), no forcing even if service is down."""
    settings = {
        "local_brain_service": {
            "enabled": False,
            "desired": "local",
        }
    }
    context = _run_session_start(monkeypatch, capsys, tmp_path, settings, operational=False)

    assert "/fritz:brain-service-setup" not in context


# ---------------------------------------------------------------------------
# Acceptance test 3c: desired absent → no forcing (conservative default)
# ---------------------------------------------------------------------------

def test_desired_absent_no_forcing(monkeypatch, capsys, tmp_path):
    """When the desired key is completely absent, no forcing (default is 'local')."""
    settings = {
        "local_brain_service": {
            "enabled": False,
            # no 'desired' key
        }
    }
    context = _run_session_start(monkeypatch, capsys, tmp_path, settings, operational=False)

    assert "/fritz:brain-service-setup" not in context


# ---------------------------------------------------------------------------
# Unit tests for brain_common helpers
# ---------------------------------------------------------------------------

def _set_central(monkeypatch, tmp_path: Path, settings: dict):
    registry_path = tmp_path / "registry.yaml"
    monkeypatch.setattr(brain_common, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_common, "REGISTRY_PATH", registry_path)
    registry_path.write_text(
        yaml.safe_dump({"version": 1, "vaults": {}, "settings": settings}),
        encoding="utf-8",
    )


def test_get_local_brain_service_desired_returns_docker(monkeypatch, tmp_path):
    _set_central(monkeypatch, tmp_path, {"local_brain_service": {"desired": "docker"}})
    assert brain_common.get_local_brain_service_desired() == "docker"


def test_get_local_brain_service_desired_returns_local(monkeypatch, tmp_path):
    _set_central(monkeypatch, tmp_path, {"local_brain_service": {"desired": "local"}})
    assert brain_common.get_local_brain_service_desired() == "local"


def test_get_local_brain_service_desired_absent_defaults_to_local(monkeypatch, tmp_path):
    _set_central(monkeypatch, tmp_path, {"local_brain_service": {"enabled": False}})
    assert brain_common.get_local_brain_service_desired() == "local"


def test_get_local_brain_service_desired_no_config_defaults_to_local(monkeypatch, tmp_path):
    _set_central(monkeypatch, tmp_path, {})
    assert brain_common.get_local_brain_service_desired() == "local"


def test_get_local_brain_service_desired_invalid_value_defaults_to_local(monkeypatch, tmp_path):
    _set_central(monkeypatch, tmp_path, {"local_brain_service": {"desired": "kubernetes"}})
    assert brain_common.get_local_brain_service_desired() == "local"


def test_get_local_brain_service_desired_normalizes_case(monkeypatch, tmp_path):
    _set_central(monkeypatch, tmp_path, {"local_brain_service": {"desired": "Docker"}})
    assert brain_common.get_local_brain_service_desired() == "docker"


def test_local_brain_service_operational_returns_false_on_connection_refused(monkeypatch, tmp_path):
    """When the service is unreachable, operational() returns False and does not raise."""
    _set_central(monkeypatch, tmp_path, {"local_brain_service": {"base_url": "http://127.0.0.1:19999"}})

    # Simulate a connection error from urlopen.
    from urllib import error as urllib_error
    import socket

    def _fake_urlopen(req, timeout):
        raise urllib_error.URLError(socket.error("Connection refused"))

    monkeypatch.setattr(brain_common.request, "urlopen", _fake_urlopen)
    assert brain_common.local_brain_service_operational() is False


def test_local_brain_service_operational_returns_true_on_200(monkeypatch, tmp_path):
    """When /v1/status responds 200, operational() returns True."""
    _set_central(monkeypatch, tmp_path, {"local_brain_service": {"base_url": "http://127.0.0.1:8765"}})

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(brain_common.request, "urlopen", lambda req, timeout: FakeResponse())
    assert brain_common.local_brain_service_operational() is True


def test_local_brain_service_operational_ignores_enabled_flag(monkeypatch, tmp_path):
    """operational() must NOT gate on local_brain_service_enabled()."""
    _set_central(monkeypatch, tmp_path, {
        "local_brain_service": {"enabled": False, "base_url": "http://127.0.0.1:8765"}
    })

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(brain_common.request, "urlopen", lambda req, timeout: FakeResponse())
    # Should return True even though enabled==False.
    assert brain_common.local_brain_service_operational() is True


def test_local_brain_service_setup_forcing_instruction_contains_skill(monkeypatch, tmp_path):
    """The forcing instruction text must contain the skill name."""
    text = brain_common.local_brain_service_setup_forcing_instruction()
    assert "/fritz:brain-service-setup" in text
    assert "REQUIRED" in text


# ---------------------------------------------------------------------------
# Project-layer override tests (.fritz-local.json cwd resolution)
# ---------------------------------------------------------------------------

def test_get_local_brain_service_desired_project_overrides_central(monkeypatch, tmp_path):
    """Project .fritz-local.json local_brain_service_desired overrides central desired.

    Central says desired=local (or absent); project flat key says docker.
    get_local_brain_service_desired(cwd=<proj>) must return "docker".
    """
    # Central: desired absent (defaults to local).
    _set_central(monkeypatch, tmp_path, {"local_brain_service": {"enabled": False}})

    # Create a project directory with .fritz-local.json declaring docker.
    proj_dir = tmp_path / "myproject"
    proj_dir.mkdir()
    fritz_local_file = proj_dir / ".fritz-local.json"
    fritz_local_file.write_text(
        json.dumps({"local_brain_service_desired": "docker"}), encoding="utf-8"
    )

    result = brain_common.get_local_brain_service_desired(cwd=str(proj_dir))
    assert result == "docker", (
        f"Expected 'docker' from .fritz-local.json project override, got {result!r}"
    )


def test_get_local_brain_service_desired_project_override_no_cwd_ignored(monkeypatch, tmp_path):
    """Without cwd, the project .fritz-local.json is NOT consulted (central wins).

    This documents the pre-fix behaviour that cwd=None skips the project layer.
    Central desired=local; project says docker — but no cwd supplied, so we get local.
    """
    _set_central(monkeypatch, tmp_path, {"local_brain_service": {"desired": "local"}})

    proj_dir = tmp_path / "myproject"
    proj_dir.mkdir()
    (proj_dir / ".fritz-local.json").write_text(
        json.dumps({"local_brain_service_desired": "docker"}), encoding="utf-8"
    )

    # No cwd → project layer skipped → central "local" wins.
    result = brain_common.get_local_brain_service_desired()
    assert result == "local"


def test_session_start_project_override_docker_forces_when_service_down(monkeypatch, capsys, tmp_path):
    """Session-start with a project .fritz-local.json docker override + service DOWN forces setup.

    Central has no desired key; the project directory has local_brain_service_desired=docker.
    The hook is invoked with cwd pointing at the project dir → forcing instruction injected.
    """
    # Central: no desired key.
    settings = {"local_brain_service": {"enabled": False}}

    # Create a project dir with .fritz-local.json declaring docker.
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    (proj_dir / ".fritz-local.json").write_text(
        json.dumps({"local_brain_service_desired": "docker"}), encoding="utf-8"
    )

    context = _run_session_start(
        monkeypatch, capsys, tmp_path, settings,
        operational=False,
        cwd=str(proj_dir),
    )

    assert "/fritz:brain-service-setup" in context, (
        "Forcing instruction missing: project .fritz-local.json desired=docker must trigger forcing"
    )
    assert "REQUIRED ACTION" in context
