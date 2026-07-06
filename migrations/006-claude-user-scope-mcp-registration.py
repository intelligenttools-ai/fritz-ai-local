#!/usr/bin/env python3
"""Register Claude's Local Brain MCP server in user scope (#278).

The Claude plugin no longer ships a plugin-root ``.mcp.json`` because that file
cannot contain a machine-local API token. This migration converges Claude Code's
user-scope MCP registration on the token stored in
``settings.local_brain_service.api_token``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml


MIGRATION_NUMBER = "006"

_THIS_FILE = Path(__file__).resolve()
sys.path.insert(0, str(_THIS_FILE.parent))

from claude_mcp_registration import (  # noqa: E402
    ClaudeMcpRegistrationError,
    DEFAULT_LOCAL_BRAIN_BASE_URL,
    register_user_scope_server,
    registration_status,
)


def brain_home() -> Path:
    """Resolve the brain root: ``$BRAIN_HOME`` if set, else ``~/.brain``."""
    env = os.environ.get("BRAIN_HOME")
    if env and env.strip():
        return Path(env.strip()).expanduser().resolve()
    return Path.home() / ".brain"


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


def _service_token_and_base_url(config: dict) -> tuple[str, str]:
    token = config.get("api_token")
    token = token.strip() if isinstance(token, str) else ""
    base_url = config.get("base_url")
    base_url = base_url.strip() if isinstance(base_url, str) and base_url.strip() else DEFAULT_LOCAL_BRAIN_BASE_URL
    return token, base_url


def run(
    root: Path,
    *,
    home: Path | None = None,
    registry_path: Path | None = None,
    dry_run: bool = False,
    refresh: bool = False,
    runner=None,
) -> list[str]:
    """Apply (or simulate) migration 006. Returns the action log."""
    actions: list[str] = []
    root = Path(root)
    home = Path(home) if home else Path.home()

    if not refresh and _migration_already_applied(root):
        actions.append(f"migration {MIGRATION_NUMBER} already applied (per .migrations-run)")
        return actions

    token, base_url = _service_token_and_base_url(read_service_config(registry_path))
    if not token:
        actions.append("no api_token in registry.local_brain_service; Claude MCP registration skipped")
        return actions

    if dry_run:
        status = registration_status(token, base_url=base_url, home=home)
        verb = "keep" if status == "current" else "register/refresh"
        actions.append(f"would {verb} Claude user-scope MCP server fritz-brain ({status}; token redacted)")
        if not refresh:
            record_completion(root, dry_run, actions)
        return actions

    try:
        actions.extend(
            register_user_scope_server(
                token,
                base_url=base_url,
                home=home,
                runner=runner,
            )
        )
    except FileNotFoundError:
        actions.append("Claude CLI unavailable; Claude MCP registration skipped")
        return actions
    except ClaudeMcpRegistrationError as exc:
        actions.append(f"Claude MCP registration failed: {exc}")
        return actions

    if not refresh:
        record_completion(root, dry_run, actions)
    return actions


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description="Register Claude user-scope Local Brain MCP server.")
    parser.add_argument("--dry-run", action="store_true", help="Print intended changes only.")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Reconcile registration even when migration 006 is already recorded.",
    )
    args = parser.parse_args(argv)

    dry_run = args.dry_run or os.environ.get("FRITZ_MIGRATION_DRY_RUN", "").strip() == "1"
    root = brain_home()
    actions = run(root, home=Path.home(), dry_run=dry_run, refresh=args.refresh)

    mode = "DRY-RUN (no changes written)" if dry_run else ("refresh" if args.refresh else "applied")
    print(f"Migration {MIGRATION_NUMBER} claude-user-scope-mcp-registration [{mode}]:")
    for action in actions:
        print(f"  - {action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
