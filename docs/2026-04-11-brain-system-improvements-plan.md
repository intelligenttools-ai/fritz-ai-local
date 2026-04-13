# Brain System Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Address five gaps in fritz-ai-local: auto-create vault structures, preserve knowledge in handovers, add version tracking + upgrade mechanism, make brain queries actually happen via context injection, and support per-project configuration.

**Architecture:** Six independently shippable tasks. Hooks are Python scripts in `hooks/`. Skills are SKILL.md files in `skills/fritz:*/`. Config templates in `registry/` and `templates/`. All changes must work agent-agnostically (Claude Code, Codex, Gemini, Hermes). No MCP servers — Fritz Local uses hooks, skills, and subagents only.

**Tech Stack:** Python 3.10+, PyYAML, Markdown (SKILL.md), JSON (`.fritz-local.json`), Git

**Spec:** `docs/2026-04-11-brain-system-improvements-design.md`

---

## Task 1: VERSION File + fritz:update Skill

Foundation for all other tasks. Enables iterative releases and upgrade detection.

**Files:**
- Create: `VERSION`
- Create: `skills/fritz:update/SKILL.md`
- Create: `migrations/.gitkeep`

- [ ] **Step 1: Create VERSION file**

```
1.0.0
```

Write this to `VERSION` at repo root. Single line, no trailing newline.

- [ ] **Step 2: Create migrations directory**

```bash
mkdir -p migrations
touch migrations/.gitkeep
```

This directory will hold numbered migration scripts for breaking changes. Empty for now.

- [ ] **Step 3: Write fritz:update SKILL.md**

Create `skills/fritz:update/SKILL.md`:

```markdown
---
name: fritz:update
description: >
  Update Fritz Local to the latest version. Pulls from git, symlinks new skills,
  runs pending migrations, and reports changes. Use when the session-start hook
  reports an update is available, or run /fritz:update manually.
---

# Update

Update the local Fritz Local installation to the latest version.

## Trigger

Activate when:
- The session-start hook reports a Fritz Local update is available
- The user asks to update or upgrade Fritz Local
- Run `/fritz:update`

## Workflow

### 1. Pull latest

Run:
```
git -C ~/.fritz-ai-local pull
```

On Windows use `%USERPROFILE%\.fritz-ai-local`. If the pull fails (dirty tree, merge conflict), report the error and stop.

### 2. Read version change

Read `~/.fritz-ai-local/VERSION` for the new version. Compare with the version shown in the update notification (if any). Report the version bump.

### 3. Symlink new skills

List all directories in `~/.fritz-ai-local/skills/`. For each `fritz:*` skill directory, check if a symlink exists in the agent's skill directory:
- Claude Code: `~/.claude/skills/`
- Codex CLI: `~/.codex/skills/`
- Gemini CLI: `~/.gemini/skills/`

If a skill directory exists in the repo but has no symlink, create the symlink.

If a skill directory was removed from the repo but a symlink still exists, **warn the user** but do NOT delete the symlink. The human decides.

### 4. Run pending migrations

Check `~/.fritz-ai-local/migrations/` for numbered Python scripts (e.g., `001-add-settings.py`). Check `~/.brain/.migrations-run` for which migrations have already been executed. Run any new migrations in order.

Each migration script:
- Receives no arguments
- Reads/modifies files in `~/.brain/` or vault directories
- Is idempotent (safe to run twice)
- Prints a summary of what it did

After running, append the migration number to `~/.brain/.migrations-run`.

### 5. Report

Show the user:
- Version change (e.g., `1.0.0 → 1.1.0`)
- New skills added
- Removed skills (warnings only)
- Migrations run and their summaries
- Any errors encountered

## Important

- Execute immediately when invoked. No second confirmation.
- If `~/.fritz-ai-local` is not a git repo, report the error and suggest re-cloning.
- On Windows, use `%USERPROFILE%` for `~` and `mklink` for symlinks.
- Log the update operation to `~/.brain/log.md`.
```

- [ ] **Step 4: Commit**

```bash
git add VERSION migrations/.gitkeep skills/fritz:update/SKILL.md
git commit -m "feat: add VERSION tracking + fritz:update skill"
```

---

## Task 2: `.fritz-local.json` Support + `brain_common.py` Loader

Project-vault binding. This is the foundation for context injection (Task 5) and enhanced session-start (Task 6).

**Files:**
- Modify: `hooks/brain_common.py`

- [ ] **Step 1: Add `load_fritz_local` and `load_settings` functions to `brain_common.py`**

Add after the existing `today_str()` function at line 112:

```python
FRITZ_LOCAL_FILENAME = ".fritz-local.json"
FRITZ_REPO = Path.home() / ".fritz-ai-local"


def load_fritz_local(cwd: str) -> dict | None:
    """Walk up from cwd looking for .fritz-local.json. Return parsed JSON or None."""
    current = Path(cwd).resolve()
    for parent in [current, *current.parents]:
        candidate = parent / FRITZ_LOCAL_FILENAME
        if candidate.exists():
            try:
                with open(candidate) as f:
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
            return vault_name, config, vault_path, fritz_local

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
        return version_path.read_text().strip()
    return None
```

