"""Bounded in-process run history."""

from __future__ import annotations

from collections import deque

from .models import CompileRunResult, RecentRun, SyncRunResult


_RUNS: deque[RecentRun] = deque(maxlen=50)


def record_compile(result: CompileRunResult) -> None:
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


def record_sync(result: SyncRunResult) -> None:
    errors = len(result.errors) + sum(len(item.errors) for item in result.results)
    pushed = sum(1 for item in result.results if item.pushed)
    status = "error" if errors else "ok"
    summary = f"{len(result.results)} vaults, {pushed} git pushes, {errors} errors"
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


def recent_runs(limit: int = 10) -> list[RecentRun]:
    bounded = max(0, min(limit, len(_RUNS)))
    return list(_RUNS)[:bounded]
