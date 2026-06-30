"""FastAPI routes for Local Brain."""

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic_ai.exceptions import ModelAPIError, UsageLimitExceeded

from .. import usage
from ..compile_workflow import run_compile
from ..config import get_settings
from ..telemetry import record_query_event
from ..embeddings import (
    embedding_status,
    probe_embedding_dimensions,
    refresh_embedding_index,
    schedule_embedding_refresh_after_compile,
    schedule_embedding_refresh_after_compile_result,
)
from ..lint_workflow import run_lint
from ..models import (
    CompileRunRequest,
    CompileRunResult,
    EmbeddingIndexRequest,
    EmbeddingIndexResult,
    EmbeddingProbeRequest,
    EmbeddingProbeResult,
    EmbeddingRefreshScheduleResult,
    EmbeddingStatusResult,
    LintRunRequest,
    LintRunResult,
    QueryRunRequest,
    QueryRunResult,
    RecentRunsResult,
    StatusResult,
    SyncRunRequest,
    SyncRunResult,
    UsageActivityResult,
    UsageKnowledgeResult,
    UsageProjectsResult,
    UsageQueriesResult,
    UsageSummaryResult,
)
from ..operation_locks import OperationAlreadyRunning, compile_lock, lint_lock, sync_lock
from ..query_workflow import run_query
from ..run_history import recent_runs, record_compile, record_sync
from ..status import build_status
from ..sync_workflow import run_sync
from .auth import require_token

router = APIRouter()


def _scheduler_task_running(request: Request | None) -> bool | None:
    if request is None:
        return None
    task = getattr(request.app.state, "scheduler_task", None)
    return isinstance(task, asyncio.Task) and not task.done()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/v1/status", response_model=StatusResult, dependencies=[Depends(require_token)])
async def status(request: Request) -> StatusResult:
    return build_status(get_settings(), scheduler_task_running=_scheduler_task_running(request))


@router.post("/v1/compile/run", response_model=CompileRunResult, dependencies=[Depends(require_token)])
async def compile_run(request: CompileRunRequest) -> CompileRunResult:
    settings = get_settings()
    try:
        async with compile_lock.guard(settings.brain_home):
            try:
                result = await run_compile(settings, request)
                record_compile(result)
                schedule_embedding_refresh_after_compile_result(settings, result, reason="compile")
                return result
            except UsageLimitExceeded as exc:
                raise HTTPException(status_code=502, detail=f"Compile agent exceeded run limits: {exc}") from exc
            except ModelAPIError as exc:
                raise HTTPException(status_code=502, detail=f"Compile model request failed: {exc.message}") from exc
    except OperationAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/v1/sync/run", response_model=SyncRunResult, dependencies=[Depends(require_token)])
async def sync_run(request: SyncRunRequest) -> SyncRunResult:
    settings = get_settings()
    try:
        async with sync_lock.guard(settings.brain_home):
            result = await run_sync(settings, request)
            record_sync(result)
            return result
    except OperationAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/v1/runs/recent", response_model=RecentRunsResult, dependencies=[Depends(require_token)])
async def runs_recent(limit: int = 10) -> RecentRunsResult:
    return RecentRunsResult(runs=recent_runs(limit))


@router.get("/v1/embeddings/status", response_model=EmbeddingStatusResult, dependencies=[Depends(require_token)])
async def embeddings_status() -> EmbeddingStatusResult:
    return embedding_status(get_settings())


@router.post("/v1/embeddings/probe", response_model=EmbeddingProbeResult, dependencies=[Depends(require_token)])
async def embeddings_probe(request: EmbeddingProbeRequest) -> EmbeddingProbeResult:
    return await probe_embedding_dimensions(get_settings(), request)


@router.post("/v1/embeddings/index/run", response_model=EmbeddingIndexResult, dependencies=[Depends(require_token)])
async def embeddings_index_run(request: EmbeddingIndexRequest) -> EmbeddingIndexResult:
    return await refresh_embedding_index(get_settings(), request)


