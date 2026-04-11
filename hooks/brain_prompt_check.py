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


def _match_file(md_file: Path, keywords: list[str]) -> bool:
    """Check if a markdown file matches any keyword by filename or heading."""
    name_lower = md_file.stem.lower().replace("-", " ").replace("_", " ")
    for kw in keywords:
        if kw in name_lower:
            return True
    # Check first 5 lines for heading match
    try:
        with open(md_file) as f:
            head = "".join(f.readline() for _ in range(5)).lower()
        for kw in keywords:
            if kw in head:
                return True
    except OSError:
        pass
    return False


def search_knowledge_files(vault_path: Path, manifest: dict, keywords: list[str],
                           project_name: str | None, max_chars: int) -> str:
    """Search knowledge directories for files matching keywords. Return formatted results."""
    results = []
    seen_paths = set()

    # Collect all search directories: knowledge path, per-project dirs, common/
    search_dirs = []

    knowledge_path = resolve_path(vault_path, manifest, "knowledge")
    if knowledge_path and knowledge_path.exists():
        search_dirs.append(knowledge_path)

    # Also search per-project directories (decisions/, runbooks/, context/)
    projects = manifest.get("projects", {})
    if project_name and project_name in projects:
        project_dir = vault_path / projects[project_name]
        for subdir in ("decisions", "runbooks", "context"):
            sub = project_dir / subdir
            if sub.exists() and sub not in search_dirs:
                search_dirs.append(sub)

    # Also search common/ if it exists
    common_dir = vault_path / "common"
    if common_dir.exists() and common_dir not in search_dirs:
        search_dirs.append(common_dir)

    if not search_dirs:
        return ""

    # Search all collected directories
    for search_dir in search_dirs:
        for md_file in search_dir.rglob("*.md"):
            if md_file.name == "index.md":
                continue
            if str(md_file) in seen_paths:
                continue
            if _match_file(md_file, keywords):
                results.append(str(md_file))
                seen_paths.add(str(md_file))

    # Search feedback files for current project (always include all, not keyword-matched)
    feedback_results = []
    if project_name and project_name in projects:
        feedback_dir = vault_path / projects[project_name] / "feedback"
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
