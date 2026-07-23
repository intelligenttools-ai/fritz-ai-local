"""Pydantic AI model factory."""

from __future__ import annotations

from time import perf_counter

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from pydantic_ai import NativeOutput
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from .config import Settings, redact_secrets
from .models import ConfigTestResult

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


async def list_models(
    *,
    protocol: str,
    base_url: str,
    api_key: str | None,
    timeout_seconds: float,
) -> list[str]:
    """List the model ids an OpenAI-/Anthropic-compatible gateway advertises.

    The caller passes an already-normalized ``base_url`` and ``api_key`` (post
    exfiltration guard). Builds a throwaway client exactly like ``probe_llm``
    (``max_retries=0`` for fast failure) and returns the sorted unique model ids.
    Exceptions propagate to the caller.
    """

    if protocol == "openai-compatible":
        client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "local-brain-no-key",
            timeout=timeout_seconds,
            max_retries=0,
        )
        result = await client.models.list()
    elif protocol == "anthropic-compatible":
        client = AsyncAnthropic(
            base_url=base_url,
            api_key=api_key or "local-brain-no-key",
            timeout=timeout_seconds,
            max_retries=0,
        )
        result = await client.models.list()
    else:
        raise ValueError("Unsupported protocol; use openai-compatible or anthropic-compatible")
    # Async-iterate the paginator so ALL advertised models are returned, not just
    # the first SDK page (Anthropic defaults to 20/page). A malformed/hostile
    # gateway can return non-string or unpaired-surrogate ids (the SDKs build
    # responses non-strictly); validate here so the caller's try/except turns it
    # into ok=false instead of a 500 at response encoding.
    ids: list[str] = []
    async for item in result:
        model_id = item.id
        if not isinstance(model_id, str):
            raise ValueError(f"gateway returned a non-string model id: {model_id!r}")
        model_id.encode("utf-8")  # raises on unpaired surrogates → caught upstream
        ids.append(model_id)
    return sorted(set(ids))


async def probe_llm(settings: Settings) -> ConfigTestResult:
    """Cheap real reachability probe for the configured LLM endpoint (#226).

    Lists models (a no-cost, no-token call) using a throwaway client built from
    *settings*. ``max_retries=0`` so an unreachable endpoint fails fast instead of
    burning the retry budget. Never persists anything, and any upstream error text
    is redacted of the API key before being returned so a probe cannot leak the
    key (SDK exceptions can echo the Authorization header from the response body).
    """

    # The key actually sent to the endpoint (post FIX-1 exfiltration guard). This
    # is exactly the value an upstream error could echo, so redact THIS.
    sent_key = settings.normalized_api_key()
    start = perf_counter()
    try:
        if settings.llm_protocol == "openai-compatible":
            client = AsyncOpenAI(
                base_url=settings.normalized_llm_base_url(),
                api_key=settings.normalized_api_key() or "local-brain-no-key",
                timeout=settings.llm_timeout_seconds,
                max_retries=0,
            )
            await client.models.list()
        elif settings.llm_protocol == "anthropic-compatible":
            client = AsyncAnthropic(
                base_url=settings.normalized_llm_base_url(),
                api_key=settings.normalized_api_key() or "local-brain-no-key",
                timeout=settings.llm_timeout_seconds,
                max_retries=0,
            )
            await client.models.list()
        else:
            return ConfigTestResult(ok=False, error="Unsupported llm_protocol; use openai-compatible or anthropic-compatible")
    except Exception as exc:  # noqa: BLE001 - external clients raise provider-specific errors.
        message = redact_secrets(str(exc), [sent_key])
        return ConfigTestResult(ok=False, latency_ms=round((perf_counter() - start) * 1000, 1), error=message)
    return ConfigTestResult(ok=True, latency_ms=round((perf_counter() - start) * 1000, 1))
