"""Tests for the usage aggregation HTTP API (#181).

Acceptance mapping:
- activity: bucketed counts correct for by=type/agent/vault; from/to filtering
  excludes out-of-range days incl. inclusive-end-date boundary.
- queries: total, hit_rate, p50/p95/p99, by_agent, top_queries ordering+limit;
  no-query case → total 0, hit_rate None, latency nulls.
- knowledge: returns compute_kb_health snapshot (articles_total etc.).
- projects: per-vault counts merge event activity + kb articles_by_vault;
  vault=None handled under "(none)".
- summary: headline numbers correct.
- AUTH: every /v1/usage/* endpoint returns 401 without the Bearer token.
- empty store: every endpoint returns a sane empty result (no crash).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from fritz_local_brain import telemetry
from fritz_local_brain.api import auth, routes
from fritz_local_brain.app import create_app
from fritz_local_brain.config import Settings

_AUTH = {"Authorization": "Bearer secret"}


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path, LOCAL_BRAIN_API_TOKEN="secret", **overrides)


def _client(monkeypatch, settings) -> TestClient:
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    return TestClient(create_app())


def _seed(settings, event_type, *, agent=None, vault=None, status="ok",
          duration_ms=None, payload=None, day="2026-06-01", time="12:00:00"):
    ts = datetime.fromisoformat(f"{day}T{time}+00:00").astimezone(timezone.utc)
    telemetry.record_event(
        settings,
        event_type,
        agent=agent,
        vault=vault,
        status=status,
        duration_ms=duration_ms,
        payload=payload,
        ts=ts,
    )


# ---------------------------------------------------------------------------
# activity
# ---------------------------------------------------------------------------

def test_activity_buckets_by_type_agent_vault(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    # Day 1: 2 capture (pi), 1 query (codex, vault a). Uses AGENT-side event
    # types so the by=agent dimension is meaningful (compile is a SYSTEM type,
    # #205, and is excluded from by=agent).
    _seed(settings, "capture", agent="pi", vault="a", day="2026-06-01")
    _seed(settings, "capture", agent="pi", vault="a", day="2026-06-01")
    _seed(settings, "query", agent="codex", vault="a", day="2026-06-01")
    # Day 2: 1 query (pi, vault b)
    _seed(settings, "query", agent="pi", vault="b", day="2026-06-02")
    client = _client(monkeypatch, settings)

    by_type = client.get("/v1/usage/activity?by=type", headers=_AUTH).json()["buckets"]
    assert by_type == {
        "2026-06-01": {"capture": 2, "query": 1},
        "2026-06-02": {"query": 1},
    }

    by_agent = client.get("/v1/usage/activity?by=agent", headers=_AUTH).json()["buckets"]
    assert by_agent == {
        "2026-06-01": {"pi": 2, "codex": 1},
        "2026-06-02": {"pi": 1},
    }

    by_vault = client.get("/v1/usage/activity?by=vault", headers=_AUTH).json()["buckets"]
    assert by_vault == {
        "2026-06-01": {"a": 3},
        "2026-06-02": {"b": 1},
    }


def test_activity_from_to_filtering_inclusive_end(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed(settings, "compile", day="2026-06-01")
    _seed(settings, "compile", day="2026-06-02")
    _seed(settings, "compile", day="2026-06-03", time="23:59:00")
    _seed(settings, "compile", day="2026-06-04")
    client = _client(monkeypatch, settings)

    # from=02, to=03 → only days 02 and 03 (03 inclusive incl. its late event).
    buckets = client.get(
        "/v1/usage/activity?from=2026-06-02&to=2026-06-03", headers=_AUTH
    ).json()["buckets"]
    assert set(buckets) == {"2026-06-02", "2026-06-03"}
    assert buckets["2026-06-03"] == {"compile": 1}


# ---------------------------------------------------------------------------
# query_events date-bound normalization (FIX 1)
# ---------------------------------------------------------------------------

def test_query_events_full_iso_offset_since(tmp_path) -> None:
    """A full-ISO since with +02:00 offset filters on the UTC instant.

    Event is stored at 11:00 UTC (= 13:00 +02:00). since="...12:00:00+02:00"
    is 10:00 UTC, so the event is INCLUDED; since="...14:00:00+02:00" is
    12:00 UTC, so it is EXCLUDED.
    """
    settings = _settings(tmp_path)
    _seed(settings, "compile", day="2026-06-03", time="11:00:00")  # 11:00 UTC

    included = telemetry.query_events(settings, since="2026-06-03T12:00:00+02:00")
    assert len(included) == 1

    excluded = telemetry.query_events(settings, since="2026-06-03T14:00:00+02:00")
    assert excluded == []


def test_query_events_full_iso_offset_until(tmp_path) -> None:
    """A full-ISO until with +02:00 offset is exclusive on the UTC instant."""
    settings = _settings(tmp_path)
    _seed(settings, "compile", day="2026-06-03", time="11:00:00")  # 11:00 UTC

    # until = 14:00 +02:00 = 12:00 UTC (exclusive) -> 11:00 UTC included.
    kept = telemetry.query_events(settings, until="2026-06-03T14:00:00+02:00")
    assert len(kept) == 1

    # until = 12:00 +02:00 = 10:00 UTC (exclusive) -> 11:00 UTC excluded.
    dropped = telemetry.query_events(settings, until="2026-06-03T12:00:00+02:00")
    assert dropped == []


def test_query_events_malformed_bare_date_ignored(tmp_path) -> None:
    """A malformed bare date ("2026-06-3") is ignored, not used as a filter.

    Lexicographically "2026-06-3" > "2026-06-03T..." so naive comparison would
    drop a valid same-day event; the bound must be ignored instead.
    """
    settings = _settings(tmp_path)
    _seed(settings, "compile", day="2026-06-03", time="12:00:00")

    # Malformed since -> ignored -> event still returned.
    assert len(telemetry.query_events(settings, since="2026-06-3")) == 1
    # Malformed until -> ignored -> event still returned.
    assert len(telemetry.query_events(settings, until="2026-06-3")) == 1


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------

def test_queries_aggregates(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    # 4 query/search events, hits: 3 of 4 → hit_rate 0.75
    _seed(settings, "query", agent="pi", duration_ms=10, payload={"hit": True, "query": "alpha"})
    _seed(settings, "query", agent="pi", duration_ms=20, payload={"hit": True, "query": "alpha"})
    _seed(settings, "search", agent="codex", duration_ms=30, payload={"hit": True, "query": "beta"})
    _seed(settings, "search", agent="codex", duration_ms=40, payload={"hit": False, "query": "gamma"})
    # A non-query event must be ignored.
    _seed(settings, "compile", agent="pi", duration_ms=999)
    client = _client(monkeypatch, settings)

    body = client.get("/v1/usage/queries", headers=_AUTH).json()
    assert body["total"] == 4
    assert body["hit_rate"] == 0.75
    # nearest-rank over sorted [10,20,30,40]: p50=ceil(.5*4)=2 →20; p95=4→40; p99=4→40
    assert body["latency_ms"] == {"p50": 20, "p95": 40, "p99": 40}
    assert body["by_agent"] == {"pi": 2, "codex": 2}
    # top_queries: alpha=2 first, then beta=1, gamma=1
    assert body["top_queries"][0] == {"query": "alpha", "count": 2}


def test_queries_top_queries_limit(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    for text, n in [("a", 3), ("b", 2), ("c", 1)]:
        for _ in range(n):
            _seed(settings, "query", payload={"hit": True, "query": text})
    client = _client(monkeypatch, settings)

    body = client.get("/v1/usage/queries?limit=2", headers=_AUTH).json()
    assert [t["query"] for t in body["top_queries"]] == ["a", "b"]


def test_queries_empty(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings)
    body = client.get("/v1/usage/queries", headers=_AUTH).json()
    assert body["total"] == 0
    assert body["hit_rate"] is None
    assert body["latency_ms"] == {"p50": None, "p95": None, "p99": None}
    assert body["by_agent"] == {}
    assert body["top_queries"] == []


# ---------------------------------------------------------------------------
# knowledge
# ---------------------------------------------------------------------------

def test_knowledge_snapshot(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    snapshot = {
        "articles_total": 7,
        "articles_by_status": {"active": 7},
        "articles_by_vault": {"a": 7},
        "growth_by_day": {"2026-06-01": 7},
        "embedding": {"documents_indexed": 5, "skipped": 0, "index_size_bytes": 10},
        "compile": {"total": 1, "ok": 1, "error": 0, "success_rate": 1.0},
        "backlog": {"pending_captures_by_source": {"agent": 2}},
    }
    monkeypatch.setattr(routes.usage, "compute_kb_health", lambda s: snapshot)
    client = _client(monkeypatch, settings)

    body = client.get("/v1/usage/knowledge", headers=_AUTH).json()
    assert body["articles_total"] == 7
    assert body["articles_by_vault"] == {"a": 7}
    assert body["backlog"] == {"pending_captures_by_source": {"agent": 2}}


def test_knowledge_extra_key_survives_serialization(monkeypatch, tmp_path) -> None:
    """A future key added to compute_kb_health passes through (extra='allow')."""
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        routes.usage,
        "compute_kb_health",
        lambda s: {"articles_total": 1, "future_metric": {"nested": 42}},
    )
    client = _client(monkeypatch, settings)

    body = client.get("/v1/usage/knowledge", headers=_AUTH).json()
    assert body["articles_total"] == 1
    assert body["future_metric"] == {"nested": 42}


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------

def test_projects_merges_events_and_articles(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed(settings, "compile", vault="a")
    _seed(settings, "query", vault="a")
    _seed(settings, "query", vault="b")
    _seed(settings, "capture", vault=None)  # → "(none)"
    monkeypatch.setattr(
        routes.usage,
        "compute_kb_health",
        lambda s: {"articles_by_vault": {"a": 5, "c": 2}, "articles_total": 7, "backlog": {}},
    )
    client = _client(monkeypatch, settings)

    projects = {p["vault"]: p for p in client.get("/v1/usage/projects", headers=_AUTH).json()["projects"]}
    assert projects["a"]["event_count"] == 2
    assert projects["a"]["events_by_type"] == {"compile": 1, "query": 1}
    assert projects["a"]["article_count"] == 5
    assert projects["b"]["event_count"] == 1
    assert projects["b"]["article_count"] == 0
    assert projects["c"]["event_count"] == 0  # only in kb articles
    assert projects["c"]["article_count"] == 2
    assert projects["(none)"]["event_count"] == 1


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

def test_summary_headline_numbers(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed(settings, "compile", agent="pi")
    _seed(settings, "query", agent="pi", payload={"hit": True})
    _seed(settings, "query", agent="codex", payload={"hit": False})
    monkeypatch.setattr(
        routes.usage,
        "compute_kb_health",
        lambda s: {
            "articles_total": 9,
            "backlog": {"pending_captures_by_source": {"x": 2, "y": 3}},
        },
    )
    client = _client(monkeypatch, settings)

    body = client.get("/v1/usage/summary", headers=_AUTH).json()
    assert body["total_events"] == 3
    assert body["events_by_type"] == {"compile": 1, "query": 2}
    assert body["total_queries"] == 2
    assert body["hit_rate"] == 0.5
    assert body["total_articles"] == 9
    assert body["backlog_pending"] == 5
    assert body["distinct_agents"] == 2


# ---------------------------------------------------------------------------
# agents discovery + per-agent filtering (#199)
# ---------------------------------------------------------------------------

def test_agents_discovered_dynamically_with_counts_and_seen(monkeypatch, tmp_path) -> None:
    """/v1/usage/agents returns distinct agents with counts + first/last seen,
    discovered from the store. Includes the literal "unknown" value AND a
    brand-new agent ("novelagent") with NO code change — the anti-hardcoding
    acceptance for #199."""
    settings = _settings(tmp_path)
    _seed(settings, "query", agent="pi", day="2026-06-01", time="08:00:00")
    _seed(settings, "query", agent="pi", day="2026-06-03", time="20:00:00")
    # Agent-side event types (SYSTEM types are excluded from agents(), #205).
    _seed(settings, "query", agent="unknown", day="2026-06-02")
    # A runtime that did not exist when this code was written. It MUST appear.
    _seed(settings, "capture", agent="novelagent", day="2026-06-05")
    # Empty/None agent must normalize to "unknown" (merges with the literal one).
    _seed(settings, "capture", agent=None, day="2026-06-04")
    client = _client(monkeypatch, settings)

    body = client.get("/v1/usage/agents", headers=_AUTH).json()
    by_agent = {a["agent"]: a for a in body["agents"]}

    # Brand-new value appears with zero code change (anti-hardcoding assertion).
    assert "novelagent" in by_agent
    assert by_agent["novelagent"]["count"] == 1

    # "unknown" is a normal, selectable value; None merged into it -> count 2.
    assert by_agent["unknown"]["count"] == 2

    # pi: count 2, first/last seen span the two days.
    assert by_agent["pi"]["count"] == 2
    assert by_agent["pi"]["first_seen"].startswith("2026-06-01")
    assert by_agent["pi"]["last_seen"].startswith("2026-06-03")

    # Sorted by count desc, then agent asc: pi(2), unknown(2), novelagent(1).
    assert [a["agent"] for a in body["agents"]] == ["pi", "unknown", "novelagent"]


