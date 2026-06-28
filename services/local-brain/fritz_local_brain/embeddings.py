"""Optional embedding endpoint probing, indexing, and vector search."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from openai import AsyncOpenAI

from .captures import list_queryable_captures, read_capture
from .config import Settings
from .knowledge import store_root
from .logs import append_global_log
from .manifests import load_manifest, resolve_manifest_path
from .models import (
    EmbeddingIndexRequest,
    EmbeddingIndexResult,
    EmbeddingMetadata,
    EmbeddingProbeRequest,
    EmbeddingProbeResult,
    EmbeddingStatusResult,
    QueryMatch,
)
from .operation_locks import OperationAlreadyRunning, embedding_lock
from .paths import PathMapper
from .registry import RegistryError, load_registry, registered_vault_paths
from .security import is_excluded

_BRAIN_VAULT_NAME = "brain"


def metadata_path(settings: Settings) -> Path:
    return settings.brain_home / "embeddings" / "metadata.json"


_background_refresh_task: asyncio.Task[None] | None = None
_background_refresh_pending = False
_last_background_refresh_started_at: float | None = None


def index_path(settings: Settings) -> Path:
    return settings.brain_home / "embeddings" / "index.json"


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

    try:
        embedding = await _embed_text(settings, "dimension probe")
    except Exception as exc:  # noqa: BLE001 - external model clients raise provider-specific errors.
        result.error = str(exc)
        return result

    dimensions = len(embedding)
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


def schedule_embedding_refresh_after_compile_result(settings: Settings, result: Any, *, reason: str = "compile") -> str | None:
    """Schedule vector refresh after a successful non-dry-run compile result."""

    if getattr(result, "dry_run", True) or getattr(result, "errors", None):
        return None
    if not (getattr(result, "applied", None) or getattr(result, "skipped", None)):
        return None
    return schedule_embedding_refresh_after_compile(settings, reason=reason)


def schedule_embedding_refresh_after_compile(settings: Settings, *, reason: str = "compile") -> str | None:
    """Schedule debounced vector refresh from the ingest/compile processing path."""

    global _background_refresh_pending, _background_refresh_task, _last_background_refresh_started_at
    if not settings.embedding_enabled or not settings.embedding_refresh_after_compile:
        return None
    if _background_refresh_task is not None and not _background_refresh_task.done():
        _background_refresh_pending = True
        return "queued"
    now = time.monotonic()
    elapsed = None if _last_background_refresh_started_at is None else now - _last_background_refresh_started_at
    if elapsed is not None and elapsed < settings.embedding_refresh_debounce_seconds:
        delay = settings.embedding_refresh_debounce_seconds - elapsed
        _background_refresh_task = asyncio.create_task(_delayed_background_refresh_embedding_index(settings, reason, delay))
        _background_refresh_task.add_done_callback(_consume_background_refresh_exception(settings))
        return "scheduled-delayed"
    _background_refresh_task = asyncio.create_task(_background_refresh_embedding_index_loop(settings, reason))
    _background_refresh_task.add_done_callback(_consume_background_refresh_exception(settings))
    return "scheduled"


def _consume_background_refresh_exception(settings: Settings):
    def _consume(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 - last-resort visibility for unobserved background task failures.
            append_global_log(settings.brain_home, "EMBEDDINGS", f"Background embedding refresh task failed unexpectedly: {exc}", False)

    return _consume


async def _delayed_background_refresh_embedding_index(settings: Settings, reason: str, delay: float) -> None:
    global _background_refresh_pending
    await asyncio.sleep(delay)
    _background_refresh_pending = False
    await _background_refresh_embedding_index_loop(settings, reason)


async def _background_refresh_embedding_index_loop(settings: Settings, reason: str) -> None:
    global _background_refresh_pending, _last_background_refresh_started_at
    try:
        while True:
            await _wait_for_refresh_debounce(settings)
            _last_background_refresh_started_at = time.monotonic()
            _background_refresh_pending = False
            await _background_refresh_embedding_index_once(settings, reason)
            if not _background_refresh_pending:
                return
    except Exception as exc:  # noqa: BLE001 - background maintenance must not fail silently.
        append_global_log(settings.brain_home, "EMBEDDINGS", f"Background embedding refresh after {reason} crashed: {exc}", False)


async def _wait_for_refresh_debounce(settings: Settings) -> None:
    if _last_background_refresh_started_at is None:
        return
    elapsed = time.monotonic() - _last_background_refresh_started_at
    remaining = settings.embedding_refresh_debounce_seconds - elapsed
    if remaining > 0:
        await asyncio.sleep(remaining)


async def _background_refresh_embedding_index_once(settings: Settings, reason: str) -> None:
    result = await refresh_embedding_index(settings, EmbeddingIndexRequest(force=False))
    if result.error:
        append_global_log(settings.brain_home, "EMBEDDINGS", f"Background embedding refresh after {reason} failed: {result.error}", False)
    elif result.indexed:
        append_global_log(
            settings.brain_home,
            "EMBEDDINGS",
            f"Background embedding refresh after {reason} indexed {result.documents_indexed} documents",
            False,
        )


async def refresh_embedding_index(
    settings: Settings, request: EmbeddingIndexRequest | None = None
) -> EmbeddingIndexResult:
    """Vectorize current knowledge and raw captures inside the Local Brain service."""

    try:
        async with embedding_lock.guard(settings.brain_home):
            return await _refresh_embedding_index_unlocked(settings, request)
    except OperationAlreadyRunning as exc:
        return EmbeddingIndexResult(enabled=settings.embedding_enabled, index_path=str(index_path(settings)), error=str(exc))


async def _refresh_embedding_index_unlocked(
    settings: Settings, request: EmbeddingIndexRequest | None = None
) -> EmbeddingIndexResult:
    path = index_path(settings)
    result = EmbeddingIndexResult(enabled=settings.embedding_enabled, index_path=str(path))
    if not settings.embedding_enabled:
        result.error = "Embedding endpoint is disabled; set EMBEDDING_ENABLED=true"
        return result
    if settings.embedding_protocol != "openai-compatible":
        result.error = "MVP supports EMBEDDING_PROTOCOL=openai-compatible only"
        return result
    try:
        documents = _collect_embedding_documents(settings)
        all_source_fingerprint = _source_fingerprint(settings, documents)
        if request and not request.force:
            try:
                data = _read_index_data(settings)
            except (OSError, ValueError, json.JSONDecodeError):
                data = None
            if data is not None and _index_data_is_compatible(settings, data, all_source_fingerprint):
                result.indexed = True
                result.documents_indexed = len(data.get("documents", [])) if isinstance(data.get("documents"), list) else 0
                try:
                    result.updated_at = datetime.fromisoformat(str(data.get("updated_at")))
                except (TypeError, ValueError):
                    result.updated_at = None
                return result

        entries: list[dict[str, Any]] = []
        indexed_documents: list[dict[str, Any]] = []  # source docs that were successfully embedded
        skipped = 0
        skipped_keys: list[list[str]] = []
        for document in documents:
            try:
                vector = await _embed_text(settings, document["text"])
            except Exception as exc:  # noqa: BLE001 - per-document failures must not abort the whole index build.
                doc_vault = document.get("vault", "")
                doc_path = document.get("path", "")
                append_global_log(settings.brain_home, "EMBEDDINGS", f"skipped {doc_vault}/{doc_path}: {exc}", False)
                skipped += 1
                skipped_keys.append([str(doc_vault), str(doc_path)])
                continue
            persisted = {key: value for key, value in document.items() if key != "text"}
            entries.append({**persisted, "embedding": vector})
            indexed_documents.append(document)

        if skipped > 0:
            append_global_log(
                settings.brain_home,
                "EMBEDDINGS",
                f"indexed {len(entries)}, skipped {skipped}",
                False,
            )

        # Guard: if ALL documents failed, do not overwrite the existing index.
        # A transient endpoint outage must not destroy a previously-good index.
        # Trade-off: a doc that fails on every refresh causes a rebuild each tick,
        # but the char-cap fix makes that rare; a transient blip self-heals next tick.
        if documents and not entries:
            result.error = f"all {len(documents)} document(s) failed to embed; leaving existing index unchanged"
            return result

        # Store the fingerprint of ONLY successfully-embedded docs so the next
        # non-force refresh detects skipped docs as "incompatible" and retries them.
        # Separately, record skipped_keys so the search-side freshness check can
        # exclude permanently-skipped (un-embeddable) docs instead of reading stale.
        source_fingerprint = _source_fingerprint(settings, indexed_documents)
        updated_at = datetime.now()
        embedding_dir = path.parent
        if embedding_dir.is_symlink() or path.is_symlink():
            raise ValueError(f"Unsafe embedding index path: {path}")
        embedding_dir.mkdir(parents=True, exist_ok=True)
        if embedding_dir.is_symlink():
            raise ValueError(f"Unsafe embedding index directory: {embedding_dir}")
        embedding_dir.resolve().relative_to(settings.brain_home.resolve())
        tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        if tmp_path.is_symlink():
            raise ValueError(f"Unsafe embedding temp path: {tmp_path}")
        payload = (
            json.dumps(
                {
                    "model": settings.embedding_model,
                    "provider_fingerprint": _provider_fingerprint(settings),
                    "updated_at": updated_at.isoformat(),
                    "source_fingerprint": source_fingerprint,
                    "skipped_keys": skipped_keys,
                    "documents": entries,
                },
                indent=2,
            )
            + "\n"
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(tmp_path, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = -1
                handle.write(payload)
        finally:
            if fd != -1:
                os.close(fd)
        tmp_path.replace(path)
    except Exception as exc:  # noqa: BLE001 - provider/filesystem errors are returned as structured index errors.
        result.error = str(exc)
        return result
    result.indexed = True
    result.documents_indexed = len(entries)
    result.updated_at = updated_at
    return result


async def ensure_embedding_index(settings: Settings) -> EmbeddingIndexResult:
    """Ensure agents have a container-built vector index before vector search."""

    if _index_is_compatible(settings):
        return await refresh_embedding_index(settings, EmbeddingIndexRequest(force=False))
    return await refresh_embedding_index(settings, EmbeddingIndexRequest(force=True))


def embedding_index_unavailable_reason(settings: Settings) -> str | None:
    """Return why vector search should be skipped without refreshing inline."""

    if not settings.embedding_enabled:
        return "Embedding endpoint is disabled; set EMBEDDING_ENABLED=true"
    if settings.embedding_protocol != "openai-compatible":
        return "MVP supports EMBEDDING_PROTOCOL=openai-compatible only"
    try:
        data = _read_index_data(settings)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return f"Embedding index is unreadable; waiting for compile/ingest refresh: {exc}"
    if data is None:
        return "Embedding index is missing; waiting for compile/ingest refresh"
    current_documents = _collect_embedding_documents(settings)
    source_fingerprint = _effective_source_fingerprint(settings, data, current_documents)
    if not _index_data_is_compatible(settings, data, source_fingerprint):
        return "Embedding index is stale; waiting for compile/ingest refresh"
    return None


async def search_embedding_index(
    settings: Settings,
    query: str,
    limit: int,
    allowed_keys: set[tuple[str, str]] | None = None,
) -> list[QueryMatch]:
    if limit <= 0 or not settings.embedding_enabled:
        return []
    data = _read_index_data(settings)
    if data is None:
        return []
    current_documents = _collect_embedding_documents(settings)
    source_fingerprint = _effective_source_fingerprint(settings, data, current_documents)
    if not _index_data_is_compatible(settings, data, source_fingerprint):
        return []
    documents = data.get("documents", [])
    query_vector = await _embed_text(settings, query)

    scored: list[tuple[float, dict[str, Any]]] = []
    for document in documents:
        if not isinstance(document, dict):
            continue
        key = (str(document.get("vault", "")), str(document.get("path", "")))
        if allowed_keys is not None and key not in allowed_keys:
            continue
        vector = document.get("embedding")
        if not isinstance(vector, list):
            continue
        if len(vector) != len(query_vector):
            continue
        score = _cosine_similarity(query_vector, vector)
        if not math.isfinite(score) or score <= 0.05:
            continue
        scored.append((score, document))
    scored.sort(key=lambda item: item[0], reverse=True)

    matches: list[QueryMatch] = []
    for score, document in scored[:limit]:
        matches.append(
            QueryMatch(
                vault=str(document.get("vault", "")),
                path=str(document.get("path", "")),
                title=str(document.get("title", "")),
                snippet=f"[vector score {score:.3f}] {str(document.get('snippet', ''))}",
            )
        )
    return matches


def _read_index_data(settings: Settings) -> dict[str, Any] | None:
    path = index_path(settings)
    if path.parent.is_symlink() or path.is_symlink() or not path.exists():
        return None
    path.resolve().relative_to(settings.brain_home.resolve())
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def _index_data_is_compatible(
    settings: Settings, data: dict[str, Any], source_fingerprint: str | None = None
) -> bool:
    compatible = data.get("model") == settings.embedding_model and data.get("provider_fingerprint") == _provider_fingerprint(settings)
    if source_fingerprint is not None:
        compatible = compatible and data.get("source_fingerprint") == source_fingerprint
    return compatible


def _source_fingerprint(settings: Settings, documents: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    digest.update(settings.embedding_model.encode("utf-8"))
    digest.update(_provider_fingerprint(settings).encode("utf-8"))
    for document in documents:
        for key in ("vault", "path", "source_mtime_ns", "source_size", "content_hash"):
            digest.update(str(document.get(key, "")).encode("utf-8"))
            digest.update(b"\0")
    return digest.hexdigest()


def _skipped_keys_from_data(data: dict[str, Any]) -> set[tuple[str, str]]:
    raw = data.get("skipped_keys")
    if not isinstance(raw, list):
        return set()
    keys: set[tuple[str, str]] = set()
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            keys.add((str(item[0]), str(item[1])))
    return keys


def _effective_source_fingerprint(settings: Settings, data: dict[str, Any], documents: list[dict[str, Any]]) -> str:
    """Fingerprint over the docs the index is expected to cover: all current
    documents minus the keys skipped at build time. A stable set of permanently
    un-embeddable docs therefore does not mark the index stale for search."""
    skipped = _skipped_keys_from_data(data)
    effective = [
        document
        for document in documents
        if (str(document.get("vault", "")), str(document.get("path", ""))) not in skipped
    ]
    return _source_fingerprint(settings, effective)


def _provider_fingerprint(settings: Settings) -> str:
    digest = hashlib.sha256()
    digest.update(settings.embedding_protocol.encode("utf-8"))
    digest.update(b"\0")
    digest.update(settings.normalized_embedding_base_url().encode("utf-8"))
    digest.update(b"\0")
    digest.update(settings.embedding_model.encode("utf-8"))
    return digest.hexdigest()


def _index_is_compatible(settings: Settings) -> bool:
    try:
        data = _read_index_data(settings)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return data is not None and _index_data_is_compatible(settings, data)


async def _embed_text(settings: Settings, text: str) -> list[float]:
    client = AsyncOpenAI(
        base_url=settings.normalized_embedding_base_url(),
        api_key=settings.normalized_embedding_api_key() or "local-brain-no-key",
        timeout=settings.embedding_timeout_seconds,
    )
    response = await client.embeddings.create(model=settings.embedding_model, input=text)
    return [float(value) for value in response.data[0].embedding]


def _collect_embedding_documents(settings: Settings) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    mapper = PathMapper(settings.path_map)
    try:
        registry = load_registry(settings.brain_home)
        vault_paths = registered_vault_paths(registry, mapper)
    except RegistryError:
        vault_paths = {}

    # Determine store mode: no usable vault manifests → brain store mode.
    manifests = {
        name: manifest
        for name, path in vault_paths.items()
        if (manifest := load_manifest(path)) is not None
    }
    if not manifests:
        # Store mode: index brain-store articles.
        brain_store_root = store_root(settings)
        if brain_store_root.exists():
            for path in sorted(brain_store_root.glob("**/*.md")):
                if path.name == "index.md":
                    continue
                if not _is_regular_knowledge_file(path, brain_store_root):
                    continue
                try:
                    stat_result = path.stat()
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                documents.append(_document(_BRAIN_VAULT_NAME, str(path.relative_to(brain_store_root)), text, stat_result, max_input_chars=settings.embedding_max_input_chars))
    else:
        for name, vault_path in vault_paths.items():
            manifest = manifests.get(name)
            if manifest is None:
                continue
            knowledge_root = resolve_manifest_path(vault_path, manifest, "knowledge")
            if knowledge_root is None or not knowledge_root.exists():
                continue
            for path in sorted(knowledge_root.glob("**/*.md")):
                if not _is_regular_knowledge_file(path, knowledge_root) or is_excluded(path, vault_path, manifest):
                    continue
                try:
                    stat_result = path.stat()
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                documents.append(_document(name, str(path.relative_to(knowledge_root)), text, stat_result, max_input_chars=settings.embedding_max_input_chars))

    for path in list_queryable_captures(settings.brain_home).paths:
        try:
            stat_result = path.stat()
            text = read_capture(path, settings.capture_max_chars)
            relpath = str(path.relative_to(settings.brain_home))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        documents.append(_document("_captures", relpath, text, stat_result, max_input_chars=settings.embedding_max_input_chars))
    return documents


def _is_regular_knowledge_file(path: Path, knowledge_root: Path) -> bool:
    if path.is_symlink():
        return False
    try:
        path.resolve(strict=True).relative_to(knowledge_root.resolve())
        path.relative_to(knowledge_root)
    except (OSError, ValueError):
        return False
    return path.is_file()


def _document(vault: str, path: str, text: str, stat_result: os.stat_result, *, max_input_chars: int = 1800) -> dict[str, Any]:
    title = _title_for(path, text)
    indexed_text = text[:max_input_chars]
    return {
        "vault": vault,
        "path": path,
        "title": title,
        "snippet": " ".join(text[:320].split()),
        "source_mtime_ns": stat_result.st_mtime_ns,
        "source_size": stat_result.st_size,
        "content_hash": hashlib.sha256(indexed_text.encode("utf-8")).hexdigest(),
        "text": indexed_text,
    }


def _title_for(path: str, text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped.removeprefix("# ").strip()
    return Path(path).stem.replace("-", " ").title()


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)
