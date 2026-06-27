"""Interpreter bootstrap for brain hooks (issue #167).

``brain_common`` does a top-level ``import yaml`` (genuinely required for
registry/manifest parsing). When a host launches a hook via a bare ``python3``
that resolves to a yaml-less interpreter (e.g. Apple's ``/usr/bin/python3``),
``import brain_common`` dies with ``ModuleNotFoundError: No module named 'yaml'``
before the hook does anything, and the host silently swallows the failure — so
no captures are ever written.

This module imports NO yaml. It exposes :func:`ensure_yaml_interpreter`, which
hooks call as their VERY FIRST statement (before importing ``brain_common``):
if the current interpreter has yaml it returns immediately; otherwise it finds a
yaml-capable interpreter (``$FRITZ_PYTHON`` override first, then a candidate
search mirroring the Pi binding's ``PYTHON_BIN_DIRS``) and re-execs the current
script under it. A ``$FRITZ_BRAIN_REEXEC`` sentinel guarantees no re-exec loop.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

# Mirror of the Pi binding's PYTHON_BIN_DIRS (bindings/pi/index.ts), plus a few
# common user/macports locations. Login-shell PATH is often NOT inherited by GUI
# host processes, so bare "python3" can resolve to a yaml-less interpreter.
PYTHON_BIN_DIRS = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/opt/local/bin",
    os.path.expanduser("~/.local/bin"),
]

_REEXEC_SENTINEL = "FRITZ_BRAIN_REEXEC"


def _has_yaml() -> bool:
    """Return True if the current interpreter can import yaml."""
    try:
        import yaml  # noqa: F401
        return True
    except ImportError:
        return False


def _interpreter_has_yaml(python: str) -> bool:
    """Return True if ``python`` is a runnable interpreter that can import yaml."""
    try:
        result = subprocess.run(
            [python, "-c", "import yaml"],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _candidate_pythons() -> list[str]:
    """Ordered list of candidate interpreter paths to probe for yaml support."""
    candidates: list[str] = []

    override = os.environ.get("FRITZ_PYTHON")
    if override:
        candidates.append(override)

    for d in PYTHON_BIN_DIRS:
        candidates.append(os.path.join(d, "python3"))

    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    # De-duplicate while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def resolve_yaml_interpreter(candidates: list[str]) -> str | None:
    """Return the first candidate that is a yaml-capable interpreter, else None."""
    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        if _interpreter_has_yaml(candidate):
            return candidate
    return None


def ensure_yaml_interpreter() -> None:
    """Guarantee the current process can import yaml, re-execing if needed.

    No-op when yaml is already importable. Otherwise resolves a yaml-capable
    interpreter and ``os.execv``-re-execs the current script under it. The
    ``FRITZ_BRAIN_REEXEC`` sentinel prevents an infinite re-exec loop: if it is
    already set we never re-exec again, so a genuinely missing yaml fails loudly
    (on the subsequent ``import brain_common``) rather than looping.
    """
    if _has_yaml():
        return
    if os.environ.get(_REEXEC_SENTINEL):
        return

    python = resolve_yaml_interpreter(_candidate_pythons())
    if not python:
        return
    if os.path.realpath(python) == os.path.realpath(sys.executable):
        return

    os.environ[_REEXEC_SENTINEL] = "1"
    os.execv(python, [python, *sys.argv])
