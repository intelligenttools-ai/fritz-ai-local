"""Tests for the Claude Code hook installer (issue #210).

The Claude binding is a directory-source marketplace, so its hooks never
auto-register — 0 Claude captures result. These tests cover the installer that
merges the four fritz hooks into ``~/.claude/settings.json``, plus the corrected
adapter agent label.

GUARDRAIL: every test runs against a TEMP settings.json under ``tmp_path``. The
live ``~/.claude/settings.json`` is never read or written.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "hooks" / "install_claude_hooks.py"


def _load_installer():
    spec = importlib.util.spec_from_file_location("install_claude_hooks", INSTALLER)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


mod = _load_installer()

# Event -> ordered list of (script, timeout_ms), mirroring the source of truth
# bindings/claude/hooks/hooks.json. Stop wires TWO commands in order.
EXPECTED = {
    "SessionStart": [("brain_session_start.py", 5000)],
    "UserPromptSubmit": [("brain_prompt_check.py", 3000)],
    "PreCompact": [("brain_capture.py", 10000)],
    "Stop": [("brain_capture.py", 10000), ("brain_autocapture_hook.py", 10000)],
}


# --- label fix (acceptance: adapter label is now "claude") ------------------


def test_claude_adapter_label_is_claude():
    """The telemetry importer takes the log's agent field verbatim, so the
    Claude adapter must report ``claude`` (not ``claude-code``)."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from adapters.claude_code import ClaudeCodeAdapter
    finally:
        sys.path.pop(0)
    assert ClaudeCodeAdapter.agent_name == "claude"


# --- installer: fresh (missing file → start from {}) ------------------------


def test_installer_missing_file_writes_four_events(tmp_path):
    settings = tmp_path / "settings.json"
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    python_bin = "/opt/homebrew/bin/python3"

    mod.install_claude_hooks(settings, hooks_dir=hooks_dir, python_bin=python_bin)

    data = json.loads(settings.read_text())
    assert set(data["hooks"]) == set(EXPECTED)
    for event, commands in EXPECTED.items():
        group = data["hooks"][event]
        assert len(group) == 1  # one fritz group per event
        hooks = group[0]["hooks"]
        # command list matches hooks.json exactly (count + order + timeouts)
        assert len(hooks) == len(commands)
        for hook, (script, timeout) in zip(hooks, commands):
            assert hook["type"] == "command"
            assert hook["command"] == f"{python_bin} {hooks_dir / script}"
            assert hook["timeout"] == timeout
            # absolute command path
            assert str(hooks_dir) in hook["command"]


def test_installer_no_backup_when_file_absent(tmp_path):
    settings = tmp_path / "settings.json"
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    mod.install_claude_hooks(settings, hooks_dir=hooks_dir, python_bin="python3")
    assert not settings.with_name("settings.json.bak").exists()


# --- installer: idempotent (run twice → no duplicates) ----------------------


def test_installer_idempotent_no_duplicates(tmp_path):
    settings = tmp_path / "settings.json"
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    python_bin = "/opt/homebrew/bin/python3"

    mod.install_claude_hooks(settings, hooks_dir=hooks_dir, python_bin=python_bin)
    first = json.loads(settings.read_text())
    mod.install_claude_hooks(settings, hooks_dir=hooks_dir, python_bin=python_bin)
    second = json.loads(settings.read_text())

    assert first["hooks"] == second["hooks"]
    for event in EXPECTED:
        assert len(second["hooks"][event]) == 1


# --- installer: preserves foreign hooks + other top-level keys --------------


def test_installer_preserves_foreign_hooks_and_keys(tmp_path):
    settings = tmp_path / "settings.json"
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()

    foreign_group = {
        "hooks": [
            {"type": "command", "command": "/usr/bin/other-plugin.sh", "timeout": 1000}
        ]
    }
    settings.write_text(
        json.dumps(
            {
                "model": "opus",
                "hooks": {
                    "SessionStart": [foreign_group],
                    "PostToolUse": [foreign_group],
                },
            }
        )
    )

    mod.install_claude_hooks(settings, hooks_dir=hooks_dir, python_bin="python3")
    data = json.loads(settings.read_text())

    # unrelated top-level key preserved
    assert data["model"] == "opus"
    # foreign event untouched
    assert data["hooks"]["PostToolUse"] == [foreign_group]
    # foreign SessionStart group kept alongside the new fritz group
    assert foreign_group in data["hooks"]["SessionStart"]
    fritz = [g for g in data["hooks"]["SessionStart"] if g.get("_source") == mod.FRITZ_MARKER]
    assert len(fritz) == 1


