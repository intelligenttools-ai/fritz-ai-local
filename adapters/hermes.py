"""Hermes Agent transcript adapter.

Parses Hermes JSONL session transcripts from ``$HERMES_HOME/sessions``.
The adapter is intentionally conservative: it extracts durable session
signals (user topics, assistant summaries, tool names) and ignores raw tool
payloads/reasoning blobs so captures stay compact and low-risk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import TranscriptAdapter, CaptureEntry


class HermesAdapter(TranscriptAdapter):
    agent_name = "hermes"

    def parse(self, transcript_path: Path, max_messages: int = 200) -> CaptureEntry:
        entry = CaptureEntry(agent=self.agent_name)
        if not transcript_path.exists() or not transcript_path.is_file():
            return entry

        records: list[dict[str, Any]] = []
        try:
            with transcript_path.open(errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, dict):
                        records.append(obj)
        except OSError:
            return entry

        if max_messages > 0:
            records = records[-max_messages:]

        for obj in records:
            role = obj.get("role")
            if role == "session_meta":
                continue

            if role == "tool":
                # Hermes tool-result records carry the call id, not always the
                # tool name. Tool names are captured from assistant tool_calls.
                continue

            if role == "assistant":
                for tc in obj.get("tool_calls") or []:
                    if isinstance(tc, dict):
                        fn = tc.get("function") or {}
                        name = fn.get("name") if isinstance(fn, dict) else None
                        if name:
                            entry.tools_used.add(str(name))

                content = _clean_text(obj.get("content"))
                if content:
                    entry.key_responses.append(_first_sentence_or_line(content))
                continue

            if role == "user":
                content = _clean_text(obj.get("content"))
                if content:
                    topic = _summarize_user_prompt(content)
                    if topic:
                        entry.topics.append(topic)

        entry.topics = _dedupe_keep_order(entry.topics)[-20:]
        entry.key_responses = _dedupe_keep_order(entry.key_responses)[-20:]
        return entry


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except TypeError:
            text = str(value)
    # Drop Slack thread-context boilerplate; keep the actual user prompt after it.
    marker = "[End of thread context]"
    if marker in text:
        text = text.split(marker, 1)[1]
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    return text


def _summarize_user_prompt(text: str, limit: int = 220) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _first_sentence_or_line(text: str, limit: int = 260) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    first = lines[0]
    if len(first) <= limit:
        return first
    return first[: limit - 1].rstrip() + "…"


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out
