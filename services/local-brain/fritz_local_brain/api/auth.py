"""API token authentication for service endpoints."""

from __future__ import annotations

from fastapi import Header, HTTPException

from ..config import get_settings


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
    if not get_settings().api_token:
        raise HTTPException(status_code=503, detail="Local Brain API token is not configured")
    if not token_matches(authorization):
        raise HTTPException(status_code=401, detail="Invalid Local Brain API token")
