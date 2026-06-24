"""build_model must forward connection-resilience config (max_retries + timeout)
to the underlying SDK clients so transient LLM connection drops are retried
instead of failing the whole compile run (#164)."""
from __future__ import annotations

from pathlib import Path

import pytest

from fritz_local_brain import llm
from fritz_local_brain.config import Settings


def _capture_client(monkeypatch: pytest.MonkeyPatch, attr: str) -> dict:
    captured: dict = {}

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(llm, attr, FakeClient)
    return captured


def test_openai_client_gets_max_retries_and_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_client(monkeypatch, "AsyncOpenAI")
    monkeypatch.setattr(llm, "OpenAIProvider", lambda openai_client: openai_client)
    monkeypatch.setattr(llm, "OpenAIChatModel", lambda model, provider: (model, provider))

    settings = Settings(
        LOCAL_BRAIN_HOME=tmp_path,
        LLM_PROTOCOL="openai-compatible",
        LLM_MAX_RETRIES=7,
        LLM_TIMEOUT_SECONDS=33.0,
    )
    llm.build_model(settings)

    assert captured["max_retries"] == 7
    assert captured["timeout"] == 33.0


def test_anthropic_client_gets_max_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_client(monkeypatch, "AsyncAnthropic")
    monkeypatch.setattr(llm, "AnthropicProvider", lambda anthropic_client: anthropic_client)
    monkeypatch.setattr(llm, "AnthropicModel", lambda model, provider: (model, provider))

    settings = Settings(
        LOCAL_BRAIN_HOME=tmp_path,
        LLM_PROTOCOL="anthropic-compatible",
        LLM_MAX_RETRIES=4,
    )
    llm.build_model(settings)

    assert captured["max_retries"] == 4


def test_llm_max_retries_default_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_MAX_RETRIES", raising=False)
    monkeypatch.delenv("LOCAL_BRAIN_LLM_MAX_RETRIES", raising=False)
    assert Settings(LOCAL_BRAIN_HOME=tmp_path).llm_max_retries == 6

    monkeypatch.setenv("LLM_MAX_RETRIES", "9")
    assert Settings(LOCAL_BRAIN_HOME=tmp_path).llm_max_retries == 9
