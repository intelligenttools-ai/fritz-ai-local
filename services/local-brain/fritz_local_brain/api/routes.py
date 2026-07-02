"""FastAPI routes for Local Brain."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic_ai.exceptions import ModelAPIError, UsageLimitExceeded

from .. import usage
from ..compile_workflow import run_compile
from ..config import (
    CONFIG_FIELD_META,
    REPROVISION_GUIDANCE,
    ConfigCoercionError,
    coerce_config_value,
    config_env_value,
    config_field_value,
    get_settings,
)
from .. import env_persist
from ..telemetry import latest_event_id, record_query_event
from ..embeddings import (
    embedding_status,
    probe_embedding_dimensions,
    refresh_embedding_index,
    schedule_embedding_refresh_after_compile,
    schedule_embedding_refresh_after_compile_result,
)
from ..lint_workflow import run_lint
from .. import knowledge_browse
from ..models import (
    CompileRunRequest,
    CompileRunResult,
    ConfigField,
    ConfigPatchResult,
    ConfigResult,
    EmbeddingIndexRequest,
    EmbeddingIndexResult,
    EmbeddingProbeRequest,
    EmbeddingProbeResult,
    EmbeddingRefreshScheduleResult,
    EmbeddingStatusResult,
    KnowledgeArticleDetail,
    KnowledgeArticlesResult,
    KnowledgeTreeNode,
    LintRunRequest,
    LintRunResult,
    QueryRunRequest,
    QueryRunResult,
    RecentRunsResult,
    RunDetail,
    RunListResult,
    StatusResult,
    UsageAgentDetailResult,
    SyncRunRequest,
    SyncRunResult,
    UsageActivityResult,
    UsageAgentsResult,
    UsageKnowledgeResult,
    UsageProjectsResult,
    UsageQueriesResult,
    UsageSummaryResult,
    UsageSystemResult,
)
from ..operation_locks import OperationAlreadyRunning, compile_lock, lint_lock, sync_lock
from ..query_workflow import run_query
from ..run_history import recent_runs, record_compile, record_sync
from ..telemetry import get_run, list_runs
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
                record_compile(result, settings, source="api")
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
            record_sync(result, settings, source="api")
            return result
    except OperationAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _to_run_detail(row: dict) -> RunDetail:
    """Map a persisted ``runs`` row (telemetry) to the RunDetail model.

    ``errors`` is surfaced as a top-level list (lifted out of ``detail``) so
    callers get the actual messages without digging into the nested record.
    """
    detail = row.get("detail") or {}
    errors = detail.get("errors") if isinstance(detail, dict) else None
    return RunDetail(
        id=row["id"],
        kind=row["kind"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        duration_ms=row.get("duration_ms"),
        dry_run=bool(row.get("dry_run")),
        status=row["status"],
        source=row.get("source"),
        summary=row.get("summary"),
        errors=list(errors) if isinstance(errors, list) else [],
        detail=detail if isinstance(detail, dict) else {},
    )


@router.get("/v1/runs", response_model=RunListResult, dependencies=[Depends(require_token)])
async def runs_list(limit: int = 10, kind: str | None = Query(default=None)) -> RunListResult:
    """List persisted runs (#223). Supersedes ``/v1/runs/recent`` (kept as alias)."""
    rows = list_runs(get_settings(), limit=limit, kind=kind)
    return RunListResult(runs=[_to_run_detail(r) for r in rows])


@router.get("/v1/runs/recent", response_model=RecentRunsResult, dependencies=[Depends(require_token)])
async def runs_recent(limit: int = 10) -> RecentRunsResult:
    return RecentRunsResult(runs=recent_runs(limit))


@router.get("/v1/runs/{run_id}", response_model=RunDetail, dependencies=[Depends(require_token)])
async def run_detail(run_id: str) -> RunDetail:
    """Full detail for one persisted run (#223); 404 for an unknown id."""
    row = get_run(get_settings(), run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown run id: {run_id}")
    return _to_run_detail(row)


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


@router.get("/v1/usage/agents", response_model=UsageAgentsResult, dependencies=[Depends(require_token)])
async def usage_agents(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None, alias="to"),
) -> UsageAgentsResult:
    return UsageAgentsResult(agents=usage.agents(get_settings(), since=from_, until=to))


@router.get("/v1/usage/agents/{agent}", response_model=UsageAgentDetailResult, dependencies=[Depends(require_token)])
async def usage_agent_detail(
    agent: str,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None, alias="to"),
    limit: int = 20,
    offset: int = 0,
) -> UsageAgentDetailResult:
    """Per-agent drill-down (#223): first/last seen, type breakdown, daily series,
    paginated recent events. Test-agent isolation is enforced by the agent filter."""
    return UsageAgentDetailResult(
        **usage.agent_detail(get_settings(), agent, since=from_, until=to, limit=limit, offset=offset)
    )


