"""Vault manifest helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .paths import is_relative_to


def load_manifest(vault_path: Path) -> dict[str, Any] | None:
    manifest_path = vault_path / ".brain" / "manifest.yaml"
    if not manifest_path.exists():
        return None
    return yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}


def resolve_manifest_path(vault_path: Path, manifest: dict[str, Any], key: str) -> Path | None:
    rel = manifest.get("paths", {}).get(key)
    if not rel:
        return None
    resolved = (vault_path / rel).resolve()
    if not is_relative_to(resolved, vault_path):
        return None
    return resolved
