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
# Codex marketplace layout (codex 0.144.1): the marketplace root is bindings/codex,
# holding .agents/plugins/marketplace.json and plugins/<name>/ for each plugin.
BINDING_ROOT = REPO_ROOT / "bindings" / "codex"
PLUGIN = BINDING_ROOT / "plugins" / "fritz-brain"
PLUGIN_MANIFEST = PLUGIN / ".codex-plugin" / "plugin.json"
MARKETPLACE = BINDING_ROOT / ".agents" / "plugins" / "marketplace.json"
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


def test_plugin_json_valid_with_required_fields_and_wired_hooks():
    data = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    assert data["name"] == "fritz-brain"
    assert isinstance(data.get("version"), str) and data["version"]
    assert data.get("description")
    assert data.get("skills") == "./skills/"
    # Lifecycle hooks are wired via a hooks.json companion (codex 0.144.1 fires
    # them — verified: SessionStart/UserPromptSubmit/Stop with Claude-style stdin).
    assert data.get("hooks") == "./hooks.json"
    assert (PLUGIN / "hooks.json").is_file()
    interface = data.get("interface")
    assert isinstance(interface, dict)
    for field in ("displayName", "shortDescription", "longDescription", "developerName", "category"):
        assert interface.get(field), f"interface.{field} required"
    assert interface.get("defaultPrompt") or interface.get("default_prompt")
    assert isinstance(interface.get("capabilities"), list)


def test_plugin_json_passes_codex_validator_modulo_stale_hooks_field():
    """plugin.json + skills pass the real Codex `validate_plugin.py`, except the
    bundled validator is stale re: the `hooks` field.

    The plugin-json spec (plugin-json-spec.md) lists `hooks` as a valid field and
    codex 0.144.1 install+runtime accept and FIRE it (verified end-to-end), but
    the bundled validate_plugin.py still rejects `hooks`. So we accept a non-zero
    exit only when the sole complaint is that stale `hooks`-field rejection — any
    OTHER validation error must still fail the test.
    """
    if not CODEX_VALIDATOR.is_file():
        pytest.skip("Codex plugin-creator validator not present locally")
    py = str(VENV_PY) if VENV_PY.exists() else PY
    proc = subprocess.run(
        [py, str(CODEX_VALIDATOR), str(PLUGIN)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode == 0:
        return
    complaints = [
        ln.strip()
        for ln in proc.stdout.splitlines()
        if ln.strip().startswith("-")
    ]
    assert complaints, f"validator failed with no itemized complaint:\n{proc.stdout}\n{proc.stderr}"
    non_hooks = [c for c in complaints if "`hooks`" not in c and "hooks " not in c]
    assert non_hooks == [], f"validator raised non-hooks errors:\n{non_hooks}"


# --- hooks.json: wires the lifecycle events to the canonical brain hooks -----


def test_hooks_json_wires_lifecycle_events():
    """hooks.json fires the brain hooks on codex's SessionStart / UserPromptSubmit
    / Stop events (Claude-style schema codex 0.144.1 honors)."""
    data = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    hooks = data["hooks"]
    assert set(hooks) == {"SessionStart", "UserPromptSubmit", "Stop"}

    def commands(event):
        return [h["command"] for block in hooks[event] for h in block["hooks"]]

    assert any("brain_session_start.py" in c for c in commands("SessionStart"))
    assert any("brain_prompt_check.py" in c for c in commands("UserPromptSubmit"))
    stop_cmds = commands("Stop")
    assert any("brain_capture.py" in c for c in stop_cmds)
    assert any("brain_autocapture_hook.py" in c for c in stop_cmds)
    # Commands target the installed canonical hooks (real files), not the plugin's
    # outward symlinks (which would break when copied into the codex plugin cache).
    for c in stop_cmds:
        assert ".brain/hooks/" in c


def test_codex_adapter_detects_and_parses_rollout(tmp_path, monkeypatch):
    """The codex rollout transcript is detected as codex (even though codex sets
    CLAUDE_PLUGIN_ROOT) and parsed into a real CaptureEntry."""
    import sys
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    from adapters.base import TranscriptAdapter
    from adapters.registry import parse_transcript

    # Codex fires hooks with a Claude-style payload AND sets CLAUDE_PLUGIN_ROOT;
    # detection must still resolve to codex via the rollout transcript path.
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/Users/x/.codex/plugins/cache/fritz-local/fritz-brain/1.0.0")
    hook_input = {
        "hook_event_name": "Stop",
        "cwd": "/proj",
        "transcript_path": "/Users/x/.codex/sessions/2026/07/23/rollout-abc.jsonl",
        "permission_mode": "bypassPermissions",
    }
    assert TranscriptAdapter.detect(hook_input) == "codex"

    roll = tmp_path / ".codex" / "sessions" / "rollout-abc.jsonl"
    roll.parent.mkdir(parents=True)
    roll.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {"type": "session_meta", "payload": {"cwd": "/proj"}},
                {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "<permissions ...>"}]}},
                {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "How do I tune the WireGuard MTU?"}]}},
                {"type": "response_item", "payload": {"type": "function_call", "name": "shell"}},
                {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Set the MTU to 1420 to avoid fragmentation over the tunnel."}]}},
            ]
        ),
        encoding="utf-8",
    )
    hook_input["transcript_path"] = str(roll)
    entry = parse_transcript(hook_input, str(roll))
    assert entry.agent == "codex"
    assert any("wireguard" in t.lower() for t in entry.topics)
    assert not any(t.startswith("<") for t in entry.topics)  # developer/system skipped
    assert "shell" in entry.tools_used
    assert entry.key_responses
    assert not entry.is_empty()


