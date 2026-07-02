"""Bounded in-process run history + durable run-detail persistence (#223).

The in-process deque keeps the summary-string ``RecentRun`` records that back
the recent-list / ``/v1/runs/recent`` alias and ``last_successful_compile_at``.
When a ``settings`` is passed the SAME record call ALSO persists a richer,
stable-``id`` detail row into the telemetry ``runs`` table (telemetry module),
so ``GET /v1/runs`` / ``GET /v1/runs/{id}`` can serve full detail. Persistence
is best-effort and never breaks the in-process behavior (backward compatible:
every existing caller that omits ``settings`` is unchanged).
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from .models import CompileRunResult, RecentRun, SyncRunResult

if TYPE_CHECKING:
    from .config import Settings


_RUNS: deque[RecentRun] = deque(maxlen=50)


def _duration_ms(started_at: datetime, finished_at: datetime) -> int:
    return int((finished_at - started_at).total_seconds() * 1000)


def _persist_run(
    settings: "Settings | None",
    *,
    run_id: str,
    kind: str,
    started_at: datetime,
    finished_at: datetime,
    dry_run: bool,
    status: str,
    source: str | None,
    summary: str,
    detail: dict[str, Any],
) -> None:
    """Best-effort durable write into the telemetry ``runs`` table.

    No-op when ``settings`` is None (backward-compatible callers). Wrapped
    defensively so persistence never breaks the in-process run history.
    """
    if settings is None:
        return
    try:
        from .telemetry import record_run

        record_run(
            settings,
            id=run_id,
            kind=kind,
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
            duration_ms=_duration_ms(started_at, finished_at),
            dry_run=dry_run,
            status=status,
            source=source,
            summary=summary,
            detail=detail,
        )
    except Exception:  # noqa: BLE001 - persistence must never break run history.
        pass


def record_compile(
    result: CompileRunResult,
    settings: "Settings | None" = None,
    *,
    source: str | None = None,
) -> None:
    status = "error" if result.errors else "ok"
    summary = f"{result.captures_considered} captures, {len(result.applied)} applied, {len(result.errors)} errors"
    _RUNS.appendleft(
        RecentRun(
            kind="compile",
            run_id=result.run_id,
            started_at=result.started_at,
            finished_at=result.finished_at,
            dry_run=result.dry_run,
            status=status,
            summary=summary,
        )
    )
    _persist_run(
        settings,
        run_id=result.run_id,
        kind="compile",
        started_at=result.started_at,
        finished_at=result.finished_at,
        dry_run=result.dry_run,
        status=status,
        source=source,
        summary=summary,
        detail={
            "captures_considered": result.captures_considered,
            "captures_by_source": dict(result.captures_by_source),
            "proposals": len(result.proposals),
            "applied": len(result.applied),
            "skipped": len(result.skipped),
            "reconciliations": len(result.reconciliations),
            "errors": list(result.errors),
        },
    )


def record_sync(
    result: SyncRunResult,
    settings: "Settings | None" = None,
    *,
    source: str | None = None,
) -> None:
    errors = list(result.errors) + [msg for item in result.results for msg in item.errors]
    pushed = sum(1 for item in result.results if item.pushed)
    status = "error" if errors else "ok"
    summary = f"{len(result.results)} vaults, {pushed} git pushes, {len(errors)} errors"
    _RUNS.appendleft(
        RecentRun(
            kind="sync",
            run_id=result.run_id,
            started_at=result.started_at,
            finished_at=result.finished_at,
            dry_run=result.dry_run,
            status=status,
            summary=summary,
        )
    )
    _persist_run(
        settings,
        run_id=result.run_id,
        kind="sync",
        started_at=result.started_at,
        finished_at=result.finished_at,
        dry_run=result.dry_run,
        status=status,
        source=source,
        summary=summary,
        detail={
            "vaults": len(result.results),
            "pushes": pushed,
            "errors": errors,
        },
    )


def record_failure(
    kind: str,
    started_at: datetime,
    finished_at: datetime,
    dry_run: bool,
    summary: str,
    settings: "Settings | None" = None,
    *,
    source: str | None = None,
) -> None:
    if kind not in {"compile", "sync"}:
        raise ValueError(f"Unsupported run history kind: {kind}")
    run_id = str(uuid4())
    _RUNS.appendleft(
        RecentRun(
            kind=kind,
            run_id=run_id,
            started_at=started_at,
            finished_at=finished_at,
            dry_run=dry_run,
            status="error",
            summary=summary,
        )
    )
    _persist_run(
        settings,
        run_id=run_id,
        kind=kind,
        started_at=started_at,
        finished_at=finished_at,
        dry_run=dry_run,
        status="error",
        source=source,
        summary=summary,
        detail={"errors": [summary]},
    )


def recent_runs(limit: int = 10) -> list[RecentRun]:
    bounded = max(0, min(limit, len(_RUNS)))
    return list(_RUNS)[:bounded]


def last_successful_compile_at() -> datetime | None:
    for run in _RUNS:
        if run.kind == "compile" and run.status == "ok":
            return run.finished_at
    return None


def clear_recent_runs_for_tests() -> None:
    _RUNS.clear()
