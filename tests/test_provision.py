"""Tests for scripts/provision_engine.py — PROV1 / issue #117.

All Docker and HTTP calls are mocked; no live infrastructure is touched.
The ~/.brain directory is redirected to a tmp_path to prevent any live write.
"""
from __future__ import annotations

import importlib.util
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# Loader helper
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
    # Register before exec so dataclass field resolution can find the module
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Preflight tests
# ---------------------------------------------------------------------------

class TestPreflight:
    def test_all_tools_present_port_free(self, monkeypatch):
        mod = load_engine()
        cfg = mod.ProvisionConfig()
        monkeypatch.setattr(mod.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(mod, "_port_free", lambda port, host="127.0.0.1": True)

        import subprocess
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: SimpleNamespace(returncode=0))

        step = mod._run_preflight(cfg)
        assert step.status == "ok"
        assert "docker" in step.detail

    def test_docker_missing_returns_failed(self, monkeypatch):
        mod = load_engine()
        cfg = mod.ProvisionConfig()
        monkeypatch.setattr(mod.shutil, "which", lambda name: None)
        monkeypatch.setattr(mod, "_port_free", lambda port, host="127.0.0.1": True)

        step = mod._run_preflight(cfg)
        assert step.status == "failed"
        assert "docker not found" in step.detail

    def test_compose_missing_returns_failed(self, monkeypatch):
        mod = load_engine()
        cfg = mod.ProvisionConfig()

        import subprocess

        def _which(name):
            return f"/usr/bin/{name}" if name == "docker" else (f"/usr/bin/{name}" if name in ("python3", "python") else None)

        monkeypatch.setattr(mod.shutil, "which", _which)
        monkeypatch.setattr(mod, "_port_free", lambda port, host="127.0.0.1": True)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **kwargs: (_ for _ in ()).throw(subprocess.CalledProcessError(1, cmd)),
        )

        step = mod._run_preflight(cfg)
        assert step.status == "failed"
        assert "docker compose" in step.detail

    def test_port_in_use_is_not_a_hard_failure(self, monkeypatch):
        """Port busy should surface as a detail note, not a failed status."""
        mod = load_engine()
        cfg = mod.ProvisionConfig()
        monkeypatch.setattr(mod.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(mod, "_port_free", lambda port, host="127.0.0.1": False)

        import subprocess
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: SimpleNamespace(returncode=0))

        step = mod._run_preflight(cfg)
        assert step.status == "ok"
        assert "in use" in step.detail

    def test_python_missing_returns_failed(self, monkeypatch):
        mod = load_engine()
        cfg = mod.ProvisionConfig()

        import subprocess

        def _which(name):
            if name == "docker":
                return "/usr/bin/docker"
            return None

        monkeypatch.setattr(mod.shutil, "which", _which)
        monkeypatch.setattr(mod, "_port_free", lambda port, host="127.0.0.1": True)
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: SimpleNamespace(returncode=0))

        step = mod._run_preflight(cfg)
        assert step.status == "failed"
        assert "python" in step.detail


# ---------------------------------------------------------------------------
# .env write / merge tests
# ---------------------------------------------------------------------------

