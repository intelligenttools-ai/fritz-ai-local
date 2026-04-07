#!/usr/bin/env python3
"""Brain capture hook — saves conversation summary before session end or compaction.

Reads the transcript JSONL file, extracts key exchanges, and writes a summary
to the vault's daily capture log.

Works with:
- Claude Code: PreCompact and Stop events (transcript_path in input)
- Codex: Stop event (transcript_path in input)
- Gemini CLI: PreCompress and SessionEnd events (transcript_path in input)

Does NOT use an external LLM for summarization — that's the flush/compile step (Phase 3).
This hook does lightweight extraction: last N messages, key decisions, file changes.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# Ensure brain_common is importable regardless of cwd
sys.path.insert(0, str(Path(__file__).resolve().parent))

from brain_common import (
    read_hook_input,
    find_vault_for_cwd,
    load_manifest,
    resolve_path,
    append_log,
    today_str,
)


MAX_MESSAGES = 200  # Max transcript lines to process
MAX_SUMMARY_CHARS = 8000  # Cap summary length


def extract_transcript_summary(transcript_path: str) -> str | None:
    """Read the JSONL transcript and extract a structured summary."""
    path = Path(transcript_path)
    if not path.exists():
        return None

    messages = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return None

    if not messages:
        return None

    # Take last N messages to avoid processing huge transcripts
    recent = messages[-MAX_MESSAGES:]

    # Extract key information
    user_messages = []
    assistant_summaries = []
    tool_uses = set()

    for msg in recent:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if isinstance(content, list):
            # Handle structured content blocks
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        tool_uses.add(block.get("name", "unknown"))
                    elif block.get("type") == "tool_result":
                        pass  # Skip tool results for summary
            content = "\n".join(text_parts)

        if not isinstance(content, str) or not content.strip():
            continue

        if role == "user":
            # Capture first line of user messages as topics
            first_line = content.strip().split("\n")[0][:200]
            if first_line and not first_line.startswith("<"):  # Skip system tags
                user_messages.append(first_line)
        elif role == "assistant":
            # Capture first meaningful line of assistant responses
            for line in content.strip().split("\n"):
                line = line.strip()
                if line and not line.startswith("<") and len(line) > 20:
                    assistant_summaries.append(line[:200])
                    break

    # Build summary
    parts = []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts.append(f"## Session {timestamp}\n")

    if user_messages:
        parts.append("### Topics discussed\n")
        for msg in user_messages[:20]:  # Cap at 20 topics
            parts.append(f"- {msg}")
        parts.append("")

    if tool_uses:
        parts.append(f"### Tools used\n")
        parts.append(f"{', '.join(sorted(tool_uses))}\n")

    if assistant_summaries:
        parts.append("### Key responses\n")
        for s in assistant_summaries[:10]:  # Cap at 10
            parts.append(f"- {s}")
        parts.append("")

    summary = "\n".join(parts)
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[:MAX_SUMMARY_CHARS] + "\n\n[... truncated ...]"

    return summary


def main():
    hook_input = read_hook_input()
    cwd = hook_input.get("cwd", "")
    transcript_path = hook_input.get("transcript_path", "")
    event = hook_input.get("hook_event_name", "")
    agent = "claude-code"  # Default; could be detected from event patterns

    if not cwd:
        sys.exit(0)

    vault_name, vault_config, vault_path = find_vault_for_cwd(cwd)
    if not vault_path:
        sys.exit(0)

    manifest = load_manifest(vault_path)
    if not manifest:
        sys.exit(0)

    # Extract summary from transcript
    summary = None
    if transcript_path:
        summary = extract_transcript_summary(transcript_path)

    if not summary:
        sys.exit(0)

    # Write to daily capture log
    daily_dir = resolve_path(vault_path, manifest, "capture_daily")
    if not daily_dir:
        # Fallback to .brain/capture/sessions/
        daily_dir = resolve_path(vault_path, manifest, "capture_sessions")
    if not daily_dir:
        daily_dir = vault_path / ".brain" / "capture" / "sessions"

    daily_dir.mkdir(parents=True, exist_ok=True)
    daily_file = daily_dir / f"{today_str()}.md"

    # Create or append to daily file
    if daily_file.exists():
        with open(daily_file, "a") as f:
            f.write(f"\n{summary}\n")
    else:
        header = f"---\ntype: capture\ntitle: Daily log {today_str()}\ndomain: {manifest.get('domain', 'unknown')}\ncreated: {today_str()}\nupdated: {today_str()}\nagent_last_edit: {agent}\n---\n\n# Daily Log — {today_str()}\n\n{summary}\n"
        with open(daily_file, "w") as f:
            f.write(header)

    # Append to operations log
    topic_count = summary.count("- ")
    append_log(vault_path, "CAPTURE", agent, f"{event}: {topic_count} items captured to {daily_file.name}")

    # Allow the operation to proceed (exit 0)
    sys.exit(0)


if __name__ == "__main__":
    main()
