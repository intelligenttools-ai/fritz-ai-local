"""Tests for read-side telemetry instrumentation (#178).

Acceptance mapping:
A. query_run records event_type "query"; search_run records "search";
   payload has result_count, hit, scope, use_vector, query (default); duration_ms int >= 0; run_id matches.
B. Agent resolution: header > body > "unknown".
C. hit/miss: 0 matches → hit False, result_count 0; ≥1 → hit True.
D. use_vector True for search_run, False for query_run.
E. TELEMETRY_ENABLED=False → no event recorded.
F. TELEMETRY_STORE_QUERY_TEXT=False → payload lacks "query" key; result_count/hit still present.
G. Defensive: record_event raising never breaks the response.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from fritz_local_brain import telemetry
from fritz_local_brain.api import routes
from fritz_local_brain.config import Settings
from fritz_local_brain.models import QueryMatch, QueryRunRequest, QueryRunResult
from fritz_local_brain.telemetry import read_events


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path, LOCAL_BRAIN_API_TOKEN="secret", **overrides)


def _result(query: str = "test", *, matches: int = 0, errors: list[str] | None = None) -> QueryRunResult:
    """Build a minimal QueryRunResult with controlled match count."""
    now = datetime.now()
    return QueryRunResult(
        run_id="run-abc123",
        started_at=now,
        finished_at=now,
        query=query,
        matches=[
            QueryMatch(vault="v", path=f"doc{i}.md", title=f"Title {i}", snippet="s")
            for i in range(matches)
        ],
        skipped=[],
        errors=errors or [],
    )


def _fake_run_query(fixed_result: QueryRunResult):
    async def _inner(settings, request, *, use_vector=False, ensure_index=False):
        return fixed_result
    return _inner


# ---------------------------------------------------------------------------
# A. Basic event recorded: event_type, payload fields, duration_ms, run_id
# ---------------------------------------------------------------------------

def test_query_run_records_query_event(monkeypatch, tmp_path) -> None:
    """query_run emits event_type='query' with expected payload."""
    settings = _settings(tmp_path)
    result = _result("hello world", matches=2)

    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "run_query", _fake_run_query(result))

    asyncio.run(routes.query_run(QueryRunRequest(query="hello world")))

    events = read_events(settings)
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "query"
    payload = json.loads(ev["payload"])
    assert payload["result_count"] == 2
    assert payload["hit"] is True
    assert payload["scope"] == "active"
    assert payload["use_vector"] is False
    assert payload["query"] == "hello world"
    assert ev["duration_ms"] >= 0
    assert ev["run_id"] == result.run_id


def test_search_run_records_search_event(monkeypatch, tmp_path) -> None:
    """search_run emits event_type='search' with use_vector True."""
    settings = _settings(tmp_path)
    result = _result("semantic", matches=1)

    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "run_query", _fake_run_query(result))

    asyncio.run(routes.search_run(QueryRunRequest(query="semantic")))

    events = read_events(settings)
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "search"
    payload = json.loads(ev["payload"])
    assert payload["use_vector"] is True
    assert payload["result_count"] == 1
    assert payload["hit"] is True


# ---------------------------------------------------------------------------
# B. Agent resolution: header > body > "unknown"
# ---------------------------------------------------------------------------

def test_agent_from_header(monkeypatch, tmp_path) -> None:
    """X-Brain-Agent header takes precedence."""
    settings = _settings(tmp_path)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "run_query", _fake_run_query(_result()))

    asyncio.run(routes.query_run(QueryRunRequest(query="q"), x_brain_agent="pi"))

    ev = read_events(settings)[0]
    assert ev["agent"] == "pi"


def test_agent_from_body_when_no_header(monkeypatch, tmp_path) -> None:
    """Body agent field used when no header."""
    settings = _settings(tmp_path)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "run_query", _fake_run_query(_result()))

    asyncio.run(routes.query_run(QueryRunRequest(query="q", agent="codex"), x_brain_agent=None))

    ev = read_events(settings)[0]
    assert ev["agent"] == "codex"


def test_agent_defaults_to_unknown(monkeypatch, tmp_path) -> None:
    """Falls back to 'unknown' when neither header nor body agent provided."""
    settings = _settings(tmp_path)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "run_query", _fake_run_query(_result()))

    asyncio.run(routes.query_run(QueryRunRequest(query="q"), x_brain_agent=None))

    ev = read_events(settings)[0]
    assert ev["agent"] == "unknown"


def test_header_wins_over_body_agent(monkeypatch, tmp_path) -> None:
    """Header agent overrides body agent when both present."""
    settings = _settings(tmp_path)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "run_query", _fake_run_query(_result()))

    asyncio.run(routes.query_run(QueryRunRequest(query="q", agent="body-agent"), x_brain_agent="header-agent"))

    ev = read_events(settings)[0]
    assert ev["agent"] == "header-agent"


# ---------------------------------------------------------------------------
# C. hit vs miss
# ---------------------------------------------------------------------------

def test_hit_false_when_no_matches(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "run_query", _fake_run_query(_result(matches=0)))

    asyncio.run(routes.query_run(QueryRunRequest(query="q")))

    payload = json.loads(read_events(settings)[0]["payload"])
    assert payload["hit"] is False
    assert payload["result_count"] == 0


def test_hit_true_when_matches_present(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "run_query", _fake_run_query(_result(matches=3)))

    asyncio.run(routes.query_run(QueryRunRequest(query="q")))

    payload = json.loads(read_events(settings)[0]["payload"])
    assert payload["hit"] is True
    assert payload["result_count"] == 3


# ---------------------------------------------------------------------------
# D. use_vector True for search_run, False for query_run
# ---------------------------------------------------------------------------

def test_use_vector_false_for_query_run(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "run_query", _fake_run_query(_result()))

    asyncio.run(routes.query_run(QueryRunRequest(query="q")))

    payload = json.loads(read_events(settings)[0]["payload"])
    assert payload["use_vector"] is False


def test_use_vector_true_for_search_run(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "run_query", _fake_run_query(_result()))

    asyncio.run(routes.search_run(QueryRunRequest(query="q")))

    payload = json.loads(read_events(settings)[0]["payload"])
    assert payload["use_vector"] is True


# ---------------------------------------------------------------------------
# E. TELEMETRY_ENABLED=False → no event recorded
# ---------------------------------------------------------------------------

def test_no_event_when_telemetry_disabled(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path, LOCAL_BRAIN_TELEMETRY_ENABLED=False)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "run_query", _fake_run_query(_result()))

    asyncio.run(routes.query_run(QueryRunRequest(query="q")))

    assert read_events(settings) == []


# ---------------------------------------------------------------------------
# F. TELEMETRY_STORE_QUERY_TEXT=False → no "query" key in payload
# ---------------------------------------------------------------------------

def test_query_text_omitted_when_store_query_text_disabled(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path, LOCAL_BRAIN_TELEMETRY_STORE_QUERY_TEXT=False)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "run_query", _fake_run_query(_result(matches=1)))

    asyncio.run(routes.query_run(QueryRunRequest(query="sensitive text")))

    events = read_events(settings)
    assert len(events) == 1
    payload = json.loads(events[0]["payload"])
    assert "query" not in payload
    assert payload["result_count"] == 1
    assert payload["hit"] is True


# ---------------------------------------------------------------------------
# G. Defensive: record_event raising never breaks the response
# ---------------------------------------------------------------------------

def test_telemetry_failure_does_not_break_query_response(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    expected_result = _result(matches=2)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "run_query", _fake_run_query(expected_result))
    # Patch the ACTUAL writer invoked inside record_query_event (not routes), so a
    # write failure is genuinely raised on the recording path and must be swallowed.
    monkeypatch.setattr(
        telemetry, "record_event", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    actual = asyncio.run(routes.query_run(QueryRunRequest(query="q")))

    # Query result is returned unchanged AND the failed write recorded nothing.
    assert actual is expected_result
    assert read_events(settings) == []


# ---------------------------------------------------------------------------
# Status: "ok" vs "error"
# ---------------------------------------------------------------------------

def test_status_is_error_when_result_has_errors(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    result = _result(errors=["something went wrong"])
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "run_query", _fake_run_query(result))

    asyncio.run(routes.query_run(QueryRunRequest(query="q")))

    ev = read_events(settings)[0]
    assert ev["status"] == "error"


def test_status_is_ok_when_no_errors(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "run_query", _fake_run_query(_result()))

    asyncio.run(routes.query_run(QueryRunRequest(query="q")))

    ev = read_events(settings)[0]
    assert ev["status"] == "ok"


# ---------------------------------------------------------------------------
# FIX 1: whitespace-only agent value falls back to "unknown"
# ---------------------------------------------------------------------------

def test_whitespace_only_header_falls_back_to_unknown(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "run_query", _fake_run_query(_result()))

    asyncio.run(routes.query_run(QueryRunRequest(query="q"), x_brain_agent="   "))

    assert read_events(settings)[0]["agent"] == "unknown"


def test_whitespace_only_body_agent_falls_back_to_unknown(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "run_query", _fake_run_query(_result()))

    asyncio.run(routes.query_run(QueryRunRequest(query="q", agent="   "), x_brain_agent=None))

    assert read_events(settings)[0]["agent"] == "unknown"


# ---------------------------------------------------------------------------
# FIX 2: REAL HTTP-level tests via FastAPI TestClient.
#
# These exercise the actual request path — FastAPI's Header(alias="X-Brain-Agent")
# parsing AND the Bearer-token auth dependency — which direct function calls
# bypass. A future alias typo would leave the direct-call tests green but break
# real clients; these lock it down.
# ---------------------------------------------------------------------------

def _http_client(monkeypatch, tmp_path, *, result=None, **settings_overrides):
    """Build a TestClient over the real app wired to a tmp-brain settings object."""
    from fastapi.testclient import TestClient

    from fritz_local_brain.api import auth
    from fritz_local_brain.app import create_app

    settings = _settings(tmp_path, **settings_overrides)
    # Both routes.get_settings and auth.get_settings are independent module refs.
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "run_query", _fake_run_query(result or _result(matches=1)))

    client = TestClient(create_app())
    return client, settings


_AUTH = {"Authorization": "Bearer secret"}


def test_http_query_run_records_agent_from_header(monkeypatch, tmp_path) -> None:
    """Real POST with literal `X-Brain-Agent: pi` → recorded agent == 'pi' (alias parsing)."""
    client, settings = _http_client(monkeypatch, tmp_path)

    resp = client.post(
        "/v1/query/run",
        json={"query": "hello"},
        headers={**_AUTH, "X-Brain-Agent": "pi"},
    )

    assert resp.status_code == 200
    events = read_events(settings)
    assert len(events) == 1
    assert events[0]["event_type"] == "query"
    assert events[0]["agent"] == "pi"


def test_http_query_run_defaults_agent_to_unknown_without_header(monkeypatch, tmp_path) -> None:
    """Real POST with NO X-Brain-Agent header → recorded agent == 'unknown'."""
    client, settings = _http_client(monkeypatch, tmp_path)

    resp = client.post("/v1/query/run", json={"query": "hello"}, headers=_AUTH)

    assert resp.status_code == 200
    events = read_events(settings)
    assert len(events) == 1
    assert events[0]["agent"] == "unknown"


def test_http_header_alias_is_case_insensitive(monkeypatch, tmp_path) -> None:
    """Lowercase `x-brain-agent` header is parsed too (HTTP headers are case-insensitive)."""
    client, settings = _http_client(monkeypatch, tmp_path)

    resp = client.post(
        "/v1/query/run",
        json={"query": "hello"},
        headers={**_AUTH, "x-brain-agent": "pi"},
    )

    assert resp.status_code == 200
    assert read_events(settings)[0]["agent"] == "pi"


def test_http_whitespace_only_header_falls_back_to_unknown(monkeypatch, tmp_path) -> None:
    """Real POST with whitespace-only `X-Brain-Agent` → 'unknown' (covers FIX 1 over HTTP)."""
    client, settings = _http_client(monkeypatch, tmp_path)

    resp = client.post(
        "/v1/query/run",
        json={"query": "hello"},
        headers={**_AUTH, "X-Brain-Agent": "   "},
    )

    assert resp.status_code == 200
    assert read_events(settings)[0]["agent"] == "unknown"


def test_http_query_run_requires_auth(monkeypatch, tmp_path) -> None:
    """Real POST without Bearer token is rejected and records nothing."""
    client, settings = _http_client(monkeypatch, tmp_path)

    resp = client.post("/v1/query/run", json={"query": "hello"})

    assert resp.status_code == 401
    assert read_events(settings) == []
