"""MCP server exposing Local Brain API-equivalent tools."""

from __future__ import annotations

import contextvars
import json
import os
from time import perf_counter
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .api.auth import token_matches
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


# #236: the same instance serves stdio (`mcp.run()` in main()) and, when mounted
# by the FastAPI service at /mcp, streamable HTTP. The extra kwargs only affect
# the HTTP transport (stdio ignores them): `stateless_http`/`json_response` give
# a single JSON body per POST (no long-lived SSE session to manage or hang on),
# `streamable_http_path="/"` so the endpoint is exactly /mcp once mounted, and
# DNS-rebinding protection is disabled because the container is reached over the
# LAN by arbitrary Host — the Bearer token is the security boundary (as for /v1/*).
mcp = FastMCP(
    "fritz-local-brain",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

# Set true by the HTTP auth wrapper (streamable_http_app) once the Bearer header
# has been verified, so tools skip the per-call `api_token` requirement over HTTP
# without weakening the stdio path (no wrapper there -> stays False -> arg required).
_http_authenticated: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "fritz_mcp_http_authenticated", default=False
)


def _resolve_mcp_agent(agent: str | None) -> str:
    """Precedence: explicit `agent` arg > FRITZ_AGENT > (http transport ? "claude" : "unknown").

    Claude Code's MCP client only speaks over the plugin-registered HTTP
    transport (#238), so a call authenticated by ``streamable_http_app`` with
    no other attribution is assumed to be Claude. The stdio path never sets
    ``_http_authenticated``, so it keeps defaulting to "unknown".
    """
    explicit = (agent or os.environ.get("FRITZ_AGENT") or "").strip()
    if explicit:
        return explicit
    return "claude" if _http_authenticated.get() else "unknown"


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
        record_compile(result, settings, source="agent")
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
        record_sync(result, settings, source="agent")
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
    # Over HTTP the Bearer header is already verified by the mount wrapper, so the
    # per-call arg is not required (#236). Stdio never sets this flag, so it keeps
    # requiring the `api_token` argument exactly as before.
    if _http_authenticated.get():
        return
    expected = getattr(settings, "api_token", None)
    if not expected or provided != expected:
        raise PermissionError("Invalid Local Brain MCP token")


def _asgi_authorization(scope: dict[str, Any]) -> str | None:
    for key, value in scope.get("headers", []):
        if key == b"authorization":
            return value.decode("latin-1")
    return None


async def _send_unauthorized(send: Any) -> None:
    body = json.dumps({"detail": "Invalid Local Brain API token"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _send_unconfigured(send: Any) -> None:
    body = json.dumps({"detail": "Local Brain API token is not configured"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 503,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


def streamable_http_app() -> Any:
    """Return the Bearer-authenticated MCP streamable-HTTP ASGI app for mounting at /mcp.

    The mounted sub-app bypasses FastAPI's ``Depends(require_token)``, so this
    wrapper enforces the SAME Bearer token (via ``auth.token_matches``) BEFORE the
    request reaches the MCP app, then flags the request as HTTP-authenticated so
    tools skip the per-call ``api_token`` requirement. The session manager's own
    lifespan is swallowed here because the service lifespan (app.py) owns it — see
    ``mcp.session_manager.run()`` there.
    """

    inner = mcp.streamable_http_app()

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            await inner(scope, receive, send)
            return
        if not get_settings().api_token:
            await _send_unconfigured(send)
            return
        if not token_matches(_asgi_authorization(scope)):
            await _send_unauthorized(send)
            return
        reset = _http_authenticated.set(True)
        try:
            await inner(scope, receive, send)
        finally:
            _http_authenticated.reset(reset)

    return app


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
