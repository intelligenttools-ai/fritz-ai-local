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


def test_default_model_endpoints_use_simple_local_openai_compatible_path(tmp_path) -> None:
    settings = Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path)

    assert settings.llm_protocol == "openai-compatible"
    assert settings.normalized_llm_base_url() == "http://host.docker.internal:11434/v1"
    assert settings.llm_model == "local-instruct-model"
    assert settings.embedding_enabled is False
    assert settings.normalized_embedding_base_url() == "http://host.docker.internal:11434/v1"
    assert settings.embedding_model == "nomic-embed-text:latest"
