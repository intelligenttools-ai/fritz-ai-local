"""Tests for telemetry retention pruning + privacy redaction (#183).

Acceptance mapping:
1. test_prune_removes_old_events_keeps_new_returns_count
2. test_retention_days_zero_is_noop_keeps_everything
3. test_disabled_telemetry_prune_is_noop_no_db_touched
4. test_store_query_text_false_omits_query_in_recorded_event
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fritz_local_brain import telemetry
from fritz_local_brain.config import Settings


def _settings(tmp_path, **overrides):
    return Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path, **overrides)


def _db_path(tmp_path):
    return tmp_path / "telemetry.db"


# Acceptance 1: prune deletes events older than retention, keeps newer, returns count.
def test_prune_removes_old_events_keeps_new_returns_count(tmp_path) -> None:
    settings = _settings(tmp_path, TELEMETRY_RETENTION_DAYS="30")

    now = datetime.now(timezone.utc)
    # Two stale events (well past the 30-day window) and one fresh event.
    telemetry.record_event(settings, "compile", ts=now - timedelta(days=100))
    telemetry.record_event(settings, "compile", ts=now - timedelta(days=40))
    telemetry.record_event(settings, "query", ts=now - timedelta(days=1))

    deleted = telemetry.prune_old_events(settings)

    assert deleted == 2
    remaining = telemetry.read_events(settings)
    assert len(remaining) == 1
    assert remaining[0]["event_type"] == "query"


# Acceptance 2: retention_days=0 -> keep forever (no-op).
def test_retention_days_zero_is_noop_keeps_everything(tmp_path) -> None:
    settings = _settings(tmp_path, TELEMETRY_RETENTION_DAYS="0")

    now = datetime.now(timezone.utc)
    telemetry.record_event(settings, "compile", ts=now - timedelta(days=10000))
    telemetry.record_event(settings, "query", ts=now)

    deleted = telemetry.prune_old_events(settings)

    assert deleted == 0
    assert len(telemetry.read_events(settings)) == 2


# Acceptance 3: telemetry disabled -> no-op, no db created, returns 0.
def test_disabled_telemetry_prune_is_noop_no_db_touched(tmp_path) -> None:
    settings = _settings(tmp_path, TELEMETRY_ENABLED="false", TELEMETRY_RETENTION_DAYS="30")

    assert not _db_path(tmp_path).exists()
    deleted = telemetry.prune_old_events(settings)

    assert deleted == 0
    assert not _db_path(tmp_path).exists()


def test_prune_on_missing_db_returns_zero(tmp_path) -> None:
    settings = _settings(tmp_path, TELEMETRY_RETENTION_DAYS="30")
    assert not _db_path(tmp_path).exists()
    assert telemetry.prune_old_events(settings) == 0
    assert not _db_path(tmp_path).exists()


def test_prune_quietly_swallows_errors(tmp_path) -> None:
    settings = _settings(tmp_path, TELEMETRY_RETENTION_DAYS="30")
    # Should never raise even with nothing recorded.
    telemetry.prune_old_events_quietly(settings)


# Acceptance 4: TELEMETRY_STORE_QUERY_TEXT=false -> recorded query event omits query text.
def test_store_query_text_false_omits_query_in_recorded_event(tmp_path) -> None:
    from types import SimpleNamespace

    settings = _settings(tmp_path, TELEMETRY_STORE_QUERY_TEXT="false")

    request = SimpleNamespace(query="my secret query", scope="all", vault="default")
    result = SimpleNamespace(matches=[1, 2], skipped=[], errors=[], run_id="run-1")

    telemetry.record_query_event(
        settings,
        use_vector=False,
        request=request,
        result=result,
        agent="tester",
        duration_ms=5,
    )

    events = telemetry.read_events(settings)
    assert len(events) == 1
    import json

    payload = json.loads(events[0]["payload"])
    assert "query" not in payload
    assert payload["result_count"] == 2


def test_store_query_text_true_includes_query(tmp_path) -> None:
    from types import SimpleNamespace

    settings = _settings(tmp_path, TELEMETRY_STORE_QUERY_TEXT="true")

    request = SimpleNamespace(query="visible query", scope="all", vault="default")
    result = SimpleNamespace(matches=[1], skipped=[], errors=[], run_id="run-2")

    telemetry.record_query_event(
        settings,
        use_vector=False,
        request=request,
        result=result,
        agent="tester",
        duration_ms=5,
    )

    import json

    payload = json.loads(telemetry.read_events(settings)[0]["payload"])
    assert payload["query"] == "visible query"
