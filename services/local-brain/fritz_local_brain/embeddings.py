"""Optional embedding endpoint probing and metadata storage."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from openai import AsyncOpenAI

from .config import Settings
from .models import EmbeddingMetadata, EmbeddingProbeRequest, EmbeddingProbeResult, EmbeddingStatusResult


def metadata_path(settings: Settings) -> Path:
    return settings.brain_home / "embeddings" / "metadata.json"


def load_embedding_metadata(settings: Settings) -> EmbeddingMetadata | None:
    path = metadata_path(settings)
    if not path.exists():
        return None
    return EmbeddingMetadata.model_validate_json(path.read_text(encoding="utf-8"))


def embedding_status(settings: Settings) -> EmbeddingStatusResult:
    return EmbeddingStatusResult(
        enabled=settings.embedding_enabled,
        protocol=settings.embedding_protocol,
        base_url=settings.normalized_embedding_base_url(),
        model=settings.embedding_model,
        metadata=load_embedding_metadata(settings),
    )


async def probe_embedding_dimensions(settings: Settings, request: EmbeddingProbeRequest) -> EmbeddingProbeResult:
    path = metadata_path(settings)
    result = EmbeddingProbeResult(
        enabled=settings.embedding_enabled,
        dry_run=request.dry_run,
        protocol=settings.embedding_protocol,
        base_url=settings.normalized_embedding_base_url(),
        model=settings.embedding_model,
        metadata_path=str(path),
    )
    if not settings.embedding_enabled:
        result.error = "Embedding endpoint is disabled; set EMBEDDING_ENABLED=true to probe dimensions"
        return result
    if settings.embedding_protocol != "openai-compatible":
        result.error = "MVP supports EMBEDDING_PROTOCOL=openai-compatible only"
        return result

    client = AsyncOpenAI(
        base_url=settings.normalized_embedding_base_url(),
        api_key=settings.normalized_embedding_api_key() or "local-brain-no-key",
        timeout=settings.embedding_timeout_seconds,
    )
    try:
        response = await client.embeddings.create(model=settings.embedding_model, input="dimension probe")
    except Exception as exc:  # noqa: BLE001 - external model clients raise provider-specific errors.
        result.error = str(exc)
        return result

    dimensions = len(response.data[0].embedding)
    result.dimensions = dimensions
    if request.dry_run:
        return result

    metadata = EmbeddingMetadata(
        protocol=settings.embedding_protocol,
        base_url=settings.normalized_embedding_base_url(),
        model=settings.embedding_model,
        dimensions=dimensions,
        probed_at=datetime.now(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    result.stored = True
    return result
