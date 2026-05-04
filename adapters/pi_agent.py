"""Pi (pi-coding-agent) transcript adapter.

Parses pi's JSONL session format with tree structure:
  {"type":"session","version":3,"id":"...","timestamp":"...","cwd":"/path"}
  {"type":"message","id":"...","parentId":"...","timestamp":"...","message":{"role":"user|assistant","content":[...]}}

Session files are stored in ~/.pi/agent/sessions/<cwd>/YYYY-MM-DDTHH-mm-ss_XXXX.jsonl
"""

import json
from pathlib import Path

from .base import TranscriptAdapter, CaptureEntry


class PiAgentAdapter(TranscriptAdapter):

    agent_name = "pi"

    def parse(self, transcript_path: Path, max_messages: int = 200) -> CaptureEntry:
        if not transcript_path.exists():
            return CaptureEntry(agent=self.agent_name)

        messages = []
        session_cwd = ""
        try:
            with open(transcript_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        msg_type = entry.get("type", "")
                        if msg_type == "session":
                            session_cwd = entry.get("cwd", "")
                        elif msg_type == "message":
                            messages.append(entry)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return CaptureEntry(agent=self.agent_name)

        if not messages:
            return CaptureEntry(agent=self.agent_name, cwd=session_cwd)

        recent = messages[-max_messages:]

        topics = []
        key_responses = []
        tools_used = set()

        for msg in recent:
            inner = msg.get("message", {})
            if not inner:
                continue

            role = inner.get("role", "")
            content_blocks = inner.get("content", [])

            if not isinstance(content_blocks, list):
                continue

            # Extract text and tools from content blocks
            user_text_parts = []
            assistant_text_parts = []

            for block in content_blocks:
                if not isinstance(block, dict):
                    continue

                block_type = block.get("type", "")

                if role == "user":
                    # User messages: extract text content
                    if block_type == "text" and "text" in block:
                        user_text_parts.append(block["text"])

                elif role == "assistant":
                    # Assistant messages: extract text and tool calls
                    if block_type == "text" and "text" in block:
                        assistant_text_parts.append(block["text"])
                    elif block_type == "toolCall":
                        tool_name = block.get("name", "")
                        if tool_name:
                            tools_used.add(tool_name)

            user_text = "\n".join(user_text_parts).strip()
            assistant_text = "\n".join(assistant_text_parts).strip()

            # Extract topics from user messages
            if role == "user" and user_text:
                first_line = user_text.split("\n")[0][:200]
                if first_line and not first_line.startswith("<"):
                    topics.append(first_line)

            # Extract key responses from assistant messages
            if role == "assistant" and assistant_text:
                for line in assistant_text.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("<") and len(line) > 20:
                        key_responses.append(line[:200])
                        break

        return CaptureEntry(
            topics=topics[:20],
            key_responses=key_responses[:10],
            tools_used=tools_used,
            agent=self.agent_name,
            cwd=session_cwd,
        )
