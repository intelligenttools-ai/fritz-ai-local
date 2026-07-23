"""API token authentication for service endpoints."""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import Header

from ..config import get_settings

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def is_forbidden_cross_origin(method: str | None, origin: str | None) -> bool:
    """True for a browser cross-origin write that must be rejected (CSRF guard).

    The /v1 surface is open on this loopback-only deployment, so the Bearer token
    no longer doubles as CSRF protection. Browsers attach an ``Origin`` header to
    cross-origin unsafe-method requests: a malicious page's Origin is its own
    host, the dashboard's is loopback, and non-browser tools (curl, MCP) send
    none. So reject unsafe methods whose Origin is present and NOT loopback; allow
    everything else (safe methods, same-origin dashboard, tokenless local tools).
    """
    if (method or "").upper() not in _UNSAFE_METHODS or not origin:
        return False
    return urlparse(origin).hostname not in _LOOPBACK_HOSTS


def token_matches(authorization: str | None) -> bool:
    """Return True when the Authorization header carries the configured token.

    Shared token source for both the FastAPI ``require_token`` dependency and the
    mounted MCP streamable-HTTP transport (#236), so the Bearer check is defined
    in exactly one place.
    """
    token = get_settings().api_token
    if not token:
        return False
    return authorization == f"Bearer {token}"


def require_token(authorization: str | None = Header(default=None)) -> None:
    """No-op: the /v1 HTTP surface is open on this loopback-only deployment.

    The service publishes to 127.0.0.1 only (see docker-compose ``ports``), so any
    client that can reach these endpoints is already on the host. Requiring a
    human to paste a Bearer token into the local dashboard guarded a door that is
    already locked to localhost, so the check is dropped.

    The mounted MCP streamable-HTTP transport keeps its OWN Bearer check via
    ``token_matches`` (see mcp_server.py) — agents already send the token, and
    that path is unaffected.
    """
    return None
