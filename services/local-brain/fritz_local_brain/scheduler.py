"""Optional interval scheduler."""

from __future__ import annotations

import asyncio
from datetime import datetime

from .compile_workflow import run_compile
from .config import Settings
from .embeddings import schedule_embedding_refresh_after_compile_result
from .logs import append_global_log
from .mirror import run_mirror
from .models import CompileRunRequest
from .operation_locks import OperationAlreadyRunning, compile_lock
from .rereconciliation import run_rereconciliation_sweep
from .run_history import record_compile, record_failure
from .telemetry import prune_old_events_quietly, sync_log_to_telemetry_quietly


async def scheduler_loop(settings: Settings, *, stop: asyncio.Event | None = None) -> None:
    """Long-lived compile scheduler.

    #208: the loop no longer PERMANENTLY exits when ``scheduler_enabled`` is
    False — it idles instead. This lets a live PATCH of the config singleton
    pause (set False) or resume (set True) the scheduler without restarting the
    service. ``interval_minutes`` and ``scheduler_dry_run`` are read fresh each
    cycle, so interval/dry-run edits take effect on the next cycle.

    The loop runs until the optional ``stop`` event is set (used by the app
    lifespan on shutdown, and by tests to bound the loop). When
    ``scheduler_enabled`` is False the cycle sleeps and then continues without
    running compile.
    """
    while stop is None or not stop.is_set():
        await asyncio.sleep(settings.interval_minutes * 60)
        if not settings.scheduler_enabled:
            continue  # idle — paused, but do NOT exit; a live resume picks up here
        try:
            async with compile_lock.guard(settings.brain_home):
                started = datetime.now()
                try:
                    result = await run_compile(
                        settings,
                        CompileRunRequest(dry_run=settings.scheduler_dry_run, max_captures=settings.compile_max_captures),
                        trusted=True,
                    )
                    record_compile(result)
                    schedule_embedding_refresh_after_compile_result(settings, result, reason="scheduler compile")
                except Exception as exc:  # noqa: BLE001 - scheduler must surface provider/filesystem failures without exiting.
                    summary = f"Scheduler compile failed: {exc}"
                    record_failure("compile", started, datetime.now(), settings.scheduler_dry_run, summary)
                    append_global_log(settings.brain_home, "COMPILE", summary, settings.scheduler_dry_run)
        except OperationAlreadyRunning:
            pass
        sync_log_to_telemetry_quietly(settings)
        prune_old_events_quietly(settings)


async def mirror_scheduler_loop(settings: Settings) -> None:
    """Optional background mirror loop, gated by ``settings.mirror_enabled``.

    Sleeps ``mirror_interval_minutes`` and runs ``run_mirror`` honoring
    ``scheduler_dry_run``. Like the compile scheduler, a failure in one pass is
    logged and the loop continues — it must never crash. Disabled by default
    (``mirror_enabled=False``), so nothing runs unless explicitly enabled.
    """
    while settings.mirror_enabled:
        await asyncio.sleep(settings.mirror_interval_minutes * 60)
        try:
            await run_mirror(settings, dry_run=settings.scheduler_dry_run)
        except Exception as exc:  # noqa: BLE001 - mirror loop must not crash on provider/filesystem failures.
            summary = f"Scheduler mirror failed: {exc}"
            append_global_log(settings.brain_home, "MIRROR", summary, settings.scheduler_dry_run)


async def rereconciliation_scheduler_loop(settings: Settings) -> None:
    """Optional background re-reconciliation sweep loop.

    Gated by ``settings.rereconciliation_enabled`` (default ``False``), so
    nothing runs unless explicitly enabled.  Sleeps
    ``rereconciliation_interval_minutes`` (default 1440 = 24 h) between passes,
    then calls :func:`run_rereconciliation_sweep` with ``dry_run`` drawn from
    ``settings.rereconciliation_dry_run`` (default ``True``).

    Failures in one pass are logged and the loop continues — it must never
    crash or exit.  Not auto-started by any existing application entry point;
    operators must wire it in explicitly.
    """
    while settings.rereconciliation_enabled:
        await asyncio.sleep(settings.rereconciliation_interval_minutes * 60)
        try:
            await run_rereconciliation_sweep(settings, dry_run=settings.rereconciliation_dry_run)
        except Exception as exc:  # noqa: BLE001 - sweep loop must not crash on provider/filesystem failures.
            summary = f"Scheduler re-reconciliation sweep failed: {exc}"
            append_global_log(settings.brain_home, "RERECONCILE", summary, settings.rereconciliation_dry_run)
