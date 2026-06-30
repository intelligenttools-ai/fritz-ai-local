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
    # Day 1: 2 compile (pi), 1 query (codex, vault a)
    _seed(settings, "compile", agent="pi", vault="a", day="2026-06-01")
    _seed(settings, "compile", agent="pi", vault="a", day="2026-06-01")
    _seed(settings, "query", agent="codex", vault="a", day="2026-06-01")
    # Day 2: 1 query (pi, vault b)
    _seed(settings, "query", agent="pi", vault="b", day="2026-06-02")
    client = _client(monkeypatch, settings)

    by_type = client.get("/v1/usage/activity?by=type", headers=_AUTH).json()["buckets"]
    assert by_type == {
        "2026-06-01": {"compile": 2, "query": 1},
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
# AUTH: every endpoint 401 without bearer token
# ---------------------------------------------------------------------------

def test_all_usage_endpoints_require_auth(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(routes.usage, "compute_kb_health", lambda s: {"articles_total": 0, "backlog": {}})
    client = _client(monkeypatch, settings)
    for path in (
        "/v1/usage/activity",
        "/v1/usage/queries",
        "/v1/usage/knowledge",
        "/v1/usage/projects",
        "/v1/usage/summary",
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

    assert client.get("/v1/usage/activity", headers=_AUTH).json()["buckets"] == {}
    q = client.get("/v1/usage/queries", headers=_AUTH).json()
    assert q["total"] == 0 and q["hit_rate"] is None
    assert client.get("/v1/usage/knowledge", headers=_AUTH).status_code == 200
    assert client.get("/v1/usage/projects", headers=_AUTH).json()["projects"] == []
    s = client.get("/v1/usage/summary", headers=_AUTH).json()
    assert s["total_events"] == 0 and s["hit_rate"] is None