class TestWriteEnv:
    def _cfg(self, api_token: str = "tok-abc123") -> object:
        mod = load_engine()
        cfg = mod.ProvisionConfig(api_token=api_token)
        return mod, cfg

    def test_creates_env_when_absent(self, tmp_path):
        mod, cfg = self._cfg()
        env_path = tmp_path / ".env"

        step, changed = mod._run_write_env(cfg, "tok-abc123", env_path)
        assert step.status == "ok"
        assert changed
        assert env_path.exists()
        text = env_path.read_text()
        assert "API_TOKEN=tok-abc123" in text
        assert "LLM_PROTOCOL=openai-compatible" in text

    def test_merges_without_discarding_custom_keys(self, tmp_path):
        mod, cfg = self._cfg()
        env_path = tmp_path / ".env"
        # Write an existing .env with a custom key we do NOT manage
        env_path.write_text(
            "MY_CUSTOM_KEY=hello\n"
            "# a comment line\n"
            "API_TOKEN=old-token\n",
            encoding="utf-8",
        )

        step, changed = mod._run_write_env(cfg, "tok-abc123", env_path)
        assert step.status == "ok"
        assert changed
        text = env_path.read_text()
        # Custom key must still be present
        assert "MY_CUSTOM_KEY=hello" in text
        # Comment must be preserved
        assert "# a comment line" in text
        # Token must be updated
        assert "API_TOKEN=tok-abc123" in text
        assert "old-token" not in text

    def test_skips_when_all_keys_already_set(self, tmp_path):
        mod, cfg = self._cfg(api_token="tok-abc123")
        env_path = tmp_path / ".env"

        # First write
        step1, _ = mod._run_write_env(cfg, "tok-abc123", env_path)
        assert step1.status == "ok"

        # Second write — identical inputs → should be skipped
        step2, changed2 = mod._run_write_env(cfg, "tok-abc123", env_path)
        assert step2.status == "skipped"
        assert not changed2

    def test_preserves_unmanaged_lines_and_comments(self, tmp_path):
        mod, cfg = self._cfg()
        env_path = tmp_path / ".env"
        env_path.write_text(
            "# Top comment\n"
            "UNRELATED_KEY=keep-me\n"
            "\n"
            "ANOTHER=value\n",
            encoding="utf-8",
        )

        step, _ = mod._run_write_env(cfg, "tok-abc123", env_path)
        assert step.status == "ok"
        text = env_path.read_text()
        assert "# Top comment" in text
        assert "UNRELATED_KEY=keep-me" in text
        assert "ANOTHER=value" in text


# ---------------------------------------------------------------------------
# registry.yaml write / merge tests
# ---------------------------------------------------------------------------

class TestWriteRegistry:
    def _registry_path(self, tmp_path: Path) -> Path:
        return tmp_path / ".brain" / "registry.yaml"

    def test_creates_minimal_registry_when_absent(self, tmp_path):
        mod = load_engine()
        cfg = mod.ProvisionConfig(base_url="http://127.0.0.1:8765")
        reg_path = self._registry_path(tmp_path)

        step, changed = mod._run_write_registry(cfg, "tok-abc123", reg_path)
        assert step.status == "ok"
        assert changed
        data = yaml.safe_load(reg_path.read_text())
        assert data["version"] == 1
        assert "vaults" in data
        svc = data["settings"]["local_brain_service"]
        assert svc["enabled"] is True
        assert svc["base_url"] == "http://127.0.0.1:8765"
        assert svc["api_token"] == "tok-abc123"

    def test_preserves_vaults_and_external_targets(self, tmp_path):
        mod = load_engine()
        cfg = mod.ProvisionConfig(base_url="http://127.0.0.1:8765")
        reg_path = self._registry_path(tmp_path)
        reg_path.parent.mkdir(parents=True, exist_ok=True)

        existing = {
            "version": 1,
            "vaults": {"my-vault": {"path": "~/Notes", "status": "active"}},
            "external_targets": {"github": {"url": "https://github.com/example/repo"}},
            "settings": {"context_injection": "light"},
        }
        reg_path.write_text(yaml.dump(existing), encoding="utf-8")

        step, changed = mod._run_write_registry(cfg, "tok-abc123", reg_path)
        assert step.status == "ok"
        assert changed
        data = yaml.safe_load(reg_path.read_text())
        # Must preserve vaults
        assert "my-vault" in data["vaults"]
        # Must preserve external_targets
        assert "github" in data["external_targets"]
        # Must preserve other settings keys
        assert data["settings"]["context_injection"] == "light"
        # Must write local_brain_service
        assert data["settings"]["local_brain_service"]["enabled"] is True

    def test_skips_when_already_set(self, tmp_path):
        mod = load_engine()
        cfg = mod.ProvisionConfig(base_url="http://127.0.0.1:8765")
        reg_path = self._registry_path(tmp_path)

        # First write
        step1, _ = mod._run_write_registry(cfg, "tok-abc123", reg_path)
        assert step1.status == "ok"

        # Second write — identical → skipped
        step2, changed2 = mod._run_write_registry(cfg, "tok-abc123", reg_path)
        assert step2.status == "skipped"
        assert not changed2

    def test_does_not_clobber_other_settings_keys(self, tmp_path):
        mod = load_engine()
        cfg = mod.ProvisionConfig(base_url="http://127.0.0.1:8765")
        reg_path = self._registry_path(tmp_path)
        reg_path.parent.mkdir(parents=True, exist_ok=True)

        existing = {
            "version": 1,
            "vaults": {},
            "settings": {
                "brain_store_path": "~/custom_brain",
                "reconciliation_autonomy": "propose",
            },
        }
        reg_path.write_text(yaml.dump(existing), encoding="utf-8")

        mod._run_write_registry(cfg, "tok-abc123", reg_path)
        data = yaml.safe_load(reg_path.read_text())
        assert data["settings"]["brain_store_path"] == "~/custom_brain"
        assert data["settings"]["reconciliation_autonomy"] == "propose"


