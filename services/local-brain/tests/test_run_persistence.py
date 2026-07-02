"""Tests for durable run-detail persistence in the telemetry runs table (#223).

Covers: round-trip record/list/get, kind filter, retention pruning, schema
migration onto a pre-#223 DB, and run_history persisting a rich record.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fritz_local_brain import telemetry
from fritz_local_brain.config import Settings
from fritz_local_brain.models import CompileRunResult, SyncRunResult, SyncVaultResult
from fritz_local_brain import run_history


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path, **overrides)


def _record(settings, **overrides):
    base = dict(
        id="run-1",
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


# ---------------------------------------------------------------------------
# Round-trip: every field survives record -> get.
# ---------------------------------------------------------------------------

def test_record_run_roundtrip_all_fields(tmp_path) -> None:
    settings = _settings(tmp_path)
    _record(settings)

    row = telemetry.get_run(settings, "run-1")
    assert row is not None
    assert row["id"] == "run-1"
    assert row["kind"] == "compile"
    assert row["started_at"] == "2026-06-01T12:00:00+00:00"
    assert row["finished_at"] == "2026-06-01T12:00:05+00:00"
    assert row["duration_ms"] == 5000
    assert row["dry_run"] is False
    assert row["status"] == "error"
    assert row["source"] == "scheduler"
    assert row["summary"] == "3 captures, 1 applied, 2 errors"
    # errors persisted as the actual messages, not just a count.
    assert row["detail"]["errors"] == ["boom", "bang"]
    assert row["detail"]["captures_considered"] == 3
    assert row["detail"]["applied"] == 1


def test_get_run_unknown_returns_none(tmp_path) -> None:
    settings = _settings(tmp_path)
    _record(settings)
    assert telemetry.get_run(settings, "nope") is None


def test_record_run_replace_is_idempotent(tmp_path) -> None:
    settings = _settings(tmp_path)
    _record(settings, status="ok")
    _record(settings, status="error")  # same id
    rows = telemetry.list_runs(settings, limit=10)
    assert len(rows) == 1
    assert rows[0]["status"] == "error"


# ---------------------------------------------------------------------------
# list_runs: newest-first, limit, kind filter.
# ---------------------------------------------------------------------------

def test_list_runs_orders_newest_first_and_limits(tmp_path) -> None:
    settings = _settings(tmp_path)
    _record(settings, id="a", started_at="2026-06-01T10:00:00+00:00")
    _record(settings, id="b", started_at="2026-06-02T10:00:00+00:00")
    _record(settings, id="c", started_at="2026-06-03T10:00:00+00:00")

    rows = telemetry.list_runs(settings, limit=2)
    assert [r["id"] for r in rows] == ["c", "b"]


def test_list_runs_kind_filter(tmp_path) -> None:
    settings = _settings(tmp_path)
    _record(settings, id="c1", kind="compile", started_at="2026-06-01T10:00:00+00:00")
    _record(settings, id="s1", kind="sync", started_at="2026-06-02T10:00:00+00:00")

    only_sync = telemetry.list_runs(settings, limit=10, kind="sync")
    assert [r["id"] for r in only_sync] == ["s1"]
    only_compile = telemetry.list_runs(settings, limit=10, kind="compile")
    assert [r["id"] for r in only_compile] == ["c1"]


def test_list_runs_empty_when_no_db(tmp_path) -> None:
    settings = _settings(tmp_path)
    assert telemetry.list_runs(settings) == []
    assert telemetry.get_run(settings, "x") is None


def test_record_run_noop_when_disabled(tmp_path) -> None:
    settings = _settings(tmp_path, TELEMETRY_ENABLED="false")
    _record(settings)
    assert not (tmp_path / "telemetry.db").exists()
    assert telemetry.list_runs(settings) == []


# ---------------------------------------------------------------------------
# Retention: runs older than telemetry_retention_days are pruned.
# ---------------------------------------------------------------------------

def test_prune_old_runs_removes_stale_keeps_fresh(tmp_path) -> None:
    settings = _settings(tmp_path, TELEMETRY_RETENTION_DAYS="30")
    now = datetime.now(timezone.utc)
    _record(settings, id="old", started_at=(now - timedelta(days=100)).isoformat())
    _record(settings, id="fresh", started_at=(now - timedelta(days=1)).isoformat())

    deleted = telemetry.prune_old_runs(settings)
    assert deleted == 1
    remaining = telemetry.list_runs(settings, limit=10)
    assert [r["id"] for r in remaining] == ["fresh"]


def test_prune_old_runs_zero_retention_is_noop(tmp_path) -> None:
    settings = _settings(tmp_path, TELEMETRY_RETENTION_DAYS="0")
    now = datetime.now(timezone.utc)
    _record(settings, id="old", started_at=(now - timedelta(days=10000)).isoformat())
    assert telemetry.prune_old_runs(settings) == 0
    assert len(telemetry.list_runs(settings, limit=10)) == 1


# ---------------------------------------------------------------------------
# Timestamp canonicalization: naive-local input -> UTC-offset stored/returned,
# and prune uses an offset-aware comparison (same contract as the events table).
# ---------------------------------------------------------------------------

def test_record_run_canonicalizes_naive_timestamps_to_utc(tmp_path) -> None:
    settings = _settings(tmp_path)
    # Naive-local ISO strings (what run_history produces from datetime.now()):
    # no offset. record_run must store them with a UTC offset.
    _record(
        settings,
        id="naive",
        started_at="2026-06-01T08:32:04.574167",
        finished_at="2026-06-01T08:32:09.574167",
    )
    row = telemetry.get_run(settings, "naive")
    assert row is not None
    assert row["started_at"].endswith("+00:00")
    assert row["finished_at"].endswith("+00:00")
    # Same instant, now offset-aware (naive treated as UTC).
    assert row["started_at"] == "2026-06-01T08:32:04.574167+00:00"


def test_prune_old_runs_offset_aware_boundary(tmp_path) -> None:
    """A run whose NAIVE started_at is stale/fresh prunes correctly, because
    record_run stores it as UTC-offset — matching the UTC-aware cutoff string
    prune uses. On a non-UTC host an offset-less naive-local string would
    otherwise mis-sort against the cutoff by the host offset."""
    settings = _settings(tmp_path, TELEMETRY_RETENTION_DAYS="30")
    now = datetime.now(timezone.utc)
    # Stored as NAIVE ISO strings (no offset), like the real workflows do.
    stale_naive = (now - timedelta(days=100)).replace(tzinfo=None).isoformat()
    fresh_naive = (now - timedelta(days=1)).replace(tzinfo=None).isoformat()
    _record(settings, id="stale", started_at=stale_naive)
    _record(settings, id="fresh", started_at=fresh_naive)

    # The stored strings carry a UTC offset now.
    assert telemetry.get_run(settings, "stale")["started_at"].endswith("+00:00")

    deleted = telemetry.prune_old_runs(settings)
    assert deleted == 1
    assert [r["id"] for r in telemetry.list_runs(settings, limit=10)] == ["fresh"]


# ---------------------------------------------------------------------------
# Schema migration: a pre-#223 DB (events only, no runs table) gains it.
# ---------------------------------------------------------------------------

def test_existing_db_without_runs_table_gets_migrated(tmp_path) -> None:
    settings = _settings(tmp_path)
    db = tmp_path / "telemetry.db"
    # Simulate a pre-#223 DB: only the events table exists.
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, "
        "event_type TEXT, agent TEXT, vault TEXT, run_id TEXT, status TEXT, "
        "duration_ms INTEGER, payload TEXT)"
    )
    conn.commit()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "runs" not in tables
    conn.close()

    # A read against the runs table must create it (no crash), then a write round-trips.
    assert telemetry.list_runs(settings) == []
    _record(settings, id="mig")
    assert telemetry.get_run(settings, "mig") is not None

    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "runs" in tables


# ---------------------------------------------------------------------------
# run_history persists a rich record when given settings.
# ---------------------------------------------------------------------------

def test_record_compile_persists_rich_detail(tmp_path) -> None:
    settings = _settings(tmp_path)
    run_history.clear_recent_runs_for_tests()
    result = CompileRunResult(
        run_id="compile-xyz",
        started_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 6, 1, 12, 0, 3, tzinfo=timezone.utc),
        dry_run=False,
        captures_considered=5,
        captures_by_source={"inbox": 3, "daily": 2},
        applied=[],
        errors=["provider timeout"],
    )
    run_history.record_compile(result, settings, source="api")

    row = telemetry.get_run(settings, "compile-xyz")
    assert row is not None
    assert row["kind"] == "compile"
    assert row["source"] == "api"
    assert row["dry_run"] is False
    assert row["duration_ms"] == 3000
    assert row["status"] == "error"
    assert row["detail"]["captures_considered"] == 5
    assert row["detail"]["captures_by_source"] == {"inbox": 3, "daily": 2}
    assert row["detail"]["errors"] == ["provider timeout"]


def test_record_compile_without_settings_does_not_persist(tmp_path) -> None:
    """Backward compat: legacy callers omit settings -> no db, deque still works."""
    settings = _settings(tmp_path)
    run_history.clear_recent_runs_for_tests()
    result = CompileRunResult(
        run_id="legacy",
        started_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 6, 1, 12, 0, 1, tzinfo=timezone.utc),
        dry_run=True,
        captures_considered=1,
    )
    run_history.record_compile(result)  # no settings
    assert not (tmp_path / "telemetry.db").exists()
    assert run_history.recent_runs()[0].run_id == "legacy"


def test_record_sync_persists_errors_as_messages(tmp_path) -> None:
    settings = _settings(tmp_path)
    run_history.clear_recent_runs_for_tests()
    result = SyncRunResult(
        run_id="sync-1",
        started_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 6, 1, 12, 0, 2, tzinfo=timezone.utc),
        dry_run=False,
        results=[
            SyncVaultResult(vault="v1", target="t", first_sync=False, pushed=True),
            SyncVaultResult(vault="v2", target="t", first_sync=False, errors=["push rejected"]),
        ],
        errors=["top-level fail"],
    )
    run_history.record_sync(result, settings, source="agent")

    row = telemetry.get_run(settings, "sync-1")
    assert row is not None
    assert row["source"] == "agent"
    assert row["detail"]["pushes"] == 1
    assert row["detail"]["vaults"] == 2
    assert "top-level fail" in row["detail"]["errors"]
    assert "push rejected" in row["detail"]["errors"]
