from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import brain_service_request  # noqa: E402


class _Response:
    status = 200

    def __init__(self, body: str, headers=None):
        self.body = body.encode()
        self.offset = 0
        self.headers = headers or {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, size=-1):
        if size < 0:
            size = len(self.body) - self.offset
        chunk = self.body[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


def test_search_calls_semantic_http_api_with_registry_token(monkeypatch):
    seen = {}
    monkeypatch.setattr(brain_service_request, "get_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_service_request, "get_local_brain_api_token", lambda: "secret-token")

    def fake_urlopen(req, timeout):
        seen.update(url=req.full_url, headers=dict(req.header_items()), body=json.loads(req.data), timeout=timeout)
        return _Response('{"matches":[]}')

    monkeypatch.setattr(brain_service_request, "_open", fake_urlopen)

    assert brain_service_request.call("search", {"query": "proxmox debian"}) == '{"matches":[]}'
    assert seen["url"] == "http://127.0.0.1:8765/v1/search/run"
    assert seen["body"] == {"query": "proxmox debian"}
    assert seen["headers"]["Authorization"] == "Bearer secret-token"
    assert seen["headers"]["X-brain-agent"] == "pi"


def test_tokenless_service_omits_authorization_header(monkeypatch):
    seen = {}
    monkeypatch.setattr(brain_service_request, "get_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_service_request, "get_local_brain_api_token", lambda: None)

    def fake_urlopen(req, timeout):
        seen["headers"] = dict(req.header_items())
        return _Response('{"matches":[]}')

    monkeypatch.setattr(brain_service_request, "_open", fake_urlopen)

    assert brain_service_request.call("search", {"query": "proxmox"}) == '{"matches":[]}'
    assert "Authorization" not in seen["headers"]


def test_response_size_limit_checks_header_and_stream(monkeypatch):
    monkeypatch.setattr(brain_service_request, "get_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_service_request, "get_local_brain_api_token", lambda: None)
    monkeypatch.setattr(brain_service_request, "MAX_RESPONSE_BYTES", 8)
    monkeypatch.setattr(brain_service_request, "READ_CHUNK_BYTES", 4)

    monkeypatch.setattr(
        brain_service_request,
        "_open",
        lambda _req, _timeout: _Response("{}", {"Content-Type": "application/json", "Content-Length": "9"}),
    )
    with pytest.raises(RuntimeError, match="exceeds 8 bytes"):
        brain_service_request.call("search", {"query": "x"})

    monkeypatch.setattr(
        brain_service_request,
        "_open",
        lambda _req, _timeout: _Response("123456789", {"Content-Type": "application/json"}),
    )
    with pytest.raises(RuntimeError, match="exceeds 8 bytes"):
        brain_service_request.call("search", {"query": "x"})


def test_api_failure_is_sanitized(monkeypatch):
    monkeypatch.setattr(brain_service_request, "get_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_service_request, "get_local_brain_api_token", lambda: "secret-token")

    def fail(_req, timeout):
        raise brain_service_request.error.HTTPError("url", 401, "contains secret-token", {}, None)

    monkeypatch.setattr(brain_service_request, "_open", fail)

    with pytest.raises(RuntimeError, match="HTTP 401") as exc:
        brain_service_request.call("search", {"query": "x"})
    assert "secret-token" not in str(exc.value)


def test_api_does_not_follow_redirects_with_bearer_token(monkeypatch):
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            paths.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/token-leak")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(brain_service_request, "get_local_brain_base_url", lambda: f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(brain_service_request, "get_local_brain_api_token", lambda: "secret-token")
    try:
        with pytest.raises(RuntimeError, match="HTTP 302"):
            brain_service_request.call("search", {"query": "x"})
    finally:
        server.shutdown()
        thread.join()
    assert paths == ["/v1/search/run"]


def test_api_rejects_non_loopback_http(monkeypatch):
    monkeypatch.setattr(brain_service_request, "get_local_brain_base_url", lambda: "http://brain.example.test")
    monkeypatch.setattr(brain_service_request, "get_local_brain_api_token", lambda: "secret-token")

    with pytest.raises(RuntimeError, match="requires HTTPS"):
        brain_service_request.call("search", {"query": "x"})


@pytest.mark.parametrize(
    "base_url",
    [
        "http://brain.example.test",
        "https://user:password@brain.example.test",
        "https://brain.example.test?",
        "https://brain.example.test#",
        "https://brain.example.test/#",
    ],
)
def test_public_config_rejects_insecure_or_credential_bearing_url(monkeypatch, base_url):
    monkeypatch.setattr(brain_service_request, "get_local_brain_base_url", lambda: base_url)
    monkeypatch.setattr(brain_service_request, "get_local_brain_api_token", lambda: "secret-token")
    monkeypatch.setattr(
        brain_service_request,
        "get_local_brain_service_config",
        lambda: {"api_token_env": "LOCAL_BRAIN_API_TOKEN"},
    )

    with pytest.raises(RuntimeError):
        brain_service_request.public_config()


def test_public_config_contains_no_token(monkeypatch):
    monkeypatch.setenv("LOCAL_BRAIN_API_TOKEN", "secret-token")
    monkeypatch.setattr(brain_service_request, "get_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_service_request, "get_local_brain_api_token", lambda: "secret-token")
    monkeypatch.setattr(
        brain_service_request,
        "get_local_brain_service_config",
        lambda: {"api_token": "secret-token", "api_token_env": "LOCAL_BRAIN_API_TOKEN"},
    )

    config = brain_service_request.public_config()

    assert config == {
        "base_url": "http://127.0.0.1:8765",
        "api_token_env": "LOCAL_BRAIN_API_TOKEN",
        "has_token": True,
        "token_env_ready": True,
    }
    assert "secret-token" not in json.dumps(config)


@pytest.mark.parametrize("env_token", ["stale-token", "registry-token "])
def test_public_config_rejects_stale_or_whitespace_altered_token_env(monkeypatch, env_token):
    monkeypatch.setenv("LOCAL_BRAIN_API_TOKEN", env_token)
    monkeypatch.setattr(brain_service_request, "get_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_service_request, "get_local_brain_api_token", lambda: "registry-token")
    monkeypatch.setattr(
        brain_service_request,
        "get_local_brain_service_config",
        lambda: {"api_token": "registry-token", "api_token_env": "LOCAL_BRAIN_API_TOKEN"},
    )

    assert brain_service_request.public_config()["token_env_ready"] is False
