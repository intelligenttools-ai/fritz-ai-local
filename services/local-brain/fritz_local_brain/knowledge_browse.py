"""Read-only knowledge-store browsing helpers (#221).

Backs the ``/v1/knowledge/{tree,articles,article}`` endpoints. All functions are
READ-ONLY and path-traversal safe: any caller-supplied relative path is resolved
and MUST land inside the store root, else ``PathTraversalError`` is raised.

Large-store discipline: the tree and list helpers parse frontmatter ONLY (they
read the file incrementally, stopping at the closing ``---`` delimiter, and
discard the body). Only ``read_article`` reads and returns the full markdown body.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config import Settings
from .knowledge import (
    DEFAULT_STATUS,
    STATUS_VALUES,
    normalize_status,
    split_front_matter,
    store_root,
)
from .paths import is_relative_to

INDEX_FILENAME = "index.md"

# Maximum articles returned in a single list response (DoS guard).
_LIST_LIMIT_CAP = 500
_LIST_LIMIT_DEFAULT = 100


class PathTraversalError(Exception):
    """Raised when a caller-supplied path escapes the store root."""


def _resolve_inside(root: Path, rel: str | None) -> Path:
    """Resolve *rel* against *root* and verify it stays inside *root*.

    Rejects absolute paths, ``..`` traversal, symlink escapes, and malformed
    paths (NUL bytes, etc.) by resolving the candidate and requiring the
    resolved store root to be a parent (via ``is_relative_to`` which compares
    ``Path.resolve()`` outputs). ``rel`` empty / ``None`` means the root itself.

    Any ``ValueError`` or ``OSError`` raised during path construction or
    resolution is treated as a traversal rejection (→ ``PathTraversalError``).
    """
    root_resolved = root.resolve()
    if not rel:
        return root_resolved
    try:
        if Path(rel).is_absolute():
            raise PathTraversalError(f"absolute path not allowed: {rel!r}")
        candidate = (root_resolved / rel).resolve()
    except (ValueError, OSError) as exc:
        raise PathTraversalError(f"invalid path: {rel!r}") from exc
    if candidate != root_resolved and not is_relative_to(candidate, root_resolved):
        raise PathTraversalError(f"path escapes store root: {rel!r}")
    return candidate


def _is_article(path: Path) -> bool:
    """A .md file that is an article (not a directory index, not a symlink)."""
    return (
        path.is_file()
        and not path.is_symlink()
        and path.suffix == ".md"
        and path.name != INDEX_FILENAME
    )


def _read_frontmatter_block(path: Path) -> str:
    """Read only the YAML frontmatter block from *path*, stopping at the closing ``---``.

    Returns the raw YAML text (without the fence lines) for parsing. Returns an
    empty string if the file does not start with a ``---`` fence or the closing
    delimiter is not found. Never reads the body.
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            first_line = fh.readline()
            if not first_line.startswith("---"):
                return ""
            lines: list[str] = []
            for line in fh:
                if line.rstrip("\n") == "---":
                    return "".join(lines)
                lines.append(line)
            # closing delimiter not found
            return ""
    except OSError:
        return ""


def _frontmatter_only(path: Path) -> dict[str, Any]:
    """Read a file and return ONLY its parsed frontmatter (body never loaded)."""
    yaml_text = _read_frontmatter_block(path)
    if not yaml_text:
        return {}
    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _status_of(frontmatter: dict[str, Any]) -> str:
    raw = frontmatter.get("status")
    if isinstance(raw, str) and raw.strip():
        status = normalize_status(raw)
        if status in STATUS_VALUES:
            return status
    return DEFAULT_STATUS


def _title_of(frontmatter: dict[str, Any], rel_path: str) -> str:
    raw = frontmatter.get("title")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return Path(rel_path).stem


