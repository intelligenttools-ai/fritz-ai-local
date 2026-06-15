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
from .run_history import record_compile, record_failure


async def scheduler_loop(settings: Settings) -> None:
    while settings.scheduler_enabled:
        await asyncio.sleep(settings.interval_minutes * 60)
        try:
            async with compile_lock.guard(settings.brain_home):
                started = datetime.now()
                try:
                    result = await run_compile(
                        settings,
                        CompileRunRequest(dry_run=settings.scheduler_dry_run, max_captures=settings.compile_max_captures),
                    )
                    record_compile(result)
                    schedule_embedding_refresh_after_compile_result(settings, result, reason="scheduler compile")
                except Exception as exc:  # noqa: BLE001 - scheduler must surface provider/filesystem failures without exiting.
                    summary = f"Scheduler compile failed: {exc}"
                    record_failure("compile", started, datetime.now(), settings.scheduler_dry_run, summary)
                    append_global_log(settings.brain_home, "COMPILE", summary, settings.scheduler_dry_run)
        except OperationAlreadyRunning:
            continue


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
