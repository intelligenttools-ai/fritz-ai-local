"""Tests for the Claude Code binding / plugin (issue #65).

The binding is a self-registering Claude Code plugin under ``bindings/claude/``.
We cannot run a live Claude session, so these tests drive the wired Python hooks
directly via hook-input JSON on stdin (tmp ``$BRAIN_HOME``), and assert the
plugin manifests + ``hooks.json`` are valid and that every registered command
resolves to an existing file.

Each test notes which of the nine capabilities (C1..C9 from
``docs/integration-contract.md`` / ``docs/capability-spec.md``) it covers.

GUARDRAIL: every test points ``$BRAIN_HOME`` at a tmp dir and uses stdin/file
fixtures only. The live ``~/.brain`` and ``~/.claude`` are never touched.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = REPO_ROOT / "hooks"
PLUGIN = REPO_ROOT / "bindings" / "claude"
PLUGIN_MANIFEST = PLUGIN / ".claude-plugin" / "plugin.json"
MARKETPLACE = PLUGIN / ".claude-plugin" / "marketplace.json"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"
PLUGIN_SKILLS = PLUGIN / "skills"

PY = sys.executable


def _run_hook(script: Path, payload: dict, brain_home: Path, cwd: Path) -> subprocess.CompletedProcess:
    """Run a plugin hook script with hook-input JSON on stdin (tmp brain)."""
    env = dict(os.environ)
    env["BRAIN_HOME"] = str(brain_home)
    env["FRITZ_REPO_PATH"] = str(REPO_ROOT)
    # Keep the live ~/.brain untouched even if a hook resolves HOME.
    env["HOME"] = str(brain_home.parent)
    # Claude Code sets CLAUDE_PLUGIN_ROOT for plugin-launched hooks; it doubles
    # as the agent-detection marker so the Claude transcript adapter is selected
    # even when the transcript lives in a tmp dir (not ~/.claude/projects).
    env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN)
    return subprocess.run(
        [PY, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
        timeout=30,
    )


def _resolve_plugin_command(command: str) -> Path:
    """Resolve a ``hooks.json`` command's script path via ${CLAUDE_PLUGIN_ROOT}.

    Claude expands ``${CLAUDE_PLUGIN_ROOT}`` to the plugin root (``PLUGIN``).
    The command looks like ``python3 ${CLAUDE_PLUGIN_ROOT}/hooks/foo.py``.
    """
    expanded = command.replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN))
    # The script path is the last whitespace-delimited token.
    return Path(expanded.split()[-1])


# --- Manifests: plugin.json + marketplace.json ------------------------------


def test_plugin_json_valid_with_required_fields():
    data = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    assert data["name"] == "fritz-brain"
    assert isinstance(data.get("version"), str) and data["version"]
    assert data.get("description")
    # Declares hooks + skills so enabling the plugin registers everything.
    assert data.get("hooks") == "./hooks/hooks.json"
    assert data.get("skills") == "./skills"


def test_marketplace_json_lists_plugin_with_local_source():
    data = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    assert isinstance(data.get("name"), str) and data["name"]
    plugins = data.get("plugins")
    assert isinstance(plugins, list) and plugins
    fritz = next((p for p in plugins if p.get("name") == "fritz-brain"), None)
    assert fritz is not None, "marketplace must list the fritz-brain plugin"
    # Local directory source path.
    assert fritz.get("source") == "./"


# --- hooks.json: events, ordering, and resolvable commands ------------------


def test_hooks_json_registers_all_canonical_events():
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    for event in ("SessionStart", "UserPromptSubmit", "PreCompact", "Stop"):
        assert event in data, f"hooks.json must register {event}"


def test_stop_runs_capture_then_autocapture_in_order():
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    stop_cmds = [h["command"] for group in data["Stop"] for h in group["hooks"]]
    assert len(stop_cmds) == 2, "Stop must run two commands"
    assert "brain_capture.py" in stop_cmds[0], "capture must run first"
    assert "brain_autocapture_hook.py" in stop_cmds[1], "autocapture must run second"


def test_all_hook_commands_use_plugin_root_and_resolve():
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    for event, groups in data.items():
        for group in groups:
            for hook in group["hooks"]:
                cmd = hook["command"]
                assert "${CLAUDE_PLUGIN_ROOT}" in cmd, f"{event} cmd must use ${{CLAUDE_PLUGIN_ROOT}}: {cmd}"
                script = _resolve_plugin_command(cmd)
                assert script.exists(), f"{event} script does not resolve: {script}"
                # Symlinks must resolve to the canonical repo hooks (single source).
                assert script.resolve().parent == HOOKS.resolve(), (
                    f"{script} must resolve into the repo hooks dir"
                )


def test_plugin_hook_symlinks_point_at_repo_hooks():
    for name in (
        "brain_session_start.py",
        "brain_prompt_check.py",
        "brain_capture.py",
        "brain_autocapture_hook.py",
    ):
        link = PLUGIN / "hooks" / name
        assert link.is_symlink(), f"{name} should be a committed symlink"
        assert link.resolve() == (HOOKS / name).resolve()


# --- C1: SessionStart context injection -------------------------------------


def test_session_start_emits_additional_context(tmp_path):
    """C1 — SessionStart hook emits hookSpecificOutput.additionalContext."""
    brain = tmp_path / "home" / ".brain"
    brain.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    script = PLUGIN / "hooks" / "brain_session_start.py"
    proc = _run_hook(script, {"cwd": str(proj), "hook_event_name": "SessionStart"}, brain, proj)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "Brain System Active" in ctx
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"


# --- C2: UserPromptSubmit BRAIN CHECK ---------------------------------------


def test_prompt_check_emits_brain_check(tmp_path):
    """C2 — UserPromptSubmit guardrail injects a BRAIN CHECK when knowledge/captures exist."""
    brain = tmp_path / "home" / ".brain"
    daily = brain / "capture" / "daily"
    daily.mkdir(parents=True)
    (daily / "2026-06-14.md").write_text("# Daily Log\n", encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    script = PLUGIN / "hooks" / "brain_prompt_check.py"
    payload = {
        "cwd": str(proj),
        "hook_event_name": "UserPromptSubmit",
        "user_prompt": "how did we decide to do auth in this project?",
    }
    proc = _run_hook(script, payload, brain, proj)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert "BRAIN CHECK" in out["hookSpecificOutput"]["additionalContext"]


def test_prompt_check_skips_trivial_prompt(tmp_path):
    """C2 — a trivial/short prompt is a no-op (no output)."""
    brain = tmp_path / "home" / ".brain"
    brain.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    script = PLUGIN / "hooks" / "brain_prompt_check.py"
    payload = {"cwd": str(proj), "hook_event_name": "UserPromptSubmit", "user_prompt": "ok"}
    proc = _run_hook(script, payload, brain, proj)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == ""


# --- C3: auto-capture bridge on Stop ----------------------------------------


def _claude_transcript(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


def test_stop_autocapture_writes_one_inbox_fact_and_dedups(tmp_path):
    """C3 — Stop hook-input -> bridge -> exactly one inbox capture; rerun dedups."""
    brain = tmp_path / "home" / ".brain"
    brain.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    transcript = proj / "session.jsonl"
    _claude_transcript(
        transcript,
        [
            {"type": "user", "message": {"role": "user", "content": "The forgejo server is at https://git.example.ai. Please remember this for future sessions."}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Understood, I will save the forgejo server location to the brain."}]}},
        ],
    )
    payload = {"cwd": str(proj), "hook_event_name": "Stop", "transcript_path": str(transcript)}
    script = PLUGIN / "hooks" / "brain_autocapture_hook.py"

    proc = _run_hook(script, payload, brain, proj)
    assert proc.returncode == 0, proc.stderr
    assert "Auto-captured to Fritz-Brain:" in proc.stdout
    inbox = list((brain / "capture" / "inbox").glob("*.md"))
    seen = list((brain / "capture" / "auto").glob("*.seen"))
    assert len(inbox) == 1
    assert len(seen) == 1

    # Rerun is a dedup no-op.
    proc2 = _run_hook(script, payload, brain, proj)
    assert proc2.returncode == 0, proc2.stderr
    assert "No auto-capture" in proc2.stdout
    assert len(list((brain / "capture" / "inbox").glob("*.md"))) == 1


def test_stop_autocapture_no_signal_writes_nothing(tmp_path):
    """C3 — a transcript without durable signal/intent writes nothing."""
    brain = tmp_path / "home" / ".brain"
    brain.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    transcript = proj / "session.jsonl"
    _claude_transcript(
        transcript,
        [
            {"type": "user", "message": {"role": "user", "content": "What is the capital of France in general?"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "The capital of France is Paris, a lovely city."}]}},
        ],
    )
    payload = {"cwd": str(proj), "hook_event_name": "Stop", "transcript_path": str(transcript)}
    script = PLUGIN / "hooks" / "brain_autocapture_hook.py"
    proc = _run_hook(script, payload, brain, proj)
    assert proc.returncode == 0, proc.stderr
    assert "No auto-capture" in proc.stdout
    assert not (brain / "capture" / "inbox").exists() or not list(
        (brain / "capture" / "inbox").glob("*.md")
    )


def test_stop_autocapture_no_double_daily_capture(tmp_path):
    """C3/C4 — the autocapture bridge does NOT write a daily capture (no double-write)."""
    brain = tmp_path / "home" / ".brain"
    brain.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    transcript = proj / "session.jsonl"
    _claude_transcript(
        transcript,
        [
            {"type": "user", "message": {"role": "user", "content": "The forgejo server is at https://git.example.ai. Please remember this for future sessions."}},
        ],
    )
    payload = {"cwd": str(proj), "hook_event_name": "Stop", "transcript_path": str(transcript)}
    script = PLUGIN / "hooks" / "brain_autocapture_hook.py"
    _run_hook(script, payload, brain, proj)
    # Bridge writes inbox, never daily.
    daily = brain / "capture" / "daily"
    assert not daily.exists() or not list(daily.glob("*.md"))


# --- C4: daily capture on Stop ----------------------------------------------


def test_capture_writes_daily_rollup(tmp_path):
    """C4 — brain_capture.py on a Stop fixture writes the daily capture."""
    brain = tmp_path / "home" / ".brain"
    brain.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    transcript = proj / "session.jsonl"
    _claude_transcript(
        transcript,
        [
            {"type": "user", "message": {"role": "user", "content": "Let us design the authentication flow for the service."}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "We will use OAuth with short-lived tokens for the service auth."}]}},
        ],
    )
    payload = {
        "cwd": str(proj),
        "hook_event_name": "Stop",
        # The Claude adapter is selected via path marker; force it through cwd.
        "transcript_path": str(transcript),
    }
    script = PLUGIN / "hooks" / "brain_capture.py"
    proc = _run_hook(script, payload, brain, proj)
    assert proc.returncode == 0, proc.stderr
    daily = brain / "capture" / "daily"
    files = list(daily.glob("*.md")) if daily.exists() else []
    assert files, "daily capture file must be written"
    content = files[0].read_text(encoding="utf-8")
    assert "Session" in content


# --- C5: explicit save via the Python core ----------------------------------


def test_save_fact_writes_inbox(tmp_path):
    """C5 — explicit save via brain_save_fact core writes a durable inbox fact."""
    spec = importlib.util.spec_from_file_location(
        "_brain_save_fact_claude", HOOKS / "brain_save_fact.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = mod.save_fact(
        title="Claude binding test fact",
        body="The deploy token lives in the vault.",
        source="claude-test",
        root=tmp_path,
    )
    assert out.exists()
    assert out.parent == tmp_path / "capture" / "inbox"
    assert "# Claude binding test fact" in out.read_text(encoding="utf-8")


def test_brain_save_skill_is_bundled():
    """C5/C8 — the fritz:brain-save skill is committed in the plugin."""
    assert (PLUGIN_SKILLS / "fritz:brain-save" / "SKILL.md").exists()


# --- C8: committed skills match fresh generator output (no drift) -----------


def test_committed_skills_match_generator_output(tmp_path):
    """C8 — committed fritz:brain-* skills equal fresh generate_variants output."""
    spec = importlib.util.spec_from_file_location(
        "_setup_hyphenated_skills_claude", HOOKS / "setup_hyphenated_skills.py"
    )
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    gen.generate_variants(fresh, "claude", dry_run=False)

    fresh_dirs = sorted(d.name for d in fresh.iterdir() if (d / "SKILL.md").exists())
    committed_dirs = sorted(
        d.name for d in PLUGIN_SKILLS.iterdir() if (d / "SKILL.md").exists()
    )
    assert committed_dirs == fresh_dirs, "committed skill set drifted from generator"

    for name in fresh_dirs:
        fresh_content = (fresh / name / "SKILL.md").read_text(encoding="utf-8")
        committed_content = (PLUGIN_SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
        assert committed_content == fresh_content, f"{name}/SKILL.md drifted from generator"


def test_committed_skills_validate(tmp_path):
    """C8 — committed skills pass the consistency validator."""
    spec = importlib.util.spec_from_file_location(
        "_setup_hyphenated_skills_claude_v", HOOKS / "setup_hyphenated_skills.py"
    )
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    errors = gen.validate_variants(PLUGIN_SKILLS, "claude")
    assert errors == [], f"committed skills failed validation: {errors}"


# --- C6/C7/C9: installer wiring for the claude agent ------------------------


def test_install_agent_claude_installs_colon_skills(tmp_path, monkeypatch):
    """C6/C7 — install --agent claude installs fritz:brain-* skills to a tmp dir."""
    spec = importlib.util.spec_from_file_location(
        "_install_claude_binding", REPO_ROOT / "scripts" / "install.py"
    )
    install = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(install)

    brain = tmp_path / "home" / ".brain"
    brain.mkdir(parents=True)
    monkeypatch.setenv("BRAIN_HOME", str(brain))
    monkeypatch.setenv("FRITZ_REPO_PATH", str(REPO_ROOT))
    monkeypatch.delenv("FRITZ_SKILLS_DIR", raising=False)

    skills = tmp_path / "claude-skills"
    rc = install.main(["install", "--agent", "claude", "--skills-dir", str(skills)])
    assert rc == 0
    assert (skills / "fritz:brain-query" / "SKILL.md").exists()
    assert (skills / "fritz:brain-save" / "SKILL.md").exists()
    assert not (skills / "fritz-brain-query").exists()
