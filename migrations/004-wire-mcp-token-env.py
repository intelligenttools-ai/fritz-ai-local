#!/usr/bin/env python3
"""Wire the Local Brain MCP token into the host environment (#259).

Claude Code's ``fritz-brain`` plugin authenticates its MCP calls through a
``Bearer ${LOCAL_BRAIN_API_TOKEN}`` header that expands from the shell
environment. Two host-side steps used to live only as README prose, so ordinary
sessions had no token exported and every MCP handshake failed. This migration
makes update/provisioning own them:

1. Manage a clearly-marked block in ``~/.zshenv`` that exports the token env var
   (``api_token_env``, default ``LOCAL_BRAIN_API_TOKEN``) by reading
   ``~/.brain/registry.yaml`` at shell-init — the literal secret is never
   written into the dotfile.
2. ``launchctl setenv`` the token for the current login session and install a
   login ``LaunchAgent`` that re-applies it (``launchctl setenv`` does not
   survive reboot; GUI-launched apps need it).

Idempotent, and safe to re-run. The same logic is reused by the PROV1
provisioning engine so both ``fritz:update`` and ``brain-service-setup`` own it.
"""

from __future__ import annotations

import argparse
import os
import platform
import plistlib
import re
import subprocess
import sys
from pathlib import Path

import yaml


MIGRATION_NUMBER = "004"
# Reuse the hand-written markers already present on installed workstations so an
# existing block converges instead of duplicating.
BLOCK_START = "# >>> fritz-local brain token >>>"
BLOCK_END = "# <<< fritz-local brain token <<<"
LAUNCH_AGENT_LABEL = "ai.fritz.local-brain-token-env"
CLAUDE_MCP_TOKEN_ENV = "LOCAL_BRAIN_API_TOKEN"

_THIS_FILE = Path(__file__).resolve()


def brain_home() -> Path:
    """Resolve the brain root: ``$BRAIN_HOME`` if set, else ``~/.brain``."""
    env = os.environ.get("BRAIN_HOME")
    if env and env.strip():
        return Path(env.strip()).expanduser().resolve()
    return Path.home() / ".brain"


def zshenv_path(home: Path) -> Path:
    return Path(home) / ".zshenv"


def launch_agent_path(home: Path) -> Path:
    return Path(home) / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def _valid_env_name(name: str) -> bool:
    return bool(isinstance(name, str) and re.fullmatch(r"[A-Z_][A-Z0-9_]*", name))


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
    return name if _valid_env_name(name) else CLAUDE_MCP_TOKEN_ENV


def token_env_names(token_env: str) -> list[str]:
    """Return env vars to wire.

    ``api_token_env`` remains supported for generic service callers, but the
    Claude plugin MCP header is fixed to ``LOCAL_BRAIN_API_TOKEN``. Mirror the
    same registry-backed token into both when an install uses a custom env var.
    """
    primary = token_env if _valid_env_name(token_env) else CLAUDE_MCP_TOKEN_ENV
    names = [primary]
    if CLAUDE_MCP_TOKEN_ENV not in names:
        names.append(CLAUDE_MCP_TOKEN_ENV)
    return names


def build_zshenv_block(token_env: str) -> str:
    """Return the managed ``~/.zshenv`` block (no trailing newline, no secret)."""
    reader = (
        "$(python3 -c 'import pathlib,yaml; "
        'r=yaml.safe_load(pathlib.Path.home().joinpath(".brain","registry.yaml").read_text()) or {}; '
        'c=(r.get("settings") or {}).get("local_brain_service") or {}; '
        "print((c.get(\"api_token\") or \"\").strip())' 2>/dev/null)"
    )
    lines = [
        BLOCK_START,
        "# Managed by fritz-local (migration 004). Exports the Local Brain MCP",
        "# token from ~/.brain/registry.yaml so Claude Code's plugin MCP header",
        "# can authenticate. Do not edit by hand; re-run /fritz-brain:update.",
    ]
    lines.extend(f'export {name}="{reader}"' for name in token_env_names(token_env))
    lines.append(BLOCK_END)
    return "\n".join(lines)


def upsert_zshenv_block(text: str, token_env: str) -> tuple[str, bool]:
    """Insert or replace the managed block, preserving all other user content."""
    block = build_zshenv_block(token_env)
    lines = text.splitlines()
    kept: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == BLOCK_START:
            j = i + 1
            while j < len(lines) and lines[j].strip() != BLOCK_END:
                j += 1
            i = j + 1  # skip the end marker too
            continue
        kept.append(lines[i])
        i += 1
    while kept and kept[-1].strip() == "":
        kept.pop()
    body = "\n".join(kept)
    new_text = (body + "\n\n" + block + "\n") if body else (block + "\n")
    return new_text, new_text != text


def build_launch_agent_plist() -> dict:
    """launchd plist for the login agent that re-applies ``launchctl setenv``."""
    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [sys.executable, str(_THIS_FILE), "--apply-env"],
        "RunAtLoad": True,
    }


