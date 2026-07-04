"""MCP streamable-HTTP transport tests (#236).

Covers the two behaviours the HTTP transport adds on top of the existing stdio
MCP server:

- an unauthenticated MCP request is rejected with 401 before it reaches the MCP
  app (the mounted sub-app bypasses FastAPI's ``Depends(require_token)``);
- an authenticated ``initialize`` -> ``tools/list`` -> ``brain_search``
  ``tools/call`` round-trip succeeds over HTTP using only the Bearer header (no
  per-call ``api_token`` argument), driven in-process through the real ASGI app.

The transport is configured stateless with JSON responses, so each POST returns
a single JSON body and there is no unbounded SSE read to trip ``--timeout=60``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from fritz_local_brain import mcp_server
from fritz_local_brain.api import auth
from fritz_local_brain.app import create_app
from fritz_local_brain.config import Settings

_MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
_INIT_PARAMS = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1.0"}}


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path, LOCAL_BRAIN_API_TOKEN="secret")


def _rpc(method: str, params: dict | None = None, id: int = 1) -> dict:
    body: dict = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def test_mcp_http_rejects_unauthenticated(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(mcp_server, "get_settings", lambda: settings)

    # No `with` block: the wrapper rejects the request before the session
    # manager is needed, so no lifespan run is required here.
    client = TestClient(create_app())
    resp = client.post("/mcp", json=_rpc("initialize", _INIT_PARAMS), headers=_MCP_HEADERS)

    assert resp.status_code == 401
    # Wrong token is rejected too.
    resp = client.post(
        "/mcp",
        json=_rpc("initialize", _INIT_PARAMS),
        headers={**_MCP_HEADERS, "Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401
    # Other requests are also rejected before reaching the MCP app.
    resp = client.post("/mcp", json=_rpc("tools/list", {}, id=2), headers=_MCP_HEADERS)
    assert resp.status_code == 401


def test_mcp_http_rejects_unconfigured_token(monkeypatch, tmp_path) -> None:
    # When API token is UNSET, MCP HTTP should return 503, matching require_token
    # behavior (service unavailable, not invalid auth).
    settings = Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path, LOCAL_BRAIN_API_TOKEN="")
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(mcp_server, "get_settings", lambda: settings)

    client = TestClient(create_app())
    resp = client.post("/mcp", json=_rpc("initialize", _INIT_PARAMS), headers=_MCP_HEADERS)

    assert resp.status_code == 503
    assert "not configured" in resp.json().get("detail", "").lower()


def test_mcp_http_authed_tool_call_roundtrip(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(mcp_server, "get_settings", lambda: settings)

    captured: dict = {}

    async def fake_run_query(settings_arg, request, *, use_vector=False, ensure_index=False):
        captured["use_vector"] = use_vector
        captured["ensure_index"] = ensure_index
        captured["query"] = request.query
        return type(
            "Result",
            (),
            {"model_dump": lambda self, mode="json": {"results": [{"title": "needle-hit"}]}},
        )()

    monkeypatch.setattr(mcp_server, "run_query", fake_run_query)

    auth_headers = {**_MCP_HEADERS, "Authorization": "Bearer secret"}

    # A single lifespan entry -> session_manager.run() runs exactly once for the
    # shared FastMCP instance across the suite.
    with TestClient(create_app()) as client:
        init = client.post("/mcp", json=_rpc("initialize", _INIT_PARAMS), headers=auth_headers)
        assert init.status_code == 200, init.text
        assert init.json()["result"]["serverInfo"]["name"] == "fritz-local-brain"

        listed = client.post("/mcp", json=_rpc("tools/list", {}, id=2), headers=auth_headers)
        assert listed.status_code == 200, listed.text
        tool_names = {tool["name"] for tool in listed.json()["result"]["tools"]}
        assert "brain_search" in tool_names

        # Bearer header only, NO api_token argument -> must still succeed over HTTP.
        called = client.post(
            "/mcp",
            json=_rpc("tools/call", {"name": "brain_search", "arguments": {"query": "needle"}}, id=3),
            headers=auth_headers,
        )
        assert called.status_code == 200, called.text
        result = called.json()["result"]
        assert result["isError"] is False
        assert "needle-hit" in called.text

    # The tool actually ran the service-backed vector search path.
    assert captured == {"use_vector": True, "ensure_index": False, "query": "needle"}