def test_installer_idempotent_keeps_single_foreign_group(tmp_path):
    """Re-running must not drop or duplicate the foreign group."""
    settings = tmp_path / "settings.json"
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    foreign_group = {
        "hooks": [{"type": "command", "command": "/usr/bin/other.sh", "timeout": 1000}]
    }
    settings.write_text(json.dumps({"hooks": {"SessionStart": [foreign_group]}}))

    mod.install_claude_hooks(settings, hooks_dir=hooks_dir, python_bin="python3")
    mod.install_claude_hooks(settings, hooks_dir=hooks_dir, python_bin="python3")
    data = json.loads(settings.read_text())

    groups = data["hooks"]["SessionStart"]
    assert groups.count(foreign_group) == 1
    fritz = [g for g in groups if g.get("_source") == mod.FRITZ_MARKER]
    assert len(fritz) == 1


# --- installer: creates .bak when the file existed --------------------------


def test_installer_creates_backup_when_file_existed(tmp_path):
    settings = tmp_path / "settings.json"
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    original = json.dumps({"model": "opus", "hooks": {}})
    settings.write_text(original)

    mod.install_claude_hooks(settings, hooks_dir=hooks_dir, python_bin="python3")

    backup = settings.with_name("settings.json.bak")
    assert backup.exists()
    assert backup.read_text() == original


# --- resolve_python honors FRITZ_PYTHON -------------------------------------


def test_resolve_python_honors_fritz_python(tmp_path, monkeypatch):
    fake = tmp_path / "my-python3"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setenv("FRITZ_PYTHON", str(fake))
    assert mod.resolve_python() == str(fake)


def test_resolve_python_ignores_missing_fritz_python(tmp_path, monkeypatch):
    monkeypatch.setenv("FRITZ_PYTHON", str(tmp_path / "does-not-exist"))
    result = mod.resolve_python()
    # falls through to a bin-dir python3 or the bare fallback
    assert result != str(tmp_path / "does-not-exist")


# --- B1: Stop registers BOTH commands in order; parity with hooks.json -------


def test_stop_registers_capture_then_autocapture_in_order(tmp_path):
    """Regression for #210 B1: the Stop event must wire brain_capture.py THEN
    brain_autocapture_hook.py (the auto-capture bridge). Dropping the second
    command yields ~0 Claude captures."""
    settings = tmp_path / "settings.json"
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    python_bin = "/opt/homebrew/bin/python3"

    mod.install_claude_hooks(settings, hooks_dir=hooks_dir, python_bin=python_bin)
    data = json.loads(settings.read_text())

    stop_hooks = data["hooks"]["Stop"][0]["hooks"]
    commands = [h["command"] for h in stop_hooks]
    assert commands == [
        f"{python_bin} {hooks_dir / 'brain_capture.py'}",
        f"{python_bin} {hooks_dir / 'brain_autocapture_hook.py'}",
    ]


def test_installed_commands_match_source_hooks_json(tmp_path):
    """Every event's command list matches the source of truth
    bindings/claude/hooks/hooks.json (script order + timeouts). Stop has 2
    commands; the other events match their single-command declarations."""
    source = json.loads(
        (REPO_ROOT / "bindings" / "claude" / "hooks" / "hooks.json").read_text()
    )
    settings = tmp_path / "settings.json"
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    python_bin = "/opt/homebrew/bin/python3"

    mod.install_claude_hooks(settings, hooks_dir=hooks_dir, python_bin=python_bin)
    installed = json.loads(settings.read_text())["hooks"]

    assert set(installed) == set(source)
    for event, source_groups in source.items():
        # source has one group per event; extract its ordered (script, timeout)
        source_cmds = source_groups[0]["hooks"]
        expected = [
            (Path(h["command"].split()[-1]).name, h["timeout"]) for h in source_cmds
        ]
        installed_hooks = installed[event][0]["hooks"]
        got = [
            (Path(h["command"].split()[-1]).name, h["timeout"]) for h in installed_hooks
        ]
        assert got == expected, event

    # explicit: Stop carries two commands
    assert len(installed["Stop"][0]["hooks"]) == 2


# --- B2: corrupt existing settings.json is NOT clobbered ---------------------


def test_installer_refuses_to_clobber_corrupt_json(tmp_path):
    """Regression for #210 B2: an existing file that fails to parse as JSON must
    cause a raise, leaving the file byte-for-byte unchanged (not reset to {})."""
    settings = tmp_path / "settings.json"
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    # invalid JSON (missing closing brace) that still holds a real key
    corrupt = '{"model": "opus", "importantKey": "keep-me"'
    settings.write_text(corrupt)

    with pytest.raises(RuntimeError):
        mod.install_claude_hooks(settings, hooks_dir=hooks_dir, python_bin="python3")

    # file unchanged; no data destroyed, no .bak written
    assert settings.read_text() == corrupt
    assert not settings.with_name("settings.json.bak").exists()


# --- ensure_hook_scripts_present ---------------------------------------------


def _make_fake_repo_hooks(tmp_path: Path, names: list[str]) -> Path:
    """Create a fake repo hooks dir with the given brain_*.py files."""
    repo_hooks = tmp_path / "repo_hooks"
    repo_hooks.mkdir()
    for name in names:
        (repo_hooks / name).write_text(f"# {name}\n")
    return repo_hooks


