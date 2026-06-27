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


# --- #167 Fix A1: yaml-interpreter bootstrap ---------------------------------


def _load_bootstrap():
    spec = importlib.util.spec_from_file_location(
        "_brain_bootstrap_167", HOOKS / "brain_bootstrap.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a1_ensure_yaml_interpreter_noop_when_yaml_present():
    """A1 — ensure_yaml_interpreter is a no-op (no re-exec) when yaml imports.

    The test interpreter has yaml installed, so the call must return without
    re-execing the process (os.execv would otherwise replace this process).
    """
    boot = _load_bootstrap()
    # Returns normally (no exception, no process replacement).
    assert boot.ensure_yaml_interpreter() is None


def test_a1_resolve_yaml_interpreter_finds_capable_interpreter():
    """A1 — the candidate resolver returns a yaml-capable interpreter.

    Given a candidate list that includes the current sys.executable (which has
    yaml), the resolver must return it.
    """
    boot = _load_bootstrap()
    bogus = "/nonexistent/python3"
    resolved = boot.resolve_yaml_interpreter([bogus, sys.executable])
    assert resolved == sys.executable


def test_a1_resolve_yaml_interpreter_returns_none_without_candidates():
    """A1 — resolver returns None when no candidate is a real yaml interpreter."""
    boot = _load_bootstrap()
    assert boot.resolve_yaml_interpreter(["/nonexistent/python3"]) is None


@pytest.mark.parametrize(
    "script_name",
    [
        "brain_session_start.py",
        "brain_prompt_check.py",
        "brain_capture.py",
        "brain_autocapture_hook.py",
    ],
)
def test_a1_hook_calls_bootstrap_before_brain_common(script_name):
    """A1 — each Claude hook entry script calls ensure_yaml_interpreter() before
    importing the yaml-dependent module (brain_common / brain_autocapture).

    If brain_common were imported first under a yaml-less python3, it would die
    on ``import yaml`` before the bootstrap could re-exec — so import order is
    load-bearing and asserted at the source level.
    """
    src = (HOOKS / script_name).read_text(encoding="utf-8")
    call_idx = src.index("ensure_yaml_interpreter()")
    # The first dependency import after sys.path setup.
    if script_name == "brain_autocapture_hook.py":
        dep_idx = src.index("from brain_autocapture import")
    else:
        dep_idx = src.index("from brain_common import")
    assert call_idx < dep_idx, (
        f"{script_name}: ensure_yaml_interpreter() must precede the yaml-dependent import"
    )


# --- #167 Fix A2: per-turn save policy parity with Pi ------------------------


def test_a2_prompt_check_injects_save_policy(tmp_path):
    """A2 — UserPromptSubmit injection now also carries a SAVE policy (Pi parity):
    durable knowledge confirmed this turn must be SAVED via /fritz:brain-save,
    not merely answered — in addition to the existing search-before-answer nudge.
    """
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
    ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    # Existing search nudge still present.
    assert "BRAIN CHECK" in ctx
    # New save policy present.
    assert "/fritz:brain-save" in ctx
    assert "SAVE" in ctx


# --- #167 Fix B: scheduler owns compile (no agent-side hand-compile) ----------


def test_b_session_start_has_no_mandatory_compile_nudge():
    """B — SessionStart no longer emits the MANDATORY background-compile spawn
    nudge (the scheduler owns compile, #162/v1.3.54). The configuration decision
    prompt path stays; the minimal-capture no-service fallback stays.
    """
    src = (HOOKS / "brain_session_start.py").read_text(encoding="utf-8")
    assert "MANDATORY: Background brain compile needed" not in src
    assert "spawn a **background subagent**" not in src
    assert "autonomous maintenance task" not in src
    # KEEP: the no-service minimal-capture fallback message.
    assert "processing not active" in src
    assert "run `/fritz:brain-compile` manually" in src


def test_b_capture_does_not_auto_compile():
    """B — brain_capture.py captures only; it never hand-compiles (Pi parity)."""
    src = (HOOKS / "brain_capture.py").read_text(encoding="utf-8")
    assert "auto_compile_after_capture" not in src


def test_b_capture_still_writes_rollup_and_log_without_compile(tmp_path):
    """B — capture still writes the daily rollup and a CAPTURE log line and
    exits 0, with no compile step.
    """
    brain = tmp_path / "home" / ".brain"
    brain.mkdir(parents=True)
    (brain / "log.md").write_text("", encoding="utf-8")
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
    payload = {"cwd": str(proj), "hook_event_name": "Stop", "transcript_path": str(transcript)}
    script = PLUGIN / "hooks" / "brain_capture.py"
    proc = _run_hook(script, payload, brain, proj)
    assert proc.returncode == 0, proc.stderr
    daily = brain / "capture" / "daily"
    assert daily.exists() and list(daily.glob("*.md")), "daily rollup must be written"
    log = (brain / "log.md").read_text(encoding="utf-8")
    assert "| CAPTURE |" in log


# --- #167 Fix 2: re-exec unit tests for brain_bootstrap.py ------------------


def test_a1_reexec_calls_execv_when_yaml_missing(monkeypatch):
    """Fix 2 — ensure_yaml_interpreter re-execs under the yaml-capable interpreter
    when yaml is not importable from the current interpreter.

    Monkeypatches _has_yaml→False and resolve_yaml_interpreter→fake path.
    Asserts os.execv is called with the fake python and argv preserved, and that
    FRITZ_BRAIN_REEXEC is set in os.environ before the exec.
    """
    boot = _load_bootstrap()
    fake_python = "/opt/fake/python3"
    execv_calls = []

    monkeypatch.setattr(boot, "_has_yaml", lambda: False)
    monkeypatch.setattr(boot, "resolve_yaml_interpreter", lambda candidates: fake_python)
    monkeypatch.delenv(boot._REEXEC_SENTINEL, raising=False)

    def capture_execv(path, args):
        execv_calls.append((path, list(args)))

    monkeypatch.setattr(boot.os, "execv", capture_execv)
    # Prevent same-interpreter guard from short-circuiting (fake != sys.executable).
    monkeypatch.setattr(boot.os.path, "realpath", lambda p: p)

    boot.ensure_yaml_interpreter()

    assert len(execv_calls) == 1, "os.execv must be called exactly once"
    assert execv_calls[0][0] == fake_python
    assert execv_calls[0][1][0] == fake_python
    assert execv_calls[0][1][1:] == sys.argv
    assert os.environ.get(boot._REEXEC_SENTINEL) == "1"

    # Cleanup sentinel so test isolation is preserved.
    os.environ.pop(boot._REEXEC_SENTINEL, None)


def test_a1_reexec_loop_guard_prevents_second_exec(monkeypatch):
    """Fix 2 — if FRITZ_BRAIN_REEXEC is already set, ensure_yaml_interpreter must
    NOT call os.execv again — it returns so the subsequent yaml import fails
    loudly rather than looping infinitely.
    """
    boot = _load_bootstrap()
    execv_calls = []

    monkeypatch.setattr(boot, "_has_yaml", lambda: False)
    monkeypatch.setattr(boot, "resolve_yaml_interpreter", lambda candidates: "/opt/fake/python3")
    monkeypatch.setenv(boot._REEXEC_SENTINEL, "1")
    monkeypatch.setattr(boot.os, "execv", lambda path, args: execv_calls.append((path, args)))

    boot.ensure_yaml_interpreter()

    assert execv_calls == [], "os.execv must NOT be called when FRITZ_BRAIN_REEXEC is already set"


def test_a1_reexec_no_exec_when_no_capable_interpreter(monkeypatch):
    """Fix 2 — when resolve_yaml_interpreter returns None (no yaml-capable
    interpreter found), ensure_yaml_interpreter must return without calling
    os.execv — the hook will subsequently fail loudly on import yaml.
    """
    boot = _load_bootstrap()
    execv_calls = []

    monkeypatch.setattr(boot, "_has_yaml", lambda: False)
    monkeypatch.setattr(boot, "resolve_yaml_interpreter", lambda candidates: None)
    monkeypatch.delenv(boot._REEXEC_SENTINEL, raising=False)
    monkeypatch.setattr(boot.os, "execv", lambda path, args: execv_calls.append((path, args)))

    boot.ensure_yaml_interpreter()

    assert execv_calls == [], "os.execv must NOT be called when no capable interpreter is found"


def test_a1_reexec_no_exec_when_same_interpreter(monkeypatch):
    """Fix 2 — if the resolved interpreter is the same as sys.executable (after
    realpath), ensure_yaml_interpreter must not re-exec to avoid an infinite loop
    where the same yaml-less interpreter keeps re-execing itself.
    """
    boot = _load_bootstrap()
    execv_calls = []

    monkeypatch.setattr(boot, "_has_yaml", lambda: False)
    # resolve returns the current interpreter (same realpath).
    monkeypatch.setattr(boot, "resolve_yaml_interpreter", lambda candidates: sys.executable)
    monkeypatch.delenv(boot._REEXEC_SENTINEL, raising=False)
    monkeypatch.setattr(boot.os, "execv", lambda path, args: execv_calls.append((path, args)))

    boot.ensure_yaml_interpreter()

    assert execv_calls == [], "os.execv must NOT be called when resolved interpreter == sys.executable"


# --- #167 Fix 3: additional save-policy injection tests ----------------------


def test_a2_save_policy_on_substantive_non_query_prompt(tmp_path):
    """Fix 3 — a SUBSTANTIVE prompt that does NOT match brain-query or
    implementation signals must still inject the SAVE policy (Pi parity).
    Previously such prompts produced no output; now they carry at least the
    save nudge so every non-trivial turn reminds the agent to save durable facts.
    """
    brain = tmp_path / "home" / ".brain"
    brain.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    script = PLUGIN / "hooks" / "brain_prompt_check.py"
    # Substantive (>15 chars, no skip prefix) but no QUERY or IMPLEMENTATION signal.
    payload = {
        "cwd": str(proj),
        "hook_event_name": "UserPromptSubmit",
        "user_prompt": "The postgres password is in 1Password under prod-db",
    }
    proc = _run_hook(script, payload, brain, proj)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip(), "must produce output for a substantive non-query prompt"
    out = json.loads(proc.stdout)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "/fritz:brain-save" in ctx, "save policy must be injected for substantive non-query prompts"
    assert "SAVE" in ctx


def test_a2_save_policy_and_brain_check_both_on_query_prompt(tmp_path):
    """Fix 3 — a brain-query prompt carries BOTH the BRAIN CHECK nudge AND the
    save policy (the save policy should not have replaced the search nudge).
    """
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
        "user_prompt": "what did we decide about the retry policy last week?",
    }
    proc = _run_hook(script, payload, brain, proj)
    assert proc.returncode == 0, proc.stderr
    ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "BRAIN CHECK" in ctx
    assert "/fritz:brain-save" in ctx
    assert "SAVE" in ctx


