"""Codex CLI transcript adapter.

Parses Codex's rollout JSONL session format
(``~/.codex/sessions/**/rollout-*.jsonl``). Each line is
``{"timestamp","type","payload"}`` where ``type`` is ``session_meta`` |
``event_msg`` | ``response_item``. User/assistant turns are ``response_item``
messages whose ``payload.content`` is a list of ``{type,text}`` blocks
(``input_text`` for user/developer, ``output_text`` for assistant); tool calls
are ``response_item`` items whose ``payload.type`` ends in ``_call``.

Rollouts can be very large (tens of MB) and interleave thousands of
reasoning/call/event records around a handful of messages, so we STREAM the file
line by line and retain only bounded deques of the extracted message candidates
(never the raw rows). Every field is defensively typed — a Stop hook must never
crash on, or OOM from, a malformed or huge rollout.
"""

import json
from collections import deque
from pathlib import Path

from .base import TranscriptAdapter, CaptureEntry

_TEXT_BLOCK_TYPES = ("input_text", "output_text", "text")
_MAX_TOOLS = 50


class CodexAdapter(TranscriptAdapter):

    agent_name = "codex"

    def parse(self, transcript_path: Path, max_messages: int = 200) -> CaptureEntry:
        if not transcript_path.exists():
            return CaptureEntry(agent=self.agent_name)

        bound = max(1, max_messages)
        topics = deque(maxlen=bound)          # user first-lines
        responses = deque(maxlen=bound)       # (phase, first substantive line)
        tools_used = set()

        try:
            with open(transcript_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):  # e.g. a bare [] or scalar line
                        continue
                    payload = row.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    ptype = payload.get("type", "")
                    if not isinstance(ptype, str):
                        continue

                    if ptype.endswith("_call"):
                        name = payload.get("name")
                        if isinstance(name, str) and name:
                            if len(tools_used) < _MAX_TOOLS:
                                tools_used.add(name)
                        elif ptype != "function_call" and len(tools_used) < _MAX_TOOLS:
                            tools_used.add(ptype)  # label the unnamed *_call variants
                        continue

                    if ptype != "message":
                        continue

                    role = payload.get("role", "")
                    content = payload.get("content", [])
                    parts = []
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") in _TEXT_BLOCK_TYPES:
                                text = block.get("text")
                                if isinstance(text, str):
                                    parts.append(text)
                    elif isinstance(content, str):
                        parts.append(content)
                    text = "\n".join(p for p in parts if p).strip()
                    if not text:
                        continue

                    if role == "user":
                        first_line = text.split("\n")[0][:200]
                        if first_line and not first_line.startswith("<"):  # skip system/developer tags
                            topics.append(first_line)
                    elif role == "assistant":
                        phase = payload.get("phase", "")
                        line_out = _first_substantive_line(text)
                        if line_out:
                            responses.append((phase if isinstance(phase, str) else "", line_out))
        except OSError:
            return CaptureEntry(agent=self.agent_name)

        # Prefer per-turn final answers over intermediate "commentary" chatter;
        # fall back to all assistant messages when phase is absent. Keep newest.
        finals = [line for (phase, line) in responses if phase == "final_answer"]
        chosen = finals if finals else [line for (_, line) in responses]

        return CaptureEntry(
            topics=list(topics)[-20:],
            key_responses=chosen[-10:],
            tools_used=tools_used,
            agent=self.agent_name,
        )


def _first_substantive_line(text: str) -> str:
    for line in text.split("\n"):
        line = line.strip()
        if line and not line.startswith("<") and len(line) > 20:
            return line[:200]
    return ""
