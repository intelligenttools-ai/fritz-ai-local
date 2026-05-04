"""Base adapter interface and common capture format."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CaptureEntry:
    """Normalized capture from any agent's session transcript."""
    topics: list[str] = field(default_factory=list)
    key_responses: list[str] = field(default_factory=list)
    tools_used: set[str] = field(default_factory=set)
    cwd: str = ""
    agent: str = "unknown"

    def is_empty(self) -> bool:
        return not self.topics and not self.key_responses


class TranscriptAdapter:
    """Base class for agent-specific transcript parsers.

    Subclass this for each agent. Implement `parse()` to read the agent's
    transcript format and return a CaptureEntry.
    """

    agent_name: str = "unknown"

    def parse(self, transcript_path: Path, max_messages: int = 200) -> CaptureEntry:
        """Parse a transcript file into a CaptureEntry.

        Args:
            transcript_path: Path to the agent's transcript/session file.
            max_messages: Max number of messages to process (from the end).

        Returns:
            CaptureEntry with extracted topics, responses, and tools.
        """
        raise NotImplementedError

    @staticmethod
    def detect(hook_input: dict) -> str:
        """Detect which agent is running from hook input.

        Returns agent identifier string. Override for agent-specific detection.
        """
        # Check for agent-specific markers in hook input
        event = hook_input.get("hook_event_name", "")

        # Gemini uses PreCompress, Claude Code uses PreCompact
        if event == "PreCompress":
            return "gemini"

        # Hermes passes event type differently
        if "event_type" in hook_input:
            return "hermes"

        # Codex uses TOML config, has different field names
        if hook_input.get("permission_mode") and not hook_input.get("hook_event_name"):
            return "codex"

        # Check environment variables
        import os
        if os.environ.get("GEMINI_SESSION_ID"):
            return "gemini"
        if os.environ.get("CODEX_SESSION_ID"):
            return "codex"

        # Path-based detection: transcript or cwd points to pi session storage.
        # This is needed because hooks run as standalone scripts where
        # PI_CODING_AGENT_DIR may not be set.
        import os
        cwd = hook_input.get("cwd", "")
        transcript_path = hook_input.get("transcript_path", "")
        if ".pi/agent/sessions" in cwd or ".pi/agent/sessions" in transcript_path:
            return "pi"

        # No known agent detected — caller must handle fallback explicitly.
        return "unknown"