def _str_date(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()[:10]
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return None


def _as_link_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _iter_articles(subtree: Path) -> list[Path]:
    """All article files under *subtree* (recursive), sorted, index.md excluded."""
    if not subtree.exists():
        return []
    if subtree.is_file():
        return [subtree] if _is_article(subtree) else []
    return [p for p in sorted(subtree.rglob("*.md")) if _is_article(p)]


# ---------------------------------------------------------------------------
# tree
# ---------------------------------------------------------------------------

def build_tree(settings: Settings) -> dict[str, Any]:
    """Directory tree rooted at the store root with per-node counts + status breakdown.

    Frontmatter-only reads (never touches article bodies).
    """
    root = store_root(settings).resolve()
    return _tree_node(root, root, name=root.name)


def _tree_node(path: Path, root: Path, *, name: str) -> dict[str, Any]:
    status_counts = {s: 0 for s in STATUS_VALUES}
    children: list[dict[str, Any]] = []

    if path.exists() and path.is_dir():
        entries = sorted(path.iterdir(), key=lambda p: p.name)
        for child_dir in [e for e in entries if e.is_dir() and not e.is_symlink()]:
            node = _tree_node(child_dir, root, name=child_dir.name)
            children.append(node)
            for s, c in node["status_counts"].items():
                status_counts[s] += c
        # Direct-child articles at THIS level.
        for f in [e for e in entries if _is_article(e)]:
            status_counts[_status_of(_frontmatter_only(f))] += 1

    article_count = sum(status_counts.values())
    rel = "" if path == root else str(path.relative_to(root))
    return {
        "name": name,
        "path": rel,
        "article_count": article_count,
        "status_counts": status_counts,
        "children": children,
    }


# ---------------------------------------------------------------------------
# flat article list
# ---------------------------------------------------------------------------

def list_articles(
    settings: Settings,
    *,
    path: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = _LIST_LIMIT_DEFAULT,
    offset: int = 0,
) -> dict[str, Any]:
    """Flat, paginated article list. Frontmatter-only reads.

    ``limit`` is clamped to [1, ``_LIST_LIMIT_CAP``]; negative or zero values
    are treated as the default. ``offset`` is clamped to ≥ 0. Raises
    ``PathTraversalError`` if *path* escapes the store root.
    """
    # Clamp limit and offset defensively so direct callers can't DoS.
    effective_limit = min(max(limit, 1), _LIST_LIMIT_CAP) if limit > 0 else _LIST_LIMIT_DEFAULT
    effective_offset = max(offset, 0)

    root = store_root(settings).resolve()
    subtree = _resolve_inside(root, path)

    status_filter = normalize_status(status) if status else None
    q_lower = q.lower() if q else None

    rows: list[dict[str, Any]] = []
    for f in _iter_articles(subtree):
        rel = str(f.relative_to(root))
        fm = _frontmatter_only(f)
        art_status = _status_of(fm)
        title = _title_of(fm, rel)
        if status_filter and art_status != status_filter:
            continue
        if q_lower and q_lower not in title.lower() and q_lower not in rel.lower():
            continue
        rows.append(
            {
                "path": rel,
                "title": title,
                "status": art_status,
                "created": _str_date(fm.get("created")),
                "updated": _str_date(fm.get("updated")),
                "tags": [str(t) for t in fm["tags"]] if isinstance(fm.get("tags"), list) else [],
            }
        )

    total = len(rows)
    window = rows[effective_offset : effective_offset + effective_limit]
    return {"total": total, "limit": effective_limit, "offset": effective_offset, "articles": window}


# ---------------------------------------------------------------------------
# single article detail
# ---------------------------------------------------------------------------

def read_article(settings: Settings, *, path: str) -> dict[str, Any] | None:
    """Full detail for ONE article, or ``None`` if it does not exist / is index / is not .md.

    Reads the full body (only endpoint that does). Raises
    ``PathTraversalError`` if *path* escapes the store root.
    """
    root = store_root(settings).resolve()
    target = _resolve_inside(root, path)
    if (
        target == root
        or not target.is_file()
        or target.suffix != ".md"
        or target.name == INDEX_FILENAME
    ):
        return None

    text = target.read_text(encoding="utf-8", errors="replace")
    frontmatter, body = split_front_matter(text)
    rel = str(target.relative_to(root))

    supersedes = _as_link_list(frontmatter.get("supersedes"))
    superseded_by = _as_link_list(frontmatter.get("superseded_by"))

    links: list[dict[str, Any]] = []
    for kind, targets in (("supersedes", supersedes), ("superseded_by", superseded_by)):
        for t in targets:
            links.append({"relation": kind, "target": t, "exists": _link_exists(root, t)})

    return {
        "path": rel,
        "title": _title_of(frontmatter, rel),
        "status": _status_of(frontmatter),
        "frontmatter": frontmatter,
        "body": body,
        "supersedes": supersedes,
        "superseded_by": superseded_by,
        "links": links,
    }


def _link_exists(root: Path, target: str) -> bool:
    """Does a supersession target resolve to an existing file inside the store?"""
    try:
        resolved = _resolve_inside(root, target)
    except PathTraversalError:
        return False
    return resolved != root and resolved.is_file()