# ---------------------------------------------------------------------------
# Verify (HTTP health + LLM probe) tests
# ---------------------------------------------------------------------------

class TestVerify:
    def _http_ok(self, probe_code: int = 200, probe_body: dict | None = None):
        """Return an HttpGateway that says service is healthy and LLM responds with probe_code."""
        import json

        probe_body_bytes = json.dumps(probe_body or {}).encode()

        mod = load_engine()

        class _FakeHttp(mod.HttpGateway):
            def get(self, url, headers=None):
                if "/health" in url or "/v1/status" in url:
                    return (200, b'{"ok":true}')
                return (probe_code, probe_body_bytes)

        return _FakeHttp()

    def test_healthy_service_with_llm_reachable(self, tmp_path):
        mod = load_engine()
        cfg = mod.ProvisionConfig(base_url="http://127.0.0.1:8765")

        import json
        from urllib import error as urllib_error

        # Patch urlopen so the compile probe returns 200
        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({}).encode()

        with patch("urllib.request.urlopen", return_value=mock_response):
            step = mod._run_verify(
                cfg,
                "tok-abc123",
                self._http_ok(),
                max_wait=0.1,
                poll_interval=0.05,
            )

        assert step.status == "ok"
        assert "healthy" in step.detail

    def test_llm_not_reachable_502(self, tmp_path):
        mod = load_engine()
        cfg = mod.ProvisionConfig(base_url="http://127.0.0.1:8765")

        import json
        from urllib import error as urllib_error

        def _urlopen_502(req, timeout=None):
            raise urllib_error.HTTPError(
                req.full_url, 502, "Bad Gateway", {}, None
            )

        with patch("urllib.request.urlopen", side_effect=_urlopen_502):
            step = mod._run_verify(
                cfg,
                "tok-abc123",
                self._http_ok(),
                max_wait=0.1,
                poll_interval=0.05,
            )

        assert step.status == "warning"
        assert "502" in step.detail or "LLM not reachable" in step.detail

    def test_service_not_reachable_at_all(self):
        mod = load_engine()
        cfg = mod.ProvisionConfig(base_url="http://127.0.0.1:9999")

        class _FailHttp(mod.HttpGateway):
            def get(self, url, headers=None):
                raise ConnectionRefusedError("no service")

        step = mod._run_verify(cfg, "tok-abc123", _FailHttp(), max_wait=0.1, poll_interval=0.01)
        assert step.status == "warning"
        assert "not reachable" in step.detail


# ---------------------------------------------------------------------------
# Full provision() integration (with mocked docker + HTTP)
# ---------------------------------------------------------------------------

