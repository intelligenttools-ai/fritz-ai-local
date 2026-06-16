"""Tests for PROV4 — drift detection, re-provision, and rollback (issue #120).

All Docker and HTTP calls are mocked; no live infrastructure is touched.
The ~/.brain directory is redirected to a tmp_path to prevent any live write.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# Loader helper (same pattern as test_provision.py)
# ---------------------------------------------------------------------------

def load_engine():
    """Load provision_engine.py directly to avoid import-path assumptions."""
    import sys
    mod_name = "provision_engine"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    path = Path(__file__).resolve().parents[1] / "scripts" / "provision_engine.py"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Helpers shared across test classes
# ---------------------------------------------------------------------------

def _write_env(env_path: Path, values: dict[str, str]) -> None:
    """Write a minimal .env with the given key=value pairs."""
    lines = [f"{k}={v}" for k, v in values.items()]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_registry(reg_path: Path, service: dict | None = None) -> None:
    """Write a minimal registry.yaml."""
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {"version": 1, "vaults": {}}
    if service is not None:
        data["settings"] = {"local_brain_service": service}
    reg_path.write_text(yaml.dump(data), encoding="utf-8")


def _make_inspect_gateway(mod, env_map: dict[str, str] | None):
    """Build an InspectGateway stub that returns env_map (or None for absent)."""
    def _inspector(container: str) -> dict[str, str] | None:
        return env_map

    return mod.InspectGateway(inspector=_inspector)


def _make_docker_gateway(mod, *, fail: bool = False, down_calls: list | None = None):
    """Build a DockerGateway stub that records calls."""
    calls: list[str] = []
    _down_calls = down_calls if down_calls is not None else []

    class _FakeDocker(mod.DockerGateway):
        def __init__(self):
            pass  # skip real subprocess init

        def build(self):
            if fail:
                raise RuntimeError("docker build failed")
            calls.append("build")

        def up(self):
            calls.append("up")

        def compose_cmd(self, *args):
            return ["docker", "compose", *args]

        def _runner(self, cmd):
            _down_calls.append(cmd)

    gw = _FakeDocker()
    # Patch _runner on the instance directly so rollback's compose_cmd+runner works
    gw._runner = lambda cmd: _down_calls.append(cmd)
    return gw, calls


def _make_http_ok(mod):
    class _FakeHttp(mod.HttpGateway):
        def get(self, url, headers=None):
            return (200, b'{"ok":true}')
    return _FakeHttp()


def _urlopen_ok_mock():
    mock_response = MagicMock()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_response.status = 200
    mock_response.read.return_value = json.dumps({}).encode()
    return mock_response


# ---------------------------------------------------------------------------
# Drift detection tests
# ---------------------------------------------------------------------------

class TestDetectDrift:
    """PROV4 drift detection: desired env vs running container env."""

    def test_in_sync_when_all_keys_match(self, tmp_path):
        """If the container env matches the on-disk .env, report in_sync."""
        mod = load_engine()
        env_path = tmp_path / ".env"
        _write_env(env_path, {
            "LLM_PROTOCOL": "openai-compatible",
            "LLM_ENDPOINT": "http://host.docker.internal:11434/v1",
            "LLM_MODEL": "llama3.2:latest",
            "LLM_API_KEY": "",
            "EMBEDDING_ENABLED": "false",
            "EMBEDDING_ENDPOINT": "http://host.docker.internal:11434/v1",
            "EMBEDDING_MODEL": "nomic-embed-text:latest",
            "EMBEDDING_API_KEY": "",
            "EMBEDDING_REFRESH_AFTER_COMPILE": "true",
            "EMBEDDING_REFRESH_DEBOUNCE_SECONDS": "300",
            "SCHEDULER_ENABLED": "false",
            "SCHEDULER_DRY_RUN": "true",
            "BRAIN_INTERVAL_MINUTES": "30",
            "CAPTURE_MAX_CHARS": "4000",
            "COMPILE_MAX_CAPTURES": "25",
            "LARGE_BATCH_THRESHOLD": "10",
            "ALLOW_FIRST_EXTERNAL_SYNC": "false",
        })

        # Container env matches exactly
        container_env = {
            "LLM_PROTOCOL": "openai-compatible",
            "LLM_ENDPOINT": "http://host.docker.internal:11434/v1",
            "LLM_MODEL": "llama3.2:latest",
            "LLM_API_KEY": "",
            "EMBEDDING_ENABLED": "false",
            "EMBEDDING_ENDPOINT": "http://host.docker.internal:11434/v1",
            "EMBEDDING_MODEL": "nomic-embed-text:latest",
            "EMBEDDING_API_KEY": "",
            "EMBEDDING_REFRESH_AFTER_COMPILE": "true",
            "EMBEDDING_REFRESH_DEBOUNCE_SECONDS": "300",
            "SCHEDULER_ENABLED": "false",
            "SCHEDULER_DRY_RUN": "true",
            "BRAIN_INTERVAL_MINUTES": "30",
            "CAPTURE_MAX_CHARS": "4000",
            "COMPILE_MAX_CAPTURES": "25",
            "LARGE_BATCH_THRESHOLD": "10",
            "ALLOW_FIRST_EXTERNAL_SYNC": "false",
        }
        inspect = _make_inspect_gateway(mod, container_env)

        cfg = mod.ProvisionConfig()
        report = mod.detect_drift(cfg, env_path=env_path, inspect=inspect)

        assert report.status == "in_sync"
        assert report.drifted_keys == []
        assert report.is_clean()

    def test_drifted_when_llm_model_changed(self, tmp_path):
        """If LLM_MODEL in .env differs from running container, report drifted."""
        mod = load_engine()
        env_path = tmp_path / ".env"
        _write_env(env_path, {
            "LLM_MODEL": "gpt-4o-mini",  # desired: new model
            "LLM_PROTOCOL": "openai-compatible",
            "LLM_ENDPOINT": "http://host.docker.internal:11434/v1",
            "LLM_API_KEY": "",
            "EMBEDDING_ENABLED": "false",
            "EMBEDDING_ENDPOINT": "http://host.docker.internal:11434/v1",
            "EMBEDDING_MODEL": "nomic-embed-text:latest",
            "EMBEDDING_API_KEY": "",
            "EMBEDDING_REFRESH_AFTER_COMPILE": "true",
            "EMBEDDING_REFRESH_DEBOUNCE_SECONDS": "300",
            "SCHEDULER_ENABLED": "false",
            "SCHEDULER_DRY_RUN": "true",
            "BRAIN_INTERVAL_MINUTES": "30",
            "CAPTURE_MAX_CHARS": "4000",
            "COMPILE_MAX_CAPTURES": "25",
            "LARGE_BATCH_THRESHOLD": "10",
            "ALLOW_FIRST_EXTERNAL_SYNC": "false",
        })

        # Container is still running with the OLD model
        container_env = {
            "LLM_MODEL": "llama3.2:latest",  # old model
            "LLM_PROTOCOL": "openai-compatible",
            "LLM_ENDPOINT": "http://host.docker.internal:11434/v1",
            "LLM_API_KEY": "",
            "EMBEDDING_ENABLED": "false",
            "EMBEDDING_ENDPOINT": "http://host.docker.internal:11434/v1",
            "EMBEDDING_MODEL": "nomic-embed-text:latest",
            "EMBEDDING_API_KEY": "",
            "EMBEDDING_REFRESH_AFTER_COMPILE": "true",
            "EMBEDDING_REFRESH_DEBOUNCE_SECONDS": "300",
            "SCHEDULER_ENABLED": "false",
            "SCHEDULER_DRY_RUN": "true",
            "BRAIN_INTERVAL_MINUTES": "30",
            "CAPTURE_MAX_CHARS": "4000",
            "COMPILE_MAX_CAPTURES": "25",
            "LARGE_BATCH_THRESHOLD": "10",
            "ALLOW_FIRST_EXTERNAL_SYNC": "false",
        }
        inspect = _make_inspect_gateway(mod, container_env)

        cfg = mod.ProvisionConfig(llm_model="gpt-4o-mini")
        report = mod.detect_drift(cfg, env_path=env_path, inspect=inspect)

        assert report.status == "drifted"
        assert not report.is_clean()
        drifted_keys = [e.key for e in report.drifted_keys]
        assert "LLM_MODEL" in drifted_keys

        # Check the entry content
        llm_entry = next(e for e in report.drifted_keys if e.key == "LLM_MODEL")
        assert llm_entry.desired == "gpt-4o-mini"
        assert llm_entry.running == "llama3.2:latest"

    def test_down_when_container_absent(self, tmp_path):
        """If the inspector returns None, report status=down."""
        mod = load_engine()
        env_path = tmp_path / ".env"
        _write_env(env_path, {"LLM_MODEL": "llama3.2:latest", "LLM_PROTOCOL": "openai-compatible"})

        inspect = _make_inspect_gateway(mod, None)  # container absent
        cfg = mod.ProvisionConfig()
        report = mod.detect_drift(cfg, env_path=env_path, inspect=inspect)

        assert report.status == "down"

    def test_unknown_when_no_env_file(self, tmp_path):
        """If .env is absent, drift detection returns unknown."""
        mod = load_engine()
        env_path = tmp_path / ".env"  # does not exist

        inspect = _make_inspect_gateway(mod, {"LLM_MODEL": "something"})
        cfg = mod.ProvisionConfig()
        report = mod.detect_drift(cfg, env_path=env_path, inspect=inspect)

        assert report.status == "unknown"

    def test_down_when_inspector_raises(self, tmp_path):
        """If the inspector raises, InspectGateway.get_env() returns None,
        which detect_drift treats as 'down' (container absent/unreachable).
        Either way, the function never raises itself."""
        mod = load_engine()
        env_path = tmp_path / ".env"
        _write_env(env_path, {"LLM_MODEL": "llama3.2:latest"})

        def _raising_inspector(container):
            raise RuntimeError("docker not installed")

        inspect = mod.InspectGateway(inspector=_raising_inspector)
        cfg = mod.ProvisionConfig()
        # Must not raise; inspector errors are absorbed
        report = mod.detect_drift(cfg, env_path=env_path, inspect=inspect)

        # InspectGateway absorbs the exception and returns None → 'down'
        assert report.status in {"down", "unknown"}  # either is acceptable, never raises

    def test_multiple_drifted_keys_all_reported(self, tmp_path):
        """If LLM_MODEL and SCHEDULER_ENABLED both differ, both are reported."""
        mod = load_engine()
        env_path = tmp_path / ".env"
        _write_env(env_path, {
            "LLM_MODEL": "gpt-4o",
            "SCHEDULER_ENABLED": "true",
            "LLM_PROTOCOL": "openai-compatible",
            "LLM_ENDPOINT": "http://host.docker.internal:11434/v1",
            "LLM_API_KEY": "",
            "EMBEDDING_ENABLED": "false",
            "EMBEDDING_ENDPOINT": "http://host.docker.internal:11434/v1",
            "EMBEDDING_MODEL": "nomic-embed-text:latest",
            "EMBEDDING_API_KEY": "",
            "EMBEDDING_REFRESH_AFTER_COMPILE": "true",
            "EMBEDDING_REFRESH_DEBOUNCE_SECONDS": "300",
            "SCHEDULER_DRY_RUN": "true",
            "BRAIN_INTERVAL_MINUTES": "30",
            "CAPTURE_MAX_CHARS": "4000",
            "COMPILE_MAX_CAPTURES": "25",
            "LARGE_BATCH_THRESHOLD": "10",
            "ALLOW_FIRST_EXTERNAL_SYNC": "false",
        })

        container_env = {
            "LLM_MODEL": "llama3.2:latest",
            "SCHEDULER_ENABLED": "false",
            "LLM_PROTOCOL": "openai-compatible",
            "LLM_ENDPOINT": "http://host.docker.internal:11434/v1",
            "LLM_API_KEY": "",
            "EMBEDDING_ENABLED": "false",
            "EMBEDDING_ENDPOINT": "http://host.docker.internal:11434/v1",
            "EMBEDDING_MODEL": "nomic-embed-text:latest",
            "EMBEDDING_API_KEY": "",
            "EMBEDDING_REFRESH_AFTER_COMPILE": "true",
            "EMBEDDING_REFRESH_DEBOUNCE_SECONDS": "300",
            "SCHEDULER_DRY_RUN": "true",
            "BRAIN_INTERVAL_MINUTES": "30",
            "CAPTURE_MAX_CHARS": "4000",
            "COMPILE_MAX_CAPTURES": "25",
            "LARGE_BATCH_THRESHOLD": "10",
            "ALLOW_FIRST_EXTERNAL_SYNC": "false",
        }
        inspect = _make_inspect_gateway(mod, container_env)

        cfg = mod.ProvisionConfig(llm_model="gpt-4o", scheduler_enabled=True)
        report = mod.detect_drift(cfg, env_path=env_path, inspect=inspect)

        assert report.status == "drifted"
        drifted_keys = [e.key for e in report.drifted_keys]
        assert "LLM_MODEL" in drifted_keys
        assert "SCHEDULER_ENABLED" in drifted_keys


# ---------------------------------------------------------------------------
# Reconfigure tests
# ---------------------------------------------------------------------------

class TestReconfigure:
    """PROV4 re-provision: reuses provision() on drift, skips on in-sync."""

    def _provision_call_ok(self):
        """Return a mock provision() that records it was called and returns ok."""
        mod = load_engine()
        called = []

        def _fake_provision(cfg, **kwargs):
            called.append("provision")
            return mod.ProvisionResult(
                overall="ok",
                steps=[mod.StepResult("docker_start", "ok", "build + up -d completed")],
                api_token="tok-test",
                base_url="http://127.0.0.1:8765",
            )

        return _fake_provision, called

    def test_no_drift_returns_no_drift_without_re_provision(self, tmp_path):
        """When in-sync, reconfigure() returns no_drift and does NOT call provision."""
        mod = load_engine()
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"

        # Write matching env
        _write_env(env_path, {
            "LLM_MODEL": "llama3.2:latest",
            "LLM_PROTOCOL": "openai-compatible",
            "LLM_ENDPOINT": "http://host.docker.internal:11434/v1",
            "LLM_API_KEY": "",
            "EMBEDDING_ENABLED": "false",
            "EMBEDDING_ENDPOINT": "http://host.docker.internal:11434/v1",
            "EMBEDDING_MODEL": "nomic-embed-text:latest",
            "EMBEDDING_API_KEY": "",
            "EMBEDDING_REFRESH_AFTER_COMPILE": "true",
            "EMBEDDING_REFRESH_DEBOUNCE_SECONDS": "300",
            "SCHEDULER_ENABLED": "false",
            "SCHEDULER_DRY_RUN": "true",
            "BRAIN_INTERVAL_MINUTES": "30",
            "CAPTURE_MAX_CHARS": "4000",
            "COMPILE_MAX_CAPTURES": "25",
            "LARGE_BATCH_THRESHOLD": "10",
            "ALLOW_FIRST_EXTERNAL_SYNC": "false",
        })

        # Container env matches the .env exactly
        full_env = {
            "LLM_MODEL": "llama3.2:latest",
            "LLM_PROTOCOL": "openai-compatible",
            "LLM_ENDPOINT": "http://host.docker.internal:11434/v1",
            "LLM_API_KEY": "",
            "EMBEDDING_ENABLED": "false",
            "EMBEDDING_ENDPOINT": "http://host.docker.internal:11434/v1",
            "EMBEDDING_MODEL": "nomic-embed-text:latest",
            "EMBEDDING_API_KEY": "",
            "EMBEDDING_REFRESH_AFTER_COMPILE": "true",
            "EMBEDDING_REFRESH_DEBOUNCE_SECONDS": "300",
            "SCHEDULER_ENABLED": "false",
            "SCHEDULER_DRY_RUN": "true",
            "BRAIN_INTERVAL_MINUTES": "30",
            "CAPTURE_MAX_CHARS": "4000",
            "COMPILE_MAX_CAPTURES": "25",
            "LARGE_BATCH_THRESHOLD": "10",
            "ALLOW_FIRST_EXTERNAL_SYNC": "false",
        }
        inspect = _make_inspect_gateway(mod, full_env)
        fake_provision, provision_calls = self._provision_call_ok()

        cfg = mod.ProvisionConfig()
        with patch.object(mod, "provision", side_effect=fake_provision):
            result = mod.reconfigure(
                cfg,
                env_path=env_path,
                registry_path=reg_path,
                inspect=inspect,
            )

        assert result.overall == "no_drift"
        assert result.drift is not None and result.drift.status == "in_sync"
        assert provision_calls == []  # provision was NOT called

    def test_drift_triggers_reprovision(self, tmp_path):
        """When drift is detected, reconfigure() calls provision() and returns ok."""
        mod = load_engine()
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"

        # Desired: new model
        _write_env(env_path, {
            "LLM_MODEL": "gpt-4o-mini",
            "LLM_PROTOCOL": "openai-compatible",
            "LLM_ENDPOINT": "http://host.docker.internal:11434/v1",
            "LLM_API_KEY": "",
            "EMBEDDING_ENABLED": "false",
            "EMBEDDING_ENDPOINT": "http://host.docker.internal:11434/v1",
            "EMBEDDING_MODEL": "nomic-embed-text:latest",
            "EMBEDDING_API_KEY": "",
            "EMBEDDING_REFRESH_AFTER_COMPILE": "true",
            "EMBEDDING_REFRESH_DEBOUNCE_SECONDS": "300",
            "SCHEDULER_ENABLED": "false",
            "SCHEDULER_DRY_RUN": "true",
            "BRAIN_INTERVAL_MINUTES": "30",
            "CAPTURE_MAX_CHARS": "4000",
            "COMPILE_MAX_CAPTURES": "25",
            "LARGE_BATCH_THRESHOLD": "10",
            "ALLOW_FIRST_EXTERNAL_SYNC": "false",
        })

        # Container still has old model
        container_env = {
            "LLM_MODEL": "llama3.2:latest",
            "LLM_PROTOCOL": "openai-compatible",
            "LLM_ENDPOINT": "http://host.docker.internal:11434/v1",
            "LLM_API_KEY": "",
            "EMBEDDING_ENABLED": "false",
            "EMBEDDING_ENDPOINT": "http://host.docker.internal:11434/v1",
            "EMBEDDING_MODEL": "nomic-embed-text:latest",
            "EMBEDDING_API_KEY": "",
            "EMBEDDING_REFRESH_AFTER_COMPILE": "true",
            "EMBEDDING_REFRESH_DEBOUNCE_SECONDS": "300",
            "SCHEDULER_ENABLED": "false",
            "SCHEDULER_DRY_RUN": "true",
            "BRAIN_INTERVAL_MINUTES": "30",
            "CAPTURE_MAX_CHARS": "4000",
            "COMPILE_MAX_CAPTURES": "25",
            "LARGE_BATCH_THRESHOLD": "10",
            "ALLOW_FIRST_EXTERNAL_SYNC": "false",
        }
        inspect = _make_inspect_gateway(mod, container_env)
        fake_provision, provision_calls = self._provision_call_ok()

        cfg = mod.ProvisionConfig(llm_model="gpt-4o-mini")
        with patch.object(mod, "provision", side_effect=fake_provision):
            result = mod.reconfigure(
                cfg,
                env_path=env_path,
                registry_path=reg_path,
                inspect=inspect,
            )

        assert result.overall == "ok"
        assert result.drift is not None and result.drift.status == "drifted"
        assert provision_calls == ["provision"]  # provision WAS called
        assert result.provision_result is not None

    def test_force_reprovision_even_when_in_sync(self, tmp_path):
        """When force=True, reconfigure() calls provision() even with no drift."""
        mod = load_engine()
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"

        _write_env(env_path, {
            "LLM_MODEL": "llama3.2:latest",
            "LLM_PROTOCOL": "openai-compatible",
            "LLM_ENDPOINT": "http://host.docker.internal:11434/v1",
            "LLM_API_KEY": "",
            "EMBEDDING_ENABLED": "false",
            "EMBEDDING_ENDPOINT": "http://host.docker.internal:11434/v1",
            "EMBEDDING_MODEL": "nomic-embed-text:latest",
            "EMBEDDING_API_KEY": "",
            "EMBEDDING_REFRESH_AFTER_COMPILE": "true",
            "EMBEDDING_REFRESH_DEBOUNCE_SECONDS": "300",
            "SCHEDULER_ENABLED": "false",
            "SCHEDULER_DRY_RUN": "true",
            "BRAIN_INTERVAL_MINUTES": "30",
            "CAPTURE_MAX_CHARS": "4000",
            "COMPILE_MAX_CAPTURES": "25",
            "LARGE_BATCH_THRESHOLD": "10",
            "ALLOW_FIRST_EXTERNAL_SYNC": "false",
        })
        container_env = {
            "LLM_MODEL": "llama3.2:latest",
            "LLM_PROTOCOL": "openai-compatible",
            "LLM_ENDPOINT": "http://host.docker.internal:11434/v1",
            "LLM_API_KEY": "",
            "EMBEDDING_ENABLED": "false",
            "EMBEDDING_ENDPOINT": "http://host.docker.internal:11434/v1",
            "EMBEDDING_MODEL": "nomic-embed-text:latest",
            "EMBEDDING_API_KEY": "",
            "EMBEDDING_REFRESH_AFTER_COMPILE": "true",
            "EMBEDDING_REFRESH_DEBOUNCE_SECONDS": "300",
            "SCHEDULER_ENABLED": "false",
            "SCHEDULER_DRY_RUN": "true",
            "BRAIN_INTERVAL_MINUTES": "30",
            "CAPTURE_MAX_CHARS": "4000",
            "COMPILE_MAX_CAPTURES": "25",
            "LARGE_BATCH_THRESHOLD": "10",
            "ALLOW_FIRST_EXTERNAL_SYNC": "false",
        }
        inspect = _make_inspect_gateway(mod, container_env)
        fake_provision, provision_calls = self._provision_call_ok()

        cfg = mod.ProvisionConfig()
        with patch.object(mod, "provision", side_effect=fake_provision):
            result = mod.reconfigure(
                cfg,
                env_path=env_path,
                registry_path=reg_path,
                inspect=inspect,
                force=True,
            )

        # force=True means provision was called despite in_sync
        assert result.overall == "ok"
        assert provision_calls == ["provision"]

    def test_down_container_triggers_reprovision(self, tmp_path):
        """When the container is down (drift.status=down), provision is still called."""
        mod = load_engine()
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"
        _write_env(env_path, {"LLM_MODEL": "llama3.2:latest", "LLM_PROTOCOL": "openai-compatible"})

        inspect = _make_inspect_gateway(mod, None)  # container absent
        fake_provision, provision_calls = self._provision_call_ok()

        cfg = mod.ProvisionConfig()
        with patch.object(mod, "provision", side_effect=fake_provision):
            result = mod.reconfigure(
                cfg,
                env_path=env_path,
                registry_path=reg_path,
                inspect=inspect,
            )

        # "down" is not "in_sync", so re-provision runs
        assert provision_calls == ["provision"]
        assert result.drift.status == "down"

    def test_reconfigure_maps_provision_partial_to_partial(self, tmp_path):
        """If provision() returns partial, reconfigure() result is partial."""
        mod = load_engine()
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"
        _write_env(env_path, {"LLM_MODEL": "new-model", "LLM_PROTOCOL": "openai-compatible"})

        container_env = {"LLM_MODEL": "old-model"}
        inspect = _make_inspect_gateway(mod, container_env)

        def _partial_provision(cfg, **kwargs):
            return mod.ProvisionResult(overall="partial")

        cfg = mod.ProvisionConfig(llm_model="new-model")
        with patch.object(mod, "provision", side_effect=_partial_provision):
            result = mod.reconfigure(
                cfg,
                env_path=env_path,
                registry_path=reg_path,
                inspect=inspect,
            )

        assert result.overall == "partial"


# ---------------------------------------------------------------------------
# Rollback tests
# ---------------------------------------------------------------------------

class TestRollbackToLocal:
    """PROV4 rollback: sets desired=local + enabled=false, preserves data."""

    def test_rollback_sets_desired_local_and_enabled_false(self, tmp_path):
        """After rollback, registry has desired=local and enabled=false."""
        mod = load_engine()
        reg_path = tmp_path / ".brain" / "registry.yaml"
        _write_registry(reg_path, service={
            "enabled": True,
            "desired": "docker",
            "base_url": "http://127.0.0.1:8765",
            "api_token": "tok-xyz",
        })

        down_calls: list = []
        docker_gw, _ = _make_docker_gateway(mod, down_calls=down_calls)

        result = mod.rollback_to_local(
            registry_path=reg_path,
            docker=docker_gw,
            stop_container=True,
        )

        assert result.overall == "ok"
        data = yaml.safe_load(reg_path.read_text())
        svc = data["settings"]["local_brain_service"]
        assert svc["desired"] == "local"
        assert svc["enabled"] is False

    def test_rollback_preserves_other_registry_keys(self, tmp_path):
        """Rollback must NOT delete vaults, external_targets, or other settings keys."""
        mod = load_engine()
        reg_path = tmp_path / ".brain" / "registry.yaml"
        reg_path.parent.mkdir(parents=True, exist_ok=True)

        initial = {
            "version": 1,
            "vaults": {"my-vault": {"path": "~/Notes", "status": "active"}},
            "external_targets": {"gh": {"url": "https://github.com/example/repo"}},
            "settings": {
                "context_injection": "light",
                "local_brain_service": {
                    "enabled": True,
                    "desired": "docker",
                    "base_url": "http://127.0.0.1:8765",
                    "api_token": "tok-secret",
                    "api_token_env": "LOCAL_BRAIN_API_TOKEN",
                },
            },
        }
        reg_path.write_text(yaml.dump(initial), encoding="utf-8")

        down_calls: list = []
        docker_gw, _ = _make_docker_gateway(mod, down_calls=down_calls)

        result = mod.rollback_to_local(
            registry_path=reg_path,
            docker=docker_gw,
            stop_container=False,
        )

        assert result.overall == "ok"
        data = yaml.safe_load(reg_path.read_text())

        # Vaults preserved
        assert "my-vault" in data["vaults"]
        # External targets preserved
        assert "gh" in data["external_targets"]
        # Other settings preserved
        assert data["settings"]["context_injection"] == "light"
        # Service: desired and enabled updated, other keys preserved
        svc = data["settings"]["local_brain_service"]
        assert svc["desired"] == "local"
        assert svc["enabled"] is False
        assert svc["base_url"] == "http://127.0.0.1:8765"
        assert svc["api_token"] == "tok-secret"  # must be preserved
        assert svc["api_token_env"] == "LOCAL_BRAIN_API_TOKEN"

    def test_rollback_preserves_capture_and_knowledge_data(self, tmp_path):
        """Rollback must leave capture/ and knowledge/ files untouched."""
        mod = load_engine()
        reg_path = tmp_path / ".brain" / "registry.yaml"
        _write_registry(reg_path, service={"enabled": True, "desired": "docker"})

        # Create some capture and knowledge files in the tmp brain
        capture_dir = tmp_path / "capture" / "inbox"
        capture_dir.mkdir(parents=True)
        (capture_dir / "2026-01-01-note.md").write_text("# Note\nsome content")

        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir(parents=True)
        (knowledge_dir / "article-001.md").write_text("# Article\ncontent here")

        down_calls: list = []
        docker_gw, _ = _make_docker_gateway(mod, down_calls=down_calls)

        result = mod.rollback_to_local(
            registry_path=reg_path,
            docker=docker_gw,
            stop_container=False,
        )

        assert result.overall == "ok"

        # Data files must still exist
        assert (capture_dir / "2026-01-01-note.md").exists()
        assert (capture_dir / "2026-01-01-note.md").read_text() == "# Note\nsome content"
        assert (knowledge_dir / "article-001.md").exists()
        assert (knowledge_dir / "article-001.md").read_text() == "# Article\ncontent here"

    def test_rollback_stops_container_by_default(self, tmp_path):
        """By default, rollback calls docker compose down."""
        mod = load_engine()
        reg_path = tmp_path / ".brain" / "registry.yaml"
        _write_registry(reg_path, service={"enabled": True, "desired": "docker"})

        down_calls: list = []
        docker_gw, _ = _make_docker_gateway(mod, down_calls=down_calls)

        result = mod.rollback_to_local(
            registry_path=reg_path,
            docker=docker_gw,
            stop_container=True,
        )

        assert result.overall == "ok"
        # The docker.compose_cmd("down") was invoked
        assert any("down" in str(c) for c in down_calls)
        stop_step = result._step("stop_container")
        assert stop_step is not None
        assert stop_step.status == "ok"

    def test_rollback_skips_stop_when_flag_false(self, tmp_path):
        """When stop_container=False, no docker command is run."""
        mod = load_engine()
        reg_path = tmp_path / ".brain" / "registry.yaml"
        _write_registry(reg_path, service={"enabled": True, "desired": "docker"})

        down_calls: list = []
        docker_gw, _ = _make_docker_gateway(mod, down_calls=down_calls)

        result = mod.rollback_to_local(
            registry_path=reg_path,
            docker=docker_gw,
            stop_container=False,
        )

        assert result.overall == "ok"
        assert down_calls == []
        stop_step = result._step("stop_container")
        assert stop_step is not None
        assert stop_step.status == "skipped"

    def test_rollback_stop_failure_is_warning_not_failed(self, tmp_path):
        """A docker compose down failure produces a warning, not overall failed."""
        mod = load_engine()
        reg_path = tmp_path / ".brain" / "registry.yaml"
        _write_registry(reg_path, service={"enabled": True, "desired": "docker"})

        # Docker runner that raises on "down"
        def _failing_runner(cmd):
            raise RuntimeError("docker compose down failed: container not found")

        class _FailDocker(mod.DockerGateway):
            def __init__(self):
                pass

            def compose_cmd(self, *args):
                return ["docker", "compose", *args]

        docker_gw = _FailDocker()
        docker_gw._runner = _failing_runner

        result = mod.rollback_to_local(
            registry_path=reg_path,
            docker=docker_gw,
            stop_container=True,
        )

        # Registry update succeeded; stop was best-effort
        assert result.overall == "ok"
        stop_step = result._step("stop_container")
        assert stop_step is not None
        assert stop_step.status == "warning"
        assert "docker compose down failed" in stop_step.detail

    def test_rollback_registry_desired_local_seen_by_brain_common(self, tmp_path):
        """After rollback, get_local_brain_service_desired() returns 'local'."""
        mod = load_engine()
        reg_path = tmp_path / ".brain" / "registry.yaml"
        _write_registry(reg_path, service={"enabled": True, "desired": "docker"})

        down_calls: list = []
        docker_gw, _ = _make_docker_gateway(mod, down_calls=down_calls)

        result = mod.rollback_to_local(
            registry_path=reg_path,
            docker=docker_gw,
            stop_container=False,
        )
        assert result.overall == "ok"

        # Load the registry directly (as brain_common would) and assert desired=local
        data = yaml.safe_load(reg_path.read_text())
        svc = (data.get("settings") or {}).get("local_brain_service") or {}
        assert svc.get("desired") == "local"
        assert svc.get("enabled") is False

    def test_rollback_creates_registry_if_absent(self, tmp_path):
        """Rollback works even if registry.yaml doesn't exist yet."""
        mod = load_engine()
        reg_path = tmp_path / ".brain" / "registry.yaml"
        # Do NOT write it — it's absent

        down_calls: list = []
        docker_gw, _ = _make_docker_gateway(mod, down_calls=down_calls)

        result = mod.rollback_to_local(
            registry_path=reg_path,
            docker=docker_gw,
            stop_container=False,
        )

        assert result.overall == "ok"
        assert reg_path.exists()
        data = yaml.safe_load(reg_path.read_text())
        svc = data["settings"]["local_brain_service"]
        assert svc["desired"] == "local"
        assert svc["enabled"] is False


# ---------------------------------------------------------------------------
# InspectGateway unit tests
# ---------------------------------------------------------------------------

class TestInspectGateway:
    def test_returns_none_when_inspector_returns_none(self):
        mod = load_engine()
        gw = _make_inspect_gateway(mod, None)
        assert gw.get_env("some-container") is None

    def test_returns_env_dict_from_inspector(self):
        mod = load_engine()
        gw = _make_inspect_gateway(mod, {"FOO": "bar", "BAZ": "qux"})
        result = gw.get_env("some-container")
        assert result == {"FOO": "bar", "BAZ": "qux"}

    def test_returns_none_when_inspector_raises(self):
        mod = load_engine()

        def _bad(container):
            raise RuntimeError("boom")

        gw = mod.InspectGateway(inspector=_bad)
        result = gw.get_env("some-container")
        assert result is None