- [ ] **Step 2: Verify the module loads without errors**

```bash
cd /Users/karsten/Work/Development/intelligenttools-ai/fritz-ai-local
python3 -c "from hooks.brain_common import load_fritz_local, resolve_project_vault, get_context_injection_level, get_fritz_version; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add hooks/brain_common.py
git commit -m "feat: add .fritz-local.json loader + settings helpers to brain_common"
```

---

## Task 3: Update Detection in `brain_session_start.py`

Adds version check, `.fritz-local.json` awareness, and project context injection to the session-start hook.

**Files:**
- Modify: `hooks/brain_session_start.py`

- [ ] **Step 1: Rewrite `brain_session_start.py`**

Replace the entire contents of `hooks/brain_session_start.py` with:

```python
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
```

- [ ] **Step 2: Verify the hook runs without errors**

```bash
echo '{"cwd": "/Users/karsten/Work/Development/intelligenttools-ai/fritz-ai-local", "hook_event_name": "SessionStart"}' | python3 hooks/brain_session_start.py
```

Expected: JSON output with `hookSpecificOutput.additionalContext` containing brain system info.

- [ ] **Step 3: Commit**

```bash
git add hooks/brain_session_start.py
git commit -m "feat: add update detection + .fritz-local.json awareness to session-start hook"
```

---

## Task 4: Context Injection in `brain_prompt_check.py`

Three-level context injection: off (today), light (file paths), full (file paths + subagent instruction).

**Files:**
- Modify: `hooks/brain_prompt_check.py`

- [ ] **Step 1: Rewrite `brain_prompt_check.py`**

Replace the entire contents of `hooks/brain_prompt_check.py` with:

