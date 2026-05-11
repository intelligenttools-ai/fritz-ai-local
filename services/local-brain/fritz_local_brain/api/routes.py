"""FastAPI routes for Local Brain."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic_ai.exceptions import ModelAPIError, UsageLimitExceeded

from ..compile_workflow import run_compile
from ..config import get_settings
from ..models import CompileRunRequest, CompileRunResult, RecentRunsResult, StatusResult, SyncRunRequest, SyncRunResult
from ..run_history import recent_runs, record_compile, record_sync
from ..sync_workflow import run_sync
from .auth import require_token

router = APIRouter()
compile_lock = asyncio.Lock()
sync_lock = asyncio.Lock()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/v1/status", response_model=StatusResult, dependencies=[Depends(require_token)])
async def status() -> StatusResult:
    settings = get_settings()
    return StatusResult(
        scheduler_enabled=settings.scheduler_enabled,
        interval_minutes=settings.interval_minutes,
        brain_home=str(settings.brain_home),
        skills_dir=str(settings.skills_dir),
        allow_first_external_sync=settings.allow_first_external_sync,
    )


@router.post("/v1/compile/run", response_model=CompileRunResult, dependencies=[Depends(require_token)])
async def compile_run(request: CompileRunRequest) -> CompileRunResult:
    if compile_lock.locked():
        raise HTTPException(status_code=409, detail="Compile already running")
    async with compile_lock:
        try:
            result = await run_compile(get_settings(), request)
            record_compile(result)
            return result
        except UsageLimitExceeded as exc:
            raise HTTPException(status_code=502, detail=f"Compile agent exceeded run limits: {exc}") from exc
        except ModelAPIError as exc:
            raise HTTPException(status_code=502, detail=f"Compile model request failed: {exc.message}") from exc


@router.post("/v1/sync/run", response_model=SyncRunResult, dependencies=[Depends(require_token)])
async def sync_run(request: SyncRunRequest) -> SyncRunResult:
    if sync_lock.locked():
        raise HTTPException(status_code=409, detail="Sync already running")
    async with sync_lock:
        result = await run_sync(get_settings(), request)
        record_sync(result)
        return result


@router.get("/v1/runs/recent", response_model=RecentRunsResult, dependencies=[Depends(require_token)])
async def runs_recent(limit: int = 10) -> RecentRunsResult:
    return RecentRunsResult(runs=recent_runs(limit))
