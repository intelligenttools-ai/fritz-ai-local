"""KB-health aggregation module (#180).

Read-only scanner: computes article counts, embedding stats, compile health
(from telemetry), and backlog counts.  No API endpoint is exposed here (#181).

Cache: one cached result per brain_home, TTL-based.  ``force=True`` bypasses
the cache.  ``invalidate_kb_health_cache()`` drops the cached entry so the
next call recomputes.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import Settings
from .embeddings import index_path
from .indexes import ARCHIVE_INDEX_FILENAME
from .knowledge import DEFAULT_STATUS, STATUS_VALUES, _split_front_matter, normalize_status, store_root
from .manifests import load_manifest, resolve_manifest_path
from .paths import PathMapper
from .registry import RegistryError, load_registry, registered_vault_paths
from .status import _best_effort_status_backlog
from .telemetry import read_events

# Vault name used for the brain-owned knowledge store (matches embeddings.py).
_BRAIN_VAULT_NAME = "brain"

# Filenames excluded from article scans (index files, not articles).
_EXCLUDED_FILENAMES = {"index.md", ARCHIVE_INDEX_FILENAME}

# Cache TTL in seconds.
_CACHE_TTL = 60.0

# Module-level cache: brain_home_str -> (computed_at_monotonic, result_dict)
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------


def invalidate_kb_health_cache(settings: Settings | None = None) -> None:
    """Drop cached KB-health for *settings* (or all entries when settings is None)."""
    if settings is None:
        _cache.clear()
    else:
        _cache.pop(str(settings.brain_home), None)


def _safe_load_manifest(path: Path):
    """Load a vault manifest, treating a corrupt/unparseable one as 'no manifest'.

    ``load_manifest`` calls ``yaml.safe_load`` with no error handling, so a single
    malformed ``.brain/manifest.yaml`` in any registered vault would otherwise crash
    the whole scan. Returning None makes that vault fall through to store mode / be
    skipped instead, preserving the never-crash contract (#181 serves this over HTTP).
    """
    try:
        return load_manifest(path)
    except Exception:  # noqa: BLE001 - a corrupt manifest must not crash the whole scan
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_kb_health(settings: Settings, *, force: bool = False) -> dict[str, Any]:
    """Scan the brain and return a KB-health snapshot dict.

    Results are cached for _CACHE_TTL seconds per brain_home.  Pass
    ``force=True`` or call ``invalidate_kb_health_cache()`` to bypass the
    cache (e.g. after a compile).

    The return value is a plain dict safe for JSON serialisation by #181.
    Keys:
      articles_total       int
      articles_by_status   {status: count}  — all STATUS_VALUES present
      articles_by_vault    {vault_name: count}
      growth_by_day        {YYYY-MM-DD: count}  — from frontmatter `updated` or mtime
      embedding            {documents_indexed, skipped, index_size_bytes}
      compile              {total, ok, error, success_rate}
      backlog              {pending_captures_by_source}
    """
    cache_key = str(settings.brain_home)
    now = time.monotonic()

    if not force:
        entry = _cache.get(cache_key)
        if entry is not None:
            cached_at, result = entry
            if now - cached_at < _CACHE_TTL:
                return result

    result = _compute(settings)
    _cache[cache_key] = (now, result)
    return result


# ---------------------------------------------------------------------------
# Internal computation
# ---------------------------------------------------------------------------


def _compute(settings: Settings) -> dict[str, Any]:
    articles_by_status: dict[str, int] = {s: 0 for s in STATUS_VALUES}
    articles_by_vault: dict[str, int] = {}
    growth_by_day: dict[str, int] = {}

    mapper = PathMapper(settings.path_map)
    try:
        registry = load_registry(settings.brain_home)
        vault_paths = registered_vault_paths(registry, mapper)
    except RegistryError:
        vault_paths = {}

    manifests = {
        name: manifest
        for name, path in vault_paths.items()
        if (manifest := _safe_load_manifest(path)) is not None
    }
    store_mode = not manifests

    if store_mode:
        _scan_store(settings, articles_by_status, articles_by_vault, growth_by_day)
    else:
        _scan_vaults(vault_paths, manifests, articles_by_status, articles_by_vault, growth_by_day)

    articles_total = sum(articles_by_status.values())

    # NOTE: articles_total counts knowledge ARTICLES only (excludes raw captures and
    # the archive index), whereas embedding.documents_indexed + skipped also covers the
    # "_captures" pseudo-vault. The two intentionally measure DIFFERENT sets, so they
    # are not expected to sum to equal — do not treat any mismatch as a bug.
    return {
        "articles_total": articles_total,
        "articles_by_status": dict(articles_by_status),
        "articles_by_vault": dict(articles_by_vault),
        "growth_by_day": dict(growth_by_day),
        "embedding": _embedding_stats(settings),
        "compile": _compile_stats(settings),
        "backlog": _backlog_stats(settings),
    }


def _scan_store(
    settings: Settings,
    articles_by_status: dict[str, int],
    articles_by_vault: dict[str, int],
    growth_by_day: dict[str, int],
) -> None:
    brain_store_root = store_root(settings)
    if not brain_store_root.exists():
        return
    vault_count = 0
    for path in sorted(brain_store_root.glob("**/*.md")):
        if path.name in _EXCLUDED_FILENAMES:
            continue
        if not path.is_file() or path.is_symlink():
            continue
        status, day = _article_meta(path)
        articles_by_status[status] = articles_by_status.get(status, 0) + 1
        vault_count += 1
        if day:
            growth_by_day[day] = growth_by_day.get(day, 0) + 1
    if vault_count:
        articles_by_vault[_BRAIN_VAULT_NAME] = vault_count


def _scan_vaults(
    vault_paths: dict[str, Path],
    manifests: dict,
    articles_by_status: dict[str, int],
    articles_by_vault: dict[str, int],
    growth_by_day: dict[str, int],
) -> None:
    for name, vault_path in vault_paths.items():
        manifest = manifests.get(name)
        if manifest is None:
            continue
        knowledge_root = resolve_manifest_path(vault_path, manifest, "knowledge")
        if knowledge_root is None or not knowledge_root.exists():
            continue
        vault_count = 0
        for path in sorted(knowledge_root.glob("**/*.md")):
            if path.name in _EXCLUDED_FILENAMES:
                continue
            if not path.is_file() or path.is_symlink():
                continue
            status, day = _article_meta(path)
            articles_by_status[status] = articles_by_status.get(status, 0) + 1
            vault_count += 1
            if day:
                growth_by_day[day] = growth_by_day.get(day, 0) + 1
        if vault_count:
            articles_by_vault[name] = vault_count


def _article_meta(path: Path) -> tuple[str, str | None]:
    """Return (normalized_status, iso_date_or_None) for an article file.

    Never raises: a malformed or unreadable file returns defaults.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return DEFAULT_STATUS, _mtime_date(path)

    try:
        frontmatter, _body = _split_front_matter(text)
    except Exception:  # noqa: BLE001
        return DEFAULT_STATUS, _mtime_date(path)

    # Status
    raw_status = frontmatter.get("status")
    if isinstance(raw_status, str) and raw_status.strip():
        status = normalize_status(raw_status)
        if status not in STATUS_VALUES:
            status = DEFAULT_STATUS
    else:
        status = DEFAULT_STATUS

    # Date: prefer frontmatter `updated`, fall back to mtime
    raw_updated = frontmatter.get("updated")
    day: str | None = None
    if isinstance(raw_updated, str) and raw_updated.strip():
        # YAML may parse YYYY-MM-DD as a date object; convert robustly
        day = raw_updated.strip()[:10]  # take first 10 chars (YYYY-MM-DD)
    elif hasattr(raw_updated, "isoformat"):
        # yaml parsed it as a date/datetime object
        day = raw_updated.isoformat()[:10]
    else:
        day = _mtime_date(path)

    return status, day


def _mtime_date(path: Path) -> str | None:
    try:
        import datetime
        mtime = path.stat().st_mtime
        return datetime.date.fromtimestamp(mtime).isoformat()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Embedding stats
# ---------------------------------------------------------------------------


def _embedding_stats(settings: Settings) -> dict[str, Any]:
    path = index_path(settings)
    if not path.exists() or path.is_symlink():
        return {"documents_indexed": 0, "skipped": 0, "index_size_bytes": None}

    try:
        index_size_bytes = path.stat().st_size
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("index.json is not a dict")
    except (OSError, json.JSONDecodeError, ValueError):
        return {"documents_indexed": 0, "skipped": 0, "index_size_bytes": None}

    documents = data.get("documents", [])
    documents_indexed = len(documents) if isinstance(documents, list) else 0

    # Prefer skipped_keys (list of [vault, path] pairs) for count; fall back to
    # skipped_entries (list of dicts with vault/path/content_hash).
    skipped_keys = data.get("skipped_keys")
    if isinstance(skipped_keys, list):
        skipped = len(skipped_keys)
    else:
        skipped_entries = data.get("skipped_entries")
        skipped = len(skipped_entries) if isinstance(skipped_entries, list) else 0

    return {
        "documents_indexed": documents_indexed,
        "skipped": skipped,
        "index_size_bytes": index_size_bytes,
    }


# ---------------------------------------------------------------------------
# Compile health from telemetry
# ---------------------------------------------------------------------------


def _compile_stats(settings: Settings) -> dict[str, Any]:
    try:
        events = read_events(settings)
    except Exception:  # noqa: BLE001 - telemetry must never break callers
        events = []

    compile_events = [e for e in events if e.get("event_type") == "compile"]
    total = len(compile_events)
    ok = sum(1 for e in compile_events if e.get("status") == "ok")
    error = sum(1 for e in compile_events if e.get("status") == "error")
    success_rate = (ok / total) if total > 0 else None

    return {"total": total, "ok": ok, "error": error, "success_rate": success_rate}


# ---------------------------------------------------------------------------
# Backlog
# ---------------------------------------------------------------------------


def _backlog_stats(settings: Settings) -> dict[str, Any]:
    try:
        backlog = _best_effort_status_backlog(settings.brain_home)
        return {"pending_captures_by_source": dict(backlog.by_source)}
    except Exception:  # noqa: BLE001 - backlog scan must never break callers
        return {"pending_captures_by_source": {}}
