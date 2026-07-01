"""Register the Fritz Local hooks into Claude Code's ``~/.claude/settings.json``.

The Claude Code binding ships as a directory-source marketplace, so its skills
load but its hook declarations (``bindings/claude/hooks/hooks.json``) are NOT
auto-registered. Result: the four fritz hooks never fire and 0 captures are
recorded for Claude. This installer merges those hook declarations directly into
``~/.claude/settings.json`` with absolute command paths.

It is idempotent: re-running replaces the fritz entries (identified by a stable
marker) rather than appending duplicates, and never touches hooks that belong to
other plugins or any other top-level settings key.

Usage:
    python install_claude_hooks.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


# The Claude hooks, mirroring the source of truth bindings/claude/hooks/hooks.json
# EXACTLY. Each event maps to an ORDERED list of (script, timeout_ms) commands;
# they are emitted in that order inside a single fritz hook group. In particular
# Stop runs brain_capture.py THEN brain_autocapture_hook.py (the auto-capture
# bridge) — dropping the second command yields ~0 Claude captures.
FRITZ_HOOKS: list[tuple[str, list[tuple[str, int]]]] = [
    ("SessionStart", [("brain_session_start.py", 5000)]),
    ("UserPromptSubmit", [("brain_prompt_check.py", 3000)]),
    ("PreCompact", [("brain_capture.py", 10000)]),
    ("Stop", [("brain_capture.py", 10000), ("brain_autocapture_hook.py", 10000)]),
]

# Stable marker recorded on every fritz-installed hook group so re-runs can find
# and replace them without depending on the (machine-specific) command string.
FRITZ_MARKER = "fritz-ai-local"

PYTHON_BIN_DIRS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]


def resolve_python() -> str:
    """Resolve an absolute python3, mirroring the Pi binding's resolvePython().

    Order: ``FRITZ_PYTHON`` env (if it points at an existing file), then the
    common bin dirs, else the bare ``python3`` (relies on PATH). An absolute,
    yaml-capable interpreter matters because a bare ``python3`` may resolve to a
    yaml-less Apple system Python when the hook runs without a login shell PATH.
    """
    override = os.environ.get("FRITZ_PYTHON")
    if override and Path(override).exists():
        return override
    for directory in PYTHON_BIN_DIRS:
        candidate = Path(directory) / "python3"
        if candidate.exists():
            return str(candidate)
    return "python3"


def resolve_hooks_dir() -> Path:
    """Resolve the installed hooks dir, mirroring the Pi binding's brainHook().

    Prefer ``~/.brain/hooks`` (the install-managed location) if present, else
    fall back to this file's own directory (the repo ``hooks/``).
    """
    installed = Path.home() / ".brain" / "hooks"
    if installed.is_dir():
        return installed
    return Path(__file__).resolve().parent


def ensure_hook_scripts_present(hooks_dir: Path, repo_hooks_dir: Path) -> list[str]:
    """Ensure every brain_*.py from repo_hooks_dir is present in hooks_dir.

    For each brain_*.py file in ``repo_hooks_dir`` that is missing from
    ``hooks_dir``, create a symlink in ``hooks_dir`` pointing at the repo file.
    This mirrors the existing symlink convention used in ``~/.brain/hooks``.

    Rules:
    - Idempotent: files/symlinks already present in hooks_dir are skipped.
    - Does NOT overwrite existing files or symlinks (even broken ones).
    - When hooks_dir and repo_hooks_dir resolve to the same directory, returns [].
    - Returns the list of newly-created symlink names.
    """
    hooks_dir = hooks_dir.resolve()
    repo_hooks_dir = repo_hooks_dir.resolve()
    if hooks_dir == repo_hooks_dir:
        return []

    linked: list[str] = []
    for src in sorted(repo_hooks_dir.glob("brain_*.py")):
        dest = hooks_dir / src.name
        if dest.exists() or dest.is_symlink():
            # Already present (file, symlink — valid or broken). Skip.
            continue
        dest.symlink_to(src)
        linked.append(src.name)
    return linked


def _fritz_group(commands: list[tuple[str, int]], hooks_dir: Path, python_bin: str) -> dict:
    """Build one fritz hook group for an event, tagged with the marker.

    ``commands`` is the ordered list of (script, timeout_ms) for the event; every
    command is emitted in order inside the single group's ``hooks`` list.
    """
    return {
        "_source": FRITZ_MARKER,
        "hooks": [
            {
                "type": "command",
                "command": f"{python_bin} {hooks_dir / script}",
                "timeout": timeout_ms,
            }
            for script, timeout_ms in commands
        ],
    }


def _fritz_scripts() -> set[str]:
    """All script filenames fritz registers, across every event."""
    return {script for _, commands in FRITZ_HOOKS for script, _ in commands}


def _is_fritz_group(group: object) -> bool:
    """True if a hook group was installed by fritz (by marker or command path)."""
    if not isinstance(group, dict):
        return False
    if group.get("_source") == FRITZ_MARKER:
        return True
    # Fallback for groups written before the marker existed: detect by any
    # command that references one of the fritz hook scripts.
    scripts = _fritz_scripts()
    for hook in group.get("hooks", []):
        if isinstance(hook, dict):
            command = hook.get("command", "")
            if isinstance(command, str) and any(s in command for s in scripts):
                return True
    return False


def install_claude_hooks(settings_path: Path, *, hooks_dir: Path, python_bin: str) -> None:
    """Merge the four fritz hooks into ``settings_path`` idempotently.

    - Reads the existing settings JSON (missing file → start from ``{}``).
    - Preserves all other top-level keys and any hook groups for other plugins.
    - Replaces existing fritz hook groups (never appends duplicates).
    - Writes atomically (tmp + os.replace), backing up a pre-existing file to
      ``<settings_path>.bak``.

    Raises ``RuntimeError`` if the file EXISTS but cannot be read or parsed as
    JSON — we refuse to clobber a real (possibly hand-edited) config.
    """
    settings_path = Path(settings_path)

    existed = settings_path.exists()
    if existed:
        try:
            raw = settings_path.read_text(encoding="utf-8")
            settings = json.loads(raw)
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(
                f"{settings_path} exists but could not be read as JSON "
                f"({exc}); refusing to overwrite; fix or remove the file"
            ) from exc
        if not isinstance(settings, dict):
            raise RuntimeError(
                f"{settings_path} exists but its top level is not a JSON object; "
                f"refusing to overwrite; fix or remove the file"
            )
    else:
        settings = {}

    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    settings["hooks"] = hooks

    for event, commands in FRITZ_HOOKS:
        existing_groups = hooks.get(event)
        if not isinstance(existing_groups, list):
            existing_groups = []
        # Drop any prior fritz groups for this event; keep foreign groups.
        kept = [g for g in existing_groups if not _is_fritz_group(g)]
        kept.append(_fritz_group(commands, hooks_dir, python_bin))
        hooks[event] = kept

    if existed:
        backup = settings_path.with_name(settings_path.name + ".bak")
        backup.write_text(settings_path.read_text(encoding="utf-8"), encoding="utf-8")

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(settings, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(settings_path.parent), prefix=".settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_name, settings_path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def main(argv: list[str] | None = None) -> int:
    repo_hooks_dir = Path(__file__).resolve().parent
    hooks_dir = resolve_hooks_dir()
    linked = ensure_hook_scripts_present(hooks_dir, repo_hooks_dir)
    if linked:
        print(f"Linked {len(linked)} hook script(s) into {hooks_dir}: {', '.join(linked)}")
    settings_path = Path.home() / ".claude" / "settings.json"
    install_claude_hooks(
        settings_path,
        hooks_dir=hooks_dir,
        python_bin=resolve_python(),
    )
    print(f"Registered {len(FRITZ_HOOKS)} Fritz Local hooks in {settings_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
