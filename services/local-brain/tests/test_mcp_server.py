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
