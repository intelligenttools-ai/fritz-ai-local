"""Shared operation locks for Local Brain entrypoints."""

from __future__ import annotations

import asyncio
import fcntl
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, TextIO


class OperationAlreadyRunning(RuntimeError):
    """Raised when another entrypoint already holds an operation lock."""


class OperationLock:
    def __init__(self, name: str, conflict_message: str) -> None:
        self.name = name
        self.conflict_message = conflict_message
        self._memory_lock = asyncio.Lock()

    def locked(self) -> bool:
        return self._memory_lock.locked()

    @asynccontextmanager
    async def guard(self, brain_home: Path) -> AsyncIterator[None]:
        if self._memory_lock.locked():
            raise OperationAlreadyRunning(self.conflict_message)

        await self._memory_lock.acquire()
        file_lock: TextIO | None = None
        try:
            file_lock = self._acquire_file_lock(brain_home)
            yield
        finally:
            if file_lock is not None:
                fcntl.flock(file_lock.fileno(), fcntl.LOCK_UN)
                file_lock.close()
            self._memory_lock.release()

    def _acquire_file_lock(self, brain_home: Path) -> TextIO:
        lock_dir = brain_home / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_file = (lock_dir / f"{self.name}.lock").open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock_file.close()
            raise OperationAlreadyRunning(self.conflict_message) from exc
        return lock_file


compile_lock = OperationLock("compile", "Compile already running")
sync_lock = OperationLock("sync", "Sync already running")
lint_lock = OperationLock("lint", "Lint already running")
embedding_lock = OperationLock("embeddings", "Embedding index refresh already running")