```python
#!/usr/bin/env python3
"""Brain-first enforcement hook with context injection.

Fires on UserPromptSubmit. Three modes:
- off: injects "BRAIN CHECK" reminder (today's behavior)
- light: searches knowledge dirs, injects matching file paths
- full: same as light, plus reminds agent to spawn a subagent

Works with:
- Claude Code: UserPromptSubmit event
- Codex: UserPromptSubmit event
- Gemini CLI: BeforeAgent event
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brain_common import (
    read_hook_input, load_registry, load_manifest, resolve_path,
    resolve_project_vault, get_context_injection_level,
    get_max_injection_chars, BRAIN_HOME,
)


# Words that suggest the user is asking a question or requesting knowledge
QUERY_SIGNALS = [
    "what do", "what did", "what was", "what is", "what are",
    "how do", "how did", "how to", "how does", "how should",
    "why do", "why did", "why is", "why are",
    "when did", "when do", "when was",
    "where do", "where is", "where did",
    "do we", "did we", "have we", "should we", "can we",
    "remember", "recall", "last time", "previously", "before",
    "decided", "decision", "pattern", "lesson", "learned",
    "what's our", "what's the",
    "explain", "tell me about", "summarize",
]

# Words that suggest implementation work
IMPLEMENTATION_SIGNALS = [
    "implement", "build", "create", "fix", "refactor", "deploy",
    "add feature", "write code", "set up", "configure", "migrate",
    "update the", "change the", "modify", "redesign",
]

# Skip enforcement for these
SKIP_PREFIXES = [
    "/", "!", "#", "yes", "no", "ok", "continue", "go ahead",
    "commit", "push", "merge",
]


def extract_keywords(prompt: str) -> list[str]:
    """Extract meaningful keywords from a prompt for file matching."""
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "do", "did", "does", "have", "has", "had", "will", "would",
        "could", "should", "can", "may", "might", "shall",
        "i", "we", "you", "they", "he", "she", "it", "my", "our",
        "this", "that", "these", "those", "what", "how", "why", "when",
        "where", "which", "who", "about", "from", "with", "for", "and",
        "or", "not", "but", "if", "then", "than", "into", "out",
        "up", "down", "on", "off", "over", "under", "to", "of", "in",
        "at", "by", "as",
    }
    words = re.findall(r'[a-zA-Z][a-zA-Z0-9_-]{2,}', prompt.lower())
    return [w for w in words if w not in stop_words]


def search_knowledge_files(vault_path: Path, manifest: dict, keywords: list[str],
                           project_name: str | None, max_chars: int) -> str:
    """Search knowledge directories for files matching keywords. Return formatted results."""
    results = []
    seen_paths = set()

    knowledge_path = resolve_path(vault_path, manifest, "knowledge")
    if not knowledge_path or not knowledge_path.exists():
        return ""

    # Search knowledge files by filename and first-line heading
    for md_file in knowledge_path.rglob("*.md"):
        if md_file.name == "index.md":
            continue
        rel = str(md_file.relative_to(vault_path))
        name_lower = md_file.stem.lower().replace("-", " ").replace("_", " ")

        matched = False
        for kw in keywords:
            if kw in name_lower:
                matched = True
                break

        if not matched:
            # Check first 5 lines for heading match
            try:
                with open(md_file) as f:
                    head = "".join(f.readline() for _ in range(5)).lower()
                for kw in keywords:
                    if kw in head:
                        matched = True
                        break
            except OSError:
                pass

        if matched and str(md_file) not in seen_paths:
            results.append(str(md_file))
            seen_paths.add(str(md_file))

    # Search feedback files for current project
    feedback_results = []
    if project_name:
        projects = manifest.get("projects", {})
        project_rel = projects.get(project_name)
        if project_rel:
            feedback_dir = vault_path / project_rel / "feedback"
            if feedback_dir.exists():
                for f in sorted(feedback_dir.glob("*.md")):
                    if f.name != "index.md":
                        feedback_results.append(str(f))

    if not results and not feedback_results:
        return ""

    # Build output within char limit
    parts = []
    chars_used = 0

    if results:
        parts.append("Brain knowledge relevant to your prompt:\n")
        parts.append("Knowledge articles:")
        for path in results[:10]:
            line = f"- {path}"
            if chars_used + len(line) > max_chars:
                break
            parts.append(line)
            chars_used += len(line)

    if feedback_results:
        parts.append("\nFeedback (user corrections):")
        for path in feedback_results:
            line = f"- {path}"
            if chars_used + len(line) > max_chars:
                break
            parts.append(line)
            chars_used += len(line)

    parts.append("\nRead these files before responding.")

    return "\n".join(parts)


def should_check_brain(prompt: str) -> str | None:
    """Determine if this prompt should trigger a brain check.

    Returns "query" for knowledge questions, "implementation" for code work, None for skip.
    """
    lower = prompt.lower().strip()

    if len(lower) < 15:
        return None
    for prefix in SKIP_PREFIXES:
        if lower.startswith(prefix):
            return None

    for signal in QUERY_SIGNALS:
        if signal in lower:
            return "query"

    for signal in IMPLEMENTATION_SIGNALS:
        if signal in lower:
            return "implementation"

    return None


def main():
    hook_input = read_hook_input()

    # Extract user prompt
    prompt = ""
    if "user_prompt" in hook_input:
        prompt = hook_input["user_prompt"]
    elif "message" in hook_input:
        msg = hook_input["message"]
        if isinstance(msg, dict):
            prompt = msg.get("content", "")
        elif isinstance(msg, str):
            prompt = msg

    if not prompt:
        sys.exit(0)

    prompt_type = should_check_brain(prompt)
    if not prompt_type:
        sys.exit(0)

    cwd = hook_input.get("cwd", "")

    # Determine context injection level
    vault_name, vault_config, vault_path, fritz_local = resolve_project_vault(cwd) if cwd else (None, None, None, None)
    level = get_context_injection_level(fritz_local)

    # Level: off — today's behavior
    if level == "off":
        registry = load_registry()
        vaults = registry.get("vaults", {})
        capture_dir = BRAIN_HOME / "capture" / "daily"
        has_captures = capture_dir.exists() and any(capture_dir.glob("*.md"))

        has_knowledge = False
        for name, config in vaults.items():
            vp = Path(config["path"]).expanduser()
            if (vp / ".brain" / "manifest.yaml").exists():
                has_knowledge = True
                break

        if not has_knowledge and not has_captures:
            sys.exit(0)

        vault_names = list(vaults.keys())
        reminder = (
            "BRAIN CHECK: Before answering, search the knowledge base. "
            f"Vaults: {', '.join(vault_names)}. "
            "Use /fritz:brain-query or search knowledge/ directories and "
            "~/.brain/capture/daily/ for relevant prior decisions, patterns, and facts."
        )

        response = {
            "hookSpecificOutput": {
                "hookEventName": hook_input.get("hook_event_name", "UserPromptSubmit"),
                "additionalContext": reminder,
            }
        }
        print(json.dumps(response))
        sys.exit(0)

    # Level: light or full — search and inject file paths
    max_chars = get_max_injection_chars(fritz_local)
    project_name = fritz_local.get("project") if fritz_local else None
    keywords = extract_keywords(prompt)

    injection = ""
    if vault_path:
        manifest = load_manifest(vault_path)
        if manifest and keywords:
            injection = search_knowledge_files(vault_path, manifest, keywords, project_name, max_chars)

    if not injection:
        # Even in light/full, fall back to basic reminder if no matches
        injection = (
            "BRAIN CHECK: No direct keyword matches found in knowledge base, "
            "but relevant knowledge may exist. Check feedback/ and decisions/ "
            "for this project before proceeding."
        )

    # Level: full — append subagent instruction
    if level == "full" and injection:
        injection += (
            "\n\nMANDATORY (context_injection: full): You MUST spawn a subagent to "
            "read and synthesize the files listed above before responding. "
            "The subagent should read all listed files, extract relevant information, "
            "and return a summary with citations. This is not optional."
        )

    response = {
        "hookSpecificOutput": {
            "hookEventName": hook_input.get("hook_event_name", "UserPromptSubmit"),
            "additionalContext": injection,
        }
    }
    print(json.dumps(response))
    sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test with off level (no .fritz-local.json)**

```bash
echo '{"user_prompt": "what did we decide about the auth middleware?", "cwd": "/tmp", "hook_event_name": "UserPromptSubmit"}' | python3 hooks/brain_prompt_check.py
```

Expected: JSON with "BRAIN CHECK" reminder (today's behavior).

- [ ] **Step 3: Test with light level**

Create a temporary test `.fritz-local.json`:
```bash
echo '{"vault": "development", "project": "agent-email", "context_injection": "light"}' > /tmp/test-fritz-local.json
```

```bash
echo '{"user_prompt": "what did we decide about the auth middleware?", "cwd": "/Users/karsten/Notes/Development", "hook_event_name": "UserPromptSubmit"}' | python3 hooks/brain_prompt_check.py
```

Expected: JSON with file paths from the development vault.

```bash
rm /tmp/test-fritz-local.json
```

- [ ] **Step 4: Commit**

```bash
git add hooks/brain_prompt_check.py
git commit -m "feat: three-level context injection in brain_prompt_check (off/light/full)"
```

---

## Task 5: Interactive `fritz:brain-setup` Redesign

Rewrite the brain-setup skill for analyze→ask→create workflow, per-project structure, common/, index files, `.fritz-local.json` creation, and context injection question.

**Files:**
- Modify: `skills/fritz:brain-setup/SKILL.md`
- Modify: `registry/registry.template.yaml`

- [ ] **Step 1: Update registry template with settings block**

Replace the entire contents of `registry/registry.template.yaml` with:

```yaml
version: 1