class TestProvision:
    def _make_docker(self, mod, fail: bool = False):
        calls: list[str] = []

        class _FakeDocker(mod.DockerGateway):
            def __init__(self):
                # Don't call super().__init__() — we don't want real subprocess
                self.calls = calls

            def build(self):
                if fail:
                    raise RuntimeError("docker build failed")
                calls.append("build")

            def up(self):
                calls.append("up")

        gw = _FakeDocker()
        return gw, calls

    def _make_http_ok(self, mod):
        import json

        class _FakeHttp(mod.HttpGateway):
            def get(self, url, headers=None):
                return (200, b'{"ok":true}')

        return _FakeHttp()

    def test_full_provision_creates_env_and_registry(self, tmp_path):
        mod = load_engine()
        cfg = mod.ProvisionConfig(
            api_token="tok-test-001",
            base_url="http://127.0.0.1:8765",
        )
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"

        docker_gw, calls = self._make_docker(mod)
        http_gw = self._make_http_ok(mod)

        import json
        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({}).encode()

        with patch("urllib.request.urlopen", return_value=mock_response):
            with patch.object(mod, "_run_preflight", return_value=mod.StepResult("preflight", "ok", "all ok")):
                result = mod.provision(cfg, env_path=env_path, registry_path=reg_path, docker=docker_gw, http=http_gw)

        assert result.overall in {"ok", "already_provisioned"}
        assert env_path.exists()
        assert reg_path.exists()
        assert "build" in calls
        assert "up" in calls

    def test_idempotent_second_run_reports_already_provisioned(self, tmp_path):
        """Calling provision twice with identical inputs → already_provisioned."""
        mod = load_engine()
        cfg = mod.ProvisionConfig(
            api_token="tok-idem-001",
            base_url="http://127.0.0.1:8765",
        )
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"

        import json
        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({}).encode()

        def _make_components():
            docker_gw, calls = self._make_docker(mod)
            http_gw = self._make_http_ok(mod)
            return docker_gw, http_gw, calls

        preflight_ok = mod.StepResult("preflight", "ok", "all ok")

        with patch("urllib.request.urlopen", return_value=mock_response):
            with patch.object(mod, "_run_preflight", return_value=preflight_ok):
                d1, h1, c1 = _make_components()
                r1 = mod.provision(cfg, env_path=env_path, registry_path=reg_path, docker=d1, http=h1)
                assert r1.overall in {"ok", "already_provisioned", "partial"}

                d2, h2, c2 = _make_components()
                r2 = mod.provision(cfg, env_path=env_path, registry_path=reg_path, docker=d2, http=h2)

        assert r2.overall == "already_provisioned"
        # No .env or registry writes on second run
        env_step2 = r2._step("write_env")
        reg_step2 = r2._step("write_registry")
        assert env_step2 is not None and env_step2.status == "skipped"
        assert reg_step2 is not None and reg_step2.status == "skipped"

    def test_preflight_failure_aborts_early(self, tmp_path):
        mod = load_engine()
        cfg = mod.ProvisionConfig()
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"

        docker_gw, calls = self._make_docker(mod)
        http_gw = self._make_http_ok(mod)

        with patch.object(
            mod,
            "_run_preflight",
            return_value=mod.StepResult("preflight", "failed", "docker not found"),
        ):
            result = mod.provision(cfg, env_path=env_path, registry_path=reg_path, docker=docker_gw, http=http_gw)

        assert result.overall == "failed"
        # No docker calls, no file writes
        assert not calls
        assert not env_path.exists()
        assert not reg_path.exists()

    def test_docker_failure_gives_partial(self, tmp_path):
        mod = load_engine()
        cfg = mod.ProvisionConfig(api_token="tok-test", base_url="http://127.0.0.1:8765")
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"

        docker_gw, _ = self._make_docker(mod, fail=True)
        http_gw = self._make_http_ok(mod)

        with patch.object(mod, "_run_preflight", return_value=mod.StepResult("preflight", "ok", "ok")):
            result = mod.provision(cfg, env_path=env_path, registry_path=reg_path, docker=docker_gw, http=http_gw)

        assert result.overall in {"partial", "failed"}

    def test_api_token_auto_generated_when_absent(self, tmp_path):
        mod = load_engine()
        # No token supplied — should be auto-generated
        cfg = mod.ProvisionConfig(api_token="", base_url="http://127.0.0.1:8765")
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"

        docker_gw, _ = self._make_docker(mod)
        http_gw = self._make_http_ok(mod)

        import json
        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({}).encode()

        with patch("urllib.request.urlopen", return_value=mock_response):
            with patch.object(mod, "_run_preflight", return_value=mod.StepResult("preflight", "ok", "ok")):
                result = mod.provision(cfg, env_path=env_path, registry_path=reg_path, docker=docker_gw, http=http_gw)

        assert len(result.api_token) >= 32
        assert result.api_token.lower() not in mod.PLACEHOLDER_API_TOKENS
        # The generated token must be in the .env
        text = env_path.read_text()
        assert f"API_TOKEN={result.api_token}" in text

    def test_existing_token_preserved_when_config_empty(self, tmp_path):
        """If .env already has a valid token and config.api_token is empty, preserve it."""
        mod = load_engine()
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"

        # Pre-existing .env with a real token
        existing_token = "pre-existing-secret-token-xyz"
        env_path.write_text(f"API_TOKEN={existing_token}\n", encoding="utf-8")

        # No token in config
        cfg = mod.ProvisionConfig(api_token="", base_url="http://127.0.0.1:8765")
        docker_gw, _ = self._make_docker(mod)
        http_gw = self._make_http_ok(mod)

        import json
        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({}).encode()

        with patch("urllib.request.urlopen", return_value=mock_response):
            with patch.object(mod, "_run_preflight", return_value=mod.StepResult("preflight", "ok", "ok")):
                result = mod.provision(cfg, env_path=env_path, registry_path=reg_path, docker=docker_gw, http=http_gw)

        assert result.api_token == existing_token


