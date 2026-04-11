#!/usr/bin/env python3
"""Brain session start hook.

Loads vault context, checks for updates, injects project awareness.

Works with:
- Claude Code (SessionStart event, outputs additionalContext JSON)
- Codex (SessionStart event, outputs additionalContext JSON)
- Gemini CLI (SessionStart event, outputs additionalContext JSON)
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brain_common import (
    read_hook_input, find_vault_for_cwd, load_manifest, resolve_path,
    load_registry, load_settings, resolve_project_vault,
    get_context_injection_level, get_fritz_version,
    BRAIN_HOME, FRITZ_REPO,
)


def check_for_updates(context_parts: list[str]):
    """Check if a Fritz Local update is available. Max once per 24h."""
    settings = load_settings()
    if not settings.get("update_check", True):
        return

    check_file = BRAIN_HOME / ".update-check"
    now = time.time()

    # Only check every 24 hours
    if check_file.exists():
        try:
            last_check = float(check_file.read_text().strip())
            if now - last_check < 86400:
                return
        except (ValueError, OSError):
            pass

    if not FRITZ_REPO.exists() or not (FRITZ_REPO / ".git").exists():
        return

    try:
        # Fetch quietly
        subprocess.run(
            ["git", "-C", str(FRITZ_REPO), "fetch", "--quiet"],
            capture_output=True, timeout=10,
        )

        # Check if behind origin/main
        result = subprocess.run(
            ["git", "-C", str(FRITZ_REPO), "log", "HEAD..origin/main",
             "--oneline", "--no-decorate"],
            capture_output=True, text=True, timeout=5,
        )

        if result.returncode == 0 and result.stdout.strip():
            commits = result.stdout.strip().split("\n")
            local_version = get_fritz_version() or "unknown"

            # Read remote VERSION
            remote_ver_result = subprocess.run(
                ["git", "-C", str(FRITZ_REPO), "show", "origin/main:VERSION"],
                capture_output=True, text=True, timeout=5,
            )
            remote_version = remote_ver_result.stdout.strip() if remote_ver_result.returncode == 0 else "unknown"

            context_parts.append(f"\n## Fritz Local update available ({local_version} → {remote_version})\n")
            context_parts.append("Changes:")
            for commit in commits[:10]:
                context_parts.append(f"- {commit}")
            if len(commits) > 10:
                context_parts.append(f"- ... and {len(commits) - 10} more")
            context_parts.append("\nRun `/fritz:update` to upgrade, or: `git -C ~/.fritz-ai-local pull`\n")

        # Record check timestamp
        check_file.write_text(str(now))
    except (subprocess.TimeoutExpired, OSError):
        pass


def inject_project_context(context_parts: list[str], vault_path: Path, manifest: dict, fritz_local: dict | None):
    """Inject project-specific context when .fritz-local.json is present."""
    if not fritz_local or "project" not in fritz_local:
        return

    project_name = fritz_local["project"]
    projects = manifest.get("projects", {})
    project_rel = projects.get(project_name)

    if not project_rel:
        return

    project_path = vault_path / project_rel
    if not project_path.exists():
        return

    context_parts.append(f"\n## Project: {project_name}\n")

    # Load project index
    project_index = project_path / "index.md"
    if project_index.exists():
        content = project_index.read_text(errors="replace").strip()
        if len(content) > 2000:
            content = content[:2000] + "\n[... truncated ...]"
        context_parts.append(f"### Project index\n\n{content}\n")

    # List feedback files (names only, not contents)
    feedback_dir = project_path / "feedback"
    if feedback_dir.exists():
        feedback_files = sorted(feedback_dir.glob("*.md"))
        feedback_files = [f for f in feedback_files if f.name != "index.md"]
        if feedback_files:
            context_parts.append("### Feedback files (user corrections — read before implementing)")
            for f in feedback_files:
                context_parts.append(f"- `{f.name}`")
            context_parts.append("")

    # Mention common/ if it exists
    common_dir = vault_path / "common"
    if common_dir.exists():
        context_parts.append("### Shared knowledge\n\n`common/` directory exists with shared patterns, research, and conventions.\n")

    # Show context injection level
    level = get_context_injection_level(fritz_local)
    if level != "off":
        context_parts.append(f"**Context injection**: `{level}` — brain hook will inject relevant knowledge on each prompt.\n")


def main():
    hook_input = read_hook_input()
    cwd = hook_input.get("cwd", "")

    context_parts = []

    # Always inject brain system awareness
    context_parts.append("# Brain System Active\n")
    context_parts.append("Knowledge base at `~/.brain/`. Use `/fritz:brain-query` to search, `/fritz:brain-compile` to promote captures, `/fritz:brain-ingest` to import sources.\n")

    # List available vaults
    registry = load_registry()
    vault_names = list(registry.get("vaults", {}).keys())
    if vault_names:
        context_parts.append(f"**Vaults**: {', '.join(vault_names)}\n")

    # Resolve vault — .fritz-local.json first, then cwd matching
    vault_name, vault_config, vault_path, fritz_local = (None, None, None, None)
    if cwd:
        vault_name, vault_config, vault_path, fritz_local = resolve_project_vault(cwd)

    if vault_path:
        manifest = load_manifest(vault_path)
        if manifest:
            context_parts.append(f"\n## Active vault: {manifest.get('name', vault_name)} ({manifest.get('domain', 'unknown')})\n")

            # Load index
            index_path = resolve_path(vault_path, manifest, "index")
            if index_path and index_path.exists():
                content = index_path.read_text(errors="replace").strip()
                context_parts.append(f"### Index\n\n{content}\n")

            # Load optional identity files
            for key, label in [("soul", "Soul"), ("user", "User"), ("memory", "Memory")]:
                file_path = resolve_path(vault_path, manifest, key)
                if file_path and file_path.exists():
                    content = file_path.read_text(errors="replace").strip()
                    if len(content) > 4000:
                        content = content[:4000] + "\n\n[... truncated ...]"
                    context_parts.append(f"### {label}\n\n{content}\n")

            # Schema reference
            schema_path = vault_path / ".brain" / "schema.md"
            if schema_path.exists():
                context_parts.append("### Schema\n\nSee `.brain/schema.md` for full writing rules.\n")

            # Project-specific context via .fritz-local.json
            inject_project_context(context_parts, vault_path, manifest, fritz_local)

    # Recent captures summary (global)
    capture_dir = BRAIN_HOME / "capture" / "daily"
    if capture_dir.exists():
        captures = sorted(capture_dir.glob("*.md"), reverse=True)[:3]
        if captures:
            context_parts.append("\n## Recent captures\n")
            for cap in captures:
                context_parts.append(f"- `{cap.name}`")

    # Check for pending compilation
    compile_needed = BRAIN_HOME / ".compile-needed"
    if compile_needed.exists():
        try:
            import json as _json
            data = _json.loads(compile_needed.read_text())
            since = data.get("since", "unknown")
            topics = data.get("topics", 0)
            context_parts.append(f"\n## MANDATORY: Background brain compile needed\n")
            context_parts.append(f"There are **{topics} uncompiled topics** captured since {since}. Before doing anything else, spawn a **background subagent** to run `/fritz:brain-compile`. The subagent MUST:")
            context_parts.append("1. Run the `/fritz:brain-compile` skill (it promotes raw captures to vault knowledge articles)")
            context_parts.append("2. Delete `~/.brain/.compile-needed` when done\n")
            context_parts.append("Use `run_in_background: true` (or platform equivalent) so the user is NOT blocked. Do NOT ask the user for permission — this is an autonomous maintenance task.")
        except (json.JSONDecodeError, OSError):
            pass

    # Check for updates (max once per 24h)
    check_for_updates(context_parts)

    full_context = "\n".join(context_parts)

    response = {
        "hookSpecificOutput": {
            "hookEventName": hook_input.get("hook_event_name", "SessionStart"),
            "additionalContext": full_context
        }
    }
    print(json.dumps(response))
    sys.exit(0)


if __name__ == "__main__":
    main()
