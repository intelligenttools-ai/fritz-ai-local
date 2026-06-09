"""Optional interval scheduler."""

from __future__ import annotations

import asyncio
from datetime import datetime

from .compile_workflow import run_compile
from .config import Settings
from .embeddings import schedule_embedding_refresh_after_compile_result
from .logs import append_global_log
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
