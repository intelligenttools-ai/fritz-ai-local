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

    # ------------------------------------------------------------------
    # approval_token written to .env
    # ------------------------------------------------------------------

    def test_approval_token_written_to_env(self, tmp_path):
        """--approval-token value must appear as APPROVAL_TOKEN in the written .env."""
        mod = load_engine()
        cfg = mod.ProvisionConfig(
            api_token="tok-appr-001",
            base_url="http://127.0.0.1:8765",
            approval_token="SECRET123",
        )
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"

        docker_gw, http_gw = self._make_provision_components(mod)

        with patch("urllib.request.urlopen", return_value=self._urlopen_ok()):
            with patch.object(mod, "_run_preflight", return_value=mod.StepResult("preflight", "ok", "ok")):
                result = mod.provision(
                    cfg,
                    env_path=env_path,
                    registry_path=reg_path,
                    docker=docker_gw,
                    http=http_gw,
                )

        assert result.overall in {"ok", "already_provisioned", "partial"}
        text = env_path.read_text()
        assert "APPROVAL_TOKEN=SECRET123" in text

    def test_approval_token_empty_by_default(self, tmp_path):
        """When no approval_token is supplied, APPROVAL_TOKEN in .env must be empty."""
        mod = load_engine()
        cfg = mod.ProvisionConfig(
            api_token="tok-appr-002",
            base_url="http://127.0.0.1:8765",
        )
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"

        docker_gw, http_gw = self._make_provision_components(mod)

        with patch("urllib.request.urlopen", return_value=self._urlopen_ok()):
            with patch.object(mod, "_run_preflight", return_value=mod.StepResult("preflight", "ok", "ok")):
                mod.provision(
                    cfg,
                    env_path=env_path,
                    registry_path=reg_path,
                    docker=docker_gw,
                    http=http_gw,
                )

        text = env_path.read_text()
        # Key must be present but empty (the env-map always writes it)
        assert "APPROVAL_TOKEN=" in text
        # Must not accidentally carry any value
        for line in text.splitlines():
            if line.startswith("APPROVAL_TOKEN="):
                assert line == "APPROVAL_TOKEN=", f"Expected empty APPROVAL_TOKEN, got: {line!r}"
                break

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


# ---------------------------------------------------------------------------
# --reconciliation-autonomy flag (Item A) and registry merge (Item B)
# ---------------------------------------------------------------------------

class TestReconciliationAutonomy:
    """Tests for --reconciliation-autonomy flag and RECONCILIATION_AUTONOMY env key."""

    def _make_provision_components(self, mod):
        build_calls: list[str] = []

        class _FakeDocker(mod.DockerGateway):
            def __init__(self):
                self.calls = build_calls

            def build(self):
                build_calls.append("build")

            def up(self):
                build_calls.append("up")

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

    def test_reconciliation_autonomy_propose_written_to_env(self, tmp_path):
        """provision --reconciliation-autonomy propose must write RECONCILIATION_AUTONOMY=propose to .env."""
        mod = load_engine()
        cfg = mod.ProvisionConfig(
            api_token="tok-ra-001",
            base_url="http://127.0.0.1:8765",
            reconciliation_autonomy="propose",
        )
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"
        docker_gw, http_gw = self._make_provision_components(mod)

        with patch("urllib.request.urlopen", return_value=self._urlopen_ok()):
            with patch.object(mod, "_run_preflight", return_value=mod.StepResult("preflight", "ok", "ok")):
                result = mod.provision(
                    cfg,
                    env_path=env_path,
                    registry_path=reg_path,
                    docker=docker_gw,
                    http=http_gw,
                )

        assert result.overall in {"ok", "already_provisioned", "partial"}
        text = env_path.read_text()
        assert "RECONCILIATION_AUTONOMY=propose" in text

    def test_reconciliation_autonomy_default_is_apply(self, tmp_path):
        """Default ProvisionConfig must write RECONCILIATION_AUTONOMY=apply to .env."""
        mod = load_engine()
        cfg = mod.ProvisionConfig(
            api_token="tok-ra-002",
            base_url="http://127.0.0.1:8765",
        )
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"
        docker_gw, http_gw = self._make_provision_components(mod)

        with patch("urllib.request.urlopen", return_value=self._urlopen_ok()):
            with patch.object(mod, "_run_preflight", return_value=mod.StepResult("preflight", "ok", "ok")):
                mod.provision(
                    cfg,
                    env_path=env_path,
                    registry_path=reg_path,
                    docker=docker_gw,
                    http=http_gw,
                )

        text = env_path.read_text()
        assert "RECONCILIATION_AUTONOMY=apply" in text

    def test_provision_help_lists_reconciliation_autonomy(self):
        """provision --help must mention --reconciliation-autonomy."""
        import argparse
        import io

        mod = load_engine()
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        mod.add_provision_subparser(sub)

        buf = io.StringIO()
        try:
            parser.parse_args(["provision", "--help"])
        except SystemExit:
            pass
        # Verify the flag exists by attempting to parse it
        ns = parser.parse_args(["provision", "--reconciliation-autonomy", "propose"])
        assert ns.reconciliation_autonomy == "propose"

        # Verify invalid value is rejected
        with pytest.raises(SystemExit):
            parser.parse_args(["provision", "--reconciliation-autonomy", "invalid"])


