"""Claude Code transcript adapter.

Parses Claude Code's JSONL transcript format:
  {"type": "user"|"assistant", "message": {"role": "...", "content": "..."|[blocks]}}
"""

import json
from pathlib import Path

from .base import TranscriptAdapter, CaptureEntry


class ClaudeCodeAdapter(TranscriptAdapter):

    agent_name = "claude-code"

    def parse(self, transcript_path: Path, max_messages: int = 200) -> CaptureEntry:
        if not transcript_path.exists():
            return CaptureEntry(agent=self.agent_name)

        messages = []
        try:
            with open(transcript_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return CaptureEntry(agent=self.agent_name)

        if not messages:
            return CaptureEntry(agent=self.agent_name)

        recent = messages[-max_messages:]

        topics = []
        key_responses = []
        tools_used = set()

        for msg in recent:
            msg_type = msg.get("type", "")
            if msg_type not in ("user", "assistant"):
                continue

            # Content is nested: msg["message"]["content"]
            inner = msg.get("message", {})
            if not inner:
                inner = msg
            role = inner.get("role", msg_type)
            content = inner.get("content", "")

            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            tools_used.add(block.get("name", "unknown"))
                content = "\n".join(text_parts)

            if not isinstance(content, str) or not content.strip():
                continue

            if role == "user" or msg_type == "user":
                first_line = content.strip().split("\n")[0][:200]
                # Skip system tags, command outputs, tool results
                if first_line and not first_line.startswith("<"):
                    topics.append(first_line)
            elif role == "assistant" or msg_type == "assistant":
                for line in content.strip().split("\n"):
                    line = line.strip()
                    if line and not line.startswith("<") and len(line) > 20:
                        key_responses.append(line[:200])
                        break

        return CaptureEntry(
            topics=topics[:20],
            key_responses=key_responses[:10],
            tools_used=tools_used,
            agent=self.agent_name,
        )
