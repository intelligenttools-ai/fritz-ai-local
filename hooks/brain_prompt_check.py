#!/usr/bin/env python3
"""Brain-first enforcement hook.

Fires on UserPromptSubmit. Injects a reminder to check the brain knowledge base
before answering questions or making decisions that the brain might already have
knowledge about.

Self-contained — does not depend on any external hook system.

Works with:
- Claude Code: UserPromptSubmit event
- Codex: UserPromptSubmit event
- Gemini CLI: BeforeAgent event
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brain_common import read_hook_input, load_registry, BRAIN_HOME


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

# Skip enforcement for these (commands, short inputs, etc.)
SKIP_PREFIXES = [
    "/", "!", "#", "yes", "no", "ok", "continue", "go ahead",
    "commit", "push", "merge", "deploy",
]


def should_check_brain(prompt: str) -> bool:
    """Determine if this prompt would benefit from a brain check."""
    lower = prompt.lower().strip()

    # Skip short inputs, commands, confirmations
    if len(lower) < 15:
        return False
    for prefix in SKIP_PREFIXES:
        if lower.startswith(prefix):
            return False

    # Check for query signals
    for signal in QUERY_SIGNALS:
        if signal in lower:
            return True

    return False


def main():
    hook_input = read_hook_input()

    # Extract user prompt — format varies by agent
    prompt = ""
    if "user_prompt" in hook_input:
        prompt = hook_input["user_prompt"]
    elif "message" in hook_input:
        msg = hook_input["message"]
        if isinstance(msg, dict):
            prompt = msg.get("content", "")
        elif isinstance(msg, str):
            prompt = msg

    if not prompt or not should_check_brain(prompt):
        sys.exit(0)

    # Check if there's actually knowledge to search
    registry = load_registry()
    vaults = registry.get("vaults", {})
    capture_dir = BRAIN_HOME / "capture" / "daily"
    has_captures = capture_dir.exists() and any(capture_dir.glob("*.md"))

    has_knowledge = False
    for name, config in vaults.items():
        vault_path = Path(config["path"]).expanduser()
        manifest_path = vault_path / ".brain" / "manifest.yaml"
        if manifest_path.exists():
            has_knowledge = True
            break

    if not has_knowledge and not has_captures:
        sys.exit(0)

    # Build the reminder
    vault_names = [n for n in vaults.keys()]
    reminder = (
        "BRAIN CHECK: Before answering, search the knowledge base. "
        f"Vaults: {', '.join(vault_names)}. "
        "Use /fritz:brain-query or search knowledge/ directories and ~/.brain/capture/daily/ for relevant prior decisions, patterns, and facts."
    )

    response = {
        "hookSpecificOutput": {
            "hookEventName": hook_input.get("hook_event_name", "UserPromptSubmit"),
            "additionalContext": reminder,
        }
    }
    print(json.dumps(response))
    sys.exit(0)


if __name__ == "__main__":
    main()
