"""Telemetry event store (#176).

A thin synchronous writer over a single SQLite table at
``$BRAIN_HOME/telemetry.db``. Foundation for the usage-dashboard epic (#175);
later items add read-side instrumentation and an aggregation API.

Importing this module has NO side effects: the database is created lazily on
the first ``record_event`` call (only when telemetry is enabled).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fritz_local_brain.config import Settings

# Individual DDL statements executed one-by-one so any error surfaces immediately.
_DDL = (
    """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        event_type TEXT NOT NULL,
        agent TEXT,
        vault TEXT,
        run_id TEXT,
        status TEXT,
        duration_ms INTEGER,
        payload TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts)",
    "CREATE INDEX IF NOT EXISTS idx_events_event_type ON events (event_type)",
    "CREATE INDEX IF NOT EXISTS idx_events_agent ON events (agent)",
    "CREATE INDEX IF NOT EXISTS idx_events_vault ON events (vault)",
)


def _db_path(settings: "Settings") -> Path:
    return Path(settings.brain_home).expanduser() / "telemetry.db"


def _connect(settings: "Settings") -> sqlite3.Connection:
    """Open the telemetry db, ensuring the directory, WAL mode, and schema."""

    path = _db_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    for stmt in _DDL:
        conn.execute(stmt)
    conn.commit()
    return conn


def record_event(
    settings: "Settings",
    event_type: str,
    *,
    agent: str | None = None,
    vault: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    duration_ms: int | None = None,
    payload: dict[str, Any] | None = None,
    ts: datetime | None = None,
) -> None:
    """Append one telemetry event.

    No-op (writes nothing, creates no db file) when telemetry is disabled.
    ``payload`` is serialized to deterministic JSON.  ``ts`` is an optional
    ``datetime`` (defaults to now-UTC); naive datetimes are treated as UTC.
    The timestamp is always stored as a canonical ISO-8601 UTC string
    (``...+00:00``), ensuring lexicographic sort and SQLite date/strftime
    aggregation are reliable for downstream items.
    """

    if not settings.telemetry_enabled:
        return

    when = ts or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    ts_str = when.astimezone(timezone.utc).isoformat()

    payload_json = None if payload is None else json.dumps(payload, sort_keys=True)

    conn = _connect(settings)
    try:
        conn.execute(
            "INSERT INTO events "
            "(ts, event_type, agent, vault, run_id, status, duration_ms, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts_str, event_type, agent, vault, run_id, status, duration_ms, payload_json),
        )
        conn.commit()
    finally:
        conn.close()


def read_events(settings: "Settings") -> list[dict[str, Any]]:
    """Return all events ordered by id (insertion order). Test/helper read path.

    The real aggregation API is a later item (#181); this stays minimal.
    """

    path = _db_path(settings)
    if not path.exists():
        return []
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM events ORDER BY id").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
