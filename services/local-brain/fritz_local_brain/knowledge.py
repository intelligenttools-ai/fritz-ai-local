"""Knowledge article write helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .config import Settings
from .models import ArticleWriteProposal

# ---------------------------------------------------------------------------
# Knowledge lifecycle vocabulary
# ---------------------------------------------------------------------------

STATUS_VALUES = ("active", "corroborated", "deprecated", "superseded", "historical")
DEFAULT_STATUS = "active"

# Statuses visible in the default ("active") retrieval scope.
DEFAULT_VISIBLE_STATUSES: frozenset[str] = frozenset({"active", "corroborated", "deprecated"})

# Statuses that are visible but ranked AFTER primary (active/corroborated) matches.
DEMOTED_STATUSES: frozenset[str] = frozenset({"deprecated"})


def normalize_status(value: str) -> str:
    """Lowercase and strip a status string."""
    return value.lower().strip()


def store_root(settings: Settings) -> Path:
    """Resolve the brain-owned knowledge store root from settings.

    Registry-free: the store location comes from ``brain_store_path`` (or the
    ``<brain_home>/knowledge`` default), never from ``registry.yaml``.
    """

    return settings.resolve_brain_store_path()


def ensure_store_root(settings: Settings) -> Path:
    """Resolve the store root and ensure its directory exists.

    Works with an absent or empty ``registry.yaml`` — no registry is read.
    Structured sub-layout and indexes are introduced in later work items.
    """

    root = store_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    return root


def render_article(proposal: ArticleWriteProposal) -> str:
    frontmatter = dict(proposal.frontmatter)
    frontmatter.setdefault("title", proposal.title)
    frontmatter.setdefault("updated", datetime.now().strftime("%Y-%m-%d"))
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    body = proposal.body.strip() + "\n"
    return f"---\n{yaml_text}\n---\n\n{body}"


def apply_article_write(target: Path, proposal: ArticleWriteProposal, dry_run: bool) -> None:
    if dry_run:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_article(proposal), encoding="utf-8")
