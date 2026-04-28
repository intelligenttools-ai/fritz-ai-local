#!/usr/bin/env python3
"""Hermes shell-hook wrapper for Fritz Local session capture.

Hermes shell hooks do not pass a transcript_path. Fritz Local's brain_capture
hook requires one, so this wrapper resolves the current Hermes JSONL transcript
from the hook session_id (or the newest session file as a fallback), then invokes
upstream brain_capture with Hermes detection markers.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

BRAIN_CAPTURE = Path.home() / ".brain" / "hooks" / "brain_capture.py"
DEFAULT_HERMES_HOME = Path.home() / ".hermes"


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", str(DEFAULT_HERMES_HOME))).expanduser()


def _candidate_transcripts(session_id: str) -> list[Path]:
    sessions = _hermes_home() / "sessions"
    if not sessions.exists():
        return []
    candidates: list[Path] = []
    if session_id:
        # Session IDs are commonly either the JSONL stem or embedded in it.
        for pattern in (f"{session_id}.jsonl", f"*{session_id}*.jsonl"):
            candidates.extend(sessions.glob(pattern))
    else:
        # Without a session id, fall back only when the session directory is
        # unambiguous. Guessing the newest transcript in a concurrent profile can
        # capture the wrong conversation.
        all_transcripts = list(sessions.glob("*.jsonl"))
        if len(all_transcripts) == 1:
            candidates.extend(all_transcripts)
    return sorted(set(candidates), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}

    session_id = str(payload.get("session_id") or "")
    transcripts = _candidate_transcripts(session_id)
    if not transcripts:
        return 0

    transcript = transcripts[0]
    payload.update({
        "event_type": "hermes",
        "hook_event_name": payload.get("hook_event_name") or "on_session_finalize",
        "transcript_path": str(transcript),
        "cwd": payload.get("cwd") or os.getcwd(),
    })

    # Avoid duplicate capture spam if Hermes fires finalize more than once for
    # the same transcript in a short interval.
    stamp_dir = Path.home() / ".brain" / ".capture-stamps"
    stamp_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp_dir / (transcript.stem + ".stamp")
    now = time.time()
    if stamp.exists():
        try:
            if now - float(stamp.read_text().strip()) < 60:
                return 0
        except (ValueError, OSError):
            pass
    stamp.write_text(str(now))

    subprocess.run(
        [sys.executable, str(BRAIN_CAPTURE)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=20,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
