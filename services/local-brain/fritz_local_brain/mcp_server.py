"""MCP server exposing Local Brain API-equivalent tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .compile_workflow import run_compile
from .config import get_settings
from .models import CompileRunRequest, SyncRunRequest
from .run_history import recent_runs, record_compile, record_sync
from .sync_workflow import run_sync


mcp = FastMCP("fritz-local-brain")


@mcp.tool()
def brain_status() -> dict[str, Any]:
    """Return Local Brain service status without secrets."""

    settings = get_settings()
    return {
        "service": "local-brain",
        "scheduler_enabled": settings.scheduler_enabled,
        "interval_minutes": settings.interval_minutes,
        "brain_home": str(settings.brain_home),
        "skills_dir": str(settings.skills_dir),
        "allow_first_external_sync": settings.allow_first_external_sync,
    }


@mcp.tool()
async def brain_compile(dry_run: bool = True, max_captures: int | None = None) -> dict[str, Any]:
    """Run the same compile workflow as POST /v1/compile/run."""

    result = await run_compile(get_settings(), CompileRunRequest(dry_run=dry_run, max_captures=max_captures))
    record_compile(result)
    return result.model_dump(mode="json")


@mcp.tool()
async def brain_sync(dry_run: bool = True, vault: str | None = None) -> dict[str, Any]:
    """Run the same sync workflow as POST /v1/sync/run."""

    result = await run_sync(get_settings(), SyncRunRequest(dry_run=dry_run, vault=vault))
    record_sync(result)
    return result.model_dump(mode="json")


@mcp.tool()
def brain_recent_runs(limit: int = 10) -> dict[str, Any]:
    """Return the same bounded run history as GET /v1/runs/recent."""

    return {"runs": [run.model_dump(mode="json") for run in recent_runs(limit)]}


@mcp.tool()
def brain_query(query: str) -> dict[str, Any]:
    """Report query availability without adding capabilities beyond the API."""

    return {
        "query": query,
        "available": False,
        "error": "Brain query is not implemented in the Local Brain service yet.",
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
