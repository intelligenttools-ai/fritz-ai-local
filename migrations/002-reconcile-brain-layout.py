#!/usr/bin/env python3
"""Reconcile an existing ~/.brain with the current canonical layout.

Non-destructive, idempotent, additive migration:

1. Creates any missing canonical capture subdirs
   (``capture/``, ``capture/inbox``, ``capture/daily``, ``capture/auto``).
   Existing files inside them are never touched.
2. Ensures a top-level ``settings`` block exists in ``registry.yaml`` *only if
   the registry already exists*. All existing content/keys are preserved. If the
   registry is absent, registry creation is skipped (vault setup owns that).
3. Self-records completion by appending ``002`` to ``<brain>/.migrations-run``
   (one per line, never duplicated).

Brain root resolution mirrors ``hooks/brain_save_fact.py``: the ``BRAIN_HOME``
environment variable wins, falling back to ``~/.brain``. This is what makes the
migration safe to test against a copy.

Supports ``--dry-run`` (or ``FRITZ_MIGRATION_DRY_RUN=1``) which prints the
intended changes but writes nothing. The no-arg invocation used by the update
skill applies changes.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml


MIGRATION_NUMBER = "002"

# Canonical capture subdirs relative to the brain root.
CAPTURE_SUBDIRS = ("inbox", "daily", "auto")


def brain_home() -> Path:
    """Resolve the brain root: ``$BRAIN_HOME`` if set, else ``~/.brain``."""
    env = os.environ.get("BRAIN_HOME")
    if env and env.strip():
        return Path(env.strip()).expanduser().resolve()
    return Path.home() / ".brain"


def _dry_run_requested(argv: list[str]) -> bool:
    parser = argparse.ArgumentParser(
        description="Reconcile an existing ~/.brain with the current layout."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print intended changes without writing anything.",
    )
    args = parser.parse_args(argv)
    env_flag = os.environ.get("FRITZ_MIGRATION_DRY_RUN", "").strip()
    return bool(args.dry_run or env_flag == "1")


def _ensure_private_dir(path: Path) -> None:
    """Create a directory (parents) with 0o700, best-effort chmod."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def reconcile_capture_dirs(root: Path, dry_run: bool, actions: list[str]) -> None:
    capture = root / "capture"
    targets = [capture] + [capture / sub for sub in CAPTURE_SUBDIRS]
    for target in targets:
        rel = target.relative_to(root)
        if target.exists():
            continue
        if dry_run:
            actions.append(f"would create directory {rel}/")
        else:
            _ensure_private_dir(target)
            actions.append(f"created directory {rel}/")


def reconcile_registry_settings(root: Path, dry_run: bool, actions: list[str]) -> None:
    registry_path = root / "registry.yaml"
    if not registry_path.exists():
        actions.append(
            "skipped registry.yaml (does not exist; vault setup owns creation)"
        )
        return

    text = registry_path.read_text(encoding="utf-8")
    registry = yaml.safe_load(text)
    if not isinstance(registry, dict):
        actions.append("skipped registry.yaml (not a YAML mapping)")
        return

    settings = registry.get("settings")
    if settings is not None:
        actions.append("registry.yaml settings block already present; no change")
        return

    if dry_run:
        actions.append("would add empty settings block to registry.yaml")
        return

    registry["settings"] = {}
    registry_path.write_text(
        yaml.safe_dump(registry, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    actions.append("added empty settings block to registry.yaml")


def record_completion(root: Path, dry_run: bool, actions: list[str]) -> bool:
    """Append the migration number once. Returns True if (would be) newly recorded."""
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

    with marker.open("a", encoding="utf-8") as fh:
        fh.write(f"{MIGRATION_NUMBER}\n")
    actions.append(f"recorded migration {MIGRATION_NUMBER} in .migrations-run")
    return True


def run(root: Path, dry_run: bool = False) -> list[str]:
    """Apply (or simulate) the migration against ``root``. Returns the action log."""
    actions: list[str] = []
    reconcile_capture_dirs(root, dry_run, actions)
    reconcile_registry_settings(root, dry_run, actions)
    record_completion(root, dry_run, actions)
    return actions


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    dry_run = _dry_run_requested(argv)
    root = brain_home()

    actions = run(root, dry_run=dry_run)

    mode = "DRY-RUN (no changes written)" if dry_run else "applied"
    print(f"Migration {MIGRATION_NUMBER} reconcile-brain-layout [{mode}] on {root}:")
    for action in actions:
        print(f"  - {action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