class TestTelemetryProvisioning:
    """Tests for telemetry flags/env keys (#183): _config_to_env_map, allowlists, argparse."""

    def test_env_map_defaults_include_telemetry_keys(self):
        mod = load_engine()
        cfg = mod.ProvisionConfig()
        env = mod._config_to_env_map(cfg, "tok-tel")
        assert env["TELEMETRY_ENABLED"] == "true"
        assert env["TELEMETRY_STORE_QUERY_TEXT"] == "true"
        assert env["TELEMETRY_RETENTION_DAYS"] == "90"

    def test_env_map_non_default_telemetry_values(self):
        mod = load_engine()
        cfg = mod.ProvisionConfig(
            telemetry_enabled=False,
            telemetry_store_query_text=False,
            telemetry_retention_days=7,
        )
        env = mod._config_to_env_map(cfg, "tok-tel")
        assert env["TELEMETRY_ENABLED"] == "false"
        assert env["TELEMETRY_STORE_QUERY_TEXT"] == "false"
        assert env["TELEMETRY_RETENTION_DAYS"] == "7"

    def test_telemetry_keys_in_managed_allowlists(self):
        mod = load_engine()
        for key in ("TELEMETRY_ENABLED", "TELEMETRY_STORE_QUERY_TEXT", "TELEMETRY_RETENTION_DAYS"):
            assert key in mod.PROVISION_ENV_KEYS
            assert key in mod.DRIFT_TRACKED_KEYS

    def test_argparse_telemetry_defaults_are_none_sentinels(self):
        """Flagless run yields None so _resolve_telemetry_from_args can preserve .env."""
        import argparse

        mod = load_engine()
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        mod.add_provision_subparser(sub)

        ns = parser.parse_args(["provision"])
        assert ns.telemetry_enabled is None
        assert ns.telemetry_store_query_text is None
        assert ns.telemetry_retention_days is None

    def test_argparse_telemetry_overrides_parse_into_config(self):
        import argparse

        mod = load_engine()
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        mod.add_provision_subparser(sub)

        ns = parser.parse_args([
            "provision",
            "--no-telemetry-enabled",
            "--no-telemetry-store-query-text",
            "--telemetry-retention-days",
            "14",
        ])
        assert ns.telemetry_enabled is False
        assert ns.telemetry_store_query_text is False
        assert ns.telemetry_retention_days == 14

    def test_reconfigure_argparse_has_telemetry_flags(self):
        import argparse

        mod = load_engine()
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        mod.add_reconfigure_subparser(sub)

        ns = parser.parse_args(["reconfigure", "--no-telemetry-enabled", "--telemetry-retention-days", "5"])
        assert ns.telemetry_enabled is False
        assert ns.telemetry_retention_days == 5


