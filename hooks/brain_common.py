"""Shared utilities for brain hooks across all agents."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml


BRAIN_HOME = Path.home() / ".brain"
REGISTRY_PATH = BRAIN_HOME / "registry.yaml"


def load_registry() -> dict:
    """Load the vault registry."""
    if not REGISTRY_PATH.exists():
        return {"version": 1, "vaults": {}}
    with open(REGISTRY_PATH) as f:
        return yaml.safe_load(f) or {"version": 1, "vaults": {}}


def get_default_vault() -> tuple[str | None, dict | None, Path | None]:
    """Get the default vault from the registry.

    Checks for 'default_vault' key, then falls back to first vault with status: active.
    Returns (vault_name, vault_config, vault_path) or (None, None, None).
    """
    registry = load_registry()
    vaults = registry.get("vaults", {})

    # Explicit default
    default_name = registry.get("default_vault")
    if default_name and default_name in vaults:
        config = vaults[default_name]
        return default_name, config, Path(config["path"]).expanduser().resolve()

    # Fallback: first vault with status: active
    for name, config in vaults.items():
        if config.get("status") == "active":
            return name, config, Path(config["path"]).expanduser().resolve()

    # Fallback: first vault
    if vaults:
        name = next(iter(vaults))
        config = vaults[name]
        return name, config, Path(config["path"]).expanduser().resolve()

    return None, None, None


def find_vault_for_cwd(cwd: str, fallback_to_default: bool = False) -> tuple[str | None, dict | None, Path | None]:
    """Find which vault the current working directory belongs to.

    If fallback_to_default is True and no vault matches cwd, returns the default vault.
    Returns (vault_name, vault_config, vault_path) or (None, None, None).
    """
    registry = load_registry()
    cwd_path = Path(cwd).resolve()

    for name, config in registry.get("vaults", {}).items():
        vault_path = Path(config["path"]).expanduser().resolve()
        try:
            cwd_path.relative_to(vault_path)
            return name, config, vault_path
        except ValueError:
            continue

    if fallback_to_default:
        return get_default_vault()

    return None, None, None


def load_manifest(vault_path: Path) -> dict | None:
    """Load the .brain/manifest.yaml for a vault."""
    manifest_path = vault_path / ".brain" / "manifest.yaml"
    if not manifest_path.exists():
        return None
    with open(manifest_path) as f:
        return yaml.safe_load(f)


def resolve_path(vault_path: Path, manifest: dict, key: str) -> Path | None:
    """Resolve a manifest path key to an absolute path."""
    paths = manifest.get("paths", {})
    rel = paths.get(key)
    if not rel:
        return None
    return vault_path / rel


def append_log(vault_path: Path, operation: str, agent: str, summary: str):
    """Append an entry to .brain/log.md."""
    log_path = vault_path / ".brain" / "log.md"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"{timestamp} | {operation} | {agent} | {summary}\n"
    with open(log_path, "a") as f:
        f.write(entry)


def read_hook_input() -> dict:
    """Read JSON input from stdin (Claude Code / Codex / Gemini hook protocol)."""
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return {}


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")
