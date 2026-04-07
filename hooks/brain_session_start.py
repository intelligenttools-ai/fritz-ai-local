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

from brain_common import read_hook_input, find_vault_for_cwd, load_manifest, resolve_path


def main():
    hook_input = read_hook_input()
    cwd = hook_input.get("cwd", "")

    if not cwd:
        sys.exit(0)

    vault_name, vault_config, vault_path = find_vault_for_cwd(cwd)
    if not vault_path:
        sys.exit(0)

    manifest = load_manifest(vault_path)
    if not manifest:
        sys.exit(0)

    # Build context to inject
    context_parts = [f"# Brain: {manifest.get('name', vault_name)} ({manifest.get('domain', 'unknown')})\n"]

    # Load index
    index_path = resolve_path(vault_path, manifest, "index")
    if index_path and index_path.exists():
        content = index_path.read_text(errors="replace").strip()
        context_parts.append(f"## Index\n\n{content}\n")

    # Load optional identity files (only if mapped in manifest)
    for key, label in [("soul", "Soul"), ("user", "User"), ("memory", "Memory")]:
        file_path = resolve_path(vault_path, manifest, key)
        if file_path and file_path.exists():
            content = file_path.read_text(errors="replace").strip()
            # Truncate large files to avoid blowing up the context
            if len(content) > 4000:
                content = content[:4000] + "\n\n[... truncated ...]"
            context_parts.append(f"## {label}\n\n{content}\n")

    # Add schema reference
    schema_path = vault_path / ".brain" / "schema.md"
    if schema_path.exists():
        context_parts.append(f"## Schema\n\nSee `.brain/schema.md` for full writing rules and operations.\n")

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
