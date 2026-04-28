#!/usr/bin/env python3
"""Hermes shell-hook wrapper for Fritz Local context injection.

Hermes shell hooks expect stdout shaped as {"context": "..."}. Fritz Local's
agent-agnostic session-start hook emits Claude-style hookSpecificOutput, so
this adapter translates it without changing upstream Fritz files.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOK = Path.home() / ".brain" / "hooks" / "brain_session_start.py"


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    payload.setdefault("event_type", "pre_llm_call")
    payload.setdefault("hook_event_name", "SessionStart")

    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=8,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return 0
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return 0
    context = (
        data.get("context")
        or data.get("hookSpecificOutput", {}).get("additionalContext")
        or ""
    )
    if context:
        print(json.dumps({"context": context}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
