"""Read-only query workflow orchestration."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .agents.query_agent import BrainQueryAgent
from .captures import list_queryable_captures, read_capture_raw
from .config import Settings
from .live_fetch import live_fetch as _live_fetch
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

# Bound on live-fetched content folded into an enriched snippet.
_LIVE_FETCH_SNIPPET_CAP = 600


def merge_matches(
    brain_matches: list[QueryMatch],
    external_matches: list[QueryMatch],
    *,
    policy: str,
    limit: int,
) -> list[QueryMatch]:
    """Merge brain and external matches under a retrieval-synthesis policy.

    This is invoked during **live-fetch retrieval-synthesis** (``live_fetch=True``
    in :func:`run_query`) to combine locally-assembled brain results with
    live-fetched external content.  It is NOT called in the normal
    ``live_fetch=False`` path, so existing query behaviour is unchanged.

    Policies
    --------
    - ``"brain-first"`` (default, epic §10): brain matches are AUTHORITATIVE.
      They always come first and win on dedup; external matches only FILL the
      remaining slots up to *limit* and never displace a brain match.
    - ``"peer-ranked"``: a flat append (brain then external) with dedup and no
      authority — a simple alternative ranking.

    Dedup key is ``(vault, path)`` when both are set, otherwise the title. The
    result is deterministic and truncated to *limit*.
    """

    def _key(match: QueryMatch) -> tuple[str, str]:
        if match.vault or match.path:
            return (match.vault, match.path)
        return ("", match.title)

    merged: list[QueryMatch] = []
    seen: set[tuple[str, str]] = set()

    # Brain matches first in both policies (authoritative for brain-first;
    # natural order for peer-ranked).
    for match in brain_matches:
        key = _key(match)
        if key in seen:
            continue
        seen.add(key)
        merged.append(match)
        if len(merged) >= limit:
            return merged[:limit]

    for match in external_matches:
        key = _key(match)
        if key in seen:
            continue
        seen.add(key)
        merged.append(match)
        if len(merged) >= limit:
            break

    return merged[:limit]


def _build_external_match(settings: Settings, match: "QueryMatch") -> "QueryMatch | None":
    """Build an enriched external QueryMatch from a live-fetched index-only capture.

    Returns a new :class:`~fritz_local_brain.models.QueryMatch` whose snippet is
    replaced with (or extended by) the live-fetched full content, or ``None``
    when the capture is not index-only, has no resolvable pointer, or the live
    fetch fails.
    """
    if match.vault != "_captures":
        return None
    capture_path = settings.brain_home / match.path
    try:
        text = read_capture_raw(capture_path)
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    meta = _mirror_capture_meta(text)
    if meta is None:
        return None
    mode, pointer = meta
    if mode != "index-only" or not pointer:
        return None
    live = _live_fetch(settings, pointer)
    if not live:
        return None
    enriched = " ".join(live.split())[:_LIVE_FETCH_SNIPPET_CAP]
    return match.model_copy(
        update={"snippet": f"{match.snippet} [live-fetch] {enriched}".strip()}
    )


def _enrich_index_only_matches(settings: Settings, matches: list[QueryMatch]) -> None:
    """Enrich index-only mirrored capture matches in-place via live-fetch.

    For each capture match whose front-matter declares ``mode: index-only`` with
    a ``pointer``, resolve the live content and fold a bounded slice into the
    match's snippet (retrieval-synthesis). Failures leave the match unchanged.
    """
    for index, match in enumerate(matches):
        enriched_match = _build_external_match(settings, match)
        if enriched_match is not None:
            matches[index] = enriched_match


def _mirror_capture_meta(text: str) -> tuple[str | None, str | None] | None:
    """Parse ``mode`` and ``pointer`` from a capture's YAML front-matter.

    Returns ``None`` when the text has no front-matter block; otherwise a
    ``(mode, pointer)`` tuple where either element may be ``None`` if absent.
    """
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    mode: str | None = None
    pointer: str | None = None
    for raw_line in text[3:end].splitlines():
        line = raw_line.strip()
        if line.startswith("mode:"):
            mode = line[len("mode:"):].strip().strip('"').strip("'")
        elif line.startswith("pointer:"):
            pointer = line[len("pointer:"):].strip().strip('"').strip("'")
    return (mode, pointer)


async def run_query(
    settings: Settings,
    request: QueryRunRequest,
    *,
    use_vector: bool = False,
    ensure_index: bool = False,
) -> QueryRunResult:
    """Run a read-only query against all configured brain sources.

    Retrieval-synthesis (live-fetch path)
    --------------------------------------
    When ``request.live_fetch`` is ``True`` the already-assembled LOCAL matches
    (store / vault / vector / captures) are treated as the BRAIN (authoritative)
    set.  For each index-only mirrored capture hit, the live-fetched full content
    is used to produce an EXTERNAL :class:`~fritz_local_brain.models.QueryMatch`.
    The brain and external sets are then combined via :func:`merge_matches` under
    ``settings.merge_policy``:

    - ``"brain-first"`` (default, epic §10): brain matches are authoritative and
      always come first; external matches only fill remaining slots up to *limit*.
    - ``"peer-ranked"``: flat append with dedup — an alternative ordering where
      neither set is privileged.

    When ``request.live_fetch`` is ``False`` (the default), the function returns
    only locally-assembled matches in their natural order and :func:`merge_matches`
    is never called, so all existing query tests are unaffected.
    """
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

    if request.live_fetch and not errors:
        # Retrieval-synthesis: partition matches into brain (authoritative local)
        # and external (live-fetched from index-only mirrored captures), then
        # combine via merge_matches under the configured merge policy.
        #
        # Brain set  — everything assembled above EXCEPT index-only captures that
        #              have a live-fetchable pointer (those become external).
        # External set — enriched QueryMatch objects built from the live-fetched
        #               full content of each index-only capture hit.
        brain_matches: list[QueryMatch] = []
        external_matches: list[QueryMatch] = []
        for match in matches:
            external = _build_external_match(settings, match)
            if external is not None:
                # Index-only mirror hit: the live-fetched version is external;
                # the original in-brain stub is NOT included in the brain set
                # (it would be a lower-quality duplicate of the external entry).
                external_matches.append(external)
            else:
                brain_matches.append(match)
        matches = merge_matches(
            brain_matches,
            external_matches,
            policy=settings.merge_policy,
            limit=request.limit,
        )

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