# Vault registry — edit paths for your machine.
# Each vault gets a .brain/ overlay without restructuring existing folders.

# Default vault for captures when cwd doesn't match any vault.
default_vault: my-vault

# Global settings (all optional, shown with defaults)
settings:
  # Context injection: off | light | full (default: off)
  # - off: advisory "BRAIN CHECK" reminder only
  # - light: hook searches knowledge dirs, injects matching file paths
  # - full: light + agent must spawn subagent to read/synthesize
  # context_injection: off

  # Max characters for context injection output (default: 8000)
  # max_injection_chars: 8000

  # Check for Fritz Local updates on session start (default: true)
  # update_check: true

vaults:
  my-vault:
    path: ~/Notes/MyVault
    domain: work
    sync: affine
    # affine_workspace_id: "<your-workspace-id>"

  work:
    path: ~/Notes/Work
    domain: work
    sync: local

  personal:
    path: ~/Notes/Personal
    domain: personal
    sync: local

  engineering:
    path: ~/Notes/Engineering
    domain: engineering
    sync: affine

  research:
    path: ~/Notes/Research
    domain: research
    sync: local
```

- [ ] **Step 2: Rewrite `fritz:brain-setup` SKILL.md**

Replace the entire contents of `skills/fritz:brain-setup/SKILL.md` with:

```markdown
---
name: fritz:brain-setup
description: >
  Set up the Fritz Local brain overlay for a new vault. Explores directory
  structure, presents findings, asks questions interactively, then creates
  structure based on human answers. Handles per-project directories, common/
  shared area, index files, .fritz-local.json creation, and context injection
  configuration. Use when the user asks to set up a brain vault, add a project,
  or run /fritz:brain-setup.
---

# Brain Setup

Set up the Fritz Local brain overlay for a vault. Explores the vault's directory
structure, presents findings, asks questions one at a time, then creates
structure based on the human's answers.

**Principle: The human decides structure. The agent discovers, proposes, and executes.**

## Trigger

Activate when the user asks to:
- Set up a vault for the brain system
- Initialize the brain for a directory/vault
- Add a new project to an existing vault
- Run `/fritz:brain-setup`

Also activate when a vault in the registry has no `.brain/manifest.yaml`.

## Workflow

### Phase 1: Analyze

Identify the target vault (user-specified path, or cwd). Check if it's already
in `~/.brain/registry.yaml`.

Explore the directory structure. Look for:

**Daily/journal patterns** (→ `capture_daily`):
- Folders: `Daily/`, `Journal/`, `*Daily*/`, files matching `YYYY-MM-DD*.md`

**Knowledge/wiki patterns** (→ `knowledge`):
- Folders: `Knowledge/`, `Wiki/`, `Articles/`, `Resources/`, `*Para*/`

**Per-project patterns**:
- Multiple top-level directories each containing markdown files
- Directories with subdirectories like `feedback/`, `decisions/`, `context/`

**Index patterns** (→ `index`):
- Files: `index.md`, `INDEX.md`, `MOC.md`, `README.md`

**Identity file patterns** (→ `soul`, `user`, `memory`):
- Files: `SOUL.md`, `USER.md`, `MEMORY.md`

**Archive patterns** (→ `archive`):
- Folders: `Archive/`, `*Archive*/`

