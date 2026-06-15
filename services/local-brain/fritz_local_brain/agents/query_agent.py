"""Read-only Brain query agent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..captures import list_queryable_captures, read_capture_raw
from ..manifests import resolve_manifest_path
from ..models import QueryMatch
from ..security import is_excluded

# Status values that are included in the default "active" scope.
_ACTIVE_STATUSES: frozenset[str] = frozenset({"active", "corroborated"})


@dataclass
class BrainQueryAgent:
    """Policy-bound deterministic query executor."""

    skill_text: str

    def search_vault(
        self,
        vault: str,
        vault_path: Path,
        manifest: dict[str, Any],
        query: str,
        remaining: int,
    ) -> list[QueryMatch]:
        knowledge_root = resolve_manifest_path(vault_path, manifest, "knowledge")
        if knowledge_root is None or not knowledge_root.exists():
            return []

        needle = query.casefold()
        matches: list[QueryMatch] = []
        for path in sorted(knowledge_root.glob("**/*.md")):
            if len(matches) >= remaining:
                break
            if not _is_regular_knowledge_file(path, knowledge_root):
                continue
            if is_excluded(path, vault_path, manifest):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            position = text.casefold().find(needle)
            if position < 0:
                continue
            matches.append(
                QueryMatch(
                    vault=vault,
                    path=str(path.relative_to(knowledge_root)),
                    title=_title_for(path, text),
                    snippet=_snippet(text, position),
                )
            )
        return matches

    def search_store(
        self,
        store_root: Path | None,
        query: str,
        remaining: int,
        scope: str = "active",
    ) -> list[QueryMatch]:
        """Search brain-owned knowledge store articles without a registry.

        Returns an empty list when *store_root* is None or does not exist.
        Each match uses vault name ``"brain"`` with a path relative to
        *store_root*.  ``index.md`` files and symlinks are skipped.
        The *scope* filter applies the article's ``status`` frontmatter value:
        ``"active"`` (the default) includes articles with no status or whose
        status is in {active, corroborated}; ``"all"`` includes everything.
        """

        if store_root is None or not store_root.exists():
            return []
        if remaining <= 0:
            return []

        needle = query.casefold()
        matches: list[QueryMatch] = []
        for path in sorted(store_root.glob("**/*.md")):
            if len(matches) >= remaining:
                break
            if path.name == "index.md":
                continue
            if not _is_regular_knowledge_file(path, store_root):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if scope != "all" and not _is_active_article(text):
                continue
            position = text.casefold().find(needle)
            if position < 0:
                continue
            matches.append(
                QueryMatch(
                    vault="brain",
                    path=str(path.relative_to(store_root)),
                    title=_title_for(path, text),
                    snippet=_snippet(text, position),
                )
            )
        return matches

    def search_captures(self, brain_home: Path, query: str, remaining: int) -> list[QueryMatch]:
        """Search raw capture files so inbox-only facts are visible to clients."""

        if remaining <= 0:
            return []

        needle = query.casefold()
        matches: list[QueryMatch] = []
        for path in list_queryable_captures(brain_home).paths:
            if len(matches) >= remaining:
                break
            try:
                text = read_capture_raw(path)
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            position = text.casefold().find(needle)
            if position < 0:
                continue
            matches.append(
                QueryMatch(
                    vault="_captures",
                    path=str(path.relative_to(brain_home)),
                    title=_title_for(path, text),
                    snippet=_snippet(text, position),
                )
            )
        return matches


def _is_regular_knowledge_file(path: Path, knowledge_root: Path) -> bool:
    try:
        path.resolve().relative_to(knowledge_root.resolve())
        path.relative_to(knowledge_root)
    except ValueError:
        return False
    return path.is_file() and not path.is_symlink()


def _title_for(path: Path, text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped.removeprefix("# ").strip()
    return path.stem.replace("-", " ").title()


def _snippet(text: str, position: int) -> str:
    start = max(0, position - 120)
    end = min(len(text), position + 220)
    return " ".join(text[start:end].split())


def _is_active_article(text: str) -> bool:
    """Return True when an article's status is compatible with the active scope.

    An article is active when:
    - it has no ``status:`` key in its YAML front matter (forward-compatible:
      today's articles have no status), OR
    - its ``status`` value is in ``_ACTIVE_STATUSES`` (``active`` or
      ``corroborated``).

    Articles with ``status: superseded``, ``status: deprecated``,
    ``status: historical``, or any other value are excluded.
    """

    if not text.startswith("---"):
        return True

    end = text.find("\n---", 3)
    if end == -1:
        return True

    front_matter_block = text[3:end]
    for raw_line in front_matter_block.splitlines():
        line = raw_line.strip()
        if line.startswith("status:"):
            status_value = line[len("status:"):].strip().strip('"').strip("'")
            return status_value in _ACTIVE_STATUSES
    # No status key found — include in active scope.
    return True
