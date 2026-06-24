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

#: Per-run request budget for the structured-output agents. A run spends:
#:   1. one request on the read-only context tool call,
#:   2. one tolerated stray re-call of the tool (the ``already_provided`` stub
#:      path — a misbehaving local model may call it a second time),
#:   3. one request on the first structured output,
#:   4. up to OUTPUT_RETRIES more on output self-repair.
#: Budget must be at least OUTPUT_RETRIES + 3 or a stray tool re-call causes
#: UsageLimitExceeded — exactly the batch-abort/502 this hardening exists to prevent.
AGENT_REQUEST_LIMIT = OUTPUT_RETRIES + 3


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
            max_retries=settings.llm_max_retries,
        )
        provider = OpenAIProvider(openai_client=client)
        return OpenAIChatModel(settings.llm_model, provider=provider)

    if settings.llm_protocol == "anthropic-compatible":
        client = AsyncAnthropic(
            base_url=settings.normalized_llm_base_url(),
            api_key=settings.normalized_api_key() or "local-brain-no-key",
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
        provider = AnthropicProvider(anthropic_client=client)
        return AnthropicModel(settings.llm_model, provider=provider)

    raise ValueError("Unsupported LOCAL_BRAIN_LLM_PROTOCOL; use openai-compatible or anthropic-compatible")
