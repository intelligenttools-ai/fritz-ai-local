"""Test sandbox: redirect BRAIN_HOME to a per-test tmp dir.

An autouse fixture sets ``BRAIN_HOME`` and ``LOCAL_BRAIN_HOME`` env vars to a
fresh tmp directory and clears the ``get_settings`` lru_cache before and after
each test. This guarantees that any incidental ``get_settings()`` call resolves
to the tmp dir, never to the real ``~/.brain``.

A regression test (``test_sandbox_telemetry_db_under_tmp``) verifies the
invariant holds: ``telemetry._db_path(get_settings())`` must be under the tmp
dir and NOT under ``Path.home() / ".brain"``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fritz_local_brain.config import get_settings


@pytest.fixture(autouse=True)
def _brain_home_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect every brain-home lookup to a per-test tmp dir."""
    brain_tmp = str(tmp_path)
    get_settings.cache_clear()
    monkeypatch.setenv("BRAIN_HOME", brain_tmp)
    monkeypatch.setenv("LOCAL_BRAIN_HOME", brain_tmp)
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Regression: sandbox invariant
# ---------------------------------------------------------------------------

def test_sandbox_telemetry_db_under_tmp(tmp_path: Path) -> None:
    """Sandbox invariant: telemetry.db resolves under tmp, never under ~/.brain."""
    from fritz_local_brain import telemetry

    settings = get_settings()
    db = telemetry._db_path(settings)

    real_brain = Path.home() / ".brain"
    assert str(db).startswith(str(tmp_path)), (
        f"telemetry.db ({db}) is NOT under tmp_path ({tmp_path})"
    )
    assert not str(db).startswith(str(real_brain)), (
        f"telemetry.db ({db}) leaked into real brain home ({real_brain})"
    )
