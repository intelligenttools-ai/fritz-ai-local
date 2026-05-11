"""Optional interval scheduler."""

from __future__ import annotations

import asyncio
from datetime import datetime

from .api.routes import compile_lock
from .compile_workflow import run_compile
from .config import Settings
from .logs import append_global_log
from .models import CompileRunRequest
from .run_history import record_compile, record_failure


async def scheduler_loop(settings: Settings) -> None:
    while settings.scheduler_enabled:
        await asyncio.sleep(settings.interval_minutes * 60)
        if compile_lock.locked():
            continue
        async with compile_lock:
            started = datetime.now()
            try:
                result = await run_compile(
                    settings,
                    CompileRunRequest(dry_run=settings.scheduler_dry_run, max_captures=settings.compile_max_captures),
                )
                record_compile(result)
            except Exception as exc:  # noqa: BLE001 - scheduler must surface provider/filesystem failures without exiting.
                summary = f"Scheduler compile failed: {exc}"
                record_failure("compile", started, datetime.now(), settings.scheduler_dry_run, summary)
                append_global_log(settings.brain_home, "COMPILE", summary, settings.scheduler_dry_run)
