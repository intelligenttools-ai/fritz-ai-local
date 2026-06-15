"""Correlation feed: find semantically related brain-store articles for compile context."""

from __future__ import annotations

import re
from pathlib import Path

from .config import Settings
from .embeddings import _is_regular_knowledge_file, _title_for, embedding_index_unavailable_reason, search_embedding_index

_BRAIN_VAULT_NAME = "brain"


async def find_related_articles(
    settings: Settings,
    query_text: str,
    *,
    store_root: Path | None,
    top_k: int,
    char_budget: int,
) -> list[dict]:
    """Return top-K related existing store articles, budget-bounded and deterministic.

    Each entry is a dict with keys: vault, path, title, content (possibly truncated).

    - Returns [] when store_root is None, doesn't exist, top_k <= 0, or char_budget <= 0.
    - Ranking: uses vector search when embeddings are enabled and index is available;
      otherwise falls back to case-insensitive token-overlap count (score > 0 only),
      sorted by (score desc, path asc).
    - Budget: articles are filled in rank order; the article that would exceed the
      remaining budget is included truncated, and no further articles are appended.
    """

    if store_root is None or not store_root.exists():
        return []
    if top_k <= 0 or char_budget <= 0:
        return []

    # Collect all candidate store .md files (no index.md, no symlinks).
    candidates: list[Path] = sorted(
        path
        for path in store_root.glob("**/*.md")
        if path.name != "index.md" and _is_regular_knowledge_file(path, store_root)
    )

    if not candidates:
        return []

    # Build a mapping from relpath string → absolute Path for fast lookup.
    relpath_to_path: dict[str, Path] = {
        str(path.relative_to(store_root)): path for path in candidates
    }

    use_embeddings = embedding_index_unavailable_reason(settings) is None

    if use_embeddings:
        allowed_keys: set[tuple[str, str]] = {(_BRAIN_VAULT_NAME, relpath) for relpath in relpath_to_path}
        matches = await search_embedding_index(settings, query_text, top_k, allowed_keys=allowed_keys)
        ranked_relpaths: list[str] = [match.path for match in matches if match.path in relpath_to_path]
    else:
        ranked_relpaths = _keyword_rank(query_text, relpath_to_path, top_k)

    result: list[dict] = []
    remaining = char_budget
    for relpath in ranked_relpaths:
        if remaining <= 0:
            break
        abs_path = relpath_to_path.get(relpath)
        if abs_path is None:
            continue
        try:
            raw = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        title = _title_for(relpath, raw)
        if len(raw) <= remaining:
            content = raw
        else:
            content = raw[:remaining]
        result.append({
            "vault": _BRAIN_VAULT_NAME,
            "path": relpath,
            "title": title,
            "content": content,
        })
        remaining -= len(content)
        if len(raw) > remaining + len(content):
            # We truncated — stop here.
            break

    return result


def _keyword_rank(query_text: str, relpath_to_path: dict[str, Path], top_k: int) -> list[str]:
    """Rank store articles by case-insensitive token overlap with query_text.

    Returns at most top_k relpath strings, sorted by (score desc, relpath asc),
    excluding articles with score == 0.
    """

    query_tokens: set[str] = set(re.findall(r"[a-z0-9]+", query_text.lower()))
    if not query_tokens:
        return []

    scored: list[tuple[int, str]] = []
    for relpath, abs_path in relpath_to_path.items():
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        article_tokens: set[str] = set(re.findall(r"[a-z0-9]+", text.lower()))
        score = len(query_tokens & article_tokens)
        if score > 0:
            scored.append((score, relpath))

    # Sort: descending score, then ascending path (stable/deterministic).
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [relpath for _, relpath in scored[:top_k]]
