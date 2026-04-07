#!/usr/bin/env python3
"""Brain capture hook — saves conversation summary on every session end or compaction.

Dumb and reliable. Always fires. Writes to ~/.brain/capture/ (global).
Uses adapter layer to parse agent-specific transcript formats.

Works with:
- Claude Code: PreCompact and Stop events
- Codex: Stop event
- Gemini CLI: PreCompress and SessionEnd events
- Hermes Agent: session:end event
"""

import sys
from datetime import datetime
from pathlib import Path

# Ensure imports work regardless of cwd
_this_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_this_dir))
sys.path.insert(0, str(_this_dir.parent))

from brain_common import read_hook_input, today_str, BRAIN_HOME
from adapters.registry import parse_transcript


MAX_SUMMARY_CHARS = 8000
CAPTURE_DIR = BRAIN_HOME / "capture" / "daily"


def format_capture(entry) -> str:
    """Format a CaptureEntry into markdown."""
    parts = []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts.append(f"## Session {timestamp}")
    if entry.cwd:
        parts.append(f"\n**cwd**: `{entry.cwd}`")
    parts.append(f"**agent**: {entry.agent}\n")

    if entry.topics:
        parts.append("### Topics\n")
        for topic in entry.topics:
            parts.append(f"- {topic}")
        parts.append("")

    if entry.tools_used:
        parts.append("### Tools used\n")
        parts.append(f"{', '.join(sorted(entry.tools_used))}\n")

    if entry.key_responses:
        parts.append("### Key responses\n")
        for resp in entry.key_responses:
            parts.append(f"- {resp}")
        parts.append("")

    summary = "\n".join(parts)
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[:MAX_SUMMARY_CHARS] + "\n\n[... truncated ...]"
    return summary


def main():
    hook_input = read_hook_input()
    transcript_path = hook_input.get("transcript_path", "")
    event = hook_input.get("hook_event_name", "")

    if not transcript_path:
        sys.exit(0)

    # Parse transcript using the adapter for the detected agent
    entry = parse_transcript(hook_input, transcript_path)

    if entry.is_empty():
        sys.exit(0)

    summary = format_capture(entry)

    # Write to global capture directory
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
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(log_file, "a") as f:
            f.write(f"{timestamp} | CAPTURE | {entry.agent} | {event}: {len(entry.topics)} topics from {entry.cwd or 'unknown'}\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