class TestTelemetryRedeployStability:
    """#183 acceptance: a flagless redeploy must NOT flip an operator's TELEMETRY_* settings."""

    def _ns(self, **overrides):
        # Default sentinels = flagless run (nothing passed).
        base = dict(telemetry_enabled=None, telemetry_store_query_text=None, telemetry_retention_days=None)
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_flagless_redeploy_preserves_disabled_telemetry(self, tmp_path):
        """Operator previously set TELEMETRY_*=false; a flagless provision keeps them false."""
        mod = load_engine()
        env_path = tmp_path / ".env"
        env_path.write_text(
            "TELEMETRY_ENABLED=false\n"
            "TELEMETRY_STORE_QUERY_TEXT=false\n"
            "TELEMETRY_RETENTION_DAYS=30\n",
            encoding="utf-8",
        )

        # Resolve as cli_provision would on a flagless run.
        enabled, store_text, retention = mod._resolve_telemetry_from_args(self._ns(), env_path)
        assert enabled is False
        assert store_text is False
        assert retention == 30

        # Full chain: cfg -> env map -> merge back to disk must NOT flip to true.
        cfg = mod.ProvisionConfig(
            telemetry_enabled=enabled,
            telemetry_store_query_text=store_text,
            telemetry_retention_days=retention,
        )
        merged = mod._merge_dotenv(env_path.read_text(), mod._config_to_env_map(cfg, "tok-x"))
        env_path.write_text(merged, encoding="utf-8")

        parsed = mod._parse_dotenv(env_path.read_text())
        assert parsed["TELEMETRY_ENABLED"] == "false"
        assert parsed["TELEMETRY_STORE_QUERY_TEXT"] == "false"
        assert parsed["TELEMETRY_RETENTION_DAYS"] == "30"

    def test_explicit_flag_overrides_existing_env(self, tmp_path):
        """Explicitly passing --telemetry-store-query-text sets true even if .env had false."""
        mod = load_engine()
        env_path = tmp_path / ".env"
        env_path.write_text("TELEMETRY_STORE_QUERY_TEXT=false\nTELEMETRY_ENABLED=false\n", encoding="utf-8")

        ns = self._ns(telemetry_store_query_text=True, telemetry_enabled=True, telemetry_retention_days=7)
        enabled, store_text, retention = mod._resolve_telemetry_from_args(ns, env_path)
        assert enabled is True
        assert store_text is True
        assert retention == 7

    def test_first_run_no_env_uses_defaults(self, tmp_path):
        """No existing .env -> defaults True / True / 90."""
        mod = load_engine()
        env_path = tmp_path / ".env"  # does not exist
        assert not env_path.exists()

        enabled, store_text, retention = mod._resolve_telemetry_from_args(self._ns(), env_path)
        assert enabled is True
        assert store_text is True
        assert retention == 90

    def test_partial_env_falls_back_to_defaults_for_missing_keys(self, tmp_path):
        """Keys absent from .env fall back to defaults; present keys are preserved."""
        mod = load_engine()
        env_path = tmp_path / ".env"
        env_path.write_text("TELEMETRY_ENABLED=false\n", encoding="utf-8")  # only one key set

        enabled, store_text, retention = mod._resolve_telemetry_from_args(self._ns(), env_path)
        assert enabled is False        # preserved from .env
        assert store_text is True      # absent -> default
        assert retention == 90         # absent -> default


