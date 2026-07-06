"""Helpers for installer-owned Claude user-scope MCP registration.

The distributed Claude plugin cannot safely ship a machine-local API token.
Registration therefore lives in installer/provisioning code and writes the
literal bearer token into Claude Code's user-scope MCP config via the Claude CLI.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable


CLAUDE_MCP_SERVER_NAME = "fritz-brain"
DEFAULT_LOCAL_BRAIN_BASE_URL = "http://127.0.0.1:8765"
CLAUDE_CONFIG_ENV = "CLAUDE_CONFIG_PATH"

Runner = Callable[[list[str], dict[str, str]], Any]


class ClaudeMcpRegistrationError(RuntimeError):
    """Sanitized Claude MCP registration failure."""


def _redact(value: Any, token: str) -> str:
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")
    if token:
        text = text.replace(token, "<redacted>")
    return re.sub(r"Bearer\s+[^\s\"']+", "Bearer <redacted>", text)


def _run_cli(
    runner: Runner,
    cmd: list[str],
    env: dict[str, str],
    *,
    step: str,
    token: str,
) -> None:
    try:
        runner(cmd, env)
    except subprocess.CalledProcessError as exc:
        stderr = _redact(getattr(exc, "stderr", ""), token).strip()
        detail = f": {stderr}" if stderr else ""
        raise ClaudeMcpRegistrationError(
            f"Claude MCP {step} failed with exit {exc.returncode}{detail}"
        ) from None


def mcp_url(base_url: str = DEFAULT_LOCAL_BRAIN_BASE_URL) -> str:
    """Return the streamable-HTTP MCP endpoint for a service base URL."""
    return f"{base_url.rstrip('/')}/mcp/"


def desired_server(token: str, base_url: str = DEFAULT_LOCAL_BRAIN_BASE_URL) -> dict[str, Any]:
    """Return the Claude MCP server JSON with a literal bearer token."""
    return {
        "type": "http",
        "url": mcp_url(base_url),
        "headers": {"Authorization": f"Bearer {token}"},
    }


def claude_user_config_path(
    *,
    home: Path | None = None,
    config_path: Path | None = None,
) -> Path:
    """Resolve Claude Code's user config path.

    ``config_path`` and ``CLAUDE_CONFIG_PATH`` are test/diagnostic overrides.
    The real Claude CLI stores user-scope MCP servers in ``$HOME/.claude.json``.
    """
    if config_path is not None:
        return Path(config_path)
    env_path = os.environ.get(CLAUDE_CONFIG_ENV)
    if env_path and env_path.strip():
        return Path(env_path.strip()).expanduser()
    return (Path(home) if home is not None else Path.home()) / ".claude.json"


def read_user_mcp_server(
    *,
    home: Path | None = None,
    config_path: Path | None = None,
    name: str = CLAUDE_MCP_SERVER_NAME,
) -> dict[str, Any] | None:
    """Read a user-scope MCP server from Claude's user config."""
    path = claude_user_config_path(home=home, config_path=config_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return None
    server = servers.get(name)
    return server if isinstance(server, dict) else None


def bearer_token(server: dict[str, Any] | None) -> str:
    """Extract a bearer token from a Claude MCP server config."""
    if not isinstance(server, dict):
        return ""
    headers = server.get("headers")
    if not isinstance(headers, dict):
        return ""
    auth = headers.get("Authorization")
    if not isinstance(auth, str):
        return ""
    prefix = "Bearer "
    return auth[len(prefix):].strip() if auth.startswith(prefix) else ""


def registration_status(
    token: str,
    *,
    base_url: str = DEFAULT_LOCAL_BRAIN_BASE_URL,
    home: Path | None = None,
    config_path: Path | None = None,
) -> str:
    """Return current Claude user-scope registration status.

    Values are ``current``, ``missing``, ``token-mismatch``, ``url-mismatch``,
    or ``no-token``.
    """
    token = token.strip() if isinstance(token, str) else ""
    if not token:
        return "no-token"
    server = read_user_mcp_server(home=home, config_path=config_path)
    if server is None:
        return "missing"
    if bearer_token(server) != token:
        return "token-mismatch"
    if server.get("type") != "http" or server.get("url") != mcp_url(base_url):
        return "url-mismatch"
    return "current"


def _default_runner(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, env=env)


def register_user_scope_server(
    token: str,
    *,
    base_url: str = DEFAULT_LOCAL_BRAIN_BASE_URL,
    home: Path | None = None,
    config_path: Path | None = None,
    runner: Runner | None = None,
) -> list[str]:
    """Register or refresh Claude's user-scope ``fritz-brain`` MCP server.

    The returned actions intentionally never include the token.
    """
    token = token.strip() if isinstance(token, str) else ""
    if not token:
        return ["no api_token in registry.local_brain_service; Claude MCP registration skipped"]

    home = Path(home) if home is not None else Path.home()
    before = registration_status(token, base_url=base_url, home=home, config_path=config_path)
    if before == "current":
        return [f"Claude user-scope MCP server {CLAUDE_MCP_SERVER_NAME} already current"]

    payload = json.dumps(desired_server(token, base_url), separators=(",", ":"))
    env = dict(os.environ)
    env["HOME"] = str(home)
    run = runner or _default_runner
    if before != "missing":
        _run_cli(
            run,
            ["claude", "mcp", "remove", CLAUDE_MCP_SERVER_NAME, "--scope", "user"],
            env,
            step="remove",
            token=token,
        )
    _run_cli(
        run,
        ["claude", "mcp", "add-json", CLAUDE_MCP_SERVER_NAME, payload, "--scope", "user"],
        env,
        step="add-json",
        token=token,
    )
    return [
        f"registered Claude user-scope MCP server {CLAUDE_MCP_SERVER_NAME} "
        f"({before}; token redacted)"
    ]
