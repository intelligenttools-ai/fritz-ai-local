from __future__ import annotations

import asyncio
from multiprocessing import get_context
from pathlib import Path

import pytest
from fastapi import HTTPException

from fritz_local_brain import mcp_server
from fritz_local_brain.api import routes
from fritz_local_brain.config import Settings
from fritz_local_brain.mcp_server import brain_sync
from fritz_local_brain.models import CompileRunRequest, SyncRunRequest
from fritz_local_brain.operation_locks import OperationAlreadyRunning, OperationLock, compile_lock, sync_lock


def _hold_operation_lock(brain_home: str, name: str, ready: object, release: object) -> None:
    async def scenario() -> None:
        async with OperationLock(name, f"{name} already running").guard(Path(brain_home)):
            ready.set()
            while not release.is_set():
                await asyncio.sleep(0.01)

    asyncio.run(scenario())


def test_compile_lock_is_shared_by_rest_and_mcp(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with compile_lock.guard(tmp_path):
            with pytest.raises(HTTPException, match="409: Compile already running"):
                await routes.compile_run(CompileRunRequest())
            with pytest.raises(RuntimeError, match="Compile already running"):
                await mcp_server.brain_compile()

    asyncio.run(scenario())


def test_sync_lock_is_shared_by_rest_and_mcp(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with sync_lock.guard(tmp_path):
            with pytest.raises(HTTPException, match="409: Sync already running"):
                await routes.sync_run(SyncRunRequest())
            with pytest.raises(RuntimeError, match="Sync already running"):
                await brain_sync()

    asyncio.run(scenario())


def test_compile_entrypoints_reject_another_process_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(target=_hold_operation_lock, args=(str(tmp_path), "compile", ready, release))
    process.start()
    try:
        assert ready.wait(5)
        settings = Settings(LOCAL_BRAIN_HOME=tmp_path)
        monkeypatch.setattr(routes, "get_settings", lambda: settings)
        monkeypatch.setattr(mcp_server, "get_settings", lambda: settings)

        async def scenario() -> None:
            with pytest.raises(HTTPException, match="409: Compile already running"):
                await routes.compile_run(CompileRunRequest())
            with pytest.raises(RuntimeError, match="Compile already running"):
                await mcp_server.brain_compile()

        asyncio.run(scenario())
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)

    assert process.exitcode == 0


def test_operation_lock_rejects_another_process(tmp_path: Path) -> None:
    context = get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(target=_hold_operation_lock, args=(str(tmp_path), "compile", ready, release))
    process.start()
    try:
        assert ready.wait(5)

        async def scenario() -> None:
            with pytest.raises(OperationAlreadyRunning, match="Compile already running"):
                async with OperationLock("compile", "Compile already running").guard(tmp_path):
                    pass

        asyncio.run(scenario())
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)

    assert process.exitcode == 0
