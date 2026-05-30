from __future__ import annotations

from fritz_local_brain.config import Settings


def test_scheduler_defaults_to_dry_run_when_unset(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("LOCAL_BRAIN_SCHEDULER_DRY_RUN", raising=False)
    monkeypatch.delenv("SCHEDULER_DRY_RUN", raising=False)

    settings = Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path)

    assert settings.scheduler_dry_run is True


def test_scheduler_apply_mode_requires_explicit_opt_in(tmp_path) -> None:
    settings = Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path, SCHEDULER_DRY_RUN="false")

    assert settings.scheduler_dry_run is False