# ---------------------------------------------------------------------------
# _port_free helper
# ---------------------------------------------------------------------------

class TestPortFree:
    def test_closed_port_is_free(self):
        mod = load_engine()
        # Pick a port that's very unlikely to be in use
        assert mod._port_free(19999) is True

    def test_bound_port_is_not_free(self):
        mod = load_engine()
        # Bind a port and verify it's detected as in-use
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        try:
            assert mod._port_free(port) is False
        finally:
            sock.close()


# ---------------------------------------------------------------------------
# _merge_dotenv unit tests
# ---------------------------------------------------------------------------

class TestMergeDotenv:
    def test_empty_existing_produces_correct_output(self):
        mod = load_engine()
        result = mod._merge_dotenv("", {"FOO": "bar", "BAZ": "qux"})
        assert "FOO=bar" in result
        assert "BAZ=qux" in result

    def test_replaces_in_place(self):
        mod = load_engine()
        existing = "FOO=old\nBAR=keep\n"
        result = mod._merge_dotenv(existing, {"FOO": "new"})
        assert "FOO=new" in result
        assert "FOO=old" not in result
        assert "BAR=keep" in result

    def test_appends_new_keys(self):
        mod = load_engine()
        existing = "FOO=old\n"
        result = mod._merge_dotenv(existing, {"NEWKEY": "val"})
        assert "NEWKEY=val" in result
        assert "FOO=old" in result

    def test_values_with_spaces_are_quoted(self):
        mod = load_engine()
        result = mod._merge_dotenv("", {"KEY": "hello world"})
        assert 'KEY="hello world"' in result

    def test_comments_are_preserved(self):
        mod = load_engine()
        existing = "# comment\nFOO=bar\n"
        result = mod._merge_dotenv(existing, {"FOO": "baz"})
        assert "# comment" in result
        assert "FOO=baz" in result


# ---------------------------------------------------------------------------
# Autostart step tests
# ---------------------------------------------------------------------------

