"""HTTP tests for run-detail + per-agent detail endpoints (#223).

Covers:
- GET /v1/runs list + limit + kind filter.
- GET /v1/runs/{id} full detail (fields, errors-as-messages, source, dry_run) + 404.
- GET /v1/runs/recent alias still returns its original shape.
- GET /v1/usage/agents/{agent} first/last seen, breakdown, daily series,
  paginated recent events, test-agent isolation, unknown agent empty shape.
- AUTH: new endpoints 401 without the Bearer token.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from fritz_local_brain import telemetry, run_history
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


def _run(settings, **overrides):
    base = dict(
        id="r1",
        kind="compile",
        started_at="2026-06-01T12:00:00+00:00",
        finished_at="2026-06-01T12:00:05+00:00",
        duration_ms=5000,
        dry_run=False,
        status="error",
        source="scheduler",
        summary="3 captures, 1 applied, 2 errors",
        detail={"captures_considered": 3, "applied": 1, "errors": ["boom", "bang"]},
    )
    base.update(overrides)
    telemetry.record_run(settings, **base)


def _seed(settings, event_type, *, agent=None, vault=None, status="ok",
          duration_ms=None, day="2026-06-01", time="12:00:00"):
    ts = datetime.fromisoformat(f"{day}T{time}+00:00").astimezone(timezone.utc)
    telemetry.record_event(settings, event_type, agent=agent, vault=vault,
                           status=status, duration_ms=duration_ms, ts=ts)


# ---------------------------------------------------------------------------
# GET /v1/runs/{id} — full detail round-trip
# ---------------------------------------------------------------------------

def test_run_detail_returns_all_fields(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _run(settings)
    client = _client(monkeypatch, settings)

    body = client.get("/v1/runs/r1", headers=_AUTH).json()
    assert body["id"] == "r1"
    assert body["kind"] == "compile"
    assert body["source"] == "scheduler"
    assert body["dry_run"] is False
    assert body["duration_ms"] == 5000
    assert body["status"] == "error"
    # errors surface as the actual messages at the top level.
    assert body["errors"] == ["boom", "bang"]
    assert body["detail"]["captures_considered"] == 3


def test_run_detail_unknown_id_404(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings)
    assert client.get("/v1/runs/nope", headers=_AUTH).status_code == 404


# ---------------------------------------------------------------------------
# GET /v1/runs — list + limit + kind
# ---------------------------------------------------------------------------

def test_runs_list_limit_and_kind(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _run(settings, id="c1", kind="compile", started_at="2026-06-01T10:00:00+00:00")
    _run(settings, id="s1", kind="sync", started_at="2026-06-02T10:00:00+00:00")
    _run(settings, id="c2", kind="compile", started_at="2026-06-03T10:00:00+00:00")
    client = _client(monkeypatch, settings)

    all_runs = client.get("/v1/runs", headers=_AUTH).json()["runs"]
    assert [r["id"] for r in all_runs] == ["c2", "s1", "c1"]  # newest first

    limited = client.get("/v1/runs?limit=1", headers=_AUTH).json()["runs"]
    assert [r["id"] for r in limited] == ["c2"]

    compiles = client.get("/v1/runs?kind=compile", headers=_AUTH).json()["runs"]
    assert {r["id"] for r in compiles} == {"c1", "c2"}


# ---------------------------------------------------------------------------
# /v1/runs/recent alias still works (unchanged shape)
# ---------------------------------------------------------------------------

def test_runs_recent_alias_unchanged_shape(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    run_history.clear_recent_runs_for_tests()
    from fritz_local_brain.models import CompileRunResult

    run_history.record_compile(
        CompileRunResult(
            run_id="rec-1",
            started_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            finished_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            dry_run=True,
            captures_considered=2,
        ),
        settings,
        source="api",
    )
    client = _client(monkeypatch, settings)

    body = client.get("/v1/runs/recent", headers=_AUTH).json()
    assert "runs" in body
    run = body["runs"][0]
    # Original RecentRun shape: run_id + summary string, NOT the rich detail keys.
    assert run["run_id"] == "rec-1"
    assert "summary" in run
    assert "detail" not in run
    run_history.clear_recent_runs_for_tests()


# ---------------------------------------------------------------------------
# GET /v1/usage/agents/{agent}
# ---------------------------------------------------------------------------

def test_agent_detail_breakdown_series_and_recent(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed(settings, "capture", agent="pi", day="2026-06-01", time="09:00:00")
    _seed(settings, "query", agent="pi", day="2026-06-01", time="10:00:00", duration_ms=12)
    _seed(settings, "capture", agent="pi", day="2026-06-02", time="11:00:00")
    # a different agent must not leak in
    _seed(settings, "capture", agent="claude", day="2026-06-01")
    client = _client(monkeypatch, settings)

    body = client.get("/v1/usage/agents/pi", headers=_AUTH).json()
    assert body["agent"] == "pi"
    assert body["event_count"] == 3
    assert body["events_by_type"] == {"capture": 2, "query": 1}
    assert body["daily_activity"] == {"2026-06-01": 2, "2026-06-02": 1}
    assert body["first_seen"].startswith("2026-06-01")
    assert body["last_seen"].startswith("2026-06-02")
    # recent events newest first
    types = [e["event_type"] for e in body["recent_events"]]
    assert types[0] == "capture"  # 2026-06-02 is newest
    assert "claude" not in str(body["recent_events"])


def test_agent_detail_pagination(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    for i in range(5):
        _seed(settings, "capture", agent="pi", day="2026-06-01", time=f"1{i}:00:00")
    client = _client(monkeypatch, settings)

    page1 = client.get("/v1/usage/agents/pi?limit=2&offset=0", headers=_AUTH).json()
    page2 = client.get("/v1/usage/agents/pi?limit=2&offset=2", headers=_AUTH).json()
    assert len(page1["recent_events"]) == 2
    assert len(page2["recent_events"]) == 2
    # no overlap between pages
    ts1 = {e["ts"] for e in page1["recent_events"]}
    ts2 = {e["ts"] for e in page2["recent_events"]}
    assert ts1.isdisjoint(ts2)
    assert page1["event_count"] == 5  # count is total, not page size


def test_agent_detail_unknown_agent_empty_shape(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed(settings, "capture", agent="pi")
    client = _client(monkeypatch, settings)

    body = client.get("/v1/usage/agents/ghost", headers=_AUTH).json()
    assert body["agent"] == "ghost"
    assert body["event_count"] == 0
    assert body["events_by_type"] == {}
    assert body["daily_activity"] == {}
    assert body["recent_events"] == []
    assert body["first_seen"] is None


def test_agent_detail_test_agent_isolation(monkeypatch, tmp_path) -> None:
    """A real agent's detail excludes test-agent events, and vice-versa (#206)."""
    settings = _settings(tmp_path)
    _seed(settings, "capture", agent="pi")
    _seed(settings, "query", agent="pwtest199")
    _seed(settings, "capture", agent="diag")
    client = _client(monkeypatch, settings)

    real = client.get("/v1/usage/agents/pi", headers=_AUTH).json()
    assert real["event_count"] == 1
    assert set(real["events_by_type"]) == {"capture"}

    # Querying the test agent returns ONLY its own rows — never the real agent's.
    test = client.get("/v1/usage/agents/pwtest199", headers=_AUTH).json()
    assert test["event_count"] == 1
    assert set(test["events_by_type"]) == {"query"}


# ---------------------------------------------------------------------------
# AUTH
# ---------------------------------------------------------------------------

def test_new_endpoints_require_token(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings)
    assert client.get("/v1/runs").status_code == 401
    assert client.get("/v1/runs/x").status_code == 401
    assert client.get("/v1/usage/agents/pi").status_code == 401
