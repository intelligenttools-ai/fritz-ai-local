"""Codex CLI transcript adapter.

TODO: Implement once Codex transcript format is known.
The agent can generate this adapter itself during setup — it knows its own format.
"""

from pathlib import Path
from .base import TranscriptAdapter, CaptureEntry


class CodexAdapter(TranscriptAdapter):

    agent_name = "codex"

    def parse(self, transcript_path: Path, max_messages: int = 200) -> CaptureEntry:
        # TODO: Parse Codex transcript format
        # Expected location: ~/.codex/ session files
        # The Codex agent should implement this during /brain-setup
        return CaptureEntry(agent=self.agent_name)