@router.post("/v1/embeddings/index/schedule", response_model=EmbeddingRefreshScheduleResult, dependencies=[Depends(require_token)])
async def embeddings_index_schedule() -> EmbeddingRefreshScheduleResult:
    settings = get_settings()
    status = schedule_embedding_refresh_after_compile(settings, reason="ingest")
    return EmbeddingRefreshScheduleResult(enabled=settings.embedding_enabled, status=status, reason="ingest")


def _resolve_agent(header_value: str | None, request: QueryRunRequest) -> str:
    """Header agent wins over body agent; whitespace-only values fall back to 'unknown'."""
    return (header_value or "").strip() or (request.agent or "").strip() or "unknown"


async def _run_query_with_telemetry(
    settings,
    request: QueryRunRequest,
    agent: str,
    *,
    use_vector: bool,
) -> QueryRunResult:
    """Run query and record one telemetry event; telemetry errors never fail the query."""
    start = perf_counter()
    result = await run_query(settings, request, use_vector=use_vector, ensure_index=False)
    duration_ms = int((perf_counter() - start) * 1000)
    record_query_event(
        settings,
        use_vector=use_vector,
        request=request,
        result=result,
        agent=agent,
        duration_ms=duration_ms,
    )
    return result


@router.post("/v1/query/run", response_model=QueryRunResult, dependencies=[Depends(require_token)])
async def query_run(
    request: QueryRunRequest,
    x_brain_agent: Annotated[str | None, Header(alias="X-Brain-Agent")] = None,
) -> QueryRunResult:
    settings = get_settings()
    agent = _resolve_agent(x_brain_agent, request)
    return await _run_query_with_telemetry(settings, request, agent, use_vector=False)


@router.post("/v1/search/run", response_model=QueryRunResult, dependencies=[Depends(require_token)])
async def search_run(
    request: QueryRunRequest,
    x_brain_agent: Annotated[str | None, Header(alias="X-Brain-Agent")] = None,
) -> QueryRunResult:
    settings = get_settings()
    agent = _resolve_agent(x_brain_agent, request)
    return await _run_query_with_telemetry(settings, request, agent, use_vector=True)


@router.get("/v1/usage/activity", response_model=UsageActivityResult, dependencies=[Depends(require_token)])
async def usage_activity(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None, alias="to"),
    bucket: str = "day",
    by: str = "type",
) -> UsageActivityResult:
    buckets = usage.activity(get_settings(), since=from_, until=to, bucket=bucket, by=by)
    return UsageActivityResult(bucket="day", by=(by if by in {"type", "agent", "vault"} else "type"), buckets=buckets)


@router.get("/v1/usage/queries", response_model=UsageQueriesResult, dependencies=[Depends(require_token)])
async def usage_queries(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None, alias="to"),
    limit: int = 10,
) -> UsageQueriesResult:
    return UsageQueriesResult(**usage.queries(get_settings(), since=from_, until=to, limit=limit))


@router.get("/v1/usage/knowledge", response_model=UsageKnowledgeResult, dependencies=[Depends(require_token)])
async def usage_knowledge() -> UsageKnowledgeResult:
    return UsageKnowledgeResult(**usage.knowledge(get_settings()))


@router.get("/v1/usage/projects", response_model=UsageProjectsResult, dependencies=[Depends(require_token)])
async def usage_projects(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None, alias="to"),
) -> UsageProjectsResult:
    return UsageProjectsResult(projects=usage.projects(get_settings(), since=from_, until=to))


@router.get("/v1/usage/summary", response_model=UsageSummaryResult, dependencies=[Depends(require_token)])
async def usage_summary(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None, alias="to"),
) -> UsageSummaryResult:
    return UsageSummaryResult(**usage.summary(get_settings(), since=from_, until=to))


@router.post("/v1/lint/run", response_model=LintRunResult, dependencies=[Depends(require_token)])
async def lint_run(request: LintRunRequest) -> LintRunResult:
    settings = get_settings()
    try:
        async with lint_lock.guard(settings.brain_home):
            return await run_lint(settings, request)
    except OperationAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
