"""Read-only Brain query agent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..captures import list_queryable_captures, read_capture_raw
from ..knowledge import ARCHIVE_STATUSES, DEFAULT_VISIBLE_STATUSES, DEMOTED_STATUSES, normalize_status
from ..manifests import resolve_manifest_path
from ..models import QueryMatch
from ..security import is_excluded


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

        Scope semantics:
        - ``"active"`` (default): INCLUDE active/corroborated/no-status
          (primary), INCLUDE deprecated (demoted — appended after primary),
          EXCLUDE superseded and historical.
        - ``"include_archive"``: same active results FIRST (primary + demoted),
          then archived (superseded/historical) appended after.
        - ``"all"``: include everything, natural order.

        The result is primary matches followed by demoted then (for
        ``include_archive``) archived matches, truncated to *remaining* total.
        """

        if store_root is None or not store_root.exists():
            return []
        if remaining <= 0:
            return []

        needle = query.casefold()
        primary: list[QueryMatch] = []
        demoted: list[QueryMatch] = []
        archived: list[QueryMatch] = []
        for path in sorted(store_root.glob("**/*.md")):
            if path.name == "index.md":
                continue
            if not _is_regular_knowledge_file(path, store_root):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # Compute status once; reuse for all routing decisions.
            status = _status_of(text)
            effective = status if status is not None else "active"

            if scope == "all":
                position = text.casefold().find(needle)
                if position >= 0:
                    primary.append(QueryMatch(
                        vault="brain",
                        path=str(path.relative_to(store_root)),
                        title=_title_for(path, text),
                        snippet=_snippet(text, position),
                    ))
                continue

            # Active and include_archive: route by status tier.
            is_archive = effective in ARCHIVE_STATUSES
            if scope == "active" and is_archive:
                # Exclude archived articles from default active scope.
                continue
            # For include_archive, archived articles are collected separately.
            position = text.casefold().find(needle)
            if position < 0:
                continue
            match = QueryMatch(
                vault="brain",
                path=str(path.relative_to(store_root)),
                title=_title_for(path, text),
                snippet=_snippet(text, position),
            )
            if is_archive:
                # Only reachable when scope == "include_archive"
                archived.append(match)
            elif effective in DEMOTED_STATUSES:
                demoted.append(match)
            else:
                primary.append(match)

        # Demoted (deprecated) matches never consume the primary budget: active/
        # corroborated matches fill the budget first; deprecated fills leftover slots.
        # Archived (superseded/historical) are appended last for include_archive.
        if scope == "include_archive":
            return (primary + demoted + archived)[:remaining]
        return (primary + demoted)[:remaining]

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


def _status_of(text: str) -> str | None:
    """Return the normalized ``status`` value from YAML frontmatter, or None.

    Returns None when there is no frontmatter or no ``status:`` key —
    callers treat None as ``"active"`` (forward-compatible default).
    """

    if not text.startswith("---"):
        return None

    end = text.find("\n---", 3)
    if end == -1:
        return None

    front_matter_block = text[3:end]
    for raw_line in front_matter_block.splitlines():
        line = raw_line.strip()
        if line.startswith("status:"):
            raw_value = line[len("status:"):].strip().strip('"').strip("'")
            return normalize_status(raw_value)
    return None