@router.get("/v1/usage/activity", response_model=UsageActivityResult, dependencies=[Depends(require_token)])
async def usage_activity(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None, alias="to"),
    bucket: str = "day",
    by: str = "type",
    agent: str | None = Query(default=None),
) -> UsageActivityResult:
    buckets = usage.activity(get_settings(), since=from_, until=to, bucket=bucket, by=by, agent=agent)
    return UsageActivityResult(bucket="day", by=(by if by in {"type", "agent", "vault"} else "type"), buckets=buckets)


@router.get("/v1/usage/queries", response_model=UsageQueriesResult, dependencies=[Depends(require_token)])
async def usage_queries(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None, alias="to"),
    limit: int = 10,
    agent: str | None = Query(default=None),
) -> UsageQueriesResult:
    return UsageQueriesResult(**usage.queries(get_settings(), since=from_, until=to, limit=limit, agent=agent))


@router.get("/v1/usage/knowledge", response_model=UsageKnowledgeResult, dependencies=[Depends(require_token)])
async def usage_knowledge() -> UsageKnowledgeResult:
    return UsageKnowledgeResult(**usage.knowledge(get_settings()))


@router.get("/v1/usage/projects", response_model=UsageProjectsResult, dependencies=[Depends(require_token)])
async def usage_projects(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None, alias="to"),
    agent: str | None = Query(default=None),
) -> UsageProjectsResult:
    return UsageProjectsResult(projects=usage.projects(get_settings(), since=from_, until=to, agent=agent))


@router.get("/v1/usage/summary", response_model=UsageSummaryResult, dependencies=[Depends(require_token)])
async def usage_summary(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None, alias="to"),
    agent: str | None = Query(default=None),
) -> UsageSummaryResult:
    return UsageSummaryResult(**usage.summary(get_settings(), since=from_, until=to, agent=agent))


@router.get("/v1/usage/system", response_model=UsageSystemResult, dependencies=[Depends(require_token)])
async def usage_system(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None, alias="to"),
) -> UsageSystemResult:
    """SYSTEM activity (the service's own compile/index/reconcile work, #205)."""
    return UsageSystemResult(**usage.system(get_settings(), since=from_, until=to))


# ---------------------------------------------------------------------------
# Read-only knowledge browse API (#221)
#
# Path-traversal safety: every caller-supplied ``path`` is resolved against the
# store root and MUST land inside it (see knowledge_browse._resolve_inside). A
# PathTraversalError (../, absolute path, symlink escape) becomes HTTP 400.
# ---------------------------------------------------------------------------


@router.get("/v1/knowledge/tree", response_model=KnowledgeTreeNode, dependencies=[Depends(require_token)])
async def knowledge_tree() -> KnowledgeTreeNode:
    return KnowledgeTreeNode(**knowledge_browse.build_tree(get_settings()))


@router.get("/v1/knowledge/articles", response_model=KnowledgeArticlesResult, dependencies=[Depends(require_token)])
async def knowledge_articles(
    path: str | None = Query(default=None),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=0),
    offset: int = Query(default=0, ge=0),
) -> KnowledgeArticlesResult:
    try:
        result = knowledge_browse.list_articles(
            get_settings(), path=path, status=status, q=q, limit=limit, offset=offset
        )
    except knowledge_browse.PathTraversalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return KnowledgeArticlesResult(**result)


@router.get("/v1/knowledge/article", response_model=KnowledgeArticleDetail, dependencies=[Depends(require_token)])
async def knowledge_article(path: str = Query(...)) -> KnowledgeArticleDetail:
    try:
        result = knowledge_browse.read_article(get_settings(), path=path)
    except knowledge_browse.PathTraversalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail=f"Article not found: {path}")
    return KnowledgeArticleDetail(**result)


# ---------------------------------------------------------------------------
# Live updates via Server-Sent Events (#198)
#
# AUTH MODEL — why a short-lived stream ticket, not the Bearer token:
# The browser EventSource API cannot set an Authorization header, and the
# long-lived Bearer token must NOT be placed in the URL/query string because it
# would leak into server access logs and the browser history. Instead the
# already-authenticated dashboard exchanges its Bearer token (via the POST below,
# which IS Bearer-protected) for a single-purpose, cryptographically-random
# ticket with a short TTL. That ticket may appear in the stream URL: it is NOT
# the Bearer token, it expires quickly, and it only authorizes the read-only
# event stream. Reuse within the TTL is allowed so a reconnect works; expiry
# still bounds exposure.
# ---------------------------------------------------------------------------

STREAM_TICKET_TTL_S = 60.0  # short-lived; bounds exposure of a leaked ticket
STREAM_POLL_S = 2.0  # how often the stream checks for new telemetry events
STREAM_HEARTBEAT_S = 15.0  # keep-alive comment cadence (proxies/idle timeouts)

# In-process ticket store: {ticket -> expiry on time.monotonic()}. Single-process
# service, so an in-memory dict is sufficient (no cross-worker sharing needed).
_stream_tickets: dict[str, float] = {}


def _prune_stream_tickets(now: float) -> None:
    """Drop expired tickets. Called on every issue/validate so the dict stays small."""
    expired = [t for t, exp in _stream_tickets.items() if exp <= now]
    for t in expired:
        _stream_tickets.pop(t, None)