class TestInstallAutostart:
    """Unit tests for _run_install_autostart and its integration in provision()."""

    def _make_autostart_gateway(self, mod, *, already_active: bool = False, fail: bool = False):
        """Build an AutostartGateway stub that records calls."""
        install_calls: list[str] = []
        check_calls: list[bool] = []

        def _checker() -> bool:
            result = already_active
            check_calls.append(result)
            return result

        def _installer() -> str:
            if fail:
                raise RuntimeError("simulated autostart install error")
            install_calls.append("install")
            return "ok"

        gw = mod.AutostartGateway(installer=_installer, checker=_checker)
        return gw, install_calls, check_calls

    # ------------------------------------------------------------------
    # _run_install_autostart unit tests
    # ------------------------------------------------------------------

    def test_not_requested_emits_skipped_step(self):
        mod = load_engine()
        cfg = mod.ProvisionConfig(install_autostart=False)
        gw, install_calls, _ = self._make_autostart_gateway(mod)

        step = mod._run_install_autostart(cfg, gw)

        assert step.name == "install_autostart"
        assert step.status == "skipped"
        assert "not requested" in step.detail
        # Installer must NOT be invoked
        assert install_calls == []

    def test_requested_and_not_active_emits_ok_and_calls_installer(self):
        mod = load_engine()
        cfg = mod.ProvisionConfig(install_autostart=True)
        gw, install_calls, _ = self._make_autostart_gateway(mod, already_active=False)

        step = mod._run_install_autostart(cfg, gw)

        assert step.name == "install_autostart"
        assert step.status == "ok"
        assert install_calls == ["install"]

    def test_requested_and_already_active_emits_skipped(self):
        mod = load_engine()
        cfg = mod.ProvisionConfig(install_autostart=True)
        gw, install_calls, _ = self._make_autostart_gateway(mod, already_active=True)

        step = mod._run_install_autostart(cfg, gw)

        assert step.name == "install_autostart"
        assert step.status == "skipped"
        assert "already installed" in step.detail
        # Installer not called because is_active() short-circuited it
        assert install_calls == []

    def test_install_failure_emits_warning_not_exception(self):
        mod = load_engine()
        cfg = mod.ProvisionConfig(install_autostart=True)
        gw, _, _ = self._make_autostart_gateway(mod, already_active=False, fail=True)

        step = mod._run_install_autostart(cfg, gw)

        assert step.name == "install_autostart"
        assert step.status == "warning"
        assert "simulated autostart install error" in step.detail

    # ------------------------------------------------------------------
    # Integration: autostart StepResult always present in provision()
    # ------------------------------------------------------------------

    def _make_provision_components(self, mod, *, docker_fail: bool = False):
        """Build all injectable components for a full provision() call."""
        build_calls: list[str] = []

        class _FakeDocker(mod.DockerGateway):
            def __init__(self):
                self.calls = build_calls

            def build(self):
                if docker_fail:
                    raise RuntimeError("docker fail")
                build_calls.append("build")

            def up(self):
                build_calls.append("up")

        import json

        class _FakeHttp(mod.HttpGateway):
            def get(self, url, headers=None):
                return (200, b'{"ok":true}')

        return _FakeDocker(), _FakeHttp()

    def _urlopen_ok(self):
        import json

        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({}).encode()
        return mock_response

    def test_autostart_false_step_always_present_in_provision(self, tmp_path):
        """Even when install_autostart=False, the autostart StepResult must be in the result."""
        mod = load_engine()
        cfg = mod.ProvisionConfig(
            api_token="tok-autos-001",
            base_url="http://127.0.0.1:8765",
            install_autostart=False,
        )
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"
        docker_gw, http_gw = self._make_provision_components(mod)
        autostart_gw, install_calls, _ = self._make_autostart_gateway(mod)

        with patch("urllib.request.urlopen", return_value=self._urlopen_ok()):
            with patch.object(mod, "_run_preflight", return_value=mod.StepResult("preflight", "ok", "ok")):
                result = mod.provision(
                    cfg,
                    env_path=env_path,
                    registry_path=reg_path,
                    docker=docker_gw,
                    http=http_gw,
                    autostart=autostart_gw,
                )

        autostart_step = result._step("install_autostart")
        assert autostart_step is not None, "autostart StepResult must always be present"
        assert autostart_step.status == "skipped"
        assert "not requested" in autostart_step.detail
        assert install_calls == []

    def test_autostart_true_invokes_installer_and_step_ok(self, tmp_path):
        """When install_autostart=True and not yet active, step is ok and installer is called."""
        mod = load_engine()
        cfg = mod.ProvisionConfig(
            api_token="tok-autos-002",
            base_url="http://127.0.0.1:8765",
            install_autostart=True,
        )
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"
        docker_gw, http_gw = self._make_provision_components(mod)
        autostart_gw, install_calls, _ = self._make_autostart_gateway(mod, already_active=False)

        with patch("urllib.request.urlopen", return_value=self._urlopen_ok()):
            with patch.object(mod, "_run_preflight", return_value=mod.StepResult("preflight", "ok", "ok")):
                result = mod.provision(
                    cfg,
                    env_path=env_path,
                    registry_path=reg_path,
                    docker=docker_gw,
                    http=http_gw,
                    autostart=autostart_gw,
                )

        autostart_step = result._step("install_autostart")
        assert autostart_step is not None
        assert autostart_step.status == "ok"
        assert install_calls == ["install"]

    def test_autostart_true_idempotent_second_run_skips(self, tmp_path):
        """Second provision() with autostart=True and already installed → step skipped, no error."""
        mod = load_engine()
        cfg = mod.ProvisionConfig(
            api_token="tok-autos-003",
            base_url="http://127.0.0.1:8765",
            install_autostart=True,
        )
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"
        # Simulate autostart already active (idempotent re-run)
        autostart_gw, install_calls, _ = self._make_autostart_gateway(mod, already_active=True)

        docker_gw, http_gw = self._make_provision_components(mod)

        with patch("urllib.request.urlopen", return_value=self._urlopen_ok()):
            with patch.object(mod, "_run_preflight", return_value=mod.StepResult("preflight", "ok", "ok")):
                result = mod.provision(
                    cfg,
                    env_path=env_path,
                    registry_path=reg_path,
                    docker=docker_gw,
                    http=http_gw,
                    autostart=autostart_gw,
                )

        autostart_step = result._step("install_autostart")
        assert autostart_step is not None
        assert autostart_step.status == "skipped"
        assert "already installed" in autostart_step.detail
        # Installer must NOT have been called
        assert install_calls == []
        # Overall must not be "failed"
        assert result.overall != "failed"
