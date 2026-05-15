from __future__ import annotations

from argparse import Namespace

import pytest

from fritz_local_brain import cli


def test_cli_resolves_base_url_and_token_from_registry(tmp_path, monkeypatch) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        "settings:\n"
        "  local_brain_service:\n"
        "    enabled: true\n"
        "    base_url: http://127.0.0.1:9999\n"
        "    api_token_env: TEST_LOCAL_BRAIN_TOKEN\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_LOCAL_BRAIN_TOKEN", "secret-token")

    connection = cli.resolve_connection(
        Namespace(base_url=None, token=None, token_env=None, registry=registry, allow_remote=False)
    )

    assert connection.base_url == "http://127.0.0.1:9999"
    assert connection.token == "secret-token"


def test_cli_explicit_args_override_registry(tmp_path, monkeypatch) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        "settings:\n"
        "  local_brain_service:\n"
        "    base_url: http://127.0.0.1:9999\n"
        "    api_token_env: TEST_LOCAL_BRAIN_TOKEN\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_LOCAL_BRAIN_TOKEN", "registry-token")

    connection = cli.resolve_connection(
        Namespace(base_url="http://127.0.0.1:8888", token="explicit-token", token_env=None, registry=registry, allow_remote=False)
    )

    assert connection.base_url == "http://127.0.0.1:8888"
    assert connection.token == "explicit-token"


def test_cli_request_uses_resolved_authorization_header(tmp_path, monkeypatch) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        "settings:\n"
        "  local_brain_service:\n"
        "    base_url: http://127.0.0.1:9999\n"
        "    api_token_env: TEST_LOCAL_BRAIN_TOKEN\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_LOCAL_BRAIN_TOKEN", "secret-token")
    captured = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return b'{"ok": true}'

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["authorization"] = req.headers.get("Authorization")
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(cli.request, "urlopen", fake_urlopen)

    result = cli._request(
        Namespace(base_url=None, token=None, token_env=None, registry=registry),
        "GET",
        "/v1/status",
    )

    assert result == {"ok": True}
    assert captured == {
        "url": "http://127.0.0.1:9999/v1/status",
        "authorization": "Bearer secret-token",
        "timeout": 120,
    }


def test_cli_rejects_remote_service_url_without_allow_remote(tmp_path) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        "settings:\n"
        "  local_brain_service:\n"
        "    base_url: https://example.invalid\n"
        "    allow_remote: false\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="Remote Local Brain URL is not allowed"):
        cli.resolve_connection(Namespace(base_url=None, token=None, token_env=None, registry=registry, allow_remote=False))


def test_cli_rejects_malformed_loopback_netloc() -> None:
    with pytest.raises(SystemExit, match="Invalid Local Brain base URL"):
        cli.resolve_connection(
            Namespace(
                base_url="http://localhost:8765;touch",
                token=None,
                token_env=None,
                registry=None,
                allow_remote=False,
            )
        )
