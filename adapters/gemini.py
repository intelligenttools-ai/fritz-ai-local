"""Gemini CLI transcript adapter.

TODO: Implement once Gemini transcript format is known.
The agent can generate this adapter itself during setup — it knows its own format.
"""

from pathlib import Path
from .base import TranscriptAdapter, CaptureEntry


class GeminiAdapter(TranscriptAdapter):

    agent_name = "gemini"

    def parse(self, transcript_path: Path, max_messages: int = 200) -> CaptureEntry:
        # TODO: Parse Gemini CLI transcript format
        # Expected location: ~/.gemini/ session files
        # The Gemini agent should implement this during /fritz:brain-setup
        return CaptureEntry(agent=self.agent_name)
