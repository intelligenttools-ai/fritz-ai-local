#!/usr/bin/env python3
"""Remove legacy manual Claude hook registrations when the plugin is enabled.

Claude Code's supported path is now the ``fritz-brain@fritz-local`` plugin. If
that plugin is enabled in ``~/.claude/settings.json``, the legacy manual hook
entries in the same settings file are duplicate sources of truth. This migration
removes only Fritz-installed hook groups from the four known Fritz events and
preserves all user-added hook groups.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path


MIGRATION_NUMBER = "003"
PLUGIN_ID = "fritz-brain@fritz-local"

ROOT = Path(__file__).resolve().parents[1]
INSTALL_CLAUDE_HOOKS_PATH = ROOT / "hooks" / "install_claude_hooks.py"


def _load_claude_hook_installer():
    spec = importlib.util.spec_from_file_location(
        "_migration_003_install_claude_hooks", INSTALL_CLAUDE_HOOKS_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


_claude_hooks = _load_claude_hook_installer()
FRITZ_HOOKS = _claude_hooks.FRITZ_HOOKS
_is_fritz_group = _claude_hooks._is_fritz_group


def brain_home() -> Path:
    """Resolve the brain root: ``$BRAIN_HOME`` if set, else ``~/.brain``."""
    env = os.environ.get("BRAIN_HOME")
    if env and env.strip():
        return Path(env.strip()).expanduser().resolve()
    return Path.home() / ".brain"


def claude_settings_path() -> Path:
    """Resolve Claude settings: ``$CLAUDE_SETTINGS_PATH`` if set, else default."""
    env = os.environ.get("CLAUDE_SETTINGS_PATH")
    if env and env.strip():
        return Path(env.strip()).expanduser().resolve()
    return Path.home() / ".claude" / "settings.json"


def _dry_run_requested(argv: list[str]) -> tuple[bool, Path | None]:
    parser = argparse.ArgumentParser(
        description="Remove legacy manual Claude Fritz hooks when plugin owns hooks."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print intended changes without writing anything.",
    )
    parser.add_argument(
        "--settings-path",
        default=None,
        help="Override Claude settings path; tests use this to avoid live settings.",
    )
    args = parser.parse_args(argv)
    env_flag = os.environ.get("FRITZ_MIGRATION_DRY_RUN", "").strip()
    settings = Path(args.settings_path).expanduser().resolve() if args.settings_path else None
    return bool(args.dry_run or env_flag == "1"), settings


def _plugin_enabled(settings: dict) -> bool:
    enabled = settings.get("enabledPlugins")
    if isinstance(enabled, list):
        return PLUGIN_ID in enabled
    if isinstance(enabled, dict):
        return PLUGIN_ID in enabled and enabled.get(PLUGIN_ID) is not False
    return False


def _read_settings(settings_path: Path) -> dict | None:
    if not settings_path.exists():
        return None
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(
            f"{settings_path} exists but could not be read as JSON ({exc}); refusing to rewrite"
        ) from exc
    if not isinstance(settings, dict):
        raise RuntimeError(
            f"{settings_path} exists but its top level is not a JSON object; refusing to rewrite"
        )
    return settings


def _write_settings(settings_path: Path, settings: dict) -> None:
    payload = json.dumps(settings, indent=2) + "\n"
    settings_path.write_text(payload, encoding="utf-8")


def _remove_legacy_fritz_hooks(settings: dict) -> int:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return 0

    removed = 0
    for event, _commands in FRITZ_HOOKS:
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept = [group for group in groups if not _is_fritz_group(group)]
        removed += len(groups) - len(kept)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    return removed


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
        actions.append(
            f"migration {MIGRATION_NUMBER} already applied (per .migrations-run)"
        )
        return False

    if dry_run:
        actions.append(f"would record migration {MIGRATION_NUMBER} in .migrations-run")
        return True

    root.mkdir(parents=True, exist_ok=True)
    with marker.open("a", encoding="utf-8") as fh:
        fh.write(f"{MIGRATION_NUMBER}\n")
    actions.append(f"recorded migration {MIGRATION_NUMBER} in .migrations-run")
    return True


def run(root: Path, *, settings_path: Path | None = None, dry_run: bool = False) -> list[str]:
    """Apply (or simulate) the migration. Returns the action log."""
    actions: list[str] = []
    root = Path(root)
    settings_path = claude_settings_path() if settings_path is None else Path(settings_path)

    if _migration_already_applied(root):
        actions.append(f"migration {MIGRATION_NUMBER} already applied (per .migrations-run)")
        return actions

    try:
        settings = _read_settings(settings_path)
    except RuntimeError as exc:
        actions.append(f"skipped {settings_path} (not valid JSON; refusing to rewrite: {exc})")
        return actions
    if settings is None:
        actions.append(f"skipped {settings_path} (does not exist)")
        record_completion(root, dry_run, actions)
        return actions

    if not _plugin_enabled(settings):
        actions.append(f"skipped {settings_path} ({PLUGIN_ID} plugin not enabled)")
        record_completion(root, dry_run, actions)
        return actions

    removed = _remove_legacy_fritz_hooks(settings)
    if removed:
        if dry_run:
            actions.append(f"would remove {removed} fritz hook group(s) from {settings_path}")
        else:
            _write_settings(settings_path, settings)
            actions.append(f"removed {removed} fritz hook group(s) from {settings_path}")
    else:
        actions.append(f"no legacy fritz hook groups found in {settings_path}")

    record_completion(root, dry_run, actions)
    return actions


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    dry_run, settings_path = _dry_run_requested(argv)
    root = brain_home()

    actions = run(root, settings_path=settings_path, dry_run=dry_run)

    mode = "DRY-RUN (no changes written)" if dry_run else "applied"
    print(f"Migration {MIGRATION_NUMBER} claude-plugin-owns-hooks [{mode}] on {root}:")
    for action in actions:
        print(f"  - {action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
