"""Markdown index maintenance for the brain-owned knowledge store.

Directory model
---------------
The store has a typed two-level layout:

    <store>/
        index.md                  ← global MOC (map of content)
        common/
            decisions/            ← arch / ADR-style records
            lessons/              ← retrospective learnings / feedback
            runbooks/             ← how-to / operational procedures
            context/              ← background knowledge, glossaries, etc.
        <project-slug>/
            index.md              ← per-project MOC
            decisions/
            lessons/
            runbooks/
            context/

``common`` is used for knowledge that is not tied to a specific project.
Each leaf directory may have its own ``index.md`` maintained by
``update_directory_index``.  The per-project (or common) ``index.md`` links
the typed section directories; the global ``<store>/index.md`` links both
``common`` and every known project.

All write helpers accept a ``dry_run`` flag; when ``True`` no files are
touched and the function is a no-op.
"""

from __future__ import annotations

from pathlib import Path

# Canonical section names.  Order is preserved in generated indexes.
SECTIONS: list[str] = ["decisions", "lessons", "runbooks", "context"]

# The slug used for non-project-specific knowledge.
COMMON_SCOPE = "common"


# ---------------------------------------------------------------------------
# Low-level directory-level index
# ---------------------------------------------------------------------------


def update_directory_index(target: Path, title: str, summary: str, dry_run: bool) -> None:
    """Append an entry for *target* to its sibling ``index.md``.

    Creates ``index.md`` with a ``# <dirname>`` header when it does not yet
    exist.  Deduplicates by link target: if ``](relname)`` is already present
    in the file the function returns without writing.

    Skips silently when *target* itself is named ``index.md``.
    """

    if dry_run:
        return
    if target.name == "index.md":
        return
    index_path = target.parent / "index.md"
    rel = target.name
    line = f"- [{title}]({rel}) -- {summary.strip()}"
    if index_path.exists():
        existing = index_path.read_text(encoding="utf-8")
        if f"]({rel})" in existing:
            return
        content = existing.rstrip() + "\n" + line + "\n"
    else:
        content = f"# {target.parent.name}\n\n{line}\n"
    index_path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Scope-level (project or common) MOC
# ---------------------------------------------------------------------------


def _scope_dir(store_root: Path, scope: str) -> Path:
    """Return the directory for *scope* (``common`` or a project slug)."""
    return store_root / scope


def _section_dir(store_root: Path, scope: str, section: str) -> Path:
    return _scope_dir(store_root, scope) / section


def _ensure_scope_index(store_root: Path, scope: str, dry_run: bool) -> None:
    """Create or refresh the ``<scope>/index.md`` linking its typed sections."""

    if dry_run:
        return
    scope_dir = _scope_dir(store_root, scope)
    index_path = scope_dir / "index.md"

    lines: list[str] = []
    for section in SECTIONS:
        section_dir = scope_dir / section
        if not section_dir.is_dir():
            continue
        has_leaf_index = (section_dir / "index.md").exists()
        has_article = any(
            p.name != "index.md" for p in section_dir.glob("*.md")
        )
        if has_leaf_index or has_article:
            lines.append(f"- [{section}]({section}/index.md)")

    if not lines:
        return

    header = f"# {scope}\n"
    body = "\n".join(lines) + "\n"
    content = header + "\n" + body

    if index_path.exists():
        existing = index_path.read_text(encoding="utf-8")
        # Only rewrite when content has actually changed.
        if existing == content:
            return

    scope_dir.mkdir(parents=True, exist_ok=True)
    index_path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Global MOC
# ---------------------------------------------------------------------------


def build_global_moc(store_root: Path, dry_run: bool) -> None:
    """Create or refresh ``<store>/index.md`` linking every known scope.

    A scope is any direct subdirectory of *store_root* that contains at least
    one typed section directory.  ``index.md`` files themselves are ignored.
    """

    if dry_run:
        return

    moc_path = store_root / "index.md"
    lines: list[str] = ["# Brain Knowledge Store\n"]

    scope_dirs = sorted(
        p for p in store_root.iterdir()
        if p.is_dir() and p.name != "index.md"
    )
    for scope_dir in scope_dirs:
        scope_index = scope_dir / "index.md"
        if scope_index.exists() or any((scope_dir / s).is_dir() for s in SECTIONS):
            rel = f"{scope_dir.name}/index.md"
            lines.append(f"- [{scope_dir.name}]({rel})")

    if len(lines) == 1:
        # Nothing to link yet.
        return

    content = "\n".join(lines) + "\n"
    store_root.mkdir(parents=True, exist_ok=True)
    moc_path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Composite update helper
