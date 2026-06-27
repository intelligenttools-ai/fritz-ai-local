#!/usr/bin/env python3
"""Stop-event auto-capture bridge for hook-input JSON runtimes (issue #65).

Thin adapter script — mirrors the ``hermes_brain_capture.py`` pattern. It does
NOT reimplement any auto-capture logic: the authoritative durable-signal +
save-intent detection, the SHA-256 ``.seen`` dedup, and the inbox write all
live in :mod:`brain_autocapture` (``maybe_auto_capture``). This script only
bridges a Claude-style ``Stop`` hook-input JSON payload to that function.

The problem it solves: :mod:`brain_autocapture` (and the Pi role model) expect
RAW transcript text on stdin, built from the agent's in-memory messages. A
Claude Code ``Stop`` hook instead delivers a hook-input JSON object on stdin —
``{cwd, hook_event_name: "Stop", transcript_path, ...}`` — with no message text.
Piping that JSON straight into ``brain_autocapture`` would mis-parse the hook
envelope as "transcript text".

So this bridge:

  1. Reads the hook-input JSON from stdin (degrading to ``{}`` on parse error).
  2. Resolves ``transcript_path`` and flattens the transcript into text via
     :func:`adapters.registry.parse_transcript` — the SAME mechanism
     ``brain_capture.py`` uses — so the transcript format handling stays a
     single source of truth in the adapter layer.
  3. Runs :func:`brain_autocapture.maybe_auto_capture` on the flattened text,
     reusing its detection/dedup unchanged.

It writes ONLY the auto-capture inbox fact + ``.seen`` marker. It never writes
a daily capture (that is ``brain_capture.py``'s job, wired separately on the
same ``Stop`` event), so there is no double-write.

Brain root resolution honors ``$BRAIN_HOME`` (else ``~/.brain``) via
:func:`brain_autocapture.maybe_auto_capture`, keeping tests off the live brain.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_this_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_this_dir))
sys.path.insert(0, str(_this_dir.parent))

from brain_bootstrap import ensure_yaml_interpreter  # noqa: E402

ensure_yaml_interpreter()

from brain_autocapture import maybe_auto_capture  # noqa: E402
from adapters.registry import ADAPTERS, parse_transcript  # noqa: E402
from adapters.base import CaptureEntry  # noqa: E402


def _read_hook_input() -> dict:
    """Read hook-input JSON from stdin, degrading to ``{}`` on any error."""
    try:
        raw = sys.stdin.read()
    except OSError:
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _flatten_entry(entry: CaptureEntry) -> str:
    """Flatten a parsed CaptureEntry into a single text blob for matching.

    Joins the user topics, the notable assistant responses, and the tool names
    so the auto-capture regexes (git URLs, credentials, "server is", save
    intent, ...) see the same durable signal the transcript carried. This reuses
    the adapter's transcript parsing rather than re-reading the file format.
    """
    parts: list[str] = []
    parts.extend(entry.topics)
    parts.extend(entry.key_responses)
    if entry.tools_used:
        parts.append(" ".join(sorted(entry.tools_used)))
    return "\n".join(p for p in parts if p)


def main() -> int:
    hook_input = _read_hook_input()
    transcript_path = hook_input.get("transcript_path", "")
    cwd = hook_input.get("cwd", "") or "."

    if not transcript_path:
        print("No auto-capture (no transcript_path in hook input).")
        return 0

    # Flatten the transcript via the shared adapter layer (same as
    # brain_capture.py). This bridge is wired by the Claude Code plugin, whose
    # Stop hook-input carries no agent marker that TranscriptAdapter.detect can
    # key off, so detection returns "unknown" and parse_transcript raises
    # KeyError. In that case fall back to the Claude Code adapter directly (the
    # transcript IS Claude JSONL) so we still get real topics/responses to scan.
    try:
        entry = parse_transcript(hook_input, transcript_path)
    except KeyError:
        adapter = ADAPTERS.get("claude_code")
        if adapter is None:
            entry = CaptureEntry(agent="unknown", cwd=cwd)
        else:
            entry = adapter.parse(Path(transcript_path))
            entry.cwd = cwd

    text = _flatten_entry(entry)
    if not text:
        print("No auto-capture (empty transcript).")
        return 0

    result = maybe_auto_capture(text, cwd)
    if result is None:
        print("No auto-capture (no signal/intent or duplicate).")
        return 0
    print(f"Auto-captured to Fritz-Brain: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
