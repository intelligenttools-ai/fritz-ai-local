"""Read-only query workflow orchestration."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .agents.query_agent import BrainQueryAgent
from .captures import list_queryable_captures
from .config import Settings
from .embeddings import embedding_index_unavailable_reason, ensure_embedding_index, search_embedding_index
from .knowledge import ARCHIVE_STATUSES, store_root
from .agents.query_agent import _status_of as _qa_status_of
from .manifests import load_manifest, resolve_manifest_path
from .models import QueryRunRequest, QueryRunResult
from .paths import PathMapper
from .registry import RegistryError, load_registry, registered_vault_paths
from .security import is_excluded
from .skill_loader import load_skill

_BRAIN_VAULT_NAME = "brain"


async def run_query(
    settings: Settings,
    request: QueryRunRequest,
    *,
    use_vector: bool = False,
    ensure_index: bool = False,
) -> QueryRunResult:
    started = datetime.now()
    errors: list[str] = []
    skipped: list[str] = []
    mapper = PathMapper(settings.path_map)
    try:
        registry = load_registry(settings.brain_home)
        vault_paths = registered_vault_paths(registry, mapper)
    except RegistryError:
        vault_paths = {}
    capture_vault_name = "_captures"
    if capture_vault_name in vault_paths:
        errors.append(f"Reserved vault name is not allowed: {capture_vault_name}")
        vault_paths = {name: path for name, path in vault_paths.items() if name != capture_vault_name}

    manifests = {
        name: manifest
        for name, path in vault_paths.items()
        if (manifest := load_manifest(path)) is not None
    }
    store_mode = not manifests

    agent = BrainQueryAgent(skill_text=load_skill(settings.skills_dir, settings.query_skill_name))
    matches = []

    if not store_mode:
        for name, vault_path in vault_paths.items():
            if request.vault and name != request.vault:
                continue
            manifest = manifests.get(name)
            if manifest is None:
                skipped.append(f"{name}: missing manifest")
                continue
            remaining = request.limit - len(matches)
            if remaining <= 0:
                break
            matches.extend(agent.search_vault(name, vault_path, manifest, request.query, remaining))

    if store_mode and (request.vault is None or request.vault == _BRAIN_VAULT_NAME):
        brain_store_root = store_root(settings)
        remaining = request.limit - len(matches)
        if remaining > 0:
            matches.extend(agent.search_store(brain_store_root, request.query, remaining, scope=request.scope))

    if request.vault is None or request.vault == capture_vault_name:
        remaining = request.limit - len(matches)
        if remaining > 0:
            matches.extend(agent.search_captures(settings.brain_home, request.query, remaining))

    known_vaults = set(vault_paths) | {capture_vault_name}
    if store_mode:
        known_vaults.add(_BRAIN_VAULT_NAME)
    if request.vault and request.vault not in known_vaults:
        errors.append(f"Unknown vault: {request.vault}")

    if use_vector and not errors:
        remaining = request.limit - len(matches)
        if remaining > 0:
            vector_search_available = True
            if ensure_index:
                embedding_result = await ensure_embedding_index(settings)
                if embedding_result.error:
                    skipped.append(f"vector search: {embedding_result.error}")
                    vector_search_available = False
            else:
                unavailable_reason = embedding_index_unavailable_reason(settings)
                if unavailable_reason:
                    skipped.append(f"vector search: {unavailable_reason}")
                    vector_search_available = False
            seen = {(match.vault, match.path) for match in matches}
            allowed_vector_paths = _allowed_vector_paths(settings, vault_paths, capture_vault_name, scope=request.scope)
            if request.vault:
                allowed_vector_paths = {key for key in allowed_vector_paths if key[0] == request.vault}
            try:
                vector_matches = (
                    await search_embedding_index(
                        settings,
                        request.query,
                        remaining,
                        allowed_keys=allowed_vector_paths,
                    )
                    if vector_search_available
                    else []
                )
            except Exception as exc:  # noqa: BLE001 - exact results should survive vector provider failures.
                skipped.append(f"vector search: {exc}")
                vector_matches = []
            for match in vector_matches:
                if request.vault and match.vault != request.vault:
                    continue
                key = (match.vault, match.path)
                if key not in allowed_vector_paths:
                    continue
                if key in seen:
                    continue
                matches.append(match)
                seen.add(key)
                if len(matches) >= request.limit:
                    break
    return QueryRunResult(
        run_id=str(uuid4()),
        started_at=started,
        finished_at=datetime.now(),
        query=request.query,
        matches=matches,
        skipped=skipped,
        errors=errors,
    )


def _allowed_vector_paths(
    settings: Settings,
    vault_paths: dict[str, Path],
    capture_vault_name: str,
    scope: str = "active",
) -> set[tuple[str, str]]:
    """Compute the set of (vault, relpath) keys allowed in vector search.

    Scope filtering for STORE keys:
    - ``"active"`` (default): exclude store articles whose status ∈
      ARCHIVE_STATUSES (superseded/historical).
    - ``"include_archive"`` or ``"all"``: include all store articles regardless
      of status.

    Capture keys and registry-vault keys are unaffected by scope.
    """
    allowed: set[tuple[str, str]] = set()
    for capture in list_queryable_captures(settings.brain_home).paths:
        try:
            allowed.add((capture_vault_name, str(capture.relative_to(settings.brain_home))))
        except ValueError:
            continue

    # Determine store mode: no usable vault manifests → brain store mode.
    manifests = {
        name: manifest
        for name, path in vault_paths.items()
        if (manifest := load_manifest(path)) is not None
    }
    if not manifests:
        # Store mode: add brain-store articles as ("brain", relpath) keys,
        # filtering by scope for archive-status articles.
        brain_store_root = store_root(settings)
        exclude_archive = scope == "active"
        if brain_store_root.exists():
            for path in brain_store_root.glob("**/*.md"):
                if path.name == "index.md":
                    continue
                if not path.is_file() or path.is_symlink():
                    continue
                if exclude_archive:
                    try:
                        text = path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    status = _qa_status_of(text)
                    effective = status if status is not None else "active"
                    if effective in ARCHIVE_STATUSES:
                        continue
                try:
                    allowed.add((_BRAIN_VAULT_NAME, str(path.relative_to(brain_store_root))))
                except ValueError:
                    continue
        return allowed

    for name, vault_path in vault_paths.items():
        manifest = manifests.get(name)
        if manifest is None:
            continue
        knowledge_root = resolve_manifest_path(vault_path, manifest, "knowledge")
        if knowledge_root is None or not knowledge_root.exists():
            continue
        for path in knowledge_root.glob("**/*.md"):
            if path.is_file() and not path.is_symlink() and not is_excluded(path, vault_path, manifest):
                try:
                    allowed.add((name, str(path.relative_to(knowledge_root))))
                except ValueError:
                    continue
    return allowed
