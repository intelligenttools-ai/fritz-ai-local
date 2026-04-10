"""Hermes Agent transcript adapter.

TODO: Implement once Hermes transcript format is known.
The agent can generate this adapter itself during setup — it knows its own format.
"""

from pathlib import Path
from .base import TranscriptAdapter, CaptureEntry


class HermesAdapter(TranscriptAdapter):

    agent_name = "hermes"

    def parse(self, transcript_path: Path, max_messages: int = 200) -> CaptureEntry:
        # TODO: Parse Hermes Agent transcript format
        # Expected location: ~/.hermes/ session files
        # The Hermes agent should implement this during /fritz:brain-setup
        return CaptureEntry(agent=self.agent_name)
