"""Tests for the Hermes binding (issue #67).

Hermes is a non-coding gateway agent with exactly two shell-hook events —
``pre_llm_call`` and ``on_session_finalize`` — and NO dedicated session-start
event (C1 is folded into ``pre_llm_call`` per the integration contract). The
binding lives under ``bindings/hermes/``: a YAML hook block plus three wrapper
scripts committed as symlinks back to the canonical repo hooks.

GUARDRAIL: every capability test points the brain at a tmp dir and overrides
``HOME`` (older hooks key off HOME, not just BRAIN_HOME) AND ``HERMES_HOME`` to
a tmp dir. The live ``~/.brain`` and ``~/.hermes`` are never written.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = REPO_ROOT / "hooks"
BINDING = REPO_ROOT / "bindings" / "hermes"
BINDING_HOOKS = BINDING / "hooks"
BINDING_YAML = BINDING / "hermes-hooks.yaml"

PY = sys.executable

# Wrapper scripts (canonical) the binding wires.
CONTEXT = HOOKS / "hermes_brain_context.py"
CAPTURE = HOOKS / "hermes_brain_capture.py"
AUTOCAPTURE = HOOKS / "hermes_brain_autocapture.py"
SAVE_FACT = HOOKS / "brain_save_fact.py"


def _brain_env(brain_home: Path, hermes_home: Path | None = None) -> dict:
    """Tmp env: BRAIN_HOME + HOME (older hooks key off HOME) + HERMES_HOME."""
    env = dict(os.environ)
    env["BRAIN_HOME"] = str(brain_home)
    # GUARDRAIL: HOME override so the live ~/.brain / ~/.hermes are never written.
    env["HOME"] = str(brain_home.parent)
    if hermes_home is not None:
        env["HERMES_HOME"] = str(hermes_home)
    # Avoid the upstream git-fetch update check (network) in brain_session_start.
    env.pop("FRITZ_REPO_PATH", None)
    return env


def _run(script: Path, payload: dict, env: dict, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
        timeout=30,
    )


def _make_brain(tmp_path: Path) -> Path:
    """A tmp brain whose hooks/ holds the canonical scripts (as the wrappers
    invoke ``~/.brain/hooks/<script>``) and a fresh update-check stamp so the
    session-start version probe does not touch the network."""
    home = tmp_path / "home"
    brain = home / ".brain"
    hooks_dir = brain / "hooks"
    hooks_dir.mkdir(parents=True)
    for script in HOOKS.glob("*.py"):
        (hooks_dir / script.name).symlink_to(script)
    (brain / ".update-check").write_text(str(time.time()))
    return brain


def _sample_session(hermes_home: Path, session_id: str, *, durable: bool) -> Path:
    """Write a sample Hermes JSONL session under ``$HERMES_HOME/sessions``."""
    sessions = hermes_home / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    if durable:
        user = (
            "Remember this for future sessions: the forgejo server is at "
            "https://git.example.com and the api token location is "
            "~/.config/secrets/forgejo."
        )
        assistant = "Noted — saving the forgejo server URL and token location to the brain."
    else:
        user = "What time is it in Tokyo right now?"
        assistant = "It is currently afternoon in Tokyo."
    lines = [
        {"role": "session_meta", "session_id": session_id},
        {"role": "user", "content": user},
        {
            "role": "assistant",
            "content": assistant,
            "tool_calls": [{"function": {"name": "save_fact"}}],
        },
    ]
    path = sessions / f"{session_id}.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return path


def _load(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- Binding layout: yaml validity + symlinks -------------------------------


@pytest.mark.skipif(yaml is None, reason="PyYAML not installed")
def test_binding_yaml_valid_and_wires_events():
    data = yaml.safe_load(BINDING_YAML.read_text(encoding="utf-8"))
    assert data.get("hooks_auto_accept") is True
    hooks = data["hooks"]
    # C1 (folded) + C2 on pre_llm_call.
    pre = [h["command"] for h in hooks["pre_llm_call"]]
    assert any("hermes_brain_context.py" in c for c in pre)
    # C5 daily capture + C4 autocapture on on_session_finalize.
    finalize = [h["command"] for h in hooks["on_session_finalize"]]
    assert any("hermes_brain_capture.py" in c for c in finalize)
    assert any("hermes_brain_autocapture.py" in c for c in finalize)
    assert len(finalize) == 2, "finalize must run daily capture + autocapture"


def test_no_dedicated_session_start_event():
    """Hermes has no session-start event; C1 must NOT be wired to one."""
    text = BINDING_YAML.read_text(encoding="utf-8")
    assert "session_start" not in text and "SessionStart" not in text


def test_binding_hook_symlinks_resolve_into_repo_hooks():
    for name in (
        "hermes_brain_context.py",
        "hermes_brain_capture.py",
        "hermes_brain_autocapture.py",
    ):
        link = BINDING_HOOKS / name
        assert link.is_symlink(), f"{name} must be a committed symlink"
        assert link.resolve() == (HOOKS / name).resolve()
        assert link.resolve().parent == HOOKS.resolve()


# --- C1: context injection on pre_llm_call ----------------------------------


def test_c1_context_injection_emits_hermes_shape(tmp_path):
    brain = _make_brain(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    env = _brain_env(brain)
    proc = _run(CONTEXT, {"cwd": str(proj), "event_type": "pre_llm_call"}, env, proj)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert "context" in out
    assert out["context"].strip(), "context must be non-empty"
    assert "Brain System Active" in out["context"]


# --- C5: daily capture on on_session_finalize -------------------------------


def test_c5_daily_capture_from_hermes_session(tmp_path):
    brain = _make_brain(tmp_path)
    hermes_home = tmp_path / "hermes-profile"
    _sample_session(hermes_home, "sess-c5", durable=False)
    env = _brain_env(brain, hermes_home)
    proc = _run(CAPTURE, {"session_id": "sess-c5"}, env, tmp_path)
    assert proc.returncode == 0, proc.stderr
    daily = list((brain / "capture" / "daily").glob("*.md"))
    assert daily, "daily capture should be written"


# --- C4: durable auto-capture on on_session_finalize ------------------------


def test_c4_autocapture_writes_one_fact_then_dedups(tmp_path):
    brain = _make_brain(tmp_path)
    hermes_home = tmp_path / "hermes-profile"
    _sample_session(hermes_home, "sess-c4", durable=True)
    env = _brain_env(brain, hermes_home)

    proc = _run(AUTOCAPTURE, {"session_id": "sess-c4", "cwd": "/work"}, env, tmp_path)
    assert proc.returncode == 0, proc.stderr
    inbox = brain / "capture" / "inbox"
    facts = list(inbox.glob("*.md"))
    assert len(facts) == 1, f"expected exactly one inbox fact, got {facts}"
    assert list((brain / "capture" / "auto").glob("*.seen")), "dedup marker written"

    # Rerun → dedup no-op.
    proc2 = _run(AUTOCAPTURE, {"session_id": "sess-c4", "cwd": "/work"}, env, tmp_path)
    assert proc2.returncode == 0, proc2.stderr
    assert len(list(inbox.glob("*.md"))) == 1, "rerun must not write a second fact"


def test_c4_no_signal_session_captures_nothing(tmp_path):
    brain = _make_brain(tmp_path)
    hermes_home = tmp_path / "hermes-profile"
    _sample_session(hermes_home, "sess-nosig", durable=False)
    env = _brain_env(brain, hermes_home)
    proc = _run(AUTOCAPTURE, {"session_id": "sess-nosig"}, env, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert not list((brain / "capture" / "inbox").glob("*.md"))


def test_c4_autocapture_does_not_write_a_daily_capture(tmp_path):
    """The autocapture step must write ONLY inbox + .seen, never a daily file."""
    brain = _make_brain(tmp_path)
    hermes_home = tmp_path / "hermes-profile"
    _sample_session(hermes_home, "sess-nodaily", durable=True)
    env = _brain_env(brain, hermes_home)
    proc = _run(AUTOCAPTURE, {"session_id": "sess-nodaily"}, env, tmp_path)
    assert proc.returncode == 0, proc.stderr
    daily_dir = brain / "capture" / "daily"
    daily = list(daily_dir.glob("*.md")) if daily_dir.exists() else []
    assert not daily, "autocapture must NOT produce a daily capture"


# --- HERMES_HOME: non-default profile resolution ----------------------------


def test_non_default_hermes_home_resolution(tmp_path):
    """A NON-default HERMES_HOME (not ~/.hermes) is honored for transcript lookup."""
    brain = _make_brain(tmp_path)
    hermes_home = tmp_path / "hermes-infra-profile"  # deliberately non-default
    _sample_session(hermes_home, "sess-custom", durable=True)
    env = _brain_env(brain, hermes_home)
    proc = _run(AUTOCAPTURE, {"session_id": "sess-custom"}, env, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert len(list((brain / "capture" / "inbox").glob("*.md"))) == 1


def test_resolve_transcript_finds_session_in_custom_home(tmp_path, monkeypatch):
    """Unit-level: resolve_transcript reads from $HERMES_HOME, resolved at call time."""
    cap = _load("hermes_brain_capture", CAPTURE)
    hermes_home = tmp_path / "alt-hermes"
    path = _sample_session(hermes_home, "sess-unit", durable=False)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    resolved = cap.resolve_transcript("sess-unit")
    assert resolved == path
    # Unknown id in a single-session dir falls back to the lone transcript.
    assert cap.resolve_transcript("") == path


# --- Adapter: parse a sample Hermes transcript ------------------------------


def test_hermes_adapter_parses_sample_transcript(tmp_path):
    sys.path.insert(0, str(REPO_ROOT))
    from adapters.hermes import HermesAdapter  # noqa: E402

    hermes_home = tmp_path / "hermes-profile"
    path = _sample_session(hermes_home, "sess-adapter", durable=True)
    entry = HermesAdapter().parse(path)
    assert any("forgejo" in t for t in entry.topics), entry.topics
    assert entry.key_responses, "assistant response should be captured"
    assert "save_fact" in entry.tools_used


# --- C3: brain_save_fact CLI ------------------------------------------------


def test_c3_brain_save_fact_writes_one_inbox_fact(tmp_path):
    brain = _make_brain(tmp_path)
    env = _brain_env(brain)
    fact = {
        "title": "Hermes gateway deploy note",
        "body": "Restart the gateway via systemctl after config merge.",
        "tags": ["FritzBrain", "Hermes"],
    }
    proc = subprocess.run(
        [PY, str(SAVE_FACT), "--json"],
        input=json.dumps(fact),
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    facts = list((brain / "capture" / "inbox").glob("*.md"))
    assert len(facts) == 1, f"expected one inbox fact, got {facts}"
    assert "Hermes gateway deploy note" in facts[0].read_text()


# --- C8 skills decision: install --agent hermes is N/A, does not crash -------


def test_install_agent_hermes_is_not_a_skills_agent():
    """Hermes has no skills mechanism — install must reject --agent hermes
    cleanly (argparse error, not a crash) rather than fake a skills install."""
    proc = subprocess.run(
        [PY, str(REPO_ROOT / "scripts" / "install.py"), "install", "--agent", "hermes", "--dry-run"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "hermes" not in proc.stdout, "hermes must not be an accepted skills agent"
    assert "invalid choice" in proc.stderr.lower()