class TestDriftWatcherProvisioning:
    """#217: drift-watcher flag round-trips through env map, allowlists, argparse,
    and provision() installs/uninstalls via the injected DriftWatcherGateway."""

    def _make_drift_gateway(self, mod, *, already_active: bool = False):
        events: list[str] = []

        def _checker() -> bool:
            return already_active

        def _installer() -> None:
            events.append("install")

        def _uninstaller() -> None:
            events.append("uninstall")

        gw = mod.DriftWatcherGateway(
            installer=_installer, uninstaller=_uninstaller, checker=_checker
        )
        return gw, events

    def test_env_map_default_enables_drift_watcher(self):
        mod = load_engine()
        env = mod._config_to_env_map(mod.ProvisionConfig(), "tok-d")
        assert env["DRIFT_WATCHER_ENABLED"] == "true"

    def test_env_map_disabled_drift_watcher(self):
        mod = load_engine()
        env = mod._config_to_env_map(mod.ProvisionConfig(drift_watcher_enabled=False), "tok-d")
        assert env["DRIFT_WATCHER_ENABLED"] == "false"

    def test_drift_key_in_managed_allowlists(self):
        mod = load_engine()
        assert "DRIFT_WATCHER_ENABLED" in mod.PROVISION_ENV_KEYS
        assert "DRIFT_WATCHER_ENABLED" in mod.DRIFT_TRACKED_KEYS

    def test_argparse_drift_watcher_defaults_true(self):
        import argparse

        mod = load_engine()
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        mod.add_provision_subparser(sub)

        ns = parser.parse_args(["provision"])
        assert ns.drift_watcher is True

    def test_argparse_no_drift_watcher_disables(self):
        import argparse

        mod = load_engine()
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        mod.add_provision_subparser(sub)

        ns = parser.parse_args(["provision", "--no-drift-watcher"])
        assert ns.drift_watcher is False

    def test_run_drift_watcher_installs_when_enabled(self):
        mod = load_engine()
        gw, events = self._make_drift_gateway(mod, already_active=False)
        step = mod._run_drift_watcher(mod.ProvisionConfig(drift_watcher_enabled=True), gw)
        assert step.name == "drift_watcher"
        assert step.status == "ok"
        assert events == ["install"]

    def test_run_drift_watcher_skips_when_already_active(self):
        mod = load_engine()
        gw, events = self._make_drift_gateway(mod, already_active=True)
        step = mod._run_drift_watcher(mod.ProvisionConfig(drift_watcher_enabled=True), gw)
        assert step.status == "skipped"
        assert events == []  # is_active short-circuits install

    def test_run_drift_watcher_uninstalls_when_disabled(self):
        mod = load_engine()
        gw, events = self._make_drift_gateway(mod, already_active=True)
        step = mod._run_drift_watcher(mod.ProvisionConfig(drift_watcher_enabled=False), gw)
        assert step.status == "skipped"  # uninstall is not a "change to provisioned state"
        assert events == ["uninstall"]

    def test_run_drift_watcher_disabled_and_absent_is_noop(self):
        mod = load_engine()
        gw, events = self._make_drift_gateway(mod, already_active=False)
        step = mod._run_drift_watcher(mod.ProvisionConfig(drift_watcher_enabled=False), gw)
        assert step.status == "skipped"
        assert events == []

    def test_run_drift_watcher_failure_is_warning_not_exception(self):
        mod = load_engine()

        def _boom() -> None:
            raise RuntimeError("simulated drift-watcher error")

        gw = mod.DriftWatcherGateway(installer=_boom, checker=lambda: False)
        step = mod._run_drift_watcher(mod.ProvisionConfig(drift_watcher_enabled=True), gw)
        assert step.status == "warning"
        assert "simulated drift-watcher error" in step.detail

    def test_non_darwin_install_degrades_to_warning_not_abort(self):
        """FIX 1: an unsupported-OS install raises a normal RuntimeError (not
        SystemExit) so _run_drift_watcher degrades it to a warning StepResult
        instead of aborting the whole provision run."""
        mod = load_engine()

        def _unsupported() -> None:
            raise RuntimeError("Drift-watcher is not implemented for Linux")

        gw = mod.DriftWatcherGateway(installer=_unsupported, checker=lambda: False)
        step = mod._run_drift_watcher(mod.ProvisionConfig(drift_watcher_enabled=True), gw)
        assert step.name == "drift_watcher"
        assert step.status == "warning"
        assert "not implemented for Linux" in step.detail

    def test_provision_completes_when_drift_watcher_install_fails(self, tmp_path):
        """FIX 1 integration: a failing drift-watcher install must NOT abort
        provision — the run finishes and other steps are still present."""
        mod = load_engine()

        def _boom() -> None:
            raise RuntimeError("Drift-watcher is not implemented for Linux")

        drift_gw = mod.DriftWatcherGateway(installer=_boom, checker=lambda: False)
        cfg = mod.ProvisionConfig(
            api_token="tok-drift-abort",
            base_url="http://127.0.0.1:8765",
            drift_watcher_enabled=True,
        )
        env_path = tmp_path / ".env"
        reg_path = tmp_path / ".brain" / "registry.yaml"

        docker_gw = _StubDocker()
        http_gw = _StubHttp()

        with patch("urllib.request.urlopen", return_value=_urlopen_ok_response()):
            with patch.object(mod, "_run_preflight", return_value=mod.StepResult("preflight", "ok", "ok")):
                result = mod.provision(
                    cfg,
                    env_path=env_path,
                    registry_path=reg_path,
                    docker=docker_gw,
                    http=http_gw,
                    drift_watcher=drift_gw,
                )

        drift_step = result._step("drift_watcher")
        assert drift_step is not None and drift_step.status == "warning"
        # Provision ran to completion (env step present) and did not raise.
        assert result._step("write_env") is not None
        assert result.overall in {"partial", "ok", "already_provisioned"}


