from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from fritz_local_brain.api import routes
from fritz_local_brain.models import CompileRunRequest


class _Settings:
    def __init__(self, brain_home: Path = Path("/brain")) -> None:
        self.api_token = "secret"
        self.brain_home = brain_home
        self.embedding_enabled = True


def test_embeddings_index_schedule_endpoint_schedules_background_refresh(monkeypatch, tmp_path) -> None:
    calls = []

    monkeypatch.setattr(routes, "get_settings", lambda: _Settings(tmp_path))
    monkeypatch.setattr(
        routes,
        "schedule_embedding_refresh_after_compile",
        lambda settings, *, reason: calls.append((settings.embedding_enabled, reason)) or "scheduled",
    )

    result = asyncio.run(routes.embeddings_index_schedule())

    assert result.enabled is True
    assert result.status == "scheduled"
    assert result.reason == "ingest"
    assert calls == [(True, "ingest")]


def test_compile_route_schedules_refresh_after_successful_apply(monkeypatch, tmp_path) -> None:
    calls = []
    result = SimpleNamespace(
        dry_run=False,
        errors=[],
        applied=[object()],
        skipped=[],
        started_at=datetime.now(),
        finished_at=datetime.now(),
    )

    async def fake_run_compile(settings, request):
        return result

    monkeypatch.setattr(routes, "get_settings", lambda: _Settings(tmp_path))
    monkeypatch.setattr(routes, "run_compile", fake_run_compile)
    monkeypatch.setattr(routes, "record_compile", lambda result, *a, **k: None)
    monkeypatch.setattr(
        routes,
        "schedule_embedding_refresh_after_compile_result",
        lambda settings, compile_result, *, reason: calls.append((compile_result, reason)) or "scheduled",
    )

    assert asyncio.run(routes.compile_run(CompileRunRequest(dry_run=False))) is result
    assert calls == [(result, "compile")]
