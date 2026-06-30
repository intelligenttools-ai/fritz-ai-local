"""MCP server exposing Local Brain API-equivalent tools."""

from __future__ import annotations

import os
from time import perf_counter
from typing import Any

from mcp.server.fastmcp import FastMCP

from .compile_workflow import run_compile
from .config import get_settings
from .embeddings import embedding_status, probe_embedding_dimensions, refresh_embedding_index, schedule_embedding_refresh_after_compile_result
from .lint_workflow import run_lint
from .models import CompileRunRequest, EmbeddingIndexRequest, EmbeddingProbeRequest, LintRunRequest, QueryRunRequest, SyncRunRequest
from .operation_locks import compile_lock, lint_lock, sync_lock
from .query_workflow import run_query
from .run_history import recent_runs, record_compile, record_sync
from .status import build_status
from .sync_workflow import run_sync
from .telemetry import record_query_event


mcp = FastMCP("fritz-local-brain")


def _resolve_mcp_agent(agent: str | None) -> str:
    return (agent or os.environ.get("FRITZ_AGENT") or "").strip() or "unknown"


@mcp.tool()
def brain_status(api_token: str | None = None) -> dict[str, Any]:
    """Return Local Brain service status without secrets."""

    settings = get_settings()
    _require_mcp_token(settings, api_token)
    return build_status(settings, service_running=False, scheduler_task_running=False).model_dump(mode="json")


@mcp.tool()
async def brain_compile(
    dry_run: bool = True,
    max_captures: int | None = None,
    approval_token: str | None = None,
    api_token: str | None = None,
) -> dict[str, Any]:
    """Run the same compile workflow as POST /v1/compile/run."""

    settings = get_settings()
    _require_mcp_token(settings, api_token)
    async with compile_lock.guard(settings.brain_home):
        result = await run_compile(
            settings,
            CompileRunRequest(dry_run=dry_run, max_captures=max_captures, approval_token=approval_token),
        )
        record_compile(result)
        schedule_embedding_refresh_after_compile_result(settings, result, reason="mcp compile")
        return result.model_dump(mode="json")


@mcp.tool()
async def brain_sync(
    dry_run: bool = True,
    vault: str | None = None,
    approval_token: str | None = None,
    api_token: str | None = None,
) -> dict[str, Any]:
    """Run the same sync workflow as POST /v1/sync/run."""

    settings = get_settings()
    _require_mcp_token(settings, api_token)
    async with sync_lock.guard(settings.brain_home):
        result = await run_sync(settings, SyncRunRequest(dry_run=dry_run, vault=vault, approval_token=approval_token))
        record_sync(result)
        return result.model_dump(mode="json")


@mcp.tool()
def brain_recent_runs(limit: int = 10, api_token: str | None = None) -> dict[str, Any]:
    """Return the same bounded run history as GET /v1/runs/recent."""

    _require_mcp_token(get_settings(), api_token)
    return {"runs": [run.model_dump(mode="json") for run in recent_runs(limit)]}


@mcp.tool()
async def brain_query(
    query: str,
    vault: str | None = None,
    limit: int = 10,
    api_token: str | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """Run the same read-only query workflow as POST /v1/query/run."""

    settings = get_settings()
    _require_mcp_token(settings, api_token)
    req = QueryRunRequest(query=query, vault=vault, limit=limit)
    start = perf_counter()
    result = await run_query(settings, req)
    duration_ms = int((perf_counter() - start) * 1000)
    record_query_event(
        settings,
        use_vector=False,
        request=req,
        result=result,
        agent=_resolve_mcp_agent(agent),
        duration_ms=duration_ms,
    )
    return result.model_dump(mode="json")


@mcp.tool()
async def brain_search(
    query: str,
    vault: str | None = None,
    limit: int = 10,
    api_token: str | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """Run service-backed search, including container-managed vector search."""

    settings = get_settings()
    _require_mcp_token(settings, api_token)
    req = QueryRunRequest(query=query, vault=vault, limit=limit)
    start = perf_counter()
    result = await run_query(settings, req, use_vector=True, ensure_index=False)
    duration_ms = int((perf_counter() - start) * 1000)
    record_query_event(
        settings,
        use_vector=True,
        request=req,
        result=result,
        agent=_resolve_mcp_agent(agent),
        duration_ms=duration_ms,
    )
    return result.model_dump(mode="json")


@mcp.tool()
async def brain_lint(dry_run: bool = True, vault: str | None = None, api_token: str | None = None) -> dict[str, Any]:
    """Run the same lint workflow as POST /v1/lint/run."""

    settings = get_settings()
    _require_mcp_token(settings, api_token)
    async with lint_lock.guard(settings.brain_home):
        result = await run_lint(settings, LintRunRequest(dry_run=dry_run, vault=vault))
        return result.model_dump(mode="json")


@mcp.tool()
def brain_embeddings_status(api_token: str | None = None) -> dict[str, Any]:
    """Return the same embedding metadata as GET /v1/embeddings/status."""

    settings = get_settings()
    _require_mcp_token(settings, api_token)
    return embedding_status(settings).model_dump(mode="json")


@mcp.tool()
async def brain_embeddings_probe(dry_run: bool = True, api_token: str | None = None) -> dict[str, Any]:
    """Run the same embedding probe as POST /v1/embeddings/probe."""

    settings = get_settings()
    _require_mcp_token(settings, api_token)
    result = await probe_embedding_dimensions(settings, EmbeddingProbeRequest(dry_run=dry_run))
    return result.model_dump(mode="json")


@mcp.tool()
async def brain_embeddings_index(force: bool = False, api_token: str | None = None) -> dict[str, Any]:
    """Vectorize knowledge and captures inside the Local Brain container."""

    settings = get_settings()
    _require_mcp_token(settings, api_token)
    result = await refresh_embedding_index(settings, EmbeddingIndexRequest(force=force))
    return result.model_dump(mode="json")


def _require_mcp_token(settings: Any, provided: str | None) -> None:
    expected = getattr(settings, "api_token", None)
    if not expected or provided != expected:
        raise PermissionError("Invalid Local Brain MCP token")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
