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


def test_cli_resolves_literal_token_from_registry_when_env_missing(tmp_path, monkeypatch) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        "settings:\n"
        "  local_brain_service:\n"
        "    enabled: true\n"
        "    base_url: http://127.0.0.1:9999\n"
        "    api_token_env: TEST_LOCAL_BRAIN_TOKEN\n"
        "    api_token: registry-token\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TEST_LOCAL_BRAIN_TOKEN", raising=False)

    connection = cli.resolve_connection(
        Namespace(base_url=None, token=None, token_env=None, registry=registry, allow_remote=False)
    )

    assert connection.base_url == "http://127.0.0.1:9999"
    assert connection.token == "registry-token"


def test_cli_registry_literal_token_overrides_stale_env(tmp_path, monkeypatch) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        "settings:\n"
        "  local_brain_service:\n"
        "    base_url: http://127.0.0.1:9999\n"
        "    api_token_env: TEST_LOCAL_BRAIN_TOKEN\n"
        "    api_token: registry-token\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_LOCAL_BRAIN_TOKEN", "stale-env-token")

    connection = cli.resolve_connection(
        Namespace(base_url=None, token=None, token_env=None, registry=registry, allow_remote=False)
    )

    assert connection.token == "registry-token"


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


def test_cli_dispatch_search_uses_search_endpoint(monkeypatch) -> None:
    captured = {}

    def fake_request(args, method, path, body=None):
        captured.update({"method": method, "path": path, "body": body})
        return {"ok": True}

    monkeypatch.setattr(cli, "_request", fake_request)

    result = cli._dispatch(Namespace(command="search", query="local brain", vault=None, limit=5))

    assert result == {"ok": True}
    assert captured == {"method": "POST", "path": "/v1/search/run", "body": {"query": "local brain", "vault": None, "limit": 5}}


def test_cli_dispatch_embeddings_index_uses_index_endpoint(monkeypatch) -> None:
    captured = {}

    def fake_request(args, method, path, body=None):
        captured.update({"method": method, "path": path, "body": body})
        return {"indexed": True}

    monkeypatch.setattr(cli, "_request", fake_request)

    result = cli._dispatch(Namespace(command="embeddings-index", force=True))

    assert result == {"indexed": True}
    assert captured == {"method": "POST", "path": "/v1/embeddings/index/run", "body": {"force": True}}


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


# ---------------------------------------------------------------------------
# Read-side attribution: every CLI request sends X-Brain-Agent (#179).
# ---------------------------------------------------------------------------

def _capture_request_header(monkeypatch, header: str) -> dict:
    captured: dict = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return b"{}"

    def fake_urlopen(req, timeout):
        # urllib title-cases header keys; req.headers.get is case-insensitive-ish
        # only via capitalize(), so fetch with the capitalized form.
        captured["value"] = req.headers.get(header)
        return _Response()

    monkeypatch.setattr(cli.request, "urlopen", fake_urlopen)
    return captured


def test_cli_request_sends_default_cli_agent_header(monkeypatch) -> None:
    monkeypatch.delenv("FRITZ_AGENT", raising=False)
    captured = _capture_request_header(monkeypatch, "X-brain-agent")

    cli._request(
        Namespace(base_url="http://127.0.0.1:8765", token=None, token_env=None, registry=None, allow_remote=False),
        "POST",
        "/v1/query/run",
        {"query": "hello"},
    )

    assert captured["value"] == "cli"


def test_cli_request_agent_header_from_fritz_agent_env(monkeypatch) -> None:
    monkeypatch.setenv("FRITZ_AGENT", "pi")
    captured = _capture_request_header(monkeypatch, "X-brain-agent")

    cli._request(
        Namespace(base_url="http://127.0.0.1:8765", token=None, token_env=None, registry=None, allow_remote=False),
        "POST",
        "/v1/search/run",
        {"query": "hello"},
    )

    assert captured["value"] == "pi"
