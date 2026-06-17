#!/usr/bin/env python3
"""Auto-capture durable session knowledge into Fritz-Brain.

Behavior-identical port of the Pi extension's ``maybeAutoCapture`` (see
``bindings/pi/index.ts``). Available both as an importable function
(:func:`maybe_auto_capture`) and as a CLI that reads transcript text from
stdin (or one or more file/positional args).

A capture is written only when BOTH a durable-signal pattern AND a save-intent
pattern match the text (case-insensitive). Captures are de-duplicated by the
first 16 hex chars of ``sha256(text)``: a ``<hash>.seen`` marker is written
under ``capture/auto/`` and a second run on the same text is a no-op.

Brain root resolution mirrors :mod:`brain_save_fact` (``$BRAIN_HOME`` env var,
falling back to ``~/.brain``); callers/tests may pass an explicit ``root``.
"""

from __future__ import annotations

import hashlib
import re
import sys
import time
from pathlib import Path

try:
    from brain_save_fact import (
        _ensure_private_dir,
        _write_private_file,
        brain_home,
        save_fact,
    )
except ImportError:  # pragma: no cover - support package-style import
    from hooks.brain_save_fact import (  # type: ignore
        _ensure_private_dir,
        _write_private_file,
        brain_home,
        save_fact,
    )


DURABLE_SIGNAL_RE = re.compile(
    r"https?://git\.|forgejo|gitea|gitlab|github pat|api[- ]?token|"
    r"access token|server is|token location|credential|recovery code",
    re.IGNORECASE,
)
SAVE_INTENT_RE = re.compile(
    r"remember|save|ingest|brain|future session|other sessions|so that.*know",
    re.IGNORECASE,
)
SENSITIVE_RE = re.compile(
    r"token|credential|secret|password|pat|api[- ]?key", re.IGNORECASE
)


def maybe_auto_capture(text: str, cwd: str, root: Path | None = None) -> Path | None:
    """Auto-capture ``text`` if it carries durable signal + save intent.

    Returns the path to the written inbox file, or ``None`` if nothing was
    written (no signal, no intent, or already-seen duplicate).
    """
    if root is None:
        root = brain_home()

    # The extension keeps the last 20000 chars of the joined transcript.
    text = text[-20000:]
    lower = text.lower()

    if not DURABLE_SIGNAL_RE.search(lower) or not SAVE_INTENT_RE.search(lower):
        return None

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    _ensure_private_dir(root)
    _ensure_private_dir(root / "capture")
    auto_dir = root / "capture" / "auto"
    _ensure_private_dir(auto_dir)

    marker = auto_dir / f"{digest}.seen"
    if marker.exists():
        return None
    _write_private_file(marker, str(int(time.time() * 1000)))

    body = "\n".join(
        [
            "Automatically captured by the Pi Fritz-Brain extension because the "
            "session contained durable access/credential/server knowledge and an "
            "explicit save/remember/Brain signal.",
            "",
            f"cwd: `{cwd}`",
            "",
            "## Relevant transcript excerpt",
            "",
            "```text",
            text[-12000:],
            "```",
        ]
    )

    return save_fact(
        title="Auto-captured durable session knowledge",
        body=body,
        source="pi-agent-end:auto-capture",
        sensitive=bool(SENSITIVE_RE.search(text)),
        tags=["FritzBrain", "AutoCapture", "PiAgent"],
        root=root,
    )


def _read_input(argv: list[str]) -> tuple[str, str]:
    """Return ``(text, cwd)`` from CLI args.

    ``--cwd`` sets the cwd label (default ``"."``). Remaining positional args are
    file paths whose contents are concatenated; if none, text is read from stdin.
    """
    cwd = "."
    paths: list[str] = []
    it = iter(argv)
    for arg in it:
        if arg == "--cwd":
            cwd = next(it, ".")
        elif arg.startswith("--cwd="):
            cwd = arg[len("--cwd=") :]
        else:
            paths.append(arg)

    if paths:
        text = "\n".join(Path(p).read_text(encoding="utf-8") for p in paths)
    else:
        text = sys.stdin.read()
    return text, cwd


def main(argv: list[str] | None = None) -> int:
    text, cwd = _read_input(sys.argv[1:] if argv is None else argv)
    result = maybe_auto_capture(text, cwd)
    if result is None:
        print("No auto-capture (no signal/intent or duplicate).")
        return 0
    print(f"Auto-captured to Fritz-Brain: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
