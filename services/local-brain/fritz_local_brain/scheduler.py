"""Optional interval scheduler."""

from __future__ import annotations

import asyncio
import contextlib

from .api.routes import compile_lock
from .compile_workflow import run_compile
from .config import Settings
from .models import CompileRunRequest


async def scheduler_loop(settings: Settings) -> None:
    while settings.scheduler_enabled:
        await asyncio.sleep(settings.interval_minutes * 60)
        if compile_lock.locked():
            continue
        async with compile_lock:
            with contextlib.suppress(Exception):
                await run_compile(
                    settings,
                    CompileRunRequest(dry_run=settings.scheduler_dry_run, max_captures=settings.compile_max_captures),
                )