# ---------------------------------------------------------------------------


def update_indexes_for_article(
    store_root: Path,
    article_path: Path,
    title: str,
    summary: str,
    dry_run: bool,
) -> None:
    """Update all indexes affected by writing *article_path*.

    Performs three updates in order:

    1. The leaf directory index (``<section>/index.md``) via
       ``update_directory_index``.
    2. The scope-level MOC (``<scope>/index.md``) linking typed sections that
       exist under that scope.
    3. The global MOC (``<store>/index.md``) linking all known scopes.

    *article_path* must be an absolute path inside *store_root*.  The function
    infers scope and section from the path components relative to the store
    root.  It is a no-op when ``dry_run=True``.
    """

    if dry_run:
        return

    # Infer scope from path relative to store root.
    try:
        rel_parts = article_path.relative_to(store_root).parts
    except ValueError:
        # article_path is outside store_root — nothing to do.
        return

    if len(rel_parts) < 2:
        # Directly under store root, no scope/section structure.
        return

    scope = rel_parts[0]

    # Update leaf directory index.
    update_directory_index(article_path, title, summary, dry_run=False)

    # Refresh the scope-level MOC.
    _ensure_scope_index(store_root, scope, dry_run=False)

    # Refresh the global MOC.
    build_global_moc(store_root, dry_run=False)


# ---------------------------------------------------------------------------
# Backfill helper
# ---------------------------------------------------------------------------


def backfill_indexes(store_root: Path, dry_run: bool) -> None:
    """Scan an existing store and rebuild all indexes and the global MOC.

    For each markdown file found under *store_root*:

    * The file's ``title`` is extracted from a YAML front-matter ``title:``
      field if present; otherwise the stem (filename without extension) is
      used.
    * The ``summary`` is extracted from a ``summary:`` front-matter field if
      present; otherwise an empty string is used.

    The leaf ``index.md`` for each containing directory is rebuilt from
    scratch (existing content is replaced).  Then the scope-level MOC and the
    global MOC are regenerated.

    Safe to re-run: all index files are rebuilt deterministically.
    """

    if not store_root.is_dir():
        return

    if dry_run:
        return

    # Collect all markdown articles (not index files).
    articles = sorted(
        p for p in store_root.rglob("*.md")
        if p.name != "index.md"
    )

    # Group by parent directory.
    by_dir: dict[Path, list[Path]] = {}
    for article in articles:
        by_dir.setdefault(article.parent, []).append(article)

    # Rebuild each leaf directory index from scratch.
    for dir_path, dir_articles in by_dir.items():
        index_path = dir_path / "index.md"
        header = f"# {dir_path.name}\n\n"
        entry_lines: list[str] = []
        seen: set[str] = set()
        for article in dir_articles:
            rel = article.name
            if rel in seen:
                continue
            seen.add(rel)
            title, summary = _extract_front_matter(article)
            entry_lines.append(f"- [{title}]({rel}) -- {summary}")
        content = header + "\n".join(entry_lines) + "\n"
        index_path.write_text(content, encoding="utf-8")

    # Refresh scope-level MOCs.
    scopes = sorted(
        p.name for p in store_root.iterdir()
        if p.is_dir()
    )
    for scope in scopes:
        _ensure_scope_index(store_root, scope, dry_run=False)

    # Rebuild global MOC.
    build_global_moc(store_root, dry_run=False)


# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------


def _extract_front_matter(path: Path) -> tuple[str, str]:
    """Return ``(title, summary)`` parsed from YAML front matter.

    Falls back to ``(stem, "")`` when the front matter is absent or
    malformed.  Intentionally avoids importing ``yaml`` at module level so
    the module stays importable with zero heavy dependencies.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return path.stem, ""

    if not text.startswith("---"):
        return path.stem, ""

    end = text.find("\n---", 3)
    if end == -1:
        return path.stem, ""

    front_matter_block = text[3:end].strip()
    title = path.stem
    summary = ""
    for raw_line in front_matter_block.splitlines():
        line = raw_line.strip()
        if line.startswith("title:"):
            title = line[len("title:"):].strip().strip('"').strip("'")
        elif line.startswith("summary:"):
            summary = line[len("summary:"):].strip().strip('"').strip("'")
    return title, summary