class _StubDocker:
    def build(self):
        pass

    def up(self):
        pass


class _StubHttp:
    def get(self, url, headers=None):
        return (200, b'{"ok":true}')


def _urlopen_ok_response():
    import json

    mock_response = MagicMock()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_response.status = 200
    mock_response.read.return_value = json.dumps({}).encode()
    return mock_response


class TestRegistryMergePreservesSubKeys:
    """Item B: _run_write_registry must MERGE existing local_brain_service sub-keys."""

    def _registry_path(self, tmp_path: Path) -> Path:
        return tmp_path / ".brain" / "registry.yaml"

    def test_existing_local_brain_service_subkeys_preserved_after_provision(self, tmp_path):
        """Manually-set sub-keys not managed by provision (e.g. desired, allow_remote)
        must survive a provision write without being clobbered."""
        mod = load_engine()
        cfg = mod.ProvisionConfig(base_url="http://127.0.0.1:8765")
        reg_path = self._registry_path(tmp_path)
        reg_path.parent.mkdir(parents=True, exist_ok=True)

        existing = {
            "version": 1,
            "vaults": {},
            "settings": {
                "local_brain_service": {
                    # These sub-keys are NOT written by provision — they should be preserved
                    "desired": "docker",
                    "allow_remote": False,
                },
            },
        }
        reg_path.write_text(yaml.dump(existing), encoding="utf-8")

        step, changed = mod._run_write_registry(cfg, "tok-merge-001", reg_path)
        assert step.status == "ok"
        assert changed

        data = yaml.safe_load(reg_path.read_text())
        svc = data["settings"]["local_brain_service"]

        # Newly written keys must be present
        assert svc["enabled"] is True
        assert svc["api_token"] == "tok-merge-001"
        assert svc["base_url"] == "http://127.0.0.1:8765"

        # Manually-set sub-keys not managed by provision must be preserved
        assert svc["desired"] == "docker", "desired sub-key was clobbered"
        assert svc["allow_remote"] is False, "allow_remote sub-key was clobbered"
