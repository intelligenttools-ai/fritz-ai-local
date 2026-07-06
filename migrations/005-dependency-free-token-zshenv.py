#!/usr/bin/env python3
"""Rewrite the Local Brain MCP token zshenv block without Python/PyYAML (#274).

Migration 004 installed a managed ``~/.zshenv`` block that resolved the token
by running ``python3 -c 'import yaml ...'`` during shell startup. On a virgin GUI
environment that can resolve to a Python without PyYAML, producing an empty
export that shadows the LaunchAgent token. This migration replaces the managed
block with a dependency-free shell snippet and records ``005`` in the brain
migration ledger.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml


MIGRATION_NUMBER = "005"

_THIS_FILE = Path(__file__).resolve()
sys.path.insert(0, str(_THIS_FILE.parent))

from token_env_wiring import (  # noqa: E402
    BLOCK_END,
    BLOCK_START,
    CLAUDE_MCP_TOKEN_ENV,
    token_env_names,
    upsert_zshenv_block,
    valid_env_name,
)


def brain_home() -> Path:
    """Resolve the brain root: ``$BRAIN_HOME`` if set, else ``~/.brain``."""
    env = os.environ.get("BRAIN_HOME")
    if env and env.strip():
        return Path(env.strip()).expanduser().resolve()
    return Path.home() / ".brain"


def zshenv_path(home: Path) -> Path:
    return Path(home) / ".zshenv"


def read_service_config(registry_path: Path | None = None) -> dict:
    """Return ``settings.local_brain_service`` from the registry, or ``{}``."""
    path = Path(registry_path) if registry_path else (Path.home() / ".brain" / "registry.yaml")
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    settings = data.get("settings")
    if not isinstance(settings, dict):
        return {}
    svc = settings.get("local_brain_service")
    return svc if isinstance(svc, dict) else {}


def resolve_token_env(config: dict) -> str:
    name = config.get("api_token_env", CLAUDE_MCP_TOKEN_ENV)
    return name if valid_env_name(name) else CLAUDE_MCP_TOKEN_ENV


def _migration_already_applied(root: Path) -> bool:
    marker = root / ".migrations-run"
    if not marker.exists():
        return False
    existing = [line.strip() for line in marker.read_text(encoding="utf-8").splitlines()]
    return MIGRATION_NUMBER in existing


def record_completion(root: Path, dry_run: bool, actions: list[str]) -> bool:
    marker = root / ".migrations-run"
    existing: list[str] = []
    if marker.exists():
        existing = [
            line.strip()
            for line in marker.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    if MIGRATION_NUMBER in existing:
        actions.append(f"migration {MIGRATION_NUMBER} already applied (per .migrations-run)")
        return False
    if dry_run:
        actions.append(f"would record migration {MIGRATION_NUMBER} in .migrations-run")
        return True
    root.mkdir(parents=True, exist_ok=True)
    with marker.open("a", encoding="utf-8") as fh:
        fh.write(f"{MIGRATION_NUMBER}\n")
    actions.append(f"recorded migration {MIGRATION_NUMBER} in .migrations-run")
    return True


def run(
    root: Path,
    *,
    home: Path | None = None,
    registry_path: Path | None = None,
    dry_run: bool = False,
) -> list[str]:
    """Apply (or simulate) migration 005. Returns the action log."""
    actions: list[str] = []
    root = Path(root)
    home = Path(home) if home else Path.home()

    if _migration_already_applied(root):
        actions.append(f"migration {MIGRATION_NUMBER} already applied (per .migrations-run)")
        return actions

    config = read_service_config(registry_path)
    token_env = resolve_token_env(config)
    env_names = token_env_names(token_env)

    zpath = zshenv_path(home)
    existing = zpath.read_text(encoding="utf-8") if zpath.exists() else ""
    new_text, changed = upsert_zshenv_block(existing, token_env)
    if dry_run:
        actions.append(f"would {'rewrite' if changed else 'keep'} {'/'.join(env_names)} block in {zpath}")
    elif changed:
        zpath.parent.mkdir(parents=True, exist_ok=True)
        zpath.write_text(new_text, encoding="utf-8")
        actions.append(f"rewrote {'/'.join(env_names)} export block in {zpath}")
    else:
        actions.append(f"{'/'.join(env_names)} export block already dependency-free in {zpath}")

    record_completion(root, dry_run, actions)
    return actions


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description="Rewrite Local Brain token zshenv block.")
    parser.add_argument("--dry-run", action="store_true", help="Print intended changes only.")
    args = parser.parse_args(argv)

    dry_run = args.dry_run or os.environ.get("FRITZ_MIGRATION_DRY_RUN", "").strip() == "1"
    root = brain_home()
    actions = run(root, home=Path.home(), dry_run=dry_run)

    mode = "DRY-RUN (no changes written)" if dry_run else "applied"
    print(f"Migration {MIGRATION_NUMBER} dependency-free-token-zshenv [{mode}]:")
    for action in actions:
        print(f"  - {action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