**Naming conventions**:
- Numeric prefixes (`100_`, `200_`) → Johnny Decimal
- Date prefixes (`YYYY-MM-DD`) → Date-based
- Plain names → Flat

**Exclusions**:
- `.obsidian/`, `.trash/`, `_Attachments/`, `node_modules/`, `.git/`
- Anything resembling credentials, keys, or secrets

### Phase 2: Present Findings

Show the user what was discovered:

```
I analyzed the directory structure. Here's what I found:

Structure:
- 3 project directories: agent-email/, agentcontrol/, esg-assistent/
- 1 shared area: patterns/
- Johnny Decimal numbering detected in some directories
- No per-directory index files

Missing:
- No common/ shared knowledge area (patterns/ exists but is ad-hoc)
- No feedback/ or decisions/ subdirectories in projects
- No index.md files in subdirectories

Existing:
- .brain/ overlay already exists with manifest and schema
```

### Phase 3: Ask Questions (one at a time)

Ask these questions sequentially. Wait for each answer before asking the next.
Skip questions that don't apply (e.g., don't ask about per-project structure
for a vault with no projects).

1. **Vault identity** (if new vault):
   "What **name** should this vault have? (lowercase, no spaces)"
   "What **domain** is this vault? [work / engineering / development / research / personal / custom]"

2. **Per-project structure**:
   "Should I create the standard per-project structure for each project?
   This adds: `feedback/`, `decisions/`, `runbooks/`, `context/` with index files.
   Projects found: [list]. [yes / no / customize]"

3. **Common shared area**:
   "Should I create a `common/` area for shared knowledge (patterns, research, conventions)?
   This adds: `common/patterns/`, `common/research/`, `common/conventions/` with index files. [yes / no]"

4. **Index files**:
   "Should I generate `index.md` files for every directory that doesn't have one? [yes / no]"

5. **Project source directory binding** (for development vaults):
   "I can create `.fritz-local.json` files in your project source directories to bind
   them to this vault. This helps agents find the right knowledge when working in your code.
   Which source directories should I bind?
   - agent-email → ~/Work/Development/.../agentic-email-and-calendar
   - (list discovered or ask)"

6. **Context injection** (per-project, only if `.fritz-local.json` is being created):
   Check global `settings.context_injection` in `~/.brain/registry.yaml` first.
   - If global is set: "Global context injection is set to `{level}`. Override for this project? [keep global / off / light / full]"
   - If global is NOT set: "Enable context injection for this project? This injects relevant knowledge into your agent's context on each prompt, which uses additional tokens. [off (default) / light / full]"

### Phase 4: Execute

Create only what the human approved.

**Per-project structure** (for each approved project):
```
<project>/
├── index.md          # "# <Project Name>\n\nProject overview.\n"
├── feedback/
│   └── index.md      # "# Feedback\n\nUser corrections and preferences.\n"
├── decisions/
│   └── index.md      # "# Decisions\n\nArchitecture and design decisions.\n"
├── runbooks/
│   └── index.md      # "# Runbooks\n\nOperational fixes and debugging guides.\n"
└── context/
    └── index.md      # "# Context\n\nRequirements, background, and current state.\n"
```

**Common area** (if approved):
```
common/
├── index.md          # "# Common Knowledge\n\nShared patterns, research, and conventions.\n"
├── patterns/
│   └── index.md      # "# Patterns\n\nReusable patterns across projects.\n"
├── research/
│   └── index.md      # "# Research\n\nResearch results and findings.\n"
└── conventions/
    └── index.md      # "# Conventions\n\nTeam conventions and standards.\n"
```

**Index files**: Generate `index.md` in every directory that lacks one. Content:
heading with directory name, list of contained files with brief descriptions.

**`.fritz-local.json`** (for each approved source directory):
```json
{
  "vault": "<vault-name>",
  "project": "<project-name>",
  "brain_home": "~/.brain",
  "context_injection": "<chosen-level>"
}
```

**Manifest** (`.brain/manifest.yaml`): Generate with discovered + created paths.
Include `project_structure` field:

```yaml
project_structure:
  - index.md
  - feedback/
  - decisions/
  - runbooks/
  - context/
```

**Registry**: Add or update the vault in `~/.brain/registry.yaml`.

**Instruction files**: Generate `CLAUDE.md`, `AGENTS.md`, `GEMINI.md` in
`.brain/instructions/` with vault name, domain, key paths, and brain knowledge
section. If context injection is `full`, include the mandatory subagent instruction.

**Schema**: Generate `.brain/schema.md` from template.

### Phase 5: Report

Show what was created, what paths were mapped, any issues found.

## Important

