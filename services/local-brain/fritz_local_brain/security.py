"""Policy checks for Local Brain writes."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from .manifests import resolve_manifest_path
from .models import ArticleWriteProposal
from .paths import is_relative_to


class PolicyError(RuntimeError):
    pass


FORBIDDEN_PARTS = {"registry.yaml", "manifest.yaml", "schema.md"}
IDENTITY_PATH_KEYS = {"soul", "user", "memory"}


def is_excluded(path: Path, vault_path: Path, manifest: dict[str, Any]) -> bool:
    try:
        rel = path.resolve().relative_to(vault_path.resolve())
    except ValueError:
        return True
    rel_str = str(rel)
    for pattern in manifest.get("exclude", []) or []:
        normalized = pattern.strip()
        if not normalized:
            continue
        if fnmatch(rel_str, normalized) or fnmatch(rel_str, f"*/{normalized}"):
            return True
        for part in rel.parts:
            if fnmatch(part, normalized.rstrip("/")):
                return True
    return False


def validate_article_write(
    proposal: ArticleWriteProposal,
    vault_paths: dict[str, Path],
    manifests: dict[str, dict[str, Any]],
    brain_home: Path,
    allowed_sources: set[Path] | None = None,
) -> Path:
    if proposal.vault not in vault_paths:
        raise PolicyError(f"Unknown vault: {proposal.vault}")
    if proposal.vault not in manifests:
        raise PolicyError(f"Missing manifest for vault: {proposal.vault}")

    if proposal.operation not in ("create", "update"):
        raise PolicyError("Only create/update operations are allowed")

    rel = Path(proposal.relative_path)
    if rel.is_absolute() or ".." in rel.parts:
        raise PolicyError(f"Unsafe relative_path: {proposal.relative_path}")
    if any(part in FORBIDDEN_PARTS for part in rel.parts):
        raise PolicyError(f"Forbidden target path: {proposal.relative_path}")
    if rel.suffix != ".md":
        raise PolicyError("Knowledge article target must be a markdown file")

    vault_path = vault_paths[proposal.vault]
    manifest = manifests[proposal.vault]
    knowledge_root = resolve_manifest_path(vault_path, manifest, "knowledge")
    if knowledge_root is None:
        raise PolicyError(f"Vault has no knowledge path: {proposal.vault}")

    target = (knowledge_root / rel).resolve()
    if not is_relative_to(target, knowledge_root):
        raise PolicyError(f"Target escapes knowledge path: {proposal.relative_path}")
    if is_excluded(target, vault_path, manifest):
        raise PolicyError(f"Target is excluded: {proposal.relative_path}")
    for key in IDENTITY_PATH_KEYS:
        identity_path = resolve_manifest_path(vault_path, manifest, key)
        if identity_path and target == identity_path.resolve():
            raise PolicyError(f"Target is a manifest identity file: {proposal.relative_path}")
    if proposal.operation == "update" and not target.exists():
        raise PolicyError(f"Update target does not exist: {proposal.relative_path}")
    if proposal.operation == "create" and target.exists():
        raise PolicyError(f"Create target already exists: {proposal.relative_path}")

    capture_root = (brain_home / "capture").resolve()
    for source in proposal.sources:
        if source.startswith("~/.brain/"):
            source_path = brain_home / source.removeprefix("~/.brain/")
        else:
            source_path = Path(source).expanduser()
        if not source_path.is_absolute():
            source_path = brain_home / source_path
        source_path = source_path.resolve()
        if not is_relative_to(source_path, capture_root):
            raise PolicyError(f"Source is not in capture root: {source}")
        if not source_path.exists():
            raise PolicyError(f"Source does not exist: {source}")
        if allowed_sources is not None and source_path not in allowed_sources:
            raise PolicyError(f"Source was not provided to the compile agent: {source}")

    proposal.frontmatter.setdefault("type", "article")
    proposal.frontmatter.setdefault("title", proposal.title)
    proposal.frontmatter.setdefault("sources", proposal.sources)

    required = {"type", "title", "sources"}
    missing = required - set(proposal.frontmatter)
    if missing:
        raise PolicyError(f"Missing frontmatter fields: {', '.join(sorted(missing))}")

    return target
