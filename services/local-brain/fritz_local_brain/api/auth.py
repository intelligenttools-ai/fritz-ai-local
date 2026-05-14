"""API token authentication for service endpoints."""

from __future__ import annotations

from fastapi import Header, HTTPException

from ..config import get_settings


def require_token(authorization: str | None = Header(default=None)) -> None:
    token = get_settings().api_token
    if not token:
        raise HTTPException(status_code=503, detail="Local Brain API token is not configured")
    expected = f"Bearer {token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid Local Brain API token")