def _issue_stream_ticket() -> str:
    now = time.monotonic()
    _prune_stream_tickets(now)
    ticket = secrets.token_urlsafe(32)
    _stream_tickets[ticket] = now + STREAM_TICKET_TTL_S
    return ticket


def _valid_stream_ticket(ticket: str | None) -> bool:
    now = time.monotonic()
    _prune_stream_tickets(now)
    if not ticket:
        return False
    expiry = _stream_tickets.get(ticket)
    return expiry is not None and expiry > now


@router.post("/v1/usage/stream-ticket", dependencies=[Depends(require_token)])
async def usage_stream_ticket() -> dict[str, object]:
    """Issue a short-lived ticket the browser EventSource can pass in the URL."""
    return {"ticket": _issue_stream_ticket(), "expires_in": int(STREAM_TICKET_TTL_S)}


async def _stream_events(request: Request, settings):
    """Async generator yielding SSE frames until the client disconnects.

    Emits an initial ``hello``, then polls ``latest_event_id`` every
    ``STREAM_POLL_S``; whenever it grows a ``changed`` frame is emitted. A
    heartbeat comment keeps idle connections alive. Stops cleanly on client
    disconnect or cancellation so no task is leaked.
    """
    yield "event: hello\ndata: {}\n\n"
    last_id = latest_event_id(settings)
    since_heartbeat = 0.0
    try:
        while True:
            if await request.is_disconnected():
                return
            await asyncio.sleep(STREAM_POLL_S)
            current = latest_event_id(settings)
            if current > last_id:
                last_id = current
                yield f"event: changed\ndata: {json.dumps({'latest_id': current})}\n\n"
                since_heartbeat = 0.0
                continue
            since_heartbeat += STREAM_POLL_S
            if since_heartbeat >= STREAM_HEARTBEAT_S:
                since_heartbeat = 0.0
                yield ": ping\n\n"
    except (asyncio.CancelledError, GeneratorExit):
        # Client went away / server shutdown — exit quietly, no leaked task.
        return


@router.get("/v1/usage/stream")
async def usage_stream(request: Request, ticket: str | None = Query(default=None)) -> StreamingResponse:
    """SSE live-update stream. Authorized by a short-lived ticket (see AUTH MODEL)."""
    if not _valid_stream_ticket(ticket):
        raise HTTPException(status_code=401, detail="Invalid or expired stream ticket")
    return StreamingResponse(
        _stream_events(request, get_settings()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/v1/lint/run", response_model=LintRunResult, dependencies=[Depends(require_token)])
async def lint_run(request: LintRunRequest) -> LintRunResult:
    settings = get_settings()
    try:
        async with lint_lock.guard(settings.brain_home):
            return await run_lint(settings, request)
    except OperationAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Live configuration (#208)
# ---------------------------------------------------------------------------


def _config_fields(settings) -> dict[str, ConfigField]:
    """Build the API-safe view of every known config field."""
    return {
        name: ConfigField(
            value=config_field_value(settings, name),
            mutable=meta.mutable,
            requires=meta.requires,
        )
        for name, meta in CONFIG_FIELD_META.items()
    }


@router.get("/v1/config", response_model=ConfigResult, dependencies=[Depends(require_token)])
async def config_get() -> ConfigResult:
    return ConfigResult(fields=_config_fields(get_settings()))


@router.patch("/v1/config", response_model=ConfigPatchResult, dependencies=[Depends(require_token)])
async def config_patch(body: dict[str, object]) -> ConfigPatchResult:
    """Apply runtime-mutable config changes live and persist them to .env.

    Runtime-mutable fields are coerced/validated, set on the live settings
    singleton, and persisted to the repo-root .env so the change survives a
    restart. Rebuild-required fields are rejected with re-provision guidance and
    left untouched. An unknown field or an invalid value returns 400 without
    applying anything.
    """
    settings = get_settings()

    # Pass 1 — validate/coerce EVERY field WITHOUT mutating anything. An unknown
    # field or an invalid runtime value returns 400 having changed no live state
    # and written no .env (atomic: all-or-nothing). Rebuild-required fields are
    # collected into ``rejected`` with guidance and do NOT 400 the request.
    coerced: dict[str, object] = {}
    rejected: list[str] = []
    for field, raw in body.items():
        meta = CONFIG_FIELD_META.get(field)
        if meta is None:
            raise HTTPException(status_code=400, detail=f"Unknown config field: {field}")
        if not meta.mutable:
            rejected.append(f"{field}: {REPROVISION_GUIDANCE}")
            continue
        try:
            coerced[field] = coerce_config_value(field, raw)
        except ConfigCoercionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Pass 2 — only now that validation fully passed, apply to the live singleton
    # and persist the changed env keys.
    env_updates: dict[str, str] = {}
    for field, value in coerced.items():
        setattr(settings, field, value)
        env_updates[CONFIG_FIELD_META[field].env_key] = config_env_value(field, value)

    if env_updates:
        env_persist.persist_env_updates(env_updates)

    return ConfigPatchResult(applied=list(coerced), rejected=rejected, config=_config_fields(settings))