def test_a2_no_output_on_trivial_prompt(tmp_path):
    """Fix 3 — trivial prompts produce NO output (save policy not injected there).

    Pins the trivial-skip behaviour to ensure the Pi-parity save-policy injection
    does not accidentally fire on short or skip-prefixed turns.
    """
    brain = tmp_path / "home" / ".brain"
    brain.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    script = PLUGIN / "hooks" / "brain_prompt_check.py"
    for trivial in ("ok", "yes", "no", "continue", "go", "!", "/help"):
        payload = {
            "cwd": str(proj),
            "hook_event_name": "UserPromptSubmit",
            "user_prompt": trivial,
        }
        proc = _run_hook(script, payload, brain, proj)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "", f"must produce no output for trivial prompt: {trivial!r}"


def test_a2_output_schema_valid(tmp_path):
    """Fix 3 — the emitted JSON for a substantive prompt is a valid
    hookSpecificOutput with hookEventName == 'UserPromptSubmit' and a non-empty
    string additionalContext.
    """
    brain = tmp_path / "home" / ".brain"
    brain.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    script = PLUGIN / "hooks" / "brain_prompt_check.py"
    payload = {
        "cwd": str(proj),
        "hook_event_name": "UserPromptSubmit",
        "user_prompt": "The postgres password is in 1Password under prod-db",
    }
    proc = _run_hook(script, payload, brain, proj)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert "hookSpecificOutput" in out
    hso = out["hookSpecificOutput"]
    assert hso.get("hookEventName") == "UserPromptSubmit"
    assert isinstance(hso.get("additionalContext"), str)
    assert hso["additionalContext"]  # non-empty


