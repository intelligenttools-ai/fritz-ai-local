#!/usr/bin/env python3
"""Brain capture hook — saves conversation summary on every session end or compaction.

Dumb and reliable. Always fires. Writes to ~/.brain/capture/ (global).
Does NOT route to vaults — that's the compile step's job.

Works with:
- Claude Code: PreCompact and Stop events (transcript_path in input)
- Codex: Stop event (transcript_path in input)
- Gemini CLI: PreCompress and SessionEnd events (transcript_path in input)
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# Ensure brain_common is importable regardless of cwd
sys.path.insert(0, str(Path(__file__).resolve().parent))

from brain_common import read_hook_input, today_str, BRAIN_HOME


MAX_MESSAGES = 200
MAX_SUMMARY_CHARS = 8000
CAPTURE_DIR = BRAIN_HOME / "capture" / "daily"


def extract_transcript_summary(transcript_path: str, cwd: str = "") -> str | None:
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

    recent = messages[-MAX_MESSAGES:]

    user_messages = []
    assistant_summaries = []
    tool_uses = set()

    for msg in recent:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        tool_uses.add(block.get("name", "unknown"))
            content = "\n".join(text_parts)

        if not isinstance(content, str) or not content.strip():
            continue

        if role == "user":
            first_line = content.strip().split("\n")[0][:200]
            if first_line and not first_line.startswith("<"):
                user_messages.append(first_line)
        elif role == "assistant":
            for line in content.strip().split("\n"):
                line = line.strip()
                if line and not line.startswith("<") and len(line) > 20:
                    assistant_summaries.append(line[:200])
                    break

    # Build summary
    parts = []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts.append(f"## Session {timestamp}")
    if cwd:
        parts.append(f"\n**cwd**: `{cwd}`\n")

    if user_messages:
        parts.append("### Topics\n")
        for msg in user_messages[:20]:
            parts.append(f"- {msg}")
        parts.append("")

    if tool_uses:
        parts.append(f"### Tools used\n")
        parts.append(f"{', '.join(sorted(tool_uses))}\n")

    if assistant_summaries:
        parts.append("### Key responses\n")
        for s in assistant_summaries[:10]:
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

    if not transcript_path:
        sys.exit(0)

    summary = extract_transcript_summary(transcript_path, cwd=cwd)
    if not summary:
        sys.exit(0)

    # Write to global capture directory — always, no vault detection
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    daily_file = CAPTURE_DIR / f"{today_str()}.md"

    if daily_file.exists():
        with open(daily_file, "a") as f:
            f.write(f"\n{summary}\n")
    else:
        header = (
            f"---\n"
            f"type: capture\n"
            f"title: Daily log {today_str()}\n"
            f"created: {today_str()}\n"
            f"---\n\n"
            f"# Daily Log — {today_str()}\n\n"
            f"{summary}\n"
        )
        with open(daily_file, "w") as f:
            f.write(header)

    # Append to global log
    log_file = BRAIN_HOME / "log.md"
    if log_file.exists():
        topic_count = summary.count("- ")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(log_file, "a") as f:
            f.write(f"{timestamp} | CAPTURE | {event} | {topic_count} items from {cwd or 'unknown'}\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
