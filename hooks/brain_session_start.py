#!/usr/bin/env python3
"""Brain session start hook.

Loads vault context (index, soul/user/memory if mapped) into the agent's session.

Works with:
- Claude Code (SessionStart event, outputs additionalContext JSON)
- Codex (SessionStart event, outputs additionalContext JSON)
- Gemini CLI (SessionStart event, outputs additionalContext JSON)
"""

import json
import sys
from pathlib import Path

# Ensure brain_common is importable regardless of cwd
sys.path.insert(0, str(Path(__file__).resolve().parent))

from brain_common import (
    read_hook_input, find_vault_for_cwd, load_manifest, resolve_path,
    load_registry, BRAIN_HOME,
)


def main():
    hook_input = read_hook_input()
    cwd = hook_input.get("cwd", "")

    context_parts = []

    # Always inject brain system awareness
    context_parts.append("# Brain System Active\n")
    context_parts.append("Knowledge base at `~/.brain/`. Use `/brain-query` to search, `/brain-compile` to promote captures, `/brain-ingest` to import sources.\n")

    # List available vaults
    registry = load_registry()
    vault_names = list(registry.get("vaults", {}).keys())
    if vault_names:
        context_parts.append(f"**Vaults**: {', '.join(vault_names)}\n")

    # If cwd is inside a vault, load that vault's context
    if cwd:
        vault_name, vault_config, vault_path = find_vault_for_cwd(cwd)
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

    # Recent captures summary (global)
    capture_dir = BRAIN_HOME / "capture" / "daily"
    if capture_dir.exists():
        captures = sorted(capture_dir.glob("*.md"), reverse=True)[:3]
        if captures:
            context_parts.append("\n## Recent captures\n")
            for cap in captures:
                context_parts.append(f"- `{cap.name}`")

    full_context = "\n".join(context_parts)

    # Output in the hook protocol format (works for Claude Code, Codex, Gemini)
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