# --- #167 Fix A (residual): off-level empty brain always emits save policy ----


def test_fix_a_empty_brain_still_emits_save_policy(tmp_path):
    """Fix A — a substantive query prompt against an EMPTY brain (no knowledge,
    no captures) must still emit the save policy.

    Before Fix A: the ``level == "off"`` path hit ``sys.exit(0)`` when
    ``has_knowledge`` and ``has_captures`` were both False, producing NO output.
    After Fix A: every post-trivial-gate exit routes through ``_emit``, so even
    with an empty brain the save policy fires.
    """
    brain = tmp_path / "home" / ".brain"
    brain.mkdir(parents=True)
    # No capture/daily dir, no vault manifests — empty brain.
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
    assert proc.stdout.strip(), "must produce output even with empty brain"
    ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "/fritz:brain-save" in ctx, "save policy must fire even for empty brain"
    assert "SAVE" in ctx


# --- #167 Fix B (residual): short substantive prompts not over-suppressed -----


def test_fix_b_short_substantive_prompt_emits_save_policy(tmp_path):
    """Fix B — a short but substantive prompt (< 15 chars, not a skip prefix)
    must emit the save policy.

    Before Fix B: ``_is_trivial`` returned True for ``len(lower) < 15``, so
    ``"where is db?"`` (12 chars) was suppressed before reaching ``_emit``.
    After Fix B: the length gate is removed; only empty/whitespace and
    SKIP_PREFIXES remain as trivial markers.
    """
    brain = tmp_path / "home" / ".brain"
    brain.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    script = PLUGIN / "hooks" / "brain_prompt_check.py"
    # 12 chars — under the old < 15 gate but not a skip prefix.
    payload = {
        "cwd": str(proj),
        "hook_event_name": "UserPromptSubmit",
        "user_prompt": "where is db?",
    }
    proc = _run_hook(script, payload, brain, proj)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip(), "short substantive prompt must produce output"
    ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "/fritz:brain-save" in ctx, "save policy must fire for short substantive prompts"
    assert "SAVE" in ctx