- NEVER create structure without asking first. Analyze → Present → Ask → Execute.
- NEVER restructure or move existing files. Map to what exists, create what's new.
- One question per message. Wait for the answer.
- If the vault already has a manifest, offer to update it rather than overwrite.
- Respect existing naming conventions (Johnny Decimal, date-based, flat).
- Exclude sensitive directories from the manifest.
```

- [ ] **Step 3: Commit**

```bash
git add skills/fritz:brain-setup/SKILL.md registry/registry.template.yaml
git commit -m "feat: interactive brain-setup + settings block in registry template"
```

---

## Task 6: `fritz:brain-compile` Structure-on-Demand

Add on-the-fly project structure creation and per-directory index maintenance.

**Files:**
- Modify: `skills/fritz:brain-compile/SKILL.md`

- [ ] **Step 1: Rewrite `fritz:brain-compile` SKILL.md**

Replace the entire contents of `skills/fritz:brain-compile/SKILL.md` with:

```markdown
---
name: fritz:brain-compile
description: >
  Promote raw captures into compiled knowledge articles. Reads from the global
  capture directory (~/.brain/capture/) and routes knowledge to the correct vault
  based on content, not working directory. Creates per-project structure on-the-fly
  when routing to a new project. Maintains per-directory index files.
  Use when the user asks to compile, flush, or promote brain captures, process
  daily logs into knowledge, update the knowledge base, or run /fritz:brain-compile.
---

# Brain Compile

Promote raw captures into compiled knowledge articles. Reads from the global
capture directory (`~/.brain/capture/`) and routes knowledge to the correct vault
based on **content**, not working directory.

## Trigger

Activate when the user asks to:
- Compile, flush, or promote brain captures
- Process daily logs into knowledge
- Update the knowledge base from recent sessions
- Run `/fritz:brain-compile`

## Architecture

Captures are dumb — every conversation is saved to `~/.brain/capture/daily/`
regardless of where the session happened. The compile step is where intelligence
lives: it reads captures, analyzes content, and routes each piece of knowledge
to the appropriate vault.

## Workflow

### 1. Read the vault registry

Read `~/.brain/registry.yaml` to get all available vaults and their domains.
Each vault has a `.brain/manifest.yaml` mapping brain concepts to actual paths.

### 2. Find unprocessed captures

Read `~/.brain/log.md` to find the last COMPILE operation timestamp. Find all
capture files in `~/.brain/capture/daily/` newer than that date.

If no previous COMPILE exists, process all captures.

### 3. Analyze and route

For each capture file, read the content and for each promotable item determine:

**Which vault does this belong in?** Route based on content:
- VanillaCore business operations → `vanillacore` vault
- Engineering runbooks, infrastructure → `engineering` vault
- Personal notes, ideas → `privat` vault
- AI agent development, research → `ai-agents` vault
- Software/code project knowledge → `development` vault
- General work topics → `work` vault

Use the `cwd` recorded in the capture as a hint, but the **content** is the
primary signal.

**Is this worth promoting?** Extract:
- **Decisions** that affect future work
- **Patterns** that solved real problems
- **Facts** about the domain not previously known
- **Corrections** to existing knowledge
- **Lessons from failures**

Skip ephemeral content: routine Q&A, tool outputs without insight, status checks.

**Is this project-specific or cross-project?**
- Project-specific knowledge → route to the project directory
- Cross-project patterns, research, conventions → route to `common/`

### 4. Ensure target structure exists

Before writing an article, check if the target directory structure exists.

**For project-specific articles:**
If the target project directory doesn't have the per-project structure, check
the vault's `manifest.yaml` for a `project_structure` field. If present, create
the full structure:

```
<project>/
├── index.md
├── feedback/
│   └── index.md
├── decisions/
│   └── index.md
├── runbooks/
│   └── index.md
└── context/
    └── index.md
```

Register the new project in `manifest.yaml` under `projects:`.

If no `project_structure` is defined in the manifest, create only the specific
subdirectory needed (e.g., `<project>/runbooks/`).

**For cross-project articles:**
If `common/` doesn't exist but the manifest indicates it should (presence of
`project_structure` field implies per-project vault), create:

```
common/
├── index.md
├── patterns/
│   └── index.md
├── research/
│   └── index.md
└── conventions/
    └── index.md
```

### 5. Create or update knowledge articles

For each promotable item, read the target vault's manifest to find its
`knowledge` path, then:

**Check if an article already covers this topic:**
- Search the vault's `knowledge/` by filename and content
- Check the vault's index for related entries

**If article exists — UPDATE it:**
- Add new information to the appropriate section
- Update `updated` date in frontmatter
- Add the capture file to `sources`

**If no article exists — CREATE one:**
- Place in the appropriate subfolder
- Use descriptive filename: `<topic-slug>.md` (lowercase, hyphenated)
- Include full frontmatter:

```yaml
---
type: article
title: "Descriptive title"
domain: <vault domain>
sources:
  - ~/.brain/capture/daily/YYYY-MM-DD.md
related:
  - <paths to related articles>
  - vault://<other-vault>/knowledge/<topic>
tags: [<relevant tags>]
confidence: medium
status: active
created: <today>
updated: <today>
promoted_from: ~/.brain/capture/daily/YYYY-MM-DD.md
agent_last_edit: <agent>
---
```

### 6. Update indexes

