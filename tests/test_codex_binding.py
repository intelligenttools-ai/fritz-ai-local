"""Tests for the Codex binding / plugin (issue #66).

The binding is a Codex plugin under ``bindings/codex/``. The plugin half (the
``fritz:brain-*`` skills) is verifiable locally: ``plugin.json`` passes the
Codex ``validate_plugin.py`` validator and the committed skills match fresh
generator output. The hook half (lifecycle wiring) is the open capability —
Codex's exact hook-config schema is not introspectable from the local CLI — so
here we test the underlying Python hook scripts the binding relies on directly
via hook-input JSON on stdin (tmp brain), the same way the Claude binding does.

GUARDRAIL: every capability test points the brain at a tmp dir and overrides
``HOME`` (older hooks key off HOME, not just BRAIN_HOME). The live ``~/.brain``
and ``~/.codex`` are never written.
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
PLUGIN = REPO_ROOT / "bindings" / "codex"
PLUGIN_MANIFEST = PLUGIN / ".codex-plugin" / "plugin.json"
MARKETPLACE = PLUGIN / "marketplace.json"
PLUGIN_SKILLS = PLUGIN / "skills"

# The Codex plugin-creator validator lives in the local Codex skills tree.
CODEX_VALIDATOR = (
    Path.home()
    / ".codex"
    / "skills"
    / ".system"
    / "plugin-creator"
    / "scripts"
    / "validate_plugin.py"
)
# The repo .venv python has PyYAML, which the validator needs.
VENV_PY = REPO_ROOT / ".venv" / "bin" / "python"

PY = sys.executable


def _run_hook(script: Path, payload: dict, brain_home: Path, cwd: Path) -> subprocess.CompletedProcess:
    """Run a hook script with hook-input JSON on stdin (tmp brain + tmp HOME)."""
    env = dict(os.environ)
    env["BRAIN_HOME"] = str(brain_home)
    env["FRITZ_REPO_PATH"] = str(REPO_ROOT)
    # GUARDRAIL: older hooks key off HOME, not BRAIN_HOME. Override HOME too so
    # the live ~/.brain is never written.
    env["HOME"] = str(brain_home.parent)
    return subprocess.run(
        [PY, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
        timeout=30,
    )


def _load(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- plugin.json: valid, required fields, NO `hooks` field ------------------


def test_plugin_json_valid_with_required_fields_and_no_hooks():
    data = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    assert data["name"] == "fritz-brain"
    assert isinstance(data.get("version"), str) and data["version"]
    assert data.get("description")
    assert data.get("skills") == "./skills/"
    # The Codex validator rejects a `hooks` manifest field.
    assert "hooks" not in data, "plugin.json must NOT contain a `hooks` field"
    interface = data.get("interface")
    assert isinstance(interface, dict)
    for field in ("displayName", "shortDescription", "longDescription", "developerName", "category"):
        assert interface.get(field), f"interface.{field} required"
    assert interface.get("defaultPrompt") or interface.get("default_prompt")
    assert isinstance(interface.get("capabilities"), list)


def test_plugin_json_passes_codex_validator():
    """plugin.json + skills pass the real Codex `validate_plugin.py` (exit 0)."""
    if not CODEX_VALIDATOR.is_file():
        pytest.skip("Codex plugin-creator validator not present locally")
    py = str(VENV_PY) if VENV_PY.exists() else PY
    proc = subprocess.run(
        [py, str(CODEX_VALIDATOR), str(PLUGIN)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"validate_plugin.py failed:\n{proc.stdout}\n{proc.stderr}"


# --- marketplace.json: valid local-source entry for fritz-brain -------------


def test_marketplace_json_lists_plugin_with_local_source():
    data = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    assert isinstance(data.get("name"), str) and data["name"]
    plugins = data.get("plugins")
    assert isinstance(plugins, list) and plugins
    fritz = next((p for p in plugins if p.get("name") == "fritz-brain"), None)
    assert fritz is not None, "marketplace must list the fritz-brain plugin"
    source = fritz.get("source")
    assert isinstance(source, dict)
    assert source.get("source") == "local"
    assert source.get("path") == "./plugins/fritz-brain"
    policy = fritz.get("policy")
    assert isinstance(policy, dict)
    assert policy.get("installation") in {"NOT_AVAILABLE", "AVAILABLE", "INSTALLED_BY_DEFAULT"}
    assert policy.get("authentication") in {"ON_INSTALL", "ON_USE"}
    assert fritz.get("category")


# --- skills: committed match generator output (drift) + validate ------------


def test_committed_skills_match_generator_output(tmp_path):
    """Committed fritz:brain-* skills equal fresh generate_variants(..,'codex')."""
    gen = _load("_setup_hyphenated_skills_codex", HOOKS / "setup_hyphenated_skills.py")
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    gen.generate_variants(fresh, "codex", dry_run=False)

    fresh_dirs = sorted(d.name for d in fresh.iterdir() if (d / "SKILL.md").exists())
    committed_dirs = sorted(d.name for d in PLUGIN_SKILLS.iterdir() if (d / "SKILL.md").exists())
    assert committed_dirs == fresh_dirs, "committed skill set drifted from generator"

    for name in fresh_dirs:
        fresh_content = (fresh / name / "SKILL.md").read_text(encoding="utf-8")
        committed_content = (PLUGIN_SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
        assert committed_content == fresh_content, f"{name}/SKILL.md drifted from generator"


def test_committed_skills_validate():
    """Committed skills pass the naming consistency validator for codex."""
    gen = _load("_setup_hyphenated_skills_codex_v", HOOKS / "setup_hyphenated_skills.py")
    errors = gen.validate_variants(PLUGIN_SKILLS, "codex")
    assert errors == [], f"committed skills failed validation: {errors}"


def test_brain_save_skill_is_bundled():
    """C5/C8 — the fritz:brain-save skill is committed in the plugin."""
    assert (PLUGIN_SKILLS / "fritz:brain-save" / "SKILL.md").exists()


def test_skills_use_colon_prefix_not_hyphen():
    """Codex shares the colon namespace; no fritz-brain-* hyphen variants."""
    names = [d.name for d in PLUGIN_SKILLS.iterdir() if (d / "SKILL.md").exists()]
    assert names, "expected committed skills"
    assert all(n.startswith("fritz:") for n in names)
    assert not any(n.startswith("fritz-brain") for n in names)


# --- hook symlinks: single source of truth ----------------------------------


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


# --- legacy codex-hooks.toml disposition: no misleading [[hooks]] -----------


def test_legacy_codex_hooks_toml_does_not_assert_disproven_format():
    """The legacy file must not present the disproven [[hooks]] event/command
    format as authoritative; it should be annotated as disproven."""
    legacy = (HOOKS / "codex-hooks.toml").read_text(encoding="utf-8")
    assert "DISPROVEN" in legacy
    # No live, uncommented [[hooks]] entry with event=/command=.
    for line in legacy.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert not stripped.startswith("[[hooks]]"), (
            "legacy file must not ship an active [[hooks]] entry"
        )


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


# --- C2: UserPromptSubmit BRAIN CHECK ---------------------------------------


def test_prompt_check_emits_brain_check(tmp_path):
    """C2 — guardrail injects a BRAIN CHECK when knowledge/captures exist."""
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


# --- C5: explicit save via the Python core ----------------------------------


def test_save_fact_writes_inbox(tmp_path):
    """C5 — explicit save via brain_save_fact core writes a durable inbox fact."""
    mod = _load("_brain_save_fact_codex", HOOKS / "brain_save_fact.py")
    out = mod.save_fact(
        title="Codex binding test fact",
        body="The Codex marketplace lives at bindings/codex.",
        source="codex-test",
        root=tmp_path,
    )
    assert out.exists()
    assert out.parent == tmp_path / "capture" / "inbox"
    assert "# Codex binding test fact" in out.read_text(encoding="utf-8")


# --- C3: auto-capture bridge dedup ------------------------------------------


def _claude_style_transcript(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


def test_autocapture_bridge_writes_one_inbox_fact_and_dedups(tmp_path):
    """C3 — turn-end hook-input -> bridge -> one inbox capture; rerun dedups."""
    brain = tmp_path / "home" / ".brain"
    brain.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    transcript = proj / "session.jsonl"
    _claude_style_transcript(
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

    proc2 = _run_hook(script, payload, brain, proj)
    assert proc2.returncode == 0, proc2.stderr
    assert "No auto-capture" in proc2.stdout
    assert len(list((brain / "capture" / "inbox").glob("*.md"))) == 1


def test_autocapture_bridge_no_transcript_is_noop(tmp_path):
    """C3 — turn-end hook-input without transcript_path is a safe no-op.

    The Codex turn-end payload shape is not verified locally; the bridge must
    degrade gracefully when it carries no transcript_path.
    """
    brain = tmp_path / "home" / ".brain"
    brain.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    script = PLUGIN / "hooks" / "brain_autocapture_hook.py"
    proc = _run_hook(script, {"cwd": str(proj), "hook_event_name": "Stop"}, brain, proj)
    assert proc.returncode == 0, proc.stderr
    assert "No auto-capture" in proc.stdout
    assert not (brain / "capture" / "inbox").exists() or not list(
        (brain / "capture" / "inbox").glob("*.md")
    )


# --- installer wiring for the codex agent -----------------------------------


def test_install_agent_codex_installs_colon_skills(tmp_path, monkeypatch):
    """install --agent codex installs fritz:brain-* skills to a tmp dir."""
    install = _load("_install_codex_binding", REPO_ROOT / "scripts" / "install.py")

    brain = tmp_path / "home" / ".brain"
    brain.mkdir(parents=True)
    monkeypatch.setenv("BRAIN_HOME", str(brain))
    monkeypatch.setenv("HOME", str(brain.parent))
    monkeypatch.setenv("FRITZ_REPO_PATH", str(REPO_ROOT))
    monkeypatch.delenv("FRITZ_SKILLS_DIR", raising=False)

    skills = tmp_path / "codex-skills"
    rc = install.main(["install", "--agent", "codex", "--skills-dir", str(skills)])
    assert rc == 0
    assert (skills / "fritz:brain-query" / "SKILL.md").exists()
    assert (skills / "fritz:brain-save" / "SKILL.md").exists()
    assert not (skills / "fritz-brain-query").exists()
