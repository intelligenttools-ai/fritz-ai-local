"""Read-only Brain query agent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
