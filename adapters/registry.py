"""Adapter registry — selects the right parser for the running agent."""

from pathlib import Path

from .base import TranscriptAdapter, CaptureEntry
from .claude_code import ClaudeCodeAdapter
from .codex import CodexAdapter
from .gemini import GeminiAdapter
from .hermes import HermesAdapter


ADAPTERS: dict[str, TranscriptAdapter] = {
    "claude_code": ClaudeCodeAdapter(),
    "codex": CodexAdapter(),
    "gemini": GeminiAdapter(),
    "hermes": HermesAdapter(),
}


def get_adapter(hook_input: dict) -> TranscriptAdapter:
    """Detect the running agent and return the appropriate adapter."""
    agent = TranscriptAdapter.detect(hook_input)
    return ADAPTERS.get(agent, ClaudeCodeAdapter())


def parse_transcript(hook_input: dict, transcript_path: str, max_messages: int = 200) -> CaptureEntry:
    """Detect agent, parse transcript, return normalized capture."""
    adapter = get_adapter(hook_input)
    path = Path(transcript_path)
    entry = adapter.parse(path, max_messages=max_messages)
    entry.cwd = hook_input.get("cwd", "")
    return entry