**Per-directory index maintenance:**
After creating or updating an article, update the `index.md` in the same
directory. The index should list all articles in that directory with their
titles and a one-line summary.

If the directory has no `index.md`, create one.

**Vault-level index:**
Update the vault's main index (at the manifest's `index` path) to reflect
new projects, new articles, and updated counts.

### 7. Log

- Append to `~/.brain/log.md`:
```
YYYY-MM-DD HH:MM | COMPILE | <agent> | Processed N captures → X articles across Y vaults
```
- Append to each affected vault's `.brain/log.md` as well

## Important

- Do NOT compile if there are no new captures since the last COMPILE
- Do NOT create articles for trivial or ephemeral content
- DO cross-reference related articles across vaults using `vault://` URIs
- DO preserve existing article content when updating — integrate, never overwrite
- A single capture file may produce knowledge for multiple vaults
- Each compile run should be idempotent
- When creating project structure on-the-fly, follow the vault's `project_structure` template
- Always update per-directory index files after writing articles
```

- [ ] **Step 2: Commit**

```bash
git add skills/fritz:brain-compile/SKILL.md
git commit -m "feat: brain-compile with structure-on-demand + per-directory index maintenance"
```

---

## Task 7: Handover Knowledge Preservation

Update handover skill to compile→ingest→sync before writing.

**Files:**
- Modify: `skills/fritz:handover/SKILL.md`

- [ ] **Step 1: Rewrite `fritz:handover` SKILL.md**

Replace the entire contents of `skills/fritz:handover/SKILL.md` with:

```markdown
---
name: fritz:handover
description: >
  Create a structured handover document for continuing work in a fresh agent session.
  Preserves knowledge before handing over: compiles pending captures, ingests session
  decisions/patterns, syncs if configured, then writes the handover document.
  Use when the user asks to hand over, hand off, create a handover, wrap up for
  continuation, save session state, prepare a fresh session, or run /handover.
---

# Handover

Create a self-contained handover document that allows a fresh agent session to
continue work seamlessly. **Preserves all knowledge before handing over** —
the receiving agent inherits compiled knowledge, not a TODO to compile it.

## Trigger

Activate when the user asks to:
- Create a handover / hand off / handover prompt
- Wrap up for a fresh session
- Save state for continuation
- Run `/handover`

## Storage

- **Global**: `~/.brain/handovers/`
- **Project-local**: `.handovers/` (relative to project root)

Default to project-local if inside a git repo, global otherwise.

## Workflow

### Phase 1: Preserve Knowledge ("leave nothing behind")

Before writing the handover document, preserve all knowledge from this session.

**Step 1: Compile pending captures**

Check `~/.brain/.compile-needed`. If it exists, run brain-compile (or spawn a
subagent to run it). Wait for completion before proceeding. This ensures all
prior session captures are promoted to knowledge articles.

**Step 2: Ingest session knowledge**

Extract from the current session:
- **Decisions** made (architecture choices, design trade-offs, tool selections)
- **Patterns** discovered (what worked, reusable approaches)
- **Corrections** from the user (feedback that should prevent future mistakes)
- **Facts** learned (domain knowledge, system behavior, configuration details)

Write these directly to the appropriate vault as knowledge articles, following
the brain-compile workflow (route by content, use frontmatter, update indexes).
This is inline ingestion — not deferred to a future agent.

**Step 3: Sync if configured**

Read `~/.brain/registry.yaml` to find the active vault. Check its `sync` setting.
If a sync target is configured (affine, notion, git, filesystem), run brain-sync
for the articles just created or updated in this session.

If no sync target or sync is `local`/`none`, skip this step.

### Phase 2: Write the Handover Document

Collect from the current session:

- **Goal**: What is the user trying to accomplish?
- **Status**: What has been done? (completed steps, files changed, decisions made)
- **Blockers**: What is stuck or unresolved?
- **Next steps**: What should the receiving agent do first? (concrete, actionable)
- **Key files**: Which files are central to the work? (paths + brief role)
- **Branch state**: Current git branch, uncommitted changes, recent commits

Create a timestamped file:

```
{storage}/handover-{YYYY-MM-DD}-{HHmm}-{slug}.md
```

Use this structure:

```markdown
---
type: handover
created: {ISO 8601 timestamp}
project: {project name or path}
branch: {current branch}
from_agent: {agent identifier}
status: pending
---

# Handover: {Brief title}

## Goal

{1-3 sentences}

## Completed

{Bulleted list of what was done this session}

## Current State

{Where things stand — working/broken/partially done}

## Key Files

{Table or list of files central to the work, with brief role}

## Open Questions / Blockers

{Anything unresolved}

## Next Steps

{Ordered list of concrete actions}

## Receiving Agent Instructions

1. Read this handover document completely
2. Verify the branch and file state described above still matches reality
3. Execute the next steps in order
4. Knowledge from the previous session has already been compiled and synced.
   If you discover additional insights while executing next steps, compile
   them before ending your session.
5. When all next steps are complete (or if the handover is no longer needed):
   - Delete this handover file: `rm {path-to-this-file}`
6. If work is still incomplete, create a new `/handover` before ending your session
```

