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
    with open(REGISTRY_PATH, encoding="utf-8") as f:
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
    with open(manifest_path, encoding="utf-8") as f:
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
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)


def read_hook_input() -> dict:
    """Read JSON input from stdin (Claude Code / Codex / Gemini hook protocol)."""
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return {}


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


FRITZ_LOCAL_FILENAME = ".fritz-local.json"
FRITZ_REPO = Path.home() / ".fritz-ai-local"


def load_fritz_local(cwd: str) -> dict | None:
    """Walk up from cwd looking for .fritz-local.json. Return parsed JSON or None."""
    current = Path(cwd).resolve()
    for parent in [current, *current.parents]:
        candidate = parent / FRITZ_LOCAL_FILENAME
        if candidate.exists():
            try:
                with open(candidate, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return None
    return None


def load_settings() -> dict:
    """Load global settings from registry.yaml. Returns empty dict if none."""
    registry = load_registry()
    return registry.get("settings", {})


def resolve_project_vault(cwd: str) -> tuple[str | None, dict | None, Path | None, dict | None]:
    """Resolve cwd to vault using .fritz-local.json first, then cwd matching.

    Returns (vault_name, vault_config, vault_path, fritz_local_config).
    fritz_local_config is the parsed .fritz-local.json or None.
    """
    fritz_local = load_fritz_local(cwd)

    if fritz_local and "vault" in fritz_local:
        registry = load_registry()
        vault_name = fritz_local["vault"]
        vaults = registry.get("vaults", {})
        if vault_name in vaults:
            config = vaults[vault_name]
            vault_path = Path(config["path"]).expanduser().resolve()
            # Trust boundary: only honor .fritz-local.json if cwd is within
            # a registered vault path or the fritz-ai-local repo itself.
            # This prevents an untrusted cloned repo from redirecting hooks
            # into personal vaults.
            cwd_resolved = Path(cwd).resolve()
            trusted = False
            for _, vc in vaults.items():
                vp = Path(vc["path"]).expanduser().resolve()
                try:
                    cwd_resolved.relative_to(vp)
                    trusted = True
                    break
                except ValueError:
                    continue
            if not trusted:
                # Check if cwd is within the fritz-ai-local repo
                try:
                    cwd_resolved.relative_to(FRITZ_REPO.resolve())
                    trusted = True
                except ValueError:
                    pass
            if trusted:
                return vault_name, config, vault_path, fritz_local
            # Untrusted location — ignore .fritz-local.json, fall through

    # Fallback to cwd matching
    vault_name, vault_config, vault_path = find_vault_for_cwd(cwd)
    return vault_name, vault_config, vault_path, fritz_local


def get_context_injection_level(fritz_local: dict | None) -> str:
    """Determine context injection level.

    Precedence:
    1. .fritz-local.json context_injection field
    2. Global settings.context_injection in registry.yaml
    3. Default: "off"

    If .fritz-local.json exists but has no context_injection → "off"
    If no .fritz-local.json → "off" (today's behavior)
    """
    if fritz_local is not None:
        level = fritz_local.get("context_injection")
        if level in ("off", "light", "full"):
            return level
        # .fritz-local.json exists but no context_injection → off
        return "off"

    # No .fritz-local.json: check global settings
    settings = load_settings()
    level = settings.get("context_injection")
    if level in ("off", "light", "full"):
        return level

    return "off"


def get_max_injection_chars(fritz_local: dict | None) -> int:
    """Get max injection chars. Project overrides global."""
    if fritz_local and "max_injection_chars" in fritz_local:
        return int(fritz_local["max_injection_chars"])
    settings = load_settings()
    return int(settings.get("max_injection_chars", 8000))


def get_fritz_version() -> str | None:
    """Read VERSION from the fritz-ai-local repo."""
    version_path = FRITZ_REPO / "VERSION"
    if version_path.exists():
        return version_path.read_text(encoding="utf-8").strip()
    return None
