"""Adapter registry — selects the right parser for the running agent."""

from pathlib import Path

from .base import TranscriptAdapter, CaptureEntry
from .claude_code import ClaudeCodeAdapter
from .codex import CodexAdapter
from .gemini import GeminiAdapter
from .hermes import HermesAdapter
from .pi_agent import PiAgentAdapter


ADAPTERS: dict[str, TranscriptAdapter] = {
    "claude_code": ClaudeCodeAdapter(),
    "codex": CodexAdapter(),
    "gemini": GeminiAdapter(),
    "hermes": HermesAdapter(),
    "pi": PiAgentAdapter(),
}


def get_adapter(hook_input: dict) -> TranscriptAdapter:
    """Detect the running agent and return the appropriate adapter.

    Raises KeyError if no known agent is detected — the caller must handle
    fallback explicitly (or not).  No implicit default.
    """
    agent = TranscriptAdapter.detect(hook_input)
    return ADAPTERS[agent]  # KeyError if unknown — caller decides fallback


def parse_transcript(hook_input: dict, transcript_path: str, max_messages: int = 200) -> CaptureEntry:
    """Detect agent, parse transcript, return normalized capture."""
    adapter = get_adapter(hook_input)
    path = Path(transcript_path)
    entry = adapter.parse(path, max_messages=max_messages)
    entry.cwd = hook_input.get("cwd", "")
    return entry
