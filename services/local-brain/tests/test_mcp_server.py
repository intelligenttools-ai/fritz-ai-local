from __future__ import annotations

from fritz_local_brain import mcp_server


def test_mcp_exposes_all_service_workflow_tools() -> None:
    assert callable(mcp_server.brain_status)
    assert callable(mcp_server.brain_compile)
    assert callable(mcp_server.brain_sync)
    assert callable(mcp_server.brain_recent_runs)
    assert callable(mcp_server.brain_query)
    assert callable(mcp_server.brain_lint)
    assert callable(mcp_server.brain_embeddings_status)
    assert callable(mcp_server.brain_embeddings_probe)


class _Settings:
    api_token = "secret"
    scheduler_enabled = False
    interval_minutes = 30
    brain_home = "/brain"
    skills_dir = "/skills"
    allow_first_external_sync = False


def test_mcp_status_requires_configured_api_token(monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "get_settings", lambda: _Settings())

    try:
        mcp_server.brain_status()
    except PermissionError as exc:
        assert "Invalid Local Brain MCP token" in str(exc)
    else:
        raise AssertionError("MCP status should reject missing token")

    assert mcp_server.brain_status(api_token="secret")["service"] == "local-brain"