### Phase 3: Provide the Kickoff Prompt

Present a ready-to-paste prompt as a fenced code block:

````markdown
```
Read the handover at {path-to-handover-file} and continue the work described
in it. Follow the receiving agent instructions at the end of the document.
```
````

## Important

- **Phase 1 is not optional.** Always compile and ingest before writing the handover.
- Keep handover documents under 200 lines — a briefing, not a transcript.
- Include only actionable context. Skip routine tool output and resolved tangents.
- Never include secrets, tokens, or credentials.
- Create `.handovers/` directory if it doesn't exist. Add to `.gitignore`.
- Set `status: pending` in frontmatter.
- A handover is ephemeral — it exists only to bridge two sessions.
```

- [ ] **Step 2: Commit**

```bash
git add skills/fritz:handover/SKILL.md
git commit -m "feat: handover preserves knowledge before writing (compile→ingest→sync)"
```

---

## Task 8: Update SETUP.md for New Features

Document `.fritz-local.json`, context injection, and `fritz:update` in the setup guide.

**Files:**
- Modify: `SETUP.md`

- [ ] **Step 1: Add new sections to SETUP.md**

After the existing Step 8 (Verify), add:

```markdown

## Step 9: Configure per-project bindings (optional)

For each source code project that should be linked to a brain vault, create a `.fritz-local.json` file in the project root:

```json
{
  "vault": "<vault-name>",
  "project": "<project-name>",
  "brain_home": "~/.brain",
  "context_injection": "off"
}
```

Fields:
- `vault`: name of the vault in `~/.brain/registry.yaml`
- `project`: project directory name within the vault
- `brain_home`: path to brain directory (default `~/.brain`)
- `context_injection`: `off` (default) | `light` | `full`

This file is safe to commit to version control — it contains no secrets.

Context injection levels:
- `off`: advisory "BRAIN CHECK" reminder only (no token cost)
- `light`: hook searches knowledge dirs, injects matching file paths (low token cost)
- `full`: light + agent must spawn subagent to read/synthesize (higher token cost)

## Step 10: Configure global settings (optional)

Add a `settings` block to `~/.brain/registry.yaml` for global defaults:

```yaml
settings:
  # context_injection: off    # off | light | full — inherited by all projects
  # max_injection_chars: 8000 # cap on injected context size
  # update_check: true        # check for Fritz Local updates on session start
```

Per-project `.fritz-local.json` overrides global settings.

## Step 11: Keeping Fritz Local updated

Fritz Local checks for updates on session start (once per 24 hours). When an update is available, you'll see a notification with the changelog.

To update, run `/fritz:update` or manually:
```
git -C ~/.fritz-ai-local pull
```

Symlinked hooks and skills update immediately after pull. New skills are automatically symlinked by `/fritz:update`.
```

- [ ] **Step 2: Commit**

```bash
git add SETUP.md
git commit -m "docs: add per-project bindings, global settings, and update instructions to SETUP.md"
```

---

## Task 9: Final Verification + Tag Release

- [ ] **Step 1: Verify all hooks load without errors**

```bash
cd /Users/karsten/Work/Development/intelligenttools-ai/fritz-ai-local
python3 -c "from hooks.brain_common import load_fritz_local, resolve_project_vault, get_context_injection_level, get_fritz_version, load_settings, get_max_injection_chars; print('brain_common OK')"
echo '{"cwd": "/tmp", "hook_event_name": "SessionStart"}' | python3 hooks/brain_session_start.py | python3 -m json.tool > /dev/null && echo "session_start OK"
echo '{"user_prompt": "hello", "cwd": "/tmp", "hook_event_name": "UserPromptSubmit"}' | python3 hooks/brain_prompt_check.py; echo "prompt_check OK (empty output expected for short prompt)"
```

Expected: All three print OK with no errors.

- [ ] **Step 2: Verify all skill files have valid frontmatter**

```bash
for skill in skills/fritz:*/SKILL.md; do
  echo -n "$skill: "
  python3 -c "
import yaml, sys
with open('$skill') as f:
    content = f.read()
    if not content.startswith('---'):
        print('MISSING FRONTMATTER'); sys.exit(1)
    end = content.index('---', 3)
    fm = yaml.safe_load(content[3:end])
    if 'name' not in fm or 'description' not in fm:
        print('MISSING name/description'); sys.exit(1)
    print('OK')
"
done
```

Expected: All skills print OK.

- [ ] **Step 3: Tag the release**

```bash
git tag -a v1.0.0 -m "v1.0.0: vault structure auto-creation, context injection, version tracking, handover knowledge preservation"
```

- [ ] **Step 4: Commit the spec and plan**

```bash
git add docs/2026-04-11-brain-system-improvements-design.md docs/2026-04-11-brain-system-improvements-plan.md
git commit -m "docs: add design spec and implementation plan for brain system improvements"
```