def test_agents_empty_store(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings)
    assert client.get("/v1/usage/agents", headers=_AUTH).json()["agents"] == []


def test_agent_filter_scopes_activity(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    # Agent-side event types (SYSTEM types are excluded from by=agent, #205).
    _seed(settings, "capture", agent="pi", day="2026-06-01")
    _seed(settings, "capture", agent="pi", day="2026-06-01")
    _seed(settings, "capture", agent="codex", day="2026-06-01")
    client = _client(monkeypatch, settings)

    # Unfiltered: both agents counted by type.
    all_b = client.get("/v1/usage/activity?by=agent", headers=_AUTH).json()["buckets"]
    assert all_b == {"2026-06-01": {"pi": 2, "codex": 1}}

    # agent=pi excludes codex entirely.
    pi_b = client.get("/v1/usage/activity?by=agent&agent=pi", headers=_AUTH).json()["buckets"]
    assert pi_b == {"2026-06-01": {"pi": 2}}


def test_agent_filter_scopes_queries(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed(settings, "query", agent="pi", payload={"hit": True})
    _seed(settings, "query", agent="pi", payload={"hit": True})
    _seed(settings, "query", agent="codex", payload={"hit": False})
    client = _client(monkeypatch, settings)

    body = client.get("/v1/usage/queries?agent=pi", headers=_AUTH).json()
    assert body["total"] == 2
    assert body["by_agent"] == {"pi": 2}  # codex excluded


def test_agent_filter_scopes_summary(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(routes.usage, "compute_kb_health", lambda s: {"articles_total": 0, "backlog": {}})
    _seed(settings, "compile", agent="pi")
    _seed(settings, "query", agent="pi", payload={"hit": True})
    _seed(settings, "query", agent="codex", payload={"hit": False})
    client = _client(monkeypatch, settings)

    body = client.get("/v1/usage/summary?agent=pi", headers=_AUTH).json()
    assert body["total_events"] == 2  # codex query excluded
    assert body["distinct_agents"] == 1
    assert body["events_by_type"] == {"compile": 1, "query": 1}


def test_agent_filter_scopes_projects(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        routes.usage, "compute_kb_health",
        lambda s: {"articles_by_vault": {}, "articles_total": 0, "backlog": {}},
    )
    _seed(settings, "compile", agent="pi", vault="a")
    _seed(settings, "compile", agent="codex", vault="a")
    client = _client(monkeypatch, settings)

    projects = {p["vault"]: p for p in
                client.get("/v1/usage/projects?agent=pi", headers=_AUTH).json()["projects"]}
    assert projects["a"]["event_count"] == 1  # only pi's event


# ---------------------------------------------------------------------------
# SYSTEM vs AGENT split (#205)
# ---------------------------------------------------------------------------

def test_agents_excludes_system_events_and_local_brain(monkeypatch, tmp_path) -> None:
    """agents() counts only agent-side events; the service's own system-type
    events (agent='local-brain') no longer surface local-brain as an agent."""
    settings = _settings(tmp_path)
    # SYSTEM events, all recorded as the service agent "local-brain".
    _seed(settings, "compile", agent="local-brain")
    _seed(settings, "embeddings", agent="local-brain")
    _seed(settings, "reconcile", agent="local-brain")
    # AGENT events from real runtimes.
    _seed(settings, "capture", agent="pi")
    _seed(settings, "query", agent="claude", payload={"hit": True})
    _seed(settings, "query", agent=None, payload={"hit": True})  # -> unknown
    client = _client(monkeypatch, settings)

    body = client.get("/v1/usage/agents", headers=_AUTH).json()
    names = {a["agent"] for a in body["agents"]}
    assert "local-brain" not in names, "system agent must be excluded from agents()"
    assert names == {"pi", "claude", "unknown"}


def test_activity_by_agent_excludes_system_by_type_keeps_all(monkeypatch, tmp_path) -> None:
    """by=agent excludes system events; by=type still counts system + agent."""
    settings = _settings(tmp_path)
    _seed(settings, "compile", agent="local-brain", day="2026-06-01")
    _seed(settings, "embeddings", agent="local-brain", day="2026-06-01")
    _seed(settings, "capture", agent="pi", day="2026-06-01")
    _seed(settings, "query", agent="claude", day="2026-06-01", payload={"hit": True})
    client = _client(monkeypatch, settings)

    by_agent = client.get("/v1/usage/activity?by=agent", headers=_AUTH).json()["buckets"]
    # System events (local-brain) excluded from the by-agent view.
    assert by_agent == {"2026-06-01": {"pi": 1, "claude": 1}}

    by_type = client.get("/v1/usage/activity?by=type", headers=_AUTH).json()["buckets"]
    # by=type keeps ALL events, including the system types.
    assert by_type == {
        "2026-06-01": {"compile": 1, "embeddings": 1, "capture": 1, "query": 1}
    }


def test_system_endpoint_per_type_counts_and_success_rate(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed(settings, "compile", agent="local-brain", status="ok")
    _seed(settings, "compile", agent="local-brain", status="error")
    _seed(settings, "embeddings", agent="local-brain", status="ok")
    _seed(settings, "reconcile", agent="local-brain", status="ok")
    # Agent-side events must NOT appear in the system aggregate.
    _seed(settings, "capture", agent="pi")
    _seed(settings, "query", agent="claude", payload={"hit": True})
    client = _client(monkeypatch, settings)

    body = client.get("/v1/usage/system", headers=_AUTH).json()
    assert body["by_type"]["compile"] == {"total": 2, "ok": 1, "error": 1}
    assert body["by_type"]["embeddings"] == {"total": 1, "ok": 1, "error": 0}
    assert body["by_type"]["reconcile"] == {"total": 1, "ok": 1, "error": 0}
    assert "capture" not in body["by_type"] and "query" not in body["by_type"]
    assert body["total"] == 4
    assert body["ok"] == 3
    assert body["error"] == 1
    assert body["success_rate"] == 0.75


def test_system_endpoint_empty_store_safe(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings)
    body = client.get("/v1/usage/system", headers=_AUTH).json()
    assert body["by_type"] == {}
    assert body["total"] == 0
    assert body["success_rate"] is None


def test_system_endpoint_requires_auth(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings)
    assert client.get("/v1/usage/system").status_code == 401


def test_no_double_counting_system_vs_agent(monkeypatch, tmp_path) -> None:
    """A compile (system) event appears in /system + activity by=type, but NOT
    in /agents or activity by=agent."""
    settings = _settings(tmp_path)
    _seed(settings, "compile", agent="local-brain", day="2026-06-01")
    _seed(settings, "capture", agent="pi", day="2026-06-01")
    client = _client(monkeypatch, settings)

    # System side: compile present.
    sys_body = client.get("/v1/usage/system", headers=_AUTH).json()
    assert sys_body["by_type"]["compile"]["total"] == 1

    # by=type: compile counted.
    by_type = client.get("/v1/usage/activity?by=type", headers=_AUTH).json()["buckets"]
    assert by_type["2026-06-01"].get("compile") == 1

    # Agents side: no local-brain, no compile leakage.
    agents = {a["agent"] for a in client.get("/v1/usage/agents", headers=_AUTH).json()["agents"]}
    assert agents == {"pi"}
    by_agent = client.get("/v1/usage/activity?by=agent", headers=_AUTH).json()["buckets"]
    assert by_agent == {"2026-06-01": {"pi": 1}}


def test_unknown_event_type_stays_agent_side(monkeypatch, tmp_path) -> None:
    """A brand-new/unknown event_type is NOT system: it stays in agents() and the
    by=agent activity so it never silently vanishes from totals."""
    settings = _settings(tmp_path)
    _seed(settings, "novelop", agent="pi", day="2026-06-01")
    client = _client(monkeypatch, settings)

    agents = {a["agent"] for a in client.get("/v1/usage/agents", headers=_AUTH).json()["agents"]}
    assert agents == {"pi"}
    sys_body = client.get("/v1/usage/system", headers=_AUTH).json()
    assert sys_body["total"] == 0  # unknown type is not system


# ---------------------------------------------------------------------------
# AUTH: every endpoint 401 without bearer token
# ---------------------------------------------------------------------------

def test_all_usage_endpoints_require_auth(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(routes.usage, "compute_kb_health", lambda s: {"articles_total": 0, "backlog": {}})
    client = _client(monkeypatch, settings)
    for path in (
        "/v1/usage/agents",
        "/v1/usage/activity",
        "/v1/usage/queries",
        "/v1/usage/knowledge",
        "/v1/usage/projects",
        "/v1/usage/summary",
        "/v1/usage/system",
    ):
        assert client.get(path).status_code == 401, path


# ---------------------------------------------------------------------------
# empty store: every endpoint returns a sane empty result (no crash)
# ---------------------------------------------------------------------------

def test_all_usage_endpoints_empty_store(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        routes.usage,
        "compute_kb_health",
        lambda s: {"articles_total": 0, "articles_by_vault": {}, "backlog": {}},
    )
    client = _client(monkeypatch, settings)

    assert client.get("/v1/usage/agents", headers=_AUTH).json()["agents"] == []
    assert client.get("/v1/usage/activity", headers=_AUTH).json()["buckets"] == {}
    q = client.get("/v1/usage/queries", headers=_AUTH).json()
    assert q["total"] == 0 and q["hit_rate"] is None
    assert client.get("/v1/usage/knowledge", headers=_AUTH).status_code == 200
    assert client.get("/v1/usage/projects", headers=_AUTH).json()["projects"] == []
    s = client.get("/v1/usage/summary", headers=_AUTH).json()
    assert s["total_events"] == 0 and s["hit_rate"] is None
    sys_body = client.get("/v1/usage/system", headers=_AUTH).json()
    assert sys_body["by_type"] == {} and sys_body["success_rate"] is None