def test_codex_adapter_prefers_final_answer_and_finds_user_past_window(tmp_path, monkeypatch):
    """A tool-heavy turn (many non-message records + commentary messages) must
    still surface the user prompt and the FINAL answer, not progress chatter."""
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    from adapters.codex import CodexAdapter

    rows = [{"type": "response_item", "payload": {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "How should we structure the gateway failover?"}]}}]
    # hundreds of interleaved reasoning/tool records after the user prompt
    for i in range(300):
        rows.append({"type": "response_item", "payload": {"type": "custom_tool_call", "name": "apply_patch"}})
        rows.append({"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "commentary",
                     "content": [{"type": "output_text", "text": f"Working on step {i} of the failover plan now."}]}})
    rows.append({"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer",
                 "content": [{"type": "output_text", "text": "Use active-passive with VRRP and a 2s health check."}]}})

    roll = tmp_path / "rollout-big.jsonl"
    roll.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    entry = CodexAdapter().parse(roll)
    assert any("failover" in t.lower() for t in entry.topics), "user prompt lost outside window"
    assert any("VRRP" in r for r in entry.key_responses), "final answer discarded for commentary"
    assert not any("Working on step" in r for r in entry.key_responses)
    assert "apply_patch" in entry.tools_used


def test_codex_adapter_survives_malformed_lines(tmp_path, monkeypatch):
    """A Stop hook must never crash: non-object lines, scalar payloads, and
    non-string text/name values are skipped, not fatal."""
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    from adapters.codex import CodexAdapter

    lines = [
        "[]",                                   # valid JSON, not an object
        "null",                                 # valid JSON scalar
        json.dumps({"payload": 7}),             # scalar payload
        json.dumps({"payload": {"type": "message", "role": "assistant",
                                "content": [{"type": "output_text", "text": 7}]}}),  # non-str text
        json.dumps({"payload": {"type": "function_call", "name": ["not", "hashable"]}}),  # non-str name
        json.dumps({"payload": {"type": "message", "role": "user",
                                "content": [{"type": "input_text", "text": "real question about DNS?"}]}}),
        "{ not json",                           # invalid JSON
    ]
    roll = tmp_path / "rollout-bad.jsonl"
    roll.write_text("\n".join(lines), encoding="utf-8")
    entry = CodexAdapter().parse(roll)  # must not raise
    assert entry.agent == "codex"
    assert any("DNS" in t for t in entry.topics)


def test_codex_detection_requires_codex_path_and_rollout_basename(monkeypatch):
    """A Claude/Pi transcript that merely happens to be named rollout-*.jsonl must
    NOT be misrouted to codex."""
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    from adapters.base import TranscriptAdapter

    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    claude_like = {"hook_event_name": "Stop", "cwd": "/w",
                   "transcript_path": "/Users/x/.claude/projects/work/rollout-review.jsonl"}
    assert TranscriptAdapter.detect(claude_like) == "claude_code"
    real_codex = {"hook_event_name": "Stop", "cwd": "/w",
                  "transcript_path": "/Users/x/.codex/sessions/2026/07/23/rollout-abc.jsonl"}
    assert TranscriptAdapter.detect(real_codex) == "codex"


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