def test_ensure_links_missing_entry_script(tmp_path):
    """ensure_hook_scripts_present symlinks a missing entry script into hooks_dir."""
    repo_hooks = _make_fake_repo_hooks(
        tmp_path, ["brain_autocapture_hook.py", "brain_common.py"]
    )
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()

    linked = mod.ensure_hook_scripts_present(hooks_dir, repo_hooks)

    assert set(linked) == {"brain_autocapture_hook.py", "brain_common.py"}
    dest = hooks_dir / "brain_autocapture_hook.py"
    assert dest.is_symlink()
    assert dest.resolve() == (repo_hooks / "brain_autocapture_hook.py").resolve()


def test_ensure_idempotent(tmp_path):
    """Second call links 0 new scripts when all are already present."""
    repo_hooks = _make_fake_repo_hooks(tmp_path, ["brain_autocapture_hook.py"])
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()

    first = mod.ensure_hook_scripts_present(hooks_dir, repo_hooks)
    assert len(first) == 1

    second = mod.ensure_hook_scripts_present(hooks_dir, repo_hooks)
    assert second == []


def test_ensure_does_not_overwrite_existing_file(tmp_path):
    """A file already present in hooks_dir (even with different content) is not overwritten."""
    repo_hooks = _make_fake_repo_hooks(tmp_path, ["brain_autocapture_hook.py"])
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    existing = hooks_dir / "brain_autocapture_hook.py"
    existing.write_text("# original\n")

    linked = mod.ensure_hook_scripts_present(hooks_dir, repo_hooks)

    assert linked == []
    assert existing.read_text() == "# original\n"


def test_ensure_does_not_overwrite_existing_symlink(tmp_path):
    """An existing symlink (even if broken) is not replaced."""
    repo_hooks = _make_fake_repo_hooks(tmp_path, ["brain_common.py"])
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    other_target = tmp_path / "other_target.py"
    other_target.write_text("# other\n")
    existing_link = hooks_dir / "brain_common.py"
    existing_link.symlink_to(other_target)

    linked = mod.ensure_hook_scripts_present(hooks_dir, repo_hooks)

    assert linked == []
    # symlink still points at original target
    assert existing_link.resolve() == other_target.resolve()


def test_ensure_noop_when_hooks_dir_equals_repo_hooks_dir(tmp_path):
    """When hooks_dir is the same as repo_hooks_dir, nothing is linked (no self-loop)."""
    repo_hooks = _make_fake_repo_hooks(tmp_path, ["brain_capture.py"])
    linked = mod.ensure_hook_scripts_present(repo_hooks, repo_hooks)
    assert linked == []


def test_ensure_links_all_brain_scripts(tmp_path):
    """When hooks_dir is empty, all brain_*.py files from repo_hooks are linked."""
    names = [
        "brain_autocapture.py",
        "brain_autocapture_hook.py",
        "brain_bootstrap.py",
        "brain_capture.py",
        "brain_common.py",
        "brain_prompt_check.py",
        "brain_security.py",
        "brain_session_start.py",
    ]
    repo_hooks = _make_fake_repo_hooks(tmp_path, names)
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()

    linked = mod.ensure_hook_scripts_present(hooks_dir, repo_hooks)
    assert set(linked) == set(names)
    for name in names:
        assert (hooks_dir / name).is_symlink()


# --- regression guard: every registered command's script exists after install --


def test_every_registered_script_exists_after_install(tmp_path):
    """Regression guard for the live bug: after a full install the script path
    in every registered command must exist on disk (file or valid symlink).

    The hooks_dir starts EMPTY — this simulates ~/.brain/hooks missing the
    brain_autocapture_hook.py script. ensure_hook_scripts_present must create
    the symlinks so that every command path resolves to an existing file.
    """
    # Fake repo hooks dir: populate it with all brain_*.py from the real repo
    repo_hooks_dir = REPO_ROOT / "hooks"
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()

    # Simulate an empty hooks_dir (no scripts pre-populated), then ensure
    linked = mod.ensure_hook_scripts_present(hooks_dir, repo_hooks_dir)
    assert len(linked) > 0, "Expected at least one script to be linked"

    # Now install
    settings = tmp_path / "settings.json"
    python_bin = "/opt/homebrew/bin/python3"
    mod.install_claude_hooks(settings, hooks_dir=hooks_dir, python_bin=python_bin)
    data = json.loads(settings.read_text())

    # Check every command's script path exists
    for event, groups in data["hooks"].items():
        for group in groups:
            for hook in group.get("hooks", []):
                command = hook.get("command", "")
                # command is "<python_bin> <script_path>"
                parts = command.split()
                assert len(parts) == 2, f"Unexpected command format: {command!r}"
                script_path = Path(parts[1])
                assert script_path.exists(), (
                    f"Registered script does not exist: {script_path} "
                    f"(event={event})"
                )
