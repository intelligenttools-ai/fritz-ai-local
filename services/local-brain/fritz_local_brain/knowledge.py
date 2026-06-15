"""Knowledge article write helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .config import Settings
from .models import ArticleWriteProposal, ReconciliationOutcome, ReconciliationVerdict
from .paths import is_relative_to

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


# ---------------------------------------------------------------------------
# Reconciliation: safe frontmatter mutation + deterministic verdict mapping
# ---------------------------------------------------------------------------


def _split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown document into (frontmatter dict, body).

    Handles missing or malformed front matter gracefully: returns an empty dict
    and the whole text as body when no valid YAML front matter is present.
    """

    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines(keepends=True)
    # lines[0] is the opening fence. Find the closing fence.
    closing = None
    for index in range(1, len(lines)):
        if lines[index].rstrip("\n") == "---":
            closing = index
            break
    if closing is None:
        return {}, text
    yaml_text = "".join(lines[1:closing])
    body = "".join(lines[closing + 1 :])
    body = body.lstrip("\n")
    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return {}, text
    if not isinstance(parsed, dict):
        return {}, text
    return parsed, body


def apply_frontmatter_update(
    path: Path,
    *,
    store_root: Path,
    status: str | None = None,
    append_links: dict[str, list[str]] | None = None,
    scope_qualifier: str | None = None,
    dry_run: bool = False,
) -> None:
    """Apply a frontmatter mutation to an EXISTING store article file in place.

    - ``path`` must resolve inside ``store_root`` (else raises ``ValueError``).
    - Reads the file, parses YAML front matter (missing/malformed handled
      gracefully), then applies: normalized ``status`` if given; for each key in
      ``append_links`` ensures a list and appends missing entries (dedup, stable
      order); sets ``scope`` from ``scope_qualifier`` if given; bumps ``updated``.
    - Re-renders ``---\\n<yaml>\\n---\\n\\n<body>`` preserving the body.
    - No-op on ``dry_run``.
    """

    resolved = path.resolve()
    root = store_root.resolve()
    if not is_relative_to(resolved, root):
        raise ValueError(f"Path escapes store root: {path}")
    if dry_run:
        return
    text = resolved.read_text(encoding="utf-8", errors="replace")
    frontmatter, body = _split_front_matter(text)

    if status is not None:
        frontmatter["status"] = normalize_status(status)

    if append_links:
        for key, values in append_links.items():
            existing = frontmatter.get(key)
            if not isinstance(existing, list):
                existing = [] if existing is None else [existing]
            for value in values:
                if value not in existing:
                    existing.append(value)
            frontmatter[key] = existing

    if scope_qualifier is not None:
        frontmatter["scope"] = scope_qualifier

    frontmatter["updated"] = datetime.now().strftime("%Y-%m-%d")

    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    body = body.strip() + "\n"
    resolved.write_text(f"---\n{yaml_text}\n---\n\n{body}", encoding="utf-8")


def _current_status(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return DEFAULT_STATUS
    frontmatter, _body = _split_front_matter(text)
    raw = frontmatter.get("status")
    if isinstance(raw, str) and raw.strip():
        return normalize_status(raw)
    return DEFAULT_STATUS


def apply_reconciliation_verdict(
    verdict: ReconciliationVerdict,
    *,
    new_path: Path,
    old_path: Path,
    store_root: Path,
    dry_run: bool,
) -> ReconciliationOutcome:
    """Apply a reconciliation verdict to the OLD/NEW store files deterministically.

    Side-effects are confined to frontmatter mutations on the two files (both must
    resolve inside ``store_root``). Returns a record of the actions taken.
    """

    new_rel = str(new_path.resolve().relative_to(store_root.resolve())) if is_relative_to(new_path.resolve(), store_root.resolve()) else str(new_path)
    old_rel = str(old_path.resolve().relative_to(store_root.resolve())) if is_relative_to(old_path.resolve(), store_root.resolve()) else str(old_path)
    actions: list[str] = []

    if verdict.verdict == "corroborates":
        # Only mark corroborated when the OLD article is currently active/absent;
        # never downgrade an already-superseded (or otherwise demoted) article.
        if _current_status(old_path) in {"active", DEFAULT_STATUS}:
            apply_frontmatter_update(
                old_path,
                store_root=store_root,
                status="corroborated",
                append_links={"corroborated_by": [new_rel]},
                dry_run=dry_run,
            )
            actions.append(f"old status -> corroborated; corroborated_by += {new_rel}")
        else:
            apply_frontmatter_update(
                old_path,
                store_root=store_root,
                append_links={"corroborated_by": [new_rel]},
                dry_run=dry_run,
            )
            actions.append(f"old corroborated_by += {new_rel} (status unchanged)")

    elif verdict.verdict == "refines":
        apply_frontmatter_update(
            old_path,
            store_root=store_root,
            append_links={"refined_by": [new_rel]},
            dry_run=dry_run,
        )
        apply_frontmatter_update(
            new_path,
            store_root=store_root,
            append_links={"refines": [old_rel]},
            dry_run=dry_run,
        )
        actions.append(f"old refined_by += {new_rel}; new refines += {old_rel}")

    elif verdict.verdict == "contradicts_supersedes":
        apply_frontmatter_update(
            old_path,
            store_root=store_root,
            status="superseded",
            append_links={"superseded_by": [new_rel]},
            dry_run=dry_run,
        )
        apply_frontmatter_update(
            new_path,
            store_root=store_root,
            append_links={"supersedes": [old_rel]},
            dry_run=dry_run,
        )
        actions.append(f"old status -> superseded; superseded_by += {new_rel}; new supersedes += {old_rel}")

    elif verdict.verdict == "context_split":
        scope = verdict.scope_qualifier or "scope-specific"
        apply_frontmatter_update(
            old_path,
            store_root=store_root,
            scope_qualifier=scope,
            dry_run=dry_run,
        )
        apply_frontmatter_update(
            new_path,
            store_root=store_root,
            scope_qualifier=scope,
            dry_run=dry_run,
        )
        actions.append(f"both retained; scope -> {scope}")

    # orthogonal: no-op.

    return ReconciliationOutcome(
        new_path=new_rel,
        old_path=old_rel,
        verdict=verdict.verdict,
        actions=actions,
        reasoning=verdict.reasoning,
    )
