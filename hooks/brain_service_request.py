#!/usr/bin/env python3
"""Call Local Brain's HTTP API for runtimes without native MCP tools.

This is the immediate fallback transport used by the Pi binding. It resolves the
service URL and token through brain_common so registry precedence and token
handling remain centralized. Error output never includes response bodies or
credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib import error, request
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brain_bootstrap import ensure_yaml_interpreter

ensure_yaml_interpreter()

from brain_common import (  # noqa: E402
    get_local_brain_api_token,
    get_local_brain_base_url,
    get_local_brain_service_config,
)


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


MAX_RESPONSE_BYTES = 4 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024

OPERATIONS = {
    "search": ("POST", "/v1/search/run"),
    "query": ("POST", "/v1/query/run"),
    "compile": ("POST", "/v1/compile/run"),
    "sync": ("POST", "/v1/sync/run"),
    "lint": ("POST", "/v1/lint/run"),
}


def public_config() -> dict[str, object]:
    """Return non-secret routing metadata for MCP config generation."""
    config = get_local_brain_service_config()
    token_env = config.get("api_token_env", "LOCAL_BRAIN_API_TOKEN")
    if not isinstance(token_env, str) or not token_env.strip():
        token_env = "LOCAL_BRAIN_API_TOKEN"
    token_env = token_env.strip()
    resolved_token = get_local_brain_api_token()
    env_token = os.environ.get(token_env, "")
    return {
        "base_url": _validated_base_url(),
        "api_token_env": token_env,
        "has_token": bool(resolved_token),
        "token_env_ready": bool(resolved_token and env_token == resolved_token),
    }


def _validated_base_url() -> str:
    base_url = get_local_brain_base_url().rstrip("/")
    parsed = urlparse(base_url)
    if "?" in base_url or "#" in base_url or parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("Local Brain API base URL is invalid")
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not loopback:
        raise RuntimeError("Local Brain API requires HTTPS for non-loopback hosts")
    return base_url


def _open(req: request.Request, timeout: float):
    return request.build_opener(_NoRedirect()).open(req, timeout=timeout)


def call(operation: str, payload: dict[str, object], *, timeout: float = 120.0) -> str:
    if operation not in OPERATIONS:
        raise ValueError(f"unsupported Local Brain operation: {operation}")
    method, path = OPERATIONS[operation]
    token = get_local_brain_api_token()
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "X-Brain-Agent": "pi",
    }
    if token:
        headers["authorization"] = f"Bearer {token}"
    req = request.Request(
        f"{_validated_base_url()}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with _open(req, timeout) as response:
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > MAX_RESPONSE_BYTES:
                        raise RuntimeError(f"Local Brain API response exceeds {MAX_RESPONSE_BYTES} bytes")
                except ValueError:
                    pass
            content_type = response.headers.get("Content-Type", "")
            if content_type and "application/json" not in content_type.lower():
                raise RuntimeError("Local Brain API returned a non-JSON response")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(min(READ_CHUNK_BYTES, MAX_RESPONSE_BYTES - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise RuntimeError(f"Local Brain API response exceeds {MAX_RESPONSE_BYTES} bytes")
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        raise RuntimeError(f"Local Brain API request failed: HTTP {exc.code}") from None
    except (error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise RuntimeError(f"Local Brain API request failed: {type(reason).__name__}") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Call the Local Brain HTTP API fallback.")
    parser.add_argument("operation", choices=[*OPERATIONS, "config"])
    args = parser.parse_args(argv)

    try:
        if args.operation == "config":
            print(json.dumps(public_config(), separators=(",", ":")))
            return 0
        raw = sys.stdin.read().strip() or "{}"
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("request payload must be a JSON object")
        print(call(args.operation, payload))
        return 0
    except (json.JSONDecodeError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
