"""Contract tests for finalizing the pi binding (issue #64).

The pi binding (``bindings/pi/index.ts``) now delegates both ``brain_save_fact``
and auto-capture to the Python core via subprocess, so there is a single
authoritative capture implementation and no TS/Python double-write.

These tests verify:

1. The binding source invokes the Python core and no longer contains the old
   inline TS frontmatter-writing duplication.
2. ``bindings/pi/README.md`` documents the SDK fork, that it supersedes the
   external copy, the deploy step, and the manual pi smoke test.
3. The exact Python-core invocation contract the binding relies on, end-to-end,
   against a tmp ``BRAIN_HOME`` (never touching the live ``~/.brain``):
   - piping a fact JSON to ``brain_save_fact.py --json`` creates one inbox file
     and prints a parseable ``Saved to Fritz-Brain: <path>`` line;
   - piping a durable-signal transcript to ``brain_autocapture.py`` captures
     exactly once and a rerun on the same text is a no-op (dedup).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PI_BINDING = REPO_ROOT / "bindings" / "pi" / "index.ts"
PI_README = REPO_ROOT / "bindings" / "pi" / "README.md"
SAVE_FACT_HOOK = REPO_ROOT / "hooks" / "brain_save_fact.py"
AUTOCAPTURE_HOOK = REPO_ROOT / "hooks" / "brain_autocapture.py"


def _binding_source() -> str:
    return PI_BINDING.read_text(encoding="utf-8")


# --- 1. Binding delegates to the Python core, no inline TS duplication --------


def test_binding_invokes_python_core_hooks():
    source = _binding_source()
    assert "brain_save_fact.py" in source, (
        "binding must invoke the Python core save-fact hook"
    )
    assert "brain_autocapture.py" in source, (
        "binding must invoke the Python core auto-capture hook"
    )


def test_binding_has_no_inline_frontmatter_duplication():
    source = _binding_source()
    # The `type: capture` frontmatter is now built only in Python
    # (hooks/brain_save_fact.py); it must no longer be assembled inline in TS.
    assert "type: capture" not in source, (
        "inline 'type: capture' frontmatter must not remain in the binding; "
        "frontmatter is now built only in hooks/brain_save_fact.py"
    )


def test_binding_save_fact_delegates_via_subprocess():
    source = _binding_source()
    # writeBrainInboxFact must now spawn python3 against the save-fact hook and
    # parse the hook's stdout, rather than writing the file itself.
    assert 'runCommand("python3"' in source, (
        "binding must delegate to python3 via runCommand"
    )
    assert "Saved to Fritz-Brain:" in source, (
        "binding must parse the save-fact hook's saved-path line"
    )
    assert "Auto-captured to Fritz-Brain:" in source, (
        "binding must parse the auto-capture hook's captured-path line"
    )


# --- 2. README documents fork + supersession + deploy + smoke test -----------


def test_readme_exists_and_documents_contract():
    assert PI_README.exists(), f"missing {PI_README}"
    text = PI_README.read_text(encoding="utf-8")
    assert "@earendil-works" in text, "README must document the @earendil-works fork"
    # Supersedes the external copy.
    assert "supersede" in text.lower(), "README must say it supersedes the external copy"
    assert "pi-extensions" in text, "README must reference the external pi-extensions copy"
    # Deploy step via the installer with --agent pi.
    assert "scripts/install.py" in text, "README must document scripts/install.py"
    assert "--agent pi" in text, "README must document the --agent pi deploy step"
    # Manual smoke test.
    assert "smoke-test" in text, "README must mention the /fritz smoke-test"


# --- 3. Python-core invocation contract, end-to-end (tmp BRAIN_HOME) ---------


def _run(hook: Path, args, stdin_text, brain_home: Path):
    return subprocess.run(
        [sys.executable, str(hook), *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        env={**os.environ, "BRAIN_HOME": str(brain_home)},
        cwd=str(REPO_ROOT / "hooks"),
    )


def test_save_fact_json_contract(tmp_path):
    """Pipe a fact JSON to brain_save_fact.py --json (what the binding does)."""
    brain_home = tmp_path / "brain"
    fact = {
        "title": "Forgejo server access",
        "body": "Server is at https://git.example.com; token in ~/.config/secrets.",
        "source": "pi-session",
        "sensitive": True,
        "tags": ["FritzBrain", "Access"],
        "agent": "pi",
    }
    result = _run(SAVE_FACT_HOOK, ["--json"], json.dumps(fact), brain_home)
    assert result.returncode == 0, result.stderr

    # The stdout path must be parseable exactly as the binding parses it.
    import re

    match = re.search(r"Saved to Fritz-Brain:\s*(.+)\s*$", result.stdout, re.MULTILINE)
    assert match, f"unparseable stdout: {result.stdout!r}"
    saved_path = Path(match.group(1).strip())
    assert saved_path.exists(), f"reported path does not exist: {saved_path}"

    inbox = brain_home / "capture" / "inbox"
    files = list(inbox.glob("*.md"))
    assert len(files) == 1, f"expected exactly one inbox file, got {files}"
    assert files[0] == saved_path
    assert "type: capture" in saved_path.read_text(encoding="utf-8")


def test_autocapture_contract_and_dedup(tmp_path):
    """Pipe a durable-signal transcript; capture once, rerun is a no-op."""
    brain_home = tmp_path / "brain"
    transcript = (
        "User: The server is at https://git.example.com and the api-token lives "
        "in ~/.config/secrets. Please remember this for future sessions so "
        "other sessions know how to reach it."
    )

    first = _run(AUTOCAPTURE_HOOK, ["--cwd", "/tmp/project"], transcript, brain_home)
    assert first.returncode == 0, first.stderr
    assert "Auto-captured to Fritz-Brain:" in first.stdout, first.stdout

    inbox = brain_home / "capture" / "inbox"
    assert len(list(inbox.glob("*.md"))) == 1, "expected exactly one capture"

    # Rerun on identical text must be deduped (no second capture).
    second = _run(AUTOCAPTURE_HOOK, ["--cwd", "/tmp/project"], transcript, brain_home)
    assert second.returncode == 0, second.stderr
    assert "No auto-capture" in second.stdout, second.stdout
    assert len(list(inbox.glob("*.md"))) == 1, "dedup must prevent a second capture"


def test_autocapture_no_signal_is_noop(tmp_path):
    """A transcript without durable signal + intent captures nothing."""
    brain_home = tmp_path / "brain"
    result = _run(
        AUTOCAPTURE_HOOK,
        ["--cwd", "/tmp/project"],
        "User: what's the weather like today?",
        brain_home,
    )
    assert result.returncode == 0, result.stderr
    assert "No auto-capture" in result.stdout
    assert not (brain_home / "capture" / "inbox").exists() or not list(
        (brain_home / "capture" / "inbox").glob("*.md")
    )
