"""Unit coverage for MCP agent attribution precedence (#238).

_resolve_mcp_agent must default unattributed calls to "claude" when they
arrived over the plugin-registered HTTP transport (the only transport Claude
Code's MCP client uses), while leaving the explicit `agent` arg and
FRITZ_AGENT overrides -- and the stdio path's "unknown" default -- unchanged.
Precedence: explicit `agent` arg > FRITZ_AGENT > (http transport ? "claude" : "unknown").
"""

from __future__ import annotations

from fritz_local_brain import mcp_server


def test_resolve_mcp_agent_http_context_defaults_to_claude(monkeypatch) -> None:
    monkeypatch.delenv("FRITZ_AGENT", raising=False)
    reset = mcp_server._http_authenticated.set(True)
    try:
        assert mcp_server._resolve_mcp_agent(None) == "claude"
    finally:
        mcp_server._http_authenticated.reset(reset)


def test_resolve_mcp_agent_stdio_context_defaults_to_unknown(monkeypatch) -> None:
    monkeypatch.delenv("FRITZ_AGENT", raising=False)
    assert mcp_server._http_authenticated.get() is False
    assert mcp_server._resolve_mcp_agent(None) == "unknown"


def test_resolve_mcp_agent_explicit_arg_overrides_http_default(monkeypatch) -> None:
    monkeypatch.delenv("FRITZ_AGENT", raising=False)
    reset = mcp_server._http_authenticated.set(True)
    try:
        assert mcp_server._resolve_mcp_agent("pi") == "pi"
    finally:
        mcp_server._http_authenticated.reset(reset)


def test_resolve_mcp_agent_fritz_agent_overrides_http_default(monkeypatch) -> None:
    monkeypatch.setenv("FRITZ_AGENT", "codex")
    reset = mcp_server._http_authenticated.set(True)
    try:
        assert mcp_server._resolve_mcp_agent(None) == "codex"
    finally:
        mcp_server._http_authenticated.reset(reset)
