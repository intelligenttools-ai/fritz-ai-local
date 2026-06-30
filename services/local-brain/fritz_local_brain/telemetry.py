"""Telemetry event store (#176).

A thin synchronous writer over a single SQLite table at
``$BRAIN_HOME/telemetry.db``. Foundation for the usage-dashboard epic (#175);
later items add read-side instrumentation and an aggregation API.

Importing this module has NO side effects: the database is created lazily on
the first ``record_event`` call (only when telemetry is enabled).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fritz_local_brain.config import Settings
    from fritz_local_brain.models import QueryRunRequest, QueryRunResult

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


def record_query_event(
    settings: "Settings",
    *,
    use_vector: bool,
    request: "QueryRunRequest",
    result: "QueryRunResult",
    agent: str,
    duration_ms: int,
) -> None:
    """Record one read-side query/search telemetry event.

    Shared by the HTTP routes (#178) and the in-process MCP tools (#179) so both
    record IDENTICALLY (critical for #181 aggregation). ``request``/``result`` are
    duck-typed (only the read attributes are accessed). The query text is included
    only when ``settings.telemetry_store_query_text``. Wrapped defensively so
    telemetry never breaks the caller's query path.
    """

    try:
        payload: dict[str, Any] = {
            "result_count": len(result.matches),
            "hit": len(result.matches) > 0,
            "scope": request.scope,
            "use_vector": use_vector,
            "skipped": result.skipped,
            "errors": result.errors,
        }
        if settings.telemetry_store_query_text:
            payload["query"] = request.query
        record_event(
            settings,
            "search" if use_vector else "query",
            agent=agent,
            vault=request.vault,
            run_id=result.run_id,
            status="error" if result.errors else "ok",
            duration_ms=duration_ms,
            payload=payload,
        )
    except Exception:  # noqa: BLE001 - telemetry must never break the query path.
        pass


_BACKFILL_STATE_FILE = "telemetry_backfill.json"


def _backfill_state_path(settings: "Settings") -> Path:
    return Path(settings.brain_home).expanduser() / _BACKFILL_STATE_FILE


def _read_imported_count(settings: "Settings") -> int:
    path = _backfill_state_path(settings)
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("global_log_lines_imported", 0))
    except (OSError, ValueError, json.JSONDecodeError):
        return 0


def _write_imported_count(settings: "Settings", count: int) -> None:
    path = _backfill_state_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: a crash mid-write must not corrupt the state file (a corrupt
    # file reads back as 0 and would re-import the entire log, duplicating every
    # event). Write to a temp sibling, then os.replace() (atomic rename).
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"global_log_lines_imported": count}), encoding="utf-8")
    os.replace(tmp, path)


def sync_log_to_telemetry(settings: "Settings") -> int:
    """Idempotently import ``$BRAIN_HOME/log.md`` lines into the telemetry store.

    Uses an append-only high-water mark persisted in
    ``$BRAIN_HOME/telemetry_backfill.json`` so the one-time historical backfill
    and the ongoing sync are the SAME operation: only lines after the recorded
    count are imported, so re-running never duplicates. If the log has fewer
    lines than the recorded mark (rotation/truncation) the mark is reset to 0
    and the file is re-imported from the start (rare edge, accepted).

    Returns the number of newly-imported lines. No-op (returns 0) when telemetry
    is disabled or the log does not exist. File IO is wrapped defensively so a
    malformed log can never crash callers.
    """

    if not settings.telemetry_enabled:
        return 0

    log_path = Path(settings.brain_home).expanduser() / "log.md"
    if not log_path.exists():
        return 0

    try:
        raw = log_path.read_text(encoding="utf-8")
    except OSError:
        return 0

    lines = raw.splitlines()
    # The host capture hook may be mid-append (bind-mounted log.md): a file not
    # ending in a newline has an incomplete final line. Leave it for the next
    # sync so it is imported exactly once when complete — and keep the
    # high-water mark below it so it is not silently skipped as malformed.
    if lines and not raw.endswith("\n"):
        lines = lines[:-1]

    already = _read_imported_count(settings)
    if len(lines) < already:  # log rotated/truncated -> re-import from start.
        already = 0

    new_lines = lines[already:]
    imported = 0
    for line in new_lines:
        if not line.strip():
            continue
        parts = line.split(" | ", 3)
        if len(parts) < 4:  # malformed line -> skip defensively.
            continue
        ts_str, operation, source, summary = parts
        event_type = operation.strip().lower()
        agent = source.strip()
        lowered = summary.lower()
        status = "error" if any(k in lowered for k in ("failed", "crash")) else "ok"
        try:
            ts = datetime.strptime(ts_str.strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            ts = None
        record_event(
            settings,
            event_type,
            agent=agent,
            status=status,
            ts=ts,
            payload={"summary": summary},
        )
        imported += 1

    _write_imported_count(settings, len(lines))
    return imported


def sync_log_to_telemetry_quietly(settings: "Settings") -> None:
    """Wiring helper: run :func:`sync_log_to_telemetry`, swallowing all errors.

    Telemetry must never break a core path, so callers at workflow/scheduler
    choke points use this fire-and-forget wrapper.
    """

    try:
        sync_log_to_telemetry(settings)
    except Exception:  # noqa: BLE001 - telemetry must never break the core path.
        pass


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
