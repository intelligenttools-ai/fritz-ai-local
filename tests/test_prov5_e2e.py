"""PROV5 e2e test — forcing → provision → verify loop closes (issue #121).

Chains the REAL components across all three PROV stages with mocked docker /
HTTP.  No live docker, no real ~/.brain — all disk I/O goes to tmp dirs
isolated via HOME and registry overrides.

Stage 1 — FORCING
  Set desired:docker in a temp registry with the service DOWN.
  Run brain_session_start.main() — assert the forcing
  ``/fritz:brain-service-setup`` instruction is injected.

Stage 2 — PROVISION
  Call provision_engine.provision() with an explicit ProvisionConfig,
  mocked DockerGateway (build + up), and mocked HttpGateway (200 ok).
  Assert:
  - .env written with API_TOKEN
  - registry.yaml written with local_brain_service.enabled = True
  - overall result is "ok" or "already_provisioned"

Stage 3 — VERIFY LOOP CLOSES
  After provisioning, stub the operational probe to return True (service is up).
  Re-run brain_session_start.main() — assert the forcing instruction is
  NOT present in the second session-start output.

Idioms reused from test_prov3_forcing_prompt.py and test_provision.py.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import brain_common          # noqa: E402
import brain_session_start  # noqa: E402


# ---------------------------------------------------------------------------
# Loader helper (same pattern as test_provision.py)
# ---------------------------------------------------------------------------

def _load_engine():
    """Load provision_engine.py directly; cached after first load."""
    mod_name = "provision_engine"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    path = ROOT / "scripts" / "provision_engine.py"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _write_registry(tmp_path: Path, settings: dict) -> Path:
    """Write a minimal registry.yaml to tmp_path."""
    reg = tmp_path / "registry.yaml"
    reg.write_text(
        yaml.safe_dump({"version": 1, "vaults": {}, "settings": settings}),
        encoding="utf-8",
    )
    return reg


def _run_session_start(
    monkeypatch,
    capsys,
    tmp_path: Path,
    settings: dict,
    *,
    operational: bool,
    registry_path: Path | None = None,
) -> str:
    """Run brain_session_start.main() in an isolated tmp environment.

    Returns the additionalContext string from the hook JSON output.
    Reuses the idiom from test_prov3_forcing_prompt.py.
    """
    reg = registry_path if registry_path is not None else _write_registry(tmp_path, settings)

    monkeypatch.setattr(brain_common, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_common, "REGISTRY_PATH", reg)
    monkeypatch.setattr(brain_session_start, "BRAIN_HOME", tmp_path)

    def _fake_load_registry():
        with open(reg, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {"version": 1, "vaults": {}}

    monkeypatch.setattr(brain_common, "load_registry", _fake_load_registry)
    monkeypatch.setattr(brain_session_start, "load_registry", _fake_load_registry)

    monkeypatch.setattr(
        brain_session_start,
        "resolve_project_vault",
        lambda cwd: (None, None, None, None),
    )
    monkeypatch.setattr(
        brain_session_start,
        "local_brain_service_operational",
        lambda **kw: operational,
    )
    monkeypatch.setattr(
        brain_common,
        "local_brain_service_operational",
        lambda **kw: operational,
    )
    monkeypatch.setattr(
        brain_session_start,
        "local_brain_service_available",
        lambda: False,
    )

    hook_input = {"hook_event_name": "SessionStart", "cwd": str(ROOT)}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook_input)))

    with pytest.raises(SystemExit):
        brain_session_start.main()

    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


def _make_docker_gw(mod):
    """Build a FakeDocker that records build/up calls."""
    calls: list[str] = []

    class _FakeDocker(mod.DockerGateway):
        def __init__(self):
            self.calls = calls

        def build(self):
            calls.append("build")

        def up(self):
            calls.append("up")

    return _FakeDocker(), calls


def _make_http_ok(mod):
    """Build an HttpGateway that always returns 200."""
    class _FakeHttp(mod.HttpGateway):
        def get(self, url, headers=None):
            return (200, b'{"ok":true}')

    return _FakeHttp()


def _urlopen_ok_mock():
    """Return a mock response object for urllib.request.urlopen."""
    mock = MagicMock()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    mock.status = 200
    mock.read.return_value = json.dumps({}).encode()
    return mock


# ---------------------------------------------------------------------------
# PROV5 e2e test
# ---------------------------------------------------------------------------

class TestProv5E2EForcingProvisionVerify:
    """Full forcing → provision → verify loop, no live infra."""

    def test_stage1_forcing_injects_setup_instruction(self, monkeypatch, capsys, tmp_path):
        """Stage 1: desired==docker + service DOWN injects the forcing instruction."""
        settings = {
            "local_brain_service": {
                "enabled": False,   # suppression-bug regression: enabled=false must not suppress
                "desired": "docker",
            }
        }
        context = _run_session_start(
            monkeypatch, capsys, tmp_path, settings, operational=False
        )

        assert "/fritz:brain-service-setup" in context, (
            "Forcing instruction not injected: desired==docker + service DOWN must inject setup"
        )
        assert "REQUIRED ACTION" in context

    def test_stage2_provision_writes_env_and_registry(self, tmp_path):
        """Stage 2: provision() writes .env + registry.yaml with correct values."""
        mod = _load_engine()
        cfg = mod.ProvisionConfig(
            api_token="tok-e2e-test-001",
            base_url="http://127.0.0.1:8765",
            llm_protocol="openai-compatible",
            llm_endpoint="http://host.docker.internal:11434/v1",
            llm_model="llama3.2:latest",
        )
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"

        docker_gw, docker_calls = _make_docker_gw(mod)
        http_gw = _make_http_ok(mod)

        with patch("urllib.request.urlopen", return_value=_urlopen_ok_mock()):
            with patch.object(
                mod, "_run_preflight",
                return_value=mod.StepResult("preflight", "ok", "all ok"),
            ):
                result = mod.provision(
                    cfg,
                    env_path=env_path,
                    registry_path=reg_path,
                    docker=docker_gw,
                    http=http_gw,
                )

        # Overall result
        assert result.overall in {"ok", "already_provisioned"}, (
            f"provision() returned unexpected overall: {result.overall}"
        )

        # .env written with the API token
        assert env_path.exists(), ".env was not written"
        env_text = env_path.read_text()
        assert "API_TOKEN=tok-e2e-test-001" in env_text, (
            "API_TOKEN not written to .env"
        )
        assert "LLM_PROTOCOL=openai-compatible" in env_text

        # registry.yaml written with service enabled
        assert reg_path.exists(), "registry.yaml was not written"
        reg_data = yaml.safe_load(reg_path.read_text())
        svc = reg_data["settings"]["local_brain_service"]
        assert svc["enabled"] is True, "local_brain_service.enabled must be True after provision"
        assert svc["api_token"] == "tok-e2e-test-001"

        # Docker gateway was actually invoked
        assert "build" in docker_calls, "docker.build() was not called"
        assert "up" in docker_calls, "docker.up() was not called"

        # Verify step succeeded (service + LLM probe)
        verify_step = result._step("verify")
        assert verify_step is not None, "verify step missing from result"
        assert verify_step.status in {"ok", "warning"}, (
            f"verify step has unexpected status: {verify_step.status}"
        )

    def test_stage3_verify_loop_closes_no_forcing_after_provision(
        self, monkeypatch, capsys, tmp_path
    ):
        """Stage 3: after provisioning, operational service → no forcing instruction."""
        # Simulate the post-provision registry (desired=docker, enabled=True)
        settings = {
            "local_brain_service": {
                "enabled": True,
                "desired": "docker",
            }
        }
        reg = _write_registry(tmp_path, settings)

        # Service is now operational (provision succeeded)
        context = _run_session_start(
            monkeypatch, capsys, tmp_path, settings,
            operational=True,
            registry_path=reg,
        )

        assert "/fritz:brain-service-setup" not in context, (
            "Forcing instruction fired even though service is operational: "
            "verify loop did not close"
        )
        assert "REQUIRED ACTION" not in context

    def test_full_chain_all_three_stages(self, monkeypatch, capsys, tmp_path):
        """Full chain: stage 1 forces → stage 2 provisions → stage 3 no longer forces.

        This single test exercises all three stages in sequence to make the
        causal relationship explicit.
        """
        mod = _load_engine()

        # ------------------------------------------------------------------
        # Stage 1: desired:docker + DOWN → forcing
        # ------------------------------------------------------------------
        settings_before = {
            "local_brain_service": {
                "enabled": False,
                "desired": "docker",
            }
        }
        context_before = _run_session_start(
            monkeypatch, capsys, tmp_path, settings_before, operational=False
        )
        assert "/fritz:brain-service-setup" in context_before, (
            "Stage 1 failed: forcing instruction not injected"
        )

        # ------------------------------------------------------------------
        # Stage 2: provision writes .env + registry
        # ------------------------------------------------------------------
        cfg = mod.ProvisionConfig(
            api_token="tok-chain-001",
            base_url="http://127.0.0.1:8765",
        )
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"

        docker_gw, _ = _make_docker_gw(mod)
        http_gw = _make_http_ok(mod)

        with patch("urllib.request.urlopen", return_value=_urlopen_ok_mock()):
            with patch.object(
                mod, "_run_preflight",
                return_value=mod.StepResult("preflight", "ok", "all ok"),
            ):
                result = mod.provision(
                    cfg,
                    env_path=env_path,
                    registry_path=reg_path,
                    docker=docker_gw,
                    http=http_gw,
                )

        assert result.overall in {"ok", "already_provisioned"}, (
            f"Stage 2 failed: provision returned {result.overall}"
        )
        assert env_path.exists()
        reg_data = yaml.safe_load(reg_path.read_text())
        assert reg_data["settings"]["local_brain_service"]["enabled"] is True

        # ------------------------------------------------------------------
        # Stage 3: service now operational → forcing must NOT fire
        # ------------------------------------------------------------------
        # The registry written by provision has desired inherited from cfg; we
        # need to ensure desired:docker is present so the forcing check runs
        # (and then passes through because operational=True).
        reg_data["settings"]["local_brain_service"]["desired"] = "docker"
        reg_path.write_text(yaml.safe_dump(reg_data), encoding="utf-8")

        context_after = _run_session_start(
            monkeypatch, capsys, tmp_path, {},  # settings ignored; reg_path used
            operational=True,
            registry_path=reg_path,
        )

        assert "/fritz:brain-service-setup" not in context_after, (
            "Stage 3 failed: forcing still fires after service became operational"
        )
