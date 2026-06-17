"""Pydantic AI model factory."""

from __future__ import annotations

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from pydantic_ai import NativeOutput
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from .config import Settings

#: Output-validation retry budget for structured-output agents. Lets the model
#: self-repair from output-validation errors (the error is fed back) before the
#: run fails. Applied to BOTH the compile and reconciliation agents.
OUTPUT_RETRIES = 3


def output_spec_for(protocol: str, model: type):
    """Pick the structured-output spec for *model* based on the LLM *protocol*.

    On ``openai-compatible`` endpoints we use ``NativeOutput`` so the backend
    does guided decoding via a ``json_schema`` response format. ``AnthropicModel``'s
    profile does not support native json_schema output (pydantic_ai raises a UserError
    at request time), so for ``anthropic-compatible`` we keep the plain model
    (pydantic_ai's default tool output).
    """

    if protocol == "openai-compatible":
        return NativeOutput(model)
    return model


def build_model(settings: Settings):
    """Build the configured chat model.

    Provider selection is protocol-based. API keys are optional; a placeholder
    is used only because some compatible clients require a non-empty value even
    when the endpoint ignores it.
    """

    if settings.llm_protocol == "openai-compatible":
        client = AsyncOpenAI(
            base_url=settings.normalized_llm_base_url(),
            api_key=settings.normalized_api_key() or "local-brain-no-key",
            timeout=settings.llm_timeout_seconds,
        )
        provider = OpenAIProvider(openai_client=client)
        return OpenAIChatModel(settings.llm_model, provider=provider)

    if settings.llm_protocol == "anthropic-compatible":
        client = AsyncAnthropic(
            base_url=settings.normalized_llm_base_url(),
            api_key=settings.normalized_api_key() or "local-brain-no-key",
            timeout=settings.llm_timeout_seconds,
        )
        provider = AnthropicProvider(anthropic_client=client)
        return AnthropicModel(settings.llm_model, provider=provider)

    raise ValueError("Unsupported LOCAL_BRAIN_LLM_PROTOCOL; use openai-compatible or anthropic-compatible")
