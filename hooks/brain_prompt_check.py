#!/usr/bin/env python3
"""Brain-first enforcement hook with context injection.

Fires on UserPromptSubmit. Three modes:
- off: injects "BRAIN CHECK" reminder (today's behavior)
- light: searches knowledge dirs, injects matching file paths
- full: same as light, but retrieves more matched knowledge (higher file cap)

Works with:
- Claude Code: UserPromptSubmit event
- Codex: UserPromptSubmit event
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brain_bootstrap import ensure_yaml_interpreter

ensure_yaml_interpreter()

from brain_common import (
    read_hook_input, load_registry, load_manifest, resolve_path,
    resolve_project_vault, get_context_injection_level,
    get_max_injection_chars, BRAIN_HOME,
    local_brain_service_available, local_brain_service_instructions,
    local_brain_setup_suggestion, local_brain_setup_suggestions_enabled,
    local_brain_service_configured, local_brain_configuration_decision_prompt,
    _resolve_client_agent, claude_form,
)


def _save_policy() -> str:
    """Always-on per-turn save policy (Pi parity — Pi injects this via
    before_agent_start). Durable operational knowledge confirmed this turn must
    be SAVED, not merely answered. Kept short because it is injected every turn.

    Runtime-aware (#239): the Claude runtime only ever resolves the sanitized
    plugin-qualified skill name, so reference that for `agent == 'claude'`;
    every other runtime keeps the `/fritz:brain-save` skill-tree name.
    """
    ref = claude_form("/fritz:brain-save") if _resolve_client_agent() == "claude" else "/fritz:brain-save"
    return (
        "BRAIN SAVE: If this turn confirms durable operational knowledge "
        "(decisions, fixes, URLs, token/credential locations, runbook facts), SAVE it "
        f"via the {ref} skill — do not merely answer it."
    )


def _emit(hook_input: dict, context: str) -> None:
    """Emit additionalContext (with the per-turn save policy appended) and exit."""
    save_policy = _save_policy()
    context = f"{context}\n\n{save_policy}" if context else save_policy
    response = {
        "hookSpecificOutput": {
            "hookEventName": hook_input.get("hook_event_name", "UserPromptSubmit"),
            "additionalContext": context,
        }
    }
    print(json.dumps(response))
    sys.exit(0)


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

BRAIN_SERVICE_PHRASES = [
    "local brain", "brain service", "dockerized local brain", "fritz local brain",
    "brain capture", "brain captures", "brain vault", "brain lint",
    "brain compile", "brain sync", "brain query", "knowledge base",
    "/fritz:brain", "/fritz:handover",
]
BRAIN_SERVICE_ACTIONS = [
    "compile", "sync", "query", "lint", "handover", "embedding", "mcp",
    "capture", "captures", "vault", "vaults", "knowledge",
]

# Skip enforcement for these
# Punctuation markers: matched via startswith (slash/bang/hash commands).
PUNCT_PREFIXES = ["/", "!", "#"]
# Word tokens: trivial only when the prompt equals the token or starts with the
# token followed by whitespace or simple punctuation — not as a loose prefix.
# "go ahead" is kept for clarity but is already covered by "go".
TRIVIAL_WORDS = [
    "yes", "no", "ok", "go", "go ahead",
    "continue", "commit", "push", "merge",
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
    if not _is_regular_markdown_file(md_file):
        return False
    name_lower = md_file.stem.lower().replace("-", " ").replace("_", " ")
    for kw in keywords:
        if kw in name_lower:
            return True
    # Check first 5 lines for heading match
    try:
        with open(md_file, encoding="utf-8", errors="replace") as f:
            head = "".join(f.readline() for _ in range(5)).lower()
        for kw in keywords:
            if kw in head:
                return True
    except OSError:
        pass
    return False


def _is_regular_markdown_file(md_file: Path) -> bool:
    return md_file.is_file() and md_file.suffix == ".md" and not md_file.is_symlink()


def search_knowledge_files(vault_path: Path, manifest: dict, keywords: list[str],
                           project_name: str | None, max_chars: int,
                           file_limit: int = 10) -> str:
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
            if not _is_regular_markdown_file(md_file):
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
                if f.name != "index.md" and _is_regular_markdown_file(f):
                    feedback_results.append(str(f))

    if not results and not feedback_results:
        return ""

    # Build output within char limit using whole lines only. Never truncate a
    # path line mid-string, and keep the final instruction whenever content is emitted.
    instruction = "Read these files before responding."
    lines = ["Brain knowledge relevant to your prompt:"]

    def _fits(candidate: list[str]) -> bool:
        return len("\n".join(candidate + [instruction])) <= max_chars

    if not _fits(lines):
        return ""

    if results and _fits(lines + ["Knowledge articles:"]):
        lines.append("Knowledge articles:")
        for path in results[:file_limit]:
            line = f"- {path}"
            if not _fits(lines + [line]):
                break
            lines.append(line)

    if feedback_results and _fits(lines + ["Feedback (user corrections):"]):
        lines.append("Feedback (user corrections):")
        for path in feedback_results:
            line = f"- {path}"
            if not _fits(lines + [line]):
                break
            lines.append(line)

    return "\n".join(lines + [instruction])


def should_check_brain(prompt: str) -> str | None:
    """Determine if this prompt should trigger a brain check.

    Returns "query" for knowledge questions, "implementation" for code work, None for skip.
    """
    lower = prompt.lower().strip()

    if _is_trivial(prompt):
        return None

    for signal in QUERY_SIGNALS:
        if signal in lower:
            return "query"

    if should_suggest_local_brain_service(prompt):
        return "query"

    for signal in IMPLEMENTATION_SIGNALS:
        if signal in lower:
            return "implementation"

    return None


def should_suggest_local_brain_service(prompt: str) -> bool:
    lower = prompt.lower()
    if any(phrase in lower for phrase in BRAIN_SERVICE_PHRASES):
        return True
    if not re.search(r"\bbrain\b", lower):
        return False
    return any(re.search(rf"\b{re.escape(action)}\b", lower) for action in BRAIN_SERVICE_ACTIONS)


def _is_trivial(prompt: str) -> bool:
    """Return True for empty/whitespace or trivial ack prompts.

    Punctuation markers (/!#) match as true startswith (slash/bang/hash commands).
    Word tokens match only when the prompt equals the token or starts with the
    token followed by whitespace or punctuation, preventing false positives like
    "google…" matching "go" or "normally…" matching "no".
    """
    lower = prompt.lower().strip()
    if not lower:
        return True
    for marker in PUNCT_PREFIXES:
        if lower.startswith(marker):
            return True
    for tok in TRIVIAL_WORDS:
        if lower == tok or lower.startswith(tok + " ") or lower.startswith(tok + ",") or lower.startswith(tok + "."):
            return True
    return False


def _extract_prompt(hook_input: dict) -> str:
    """Extract the user prompt from supported hook payload shapes."""
    prompt = hook_input.get("prompt")
    if isinstance(prompt, str):
        return prompt

    prompt = hook_input.get("user_prompt")
    if isinstance(prompt, str):
        return prompt

    msg = hook_input.get("message")
    if isinstance(msg, dict):
        content = msg.get("content", "")
        return content if isinstance(content, str) else ""
    if isinstance(msg, str):
        return msg
    return ""


def main():
    hook_input = read_hook_input()

    prompt = _extract_prompt(hook_input)
    if not prompt:
        sys.exit(0)

    # Truly trivial prompts: emit nothing (don't spam the save policy on "ok" etc.)
    if _is_trivial(prompt):
        sys.exit(0)

    prompt_type = should_check_brain(prompt)
    if not prompt_type:
        # Substantive prompt but not a brain-query/implementation — still inject the
        # save policy so every non-trivial turn carries the Pi-parity save nudge.
        _emit(hook_input, "")
        return

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
            _emit(hook_input, "")
            return

        if should_suggest_local_brain_service(prompt) and local_brain_service_available():
            reminder = (
                "BRAIN CHECK: Before answering, use the Local Brain service for supported brain workflows.\n\n"
                f"{local_brain_service_instructions()}\n\n"
                "For this prompt, prefer the service-backed query command shown above "
                "before falling back to local file search."
            )
        else:
            vault_names = list(vaults.keys())
            query_ref = claude_form("/fritz:brain-query") if _resolve_client_agent() == "claude" else "/fritz:brain-query"
            reminder = (
                "BRAIN CHECK: Before answering, search the knowledge base. "
                f"Vaults: {', '.join(vault_names)}. "
                f"Use {query_ref} or search knowledge/ directories and "
                "~/.brain/capture/daily/ for relevant prior decisions, patterns, and facts."
            )
            if not local_brain_service_configured() and should_suggest_local_brain_service(prompt):
                reminder = f"{reminder}\n\n{local_brain_configuration_decision_prompt()}"
            elif local_brain_setup_suggestions_enabled() and should_suggest_local_brain_service(prompt):
                reminder = f"{reminder}\n\n{local_brain_setup_suggestion()}"

        _emit(hook_input, reminder)

    # Level: light or full — search and inject file paths
    if should_suggest_local_brain_service(prompt) and local_brain_service_available():
        injection = (
            f"{local_brain_service_instructions()}\n\n"
            "BRAIN CHECK: Use the service-backed query path for this prompt before local file search. "
            "If the service returns insufficient results, then fall back to local knowledge files."
        )
        _emit(hook_input, injection)

    max_chars = get_max_injection_chars(fritz_local)
    file_limit = 25 if level == "full" else 10
    project_name = fritz_local.get("project") if fritz_local else None
    keywords = extract_keywords(prompt)

    injection = ""
    if vault_path:
        manifest = load_manifest(vault_path)
        if manifest and keywords:
            injection = search_knowledge_files(vault_path, manifest, keywords, project_name, max_chars, file_limit)

    if not injection:
        # Even in light/full, fall back to basic reminder if no matches
        injection = (
            "BRAIN CHECK: No direct keyword matches found in knowledge base, "
            "but relevant knowledge may exist. Check feedback/ and decisions/ "
            "for this project before proceeding."
        )

    if not local_brain_service_configured() and should_suggest_local_brain_service(prompt):
        injection = f"{injection}\n\n{local_brain_configuration_decision_prompt()}"
    elif local_brain_setup_suggestions_enabled() and should_suggest_local_brain_service(prompt):
        injection = f"{injection}\n\n{local_brain_setup_suggestion()}"

    _emit(hook_input, injection)


if __name__ == "__main__":
    main()
