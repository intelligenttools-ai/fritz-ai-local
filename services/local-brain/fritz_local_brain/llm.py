"""Pydantic AI model factory."""

from __future__ import annotations

from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from .config import Settings


def build_model(settings: Settings):
    """Build the configured chat model.

    The MVP supports OpenAI-compatible endpoints. API keys are optional; a
    placeholder is used only because some OpenAI-compatible clients require a
    non-empty value even when the endpoint ignores it.
    """

    if settings.llm_protocol != "openai-compatible":
        raise ValueError("MVP supports LOCAL_BRAIN_LLM_PROTOCOL=openai-compatible only")

    client = AsyncOpenAI(
        base_url=settings.normalized_llm_base_url(),
        api_key=settings.normalized_api_key() or "local-brain-no-key",
        timeout=settings.llm_timeout_seconds,
    )
    provider = OpenAIProvider(openai_client=client)
    return OpenAIChatModel(settings.llm_model, provider=provider)