def _default_launchctl(argv: list[str]) -> None:
    subprocess.run(argv, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wire_token_env(
    token: str,
    *,
    token_env: str = "LOCAL_BRAIN_API_TOKEN",
    home: Path | None = None,
    install_agent: bool = True,
    launchctl=None,
    dry_run: bool = False,
) -> list[str]:
    """Write the zshenv block, ``launchctl setenv`` the token, install the agent.

    The literal ``token`` is used only for the in-session ``launchctl setenv``;
    it is never written to disk (the zshenv block and the login agent both read
    it back from the registry).
    """
    home = Path(home) if home else Path.home()
    if not _valid_env_name(token_env):
        token_env = CLAUDE_MCP_TOKEN_ENV
    env_names = token_env_names(token_env)
    actions: list[str] = []

    zpath = zshenv_path(home)
    existing = zpath.read_text(encoding="utf-8") if zpath.exists() else ""
    new_text, changed = upsert_zshenv_block(existing, token_env)
    if dry_run:
        actions.append(f"would {'update' if changed else 'keep'} {'/'.join(env_names)} block in {zpath}")
    elif changed:
        zpath.parent.mkdir(parents=True, exist_ok=True)
        zpath.write_text(new_text, encoding="utf-8")
        actions.append(f"wrote {'/'.join(env_names)} export block to {zpath}")
    else:
        actions.append(f"{'/'.join(env_names)} export block already current in {zpath}")

    use_launchctl = launchctl is not None or platform.system() == "Darwin"
    if not use_launchctl:
        actions.append(f"skipped launchctl setenv + LaunchAgent (not macOS: {platform.system()})")
        return actions

    lc = launchctl or _default_launchctl
    if dry_run:
        actions.append(f"would launchctl setenv {'/'.join(env_names)} and install LaunchAgent {LAUNCH_AGENT_LABEL}")
        return actions

    if token:
        for name in env_names:
            lc(["launchctl", "setenv", name, token])
        actions.append(f"launchctl setenv {'/'.join(env_names)} (current login session)")
    if install_agent:
        apath = launch_agent_path(home)
        apath.parent.mkdir(parents=True, exist_ok=True)
        with apath.open("wb") as fh:
            plistlib.dump(build_launch_agent_plist(), fh)
        lc(["launchctl", "bootout", f"gui/{os.getuid()}", str(apath)])
        lc(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(apath)])
        lc(["launchctl", "enable", f"gui/{os.getuid()}/{LAUNCH_AGENT_LABEL}"])
        actions.append(f"installed login LaunchAgent {LAUNCH_AGENT_LABEL} at {apath}")
    return actions


def apply_env(*, registry_path: Path | None = None, launchctl=None) -> list[str]:
    """Login-agent entry point: ``launchctl setenv`` the token from the registry."""
    config = read_service_config(registry_path)
    token = config.get("api_token")
    token = token.strip() if isinstance(token, str) else ""
    token_env = resolve_token_env(config)
    if not token:
        return ["no api_token in registry.local_brain_service; nothing to apply"]
    lc = launchctl or _default_launchctl
    names = token_env_names(token_env)
    for name in names:
        lc(["launchctl", "setenv", name, token])
    return [f"launchctl setenv {'/'.join(names)} (from registry)"]


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
    launchctl=None,
    dry_run: bool = False,
) -> list[str]:
    """Apply (or simulate) the migration. Returns the action log."""
    actions: list[str] = []
    root = Path(root)
    home = Path(home) if home else Path.home()

    if _migration_already_applied(root):
        actions.append(f"migration {MIGRATION_NUMBER} already applied (per .migrations-run)")
        return actions

    config = read_service_config(registry_path)
    token = config.get("api_token")
    token = token.strip() if isinstance(token, str) else ""
    token_env = resolve_token_env(config)

    if not token:
        actions.append("no api_token in registry.local_brain_service; token env wiring skipped")
        return actions

    actions.extend(
        wire_token_env(token, token_env=token_env, home=home, launchctl=launchctl, dry_run=dry_run)
    )
    record_completion(root, dry_run, actions)
    return actions


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description="Wire the Local Brain MCP token into the host env.")
    parser.add_argument(
        "--apply-env",
        action="store_true",
        help="Only re-apply launchctl setenv from the registry (used by the login LaunchAgent).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print intended changes only.")
    args = parser.parse_args(argv)

    if args.apply_env:
        for action in apply_env():
            print(f"  - {action}")
        return 0

    dry_run = args.dry_run or os.environ.get("FRITZ_MIGRATION_DRY_RUN", "").strip() == "1"
    root = brain_home()
    actions = run(root, home=Path.home(), dry_run=dry_run)

    mode = "DRY-RUN (no changes written)" if dry_run else "applied"
    print(f"Migration {MIGRATION_NUMBER} wire-mcp-token-env [{mode}]:")
    for action in actions:
        print(f"  - {action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
