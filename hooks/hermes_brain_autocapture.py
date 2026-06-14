#!/usr/bin/env python3
"""Hermes shell-hook wrapper for Fritz Local durable auto-capture (C4).

Hermes fires ``on_session_finalize`` at the end of a session. The companion
``hermes_brain_capture.py`` wrapper handles the C5 *daily* capture on that same
event; this wrapper handles the C4 *durable auto-capture* — writing a single
inbox fact (plus a ``.seen`` dedup marker) when the session carried a durable
access/credential/server signal together with an explicit save/remember intent.

Single source of truth:

  * Transcript resolution is shared with the daily-capture wrapper via
    :func:`hermes_brain_capture.resolve_transcript` (same ``$HERMES_HOME/sessions``
    lookup, honoring a non-default ``HERMES_HOME``).
  * Transcript → text flattening reuses the Hermes adapter through
    :func:`adapters.registry.parse_transcript` — the same mechanism
    ``brain_capture.py`` and the #65 ``brain_autocapture_hook.py`` bridge use.
  * The durable-signal/save-intent detection and the SHA-256 ``.seen`` dedup
    live unchanged in :func:`brain_autocapture.maybe_auto_capture`.

This wrapper writes ONLY the auto-capture inbox fact + ``.seen`` marker. It does
NOT run a daily capture (that is ``hermes_brain_capture.py``'s job, wired
separately on the same ``on_session_finalize`` event), so there is no
double daily-write. Brain root resolution honors ``$BRAIN_HOME`` (else
``~/.brain``) via ``maybe_auto_capture``, keeping tests off the live brain.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_this_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_this_dir))
sys.path.insert(0, str(_this_dir.parent))

from brain_autocapture import maybe_auto_capture  # noqa: E402
from hermes_brain_capture import resolve_transcript  # noqa: E402
from adapters.registry import parse_transcript  # noqa: E402
from adapters.base import CaptureEntry  # noqa: E402


def _flatten_entry(entry: CaptureEntry) -> str:
    """Flatten a parsed CaptureEntry into a single text blob for matching.

    Mirrors ``brain_autocapture_hook._flatten_entry``: joins user topics,
    notable assistant responses, and tool names so the auto-capture regexes see
    the same durable signal the transcript carried.
    """
    parts: list[str] = []
    parts.extend(entry.topics)
    parts.extend(entry.key_responses)
    if entry.tools_used:
        parts.append(" ".join(sorted(entry.tools_used)))
    return "\n".join(p for p in parts if p)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}

    session_id = str(payload.get("session_id") or "")
    transcript = resolve_transcript(session_id)
    if transcript is None:
        print("No auto-capture (no Hermes transcript resolved).")
        return 0

    # Force Hermes detection so the registry selects the Hermes adapter even when
    # the finalize payload carries no event marker of its own.
    payload.setdefault("event_type", "hermes")
    cwd = payload.get("cwd") or os.getcwd()

    entry = parse_transcript(payload, str(transcript))
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
