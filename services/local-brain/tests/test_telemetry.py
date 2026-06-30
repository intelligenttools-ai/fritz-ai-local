"""Tests for the telemetry event store (#176).

Acceptance mapping:
1. test_first_record_creates_db_with_events_table_and_indexes
2. test_record_event_round_trips_all_fields
3. test_disabled_telemetry_writes_nothing_and_creates_no_db
4. test_import_has_no_db_side_effects
5. test_wal_mode_is_enabled_after_first_write
6. test_multiple_inserts_accumulate_and_are_ordered
7. test_naive_datetime_stored_as_utc
8. test_offset_datetime_normalized_to_utc
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

import pytest

from fritz_local_brain.config import Settings


def _settings(tmp_path, **overrides):
    return Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path, **overrides)


def _db_path(tmp_path):
    return tmp_path / "telemetry.db"


# Acceptance 1: table + indexes created on first use.
def test_first_record_creates_db_with_events_table_and_indexes(tmp_path) -> None:
    from fritz_local_brain import telemetry

    settings = _settings(tmp_path)
    assert not _db_path(tmp_path).exists()

    telemetry.record_event(settings, "capture")

    db = _db_path(tmp_path)
    assert db.exists()

    conn = sqlite3.connect(db)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "events" in tables

        cols = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
        assert cols == {
            "id",
            "ts",
            "event_type",
            "agent",
            "vault",
            "run_id",
            "status",
            "duration_ms",
            "payload",
        }

        indexed_cols = set()
        for idx in conn.execute("PRAGMA index_list(events)").fetchall():
            idx_name = idx[1]
            for info in conn.execute(f"PRAGMA index_info({idx_name})").fetchall():
                indexed_cols.add(info[2])
        # Indexes on (ts), (event_type), (agent), (vault).
        assert {"ts", "event_type", "agent", "vault"} <= indexed_cols
    finally:
        conn.close()


# Acceptance 2: full round-trip incl. payload JSON dict + nullable NULLs + UTC ts.
def test_record_event_round_trips_all_fields(tmp_path) -> None:
    from fritz_local_brain import telemetry

    settings = _settings(tmp_path)
    payload = {"b": 2, "a": 1, "nested": {"x": [1, 2, 3]}}
    fixed_ts = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

    telemetry.record_event(
        settings,
        "compile",
        agent="claude",
        vault="proj",
        run_id="run-123",
        status="ok",
        duration_ms=42,
        payload=payload,
        ts=fixed_ts,
    )

    rows = telemetry.read_events(settings)
    assert len(rows) == 1
    row = rows[0]
    assert row["event_type"] == "compile"
    assert row["agent"] == "claude"
    assert row["vault"] == "proj"
    assert row["run_id"] == "run-123"
    assert row["status"] == "ok"
    assert row["duration_ms"] == 42
    assert json.loads(row["payload"]) == payload
    # ts is stored as canonical UTC ISO-8601 string ending with +00:00.
    assert row["ts"] == "2024-06-15T12:00:00+00:00"
    assert row["ts"].endswith("+00:00")

    # Nullable fields stored as SQL NULL when omitted.
    telemetry.record_event(settings, "query")
    rows = telemetry.read_events(settings)
    minimal = rows[-1]
    assert minimal["event_type"] == "query"
    for field in ("agent", "vault", "run_id", "status", "duration_ms", "payload"):
        assert minimal[field] is None


# Acceptance 3: disabled telemetry writes nothing AND creates no db file.
def test_disabled_telemetry_writes_nothing_and_creates_no_db(tmp_path) -> None:
    from fritz_local_brain import telemetry

    settings = _settings(tmp_path, TELEMETRY_ENABLED="false")
    assert settings.telemetry_enabled is False

    telemetry.record_event(settings, "capture", agent="claude")

    assert not _db_path(tmp_path).exists()


# Acceptance 4: importing the module must not create a db (no import-time side effects).
def test_import_has_no_db_side_effects(tmp_path, monkeypatch) -> None:
    # Redirect any brain-home env vars so a regression could not touch ~/.brain.
    monkeypatch.setenv("BRAIN_HOME", str(tmp_path))
    monkeypatch.setenv("LOCAL_BRAIN_HOME", str(tmp_path))

    # Force a fresh import of the module.
    sys.modules.pop("fritz_local_brain.telemetry", None)
    importlib.import_module("fritz_local_brain.telemetry")

    # No db anywhere under the tmp brain home was created merely by importing.
    assert not _db_path(tmp_path).exists()
    assert list(tmp_path.glob("*.db")) == []


# Acceptance 5: WAL mode is enabled after first write.
def test_wal_mode_is_enabled_after_first_write(tmp_path) -> None:
    from fritz_local_brain import telemetry

    settings = _settings(tmp_path)
    telemetry.record_event(settings, "sync")

    conn = sqlite3.connect(_db_path(tmp_path))
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


# Acceptance 6: multiple inserts accumulate and are queryable in order.
def test_multiple_inserts_accumulate_and_are_ordered(tmp_path) -> None:
    from fritz_local_brain import telemetry

    settings = _settings(tmp_path)
    for et in ("capture", "compile", "sync", "query"):
        telemetry.record_event(settings, et)

    rows = telemetry.read_events(settings)
    assert [r["event_type"] for r in rows] == ["capture", "compile", "sync", "query"]
    assert [r["id"] for r in rows] == sorted(r["id"] for r in rows)


# Acceptance 7: naive datetime is interpreted/stored as UTC.
def test_naive_datetime_stored_as_utc(tmp_path) -> None:
    from fritz_local_brain import telemetry

    settings = _settings(tmp_path)
    naive_dt = datetime(2024, 3, 10, 8, 30, 0)  # no tzinfo
    assert naive_dt.tzinfo is None

    telemetry.record_event(settings, "capture", ts=naive_dt)

    rows = telemetry.read_events(settings)
    assert len(rows) == 1
    stored = rows[0]["ts"]
    # Must be stored as UTC canonical string.
    assert stored == "2024-03-10T08:30:00+00:00"
    assert stored.endswith("+00:00")


# Acceptance 8: aware datetime with non-UTC offset is normalized to +00:00 in storage.
def test_offset_datetime_normalized_to_utc(tmp_path) -> None:
    from fritz_local_brain import telemetry

    settings = _settings(tmp_path)
    tz_plus2 = timezone(timedelta(hours=2))
    dt_plus2 = datetime(2024, 3, 10, 10, 30, 0, tzinfo=tz_plus2)  # 08:30 UTC

    telemetry.record_event(settings, "compile", ts=dt_plus2)

    rows = telemetry.read_events(settings)
    assert len(rows) == 1
    stored = rows[0]["ts"]
    # Stored as UTC: 10:30+02:00 == 08:30+00:00.
    assert stored == "2024-03-10T08:30:00+00:00"
    assert stored.endswith("+00:00")
