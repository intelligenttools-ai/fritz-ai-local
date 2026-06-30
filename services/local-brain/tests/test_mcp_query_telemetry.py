"""Read-side attribution for the in-process MCP tools (#179).

The MCP brain_query / brain_search tools call run_query IN-PROCESS and bypass the
HTTP route telemetry from #178, so they must record telemetry themselves via the
shared record_query_event helper.

Acceptance mapping:
- MCP path records event_type "query" (brain_query) / "search" (brain_search).
- agent param wins (agent="pi" -> recorded agent "pi", non-"unknown").
- FRITZ_AGENT env used when no agent param.
- no agent + no env -> agent "unknown".
- result_count / hit present, gated query text present by default.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from fritz_local_brain import mcp_server
from fritz_local_brain.config import Settings
from fritz_local_brain.models import QueryMatch, QueryRunResult
from fritz_local_brain.telemetry import read_events


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path, LOCAL_BRAIN_API_TOKEN="secret", **overrides)


def _result(query: str = "needle", *, matches: int = 1) -> QueryRunResult:
    now = datetime.now()
    return QueryRunResult(
        run_id="run-mcp-1",
        started_at=now,
        finished_at=now,
        query=query,
        matches=[QueryMatch(vault="v", path=f"d{i}.md", title=f"T{i}", snippet="s") for i in range(matches)],
        skipped=[],
        errors=[],
    )


def _wire(monkeypatch, settings, result: QueryRunResult):
    async def fake_run_query(s, request, *, use_vector=False, ensure_index=False):
        return result

    monkeypatch.setattr(mcp_server, "get_settings", lambda: settings)
    monkeypatch.setattr(mcp_server, "run_query", fake_run_query)


def test_brain_query_records_query_event_with_agent(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _wire(monkeypatch, settings, _result("hello", matches=2))

    asyncio.run(mcp_server.brain_query("hello", api_token="secret", agent="pi"))

    events = read_events(settings)
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "query"
    assert ev["agent"] == "pi"
    payload = json.loads(ev["payload"])
    assert payload["result_count"] == 2
    assert payload["hit"] is True
    assert payload["use_vector"] is False
    assert payload["query"] == "hello"


def test_brain_search_records_search_event_with_agent(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _wire(monkeypatch, settings, _result("semantic", matches=1))

    asyncio.run(mcp_server.brain_search("semantic", api_token="secret", agent="pi"))

    events = read_events(settings)
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "search"
    assert ev["agent"] == "pi"
    payload = json.loads(ev["payload"])
    assert payload["use_vector"] is True
    assert payload["result_count"] == 1
    assert payload["hit"] is True


def test_brain_query_uses_fritz_agent_env(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _wire(monkeypatch, settings, _result())
    monkeypatch.setenv("FRITZ_AGENT", "codex")

    asyncio.run(mcp_server.brain_query("q", api_token="secret"))

    assert read_events(settings)[0]["agent"] == "codex"


def test_brain_query_defaults_to_unknown_without_agent_or_env(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _wire(monkeypatch, settings, _result())
    monkeypatch.delenv("FRITZ_AGENT", raising=False)

    asyncio.run(mcp_server.brain_query("q", api_token="secret"))

    assert read_events(settings)[0]["agent"] == "unknown"


def test_brain_query_agent_param_wins_over_env(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _wire(monkeypatch, settings, _result())
    monkeypatch.setenv("FRITZ_AGENT", "env-agent")

    asyncio.run(mcp_server.brain_query("q", api_token="secret", agent="param-agent"))

    assert read_events(settings)[0]["agent"] == "param-agent"
