from __future__ import annotations

from pathlib import Path

from fritz_local_brain import mcp_server


def test_mcp_exposes_all_service_workflow_tools() -> None:
    assert callable(mcp_server.brain_status)
    assert callable(mcp_server.brain_compile)
    assert callable(mcp_server.brain_sync)
    assert callable(mcp_server.brain_recent_runs)
    assert callable(mcp_server.brain_query)
    assert callable(mcp_server.brain_search)
    assert callable(mcp_server.brain_lint)
    assert callable(mcp_server.brain_embeddings_status)
    assert callable(mcp_server.brain_embeddings_probe)
    assert callable(mcp_server.brain_embeddings_index)


class _Settings:
    api_token = "secret"
    scheduler_enabled = False
    scheduler_dry_run = False
    local_brain_autostart_installed = False
    interval_minutes = 30
    brain_home = Path("/brain")
    skills_dir = Path("/skills")
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


def test_mcp_status_does_not_claim_http_scheduler_is_running(monkeypatch, tmp_path) -> None:
    class SchedulerEnabledSettings(_Settings):
        scheduler_enabled = True
        scheduler_dry_run = True
        brain_home = tmp_path
        skills_dir = tmp_path / "skills"

    monkeypatch.setattr(mcp_server, "get_settings", lambda: SchedulerEnabledSettings())

    result = mcp_server.brain_status(api_token="secret")

    assert result["service_running"] is False
    assert result["scheduler_enabled"] is True
    assert result["processing_mode"] == "dry-run"
    assert result["processing_active"] is False
    assert "no scheduler task is running" in result["processing_note"]
