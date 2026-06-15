"""Brain registry loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from .paths import PathMapper


class RegistryError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# External-target schema
# ---------------------------------------------------------------------------

EXTERNAL_TARGET_KINDS = ("local-vault", "mcp", "drive", "offsite")
MIRROR_MODES = ("index-only", "full-summary")


class ExternalTarget(BaseModel):
    """Schema for a single external target in the registry.

    External targets describe off-brain systems (other vaults, MCPs, shared
    drives, off-site services) that the optional Docker mirror agent can pull
    data FROM into the brain store.  They are service-mode only and entirely
    additive — the brain store, index, and lifecycle all work without them.
    """

    model_config = ConfigDict(extra="allow")  # per-kind extra fields allowed

    name: str
    kind: Literal["local-vault", "mcp", "drive", "offsite"]
    connection: str | None = None   # path / URL / URI / MCP server ref
    auth: Any | None = None         # token, env-var name, or creds ref (opaque)
    mirror_mode: Literal["index-only", "full-summary"] = "index-only"


# ---------------------------------------------------------------------------
# Registry loaders
# ---------------------------------------------------------------------------


def load_registry(brain_home: Path) -> dict[str, Any]:
    """Load the registry, raising RegistryError when absent.

    Kept for back-compat — compile/query/embeddings call this and guard with
    try/except RegistryError so they work registry-free (store mode).
    Do NOT change the signature or raising contract.
    """
    registry_path = brain_home / "registry.yaml"
    if not registry_path.exists():
        raise RegistryError(f"Registry not found: {registry_path}")
    return yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {"vaults": {}}


def load_registry_optional(brain_home: Path) -> dict[str, Any]:
    """Load the registry without raising when absent.

    Returns the parsed dict when ``registry.yaml`` is present, or ``{}`` when
    it is absent.  The brain core (compile, query, store) is fully functional
    without a registry; call this when registry data is optional/additive.
    """
    registry_path = brain_home / "registry.yaml"
    if not registry_path.exists():
        return {}
    return yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}


def load_external_targets(brain_home: Path) -> list[ExternalTarget]:
    """Load and validate ``external_targets:`` from the registry.

    Returns an empty list when:
    - ``registry.yaml`` is absent (brain-core-only mode), or
    - the file is present but has no ``external_targets:`` key.

    Raises ``RegistryError`` when an entry has an invalid ``kind`` or
    ``mirror_mode`` so misconfiguration is surfaced immediately.

    The returned list is in deterministic (name-sorted) order.
    """
    registry = load_registry_optional(brain_home)
    raw_targets: dict[str, Any] = registry.get("external_targets") or {}

    targets: list[ExternalTarget] = []
    for name in sorted(raw_targets):
        config: dict[str, Any] = dict(raw_targets[name] or {})
        config["name"] = name
        try:
            targets.append(ExternalTarget.model_validate(config))
        except ValidationError as exc:
            raise RegistryError(
                f"Invalid external_target {name!r} in registry.yaml: {exc}"
            ) from exc
    return targets


# ---------------------------------------------------------------------------
# Vault helpers (unchanged)
# ---------------------------------------------------------------------------


def registered_vault_paths(registry: dict[str, Any], mapper: PathMapper) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for name, config in registry.get("vaults", {}).items():
        raw_path = config.get("path")
        if raw_path:
            paths[name] = mapper.to_container(raw_path)
    return paths
