"""Brain registry loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .paths import PathMapper


class RegistryError(RuntimeError):
    pass


def load_registry(brain_home: Path) -> dict[str, Any]:
    registry_path = brain_home / "registry.yaml"
    if not registry_path.exists():
        raise RegistryError(f"Registry not found: {registry_path}")
    return yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {"vaults": {}}


def registered_vault_paths(registry: dict[str, Any], mapper: PathMapper) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for name, config in registry.get("vaults", {}).items():
        raw_path = config.get("path")
        if raw_path:
            paths[name] = mapper.to_container(raw_path)
    return paths
