"""Tests for issue #239 (epic #235): runtime-aware Local Brain service
instructions + correct Claude Code skill references.

Claude Code registers each plain ``bindings/claude/skills/<name>`` directory
under the fixed plugin name ``fritz-brain`` as ``fritz-brain:<name>``, so the
live Claude slash-command form of a canonical ``/fritz:X`` skill-tree reference
is ``/fritz-brain:X``, not the raw ``/fritz:X`` name. Every other runtime (pi,
Codex, Hermes, unknown) must keep today's ``/fritz:X`` text byte-identical.

CRITICAL: pytest itself runs inside a Claude Code session, so CLAUDECODE /
CLAUDE_CODE_ENTRYPOINT are set in the ambient env and
``_resolve_client_agent()`` resolves to 'claude' by default here. Tests that
need the non-Claude (byte-identical) rendering must clear those markers.
"""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import brain_common  # noqa: E402
import brain_prompt_check  # noqa: E402
import brain_session_start  # noqa: E402

CLAUDE_SKILLS_DIR = ROOT / "bindings" / "claude" / "skills"


def _clear_claude_markers(monkeypatch):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("FRITZ_AGENT", raising=False)


def _set_claude(monkeypatch):
    monkeypatch.delenv("FRITZ_AGENT", raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")


# ---------------------------------------------------------------------------
# claude_form() — the sanitization rule
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ref,expected",
    [
        ("/fritz:brain-query", "/fritz-brain:brain-query"),
        ("/fritz:brain-compile", "/fritz-brain:brain-compile"),
        ("/fritz:brain-sync", "/fritz-brain:brain-sync"),
        ("/fritz:brain-lint", "/fritz-brain:brain-lint"),
        ("/fritz:brain-save", "/fritz-brain:brain-save"),
        ("/fritz:brain-ingest", "/fritz-brain:brain-ingest"),
        ("/fritz:brain-setup", "/fritz-brain:brain-setup"),
        ("/fritz:brain-service-setup", "/fritz-brain:brain-service-setup"),
        ("/fritz:handover", "/fritz-brain:handover"),
        ("/fritz:update", "/fritz-brain:update"),
    ],
)
def test_claude_form_matches_ground_truth(ref, expected):
    assert brain_common.claude_form(ref) == expected


# ---------------------------------------------------------------------------
# Acceptance criterion 2: every skill name emitted into Claude context
# resolves to a registered skill (walk bindings/claude/skills/, apply
# Claude Code's own sanitization rule).
# ---------------------------------------------------------------------------

def _registered_claude_skill_names() -> set[str]:
    names = set()
    for entry in CLAUDE_SKILLS_DIR.iterdir():
        if entry.is_dir():
            names.add(f"/fritz-brain:{entry.name.replace(':', '-')}")
    return names


def test_registered_skill_names_walk_matches_ground_truth():
    names = _registered_claude_skill_names()
    for expected in (
        "/fritz-brain:brain-query",
        "/fritz-brain:brain-compile",
        "/fritz-brain:brain-sync",
        "/fritz-brain:brain-lint",
        "/fritz-brain:brain-save",
        "/fritz-brain:brain-ingest",
        "/fritz-brain:brain-setup",
        "/fritz-brain:brain-service-setup",
        "/fritz-brain:handover",
        "/fritz-brain:update",
    ):
        assert expected in names


_SKILL_REF_RE = re.compile(r"/fritz(?:-brain)?[A-Za-z0-9:_-]*")


def _extract_skill_refs(text: str) -> set[str]:
    return set(_SKILL_REF_RE.findall(text))


def test_all_claude_emitted_skill_names_are_registered(monkeypatch):
    """Walks bindings/claude/skills/ to build the registered-name set, then
    asserts every skill slash-name emitted for the Claude runtime by the
    service instructions and the save policy resolves to a name in that set,
    and that no raw /fritz:* colon-form name leaks through.
    """
    _set_claude(monkeypatch)
    registered = _registered_claude_skill_names()

    monkeypatch.setattr(brain_common, "get_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_common, "get_local_brain_api_token", lambda: None)
    service_instructions = brain_common.local_brain_service_instructions()
    save_policy = brain_prompt_check._save_policy()

    refs = _extract_skill_refs(service_instructions) | _extract_skill_refs(save_policy)
    assert refs, "expected at least one skill ref to check"
    for ref in refs:
        assert ref in registered, f"{ref} does not resolve to a registered Claude skill"


# ---------------------------------------------------------------------------
# local_brain_service_instructions(): Claude leads with MCP tools, non-Claude
# byte-identical (the non-Claude snapshot itself is pinned in
# tests/test_brain_prompt_check.py::test_service_instructions_snapshot_unchanged_for_non_claude_runtime).
# ---------------------------------------------------------------------------

def test_service_instructions_claude_leads_with_mcp_tools(monkeypatch):
    _set_claude(monkeypatch)
    monkeypatch.setattr(brain_common, "get_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_common, "get_local_brain_api_token", lambda: None)

    instructions = brain_common.local_brain_service_instructions()

    assert "brain_search" in instructions
    assert "fallback" in instructions.lower()
    # brain_search / the MCP-tool guidance leads; the curl fallback trails it.
    assert instructions.index("brain_search") < instructions.index("curl")
    # No raw /fritz:brain-* colon-form skill-tree name leaks into Claude context.
    assert "/fritz:brain-" not in instructions


def test_service_instructions_claude_contains_query_guidance(monkeypatch):
    _set_claude(monkeypatch)
    monkeypatch.setattr(brain_common, "get_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_common, "get_local_brain_api_token", lambda: None)

    instructions = brain_common.local_brain_service_instructions()

    assert "SHORT and CONCEPTUAL (2-6 terms)" in instructions
    assert "Good: `proxmox cloudinit template debian`" in instructions
    assert "192.168.1.53" in instructions  # bad-example IP dump


def test_service_instructions_non_claude_contains_query_guidance(monkeypatch):
    _clear_claude_markers(monkeypatch)
    monkeypatch.setattr(brain_common, "get_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_common, "get_local_brain_api_token", lambda: None)

    instructions = brain_common.local_brain_service_instructions()

    assert "SHORT and CONCEPTUAL (2-6 terms)" in instructions
    assert "Good: `proxmox cloudinit template debian`" in instructions


def test_service_instructions_claude_contains_only_registered_skill_names(monkeypatch):
    _set_claude(monkeypatch)
    monkeypatch.setattr(brain_common, "get_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_common, "get_local_brain_api_token", lambda: None)

    instructions = brain_common.local_brain_service_instructions()
    registered = _registered_claude_skill_names()
    for ref in _extract_skill_refs(instructions):
        assert ref in registered, f"{ref} is not a registered Claude skill"


# ---------------------------------------------------------------------------
# SAVE_POLICY runtime-aware (brain_prompt_check._save_policy())
# ---------------------------------------------------------------------------

def test_save_policy_claude_uses_sanitized_name(monkeypatch):
    _set_claude(monkeypatch)
    policy = brain_prompt_check._save_policy()
    assert "/fritz-brain:brain-save" in policy
    assert "/fritz:brain-save" not in policy


def test_save_policy_non_claude_unchanged(monkeypatch):
    _clear_claude_markers(monkeypatch)
    policy = brain_prompt_check._save_policy()
    assert policy == (
        "BRAIN SAVE: If this turn confirms durable operational knowledge "
        "(decisions, fixes, URLs, token/credential locations, runbook facts), SAVE it "
        "via the /fritz:brain-save skill — do not merely answer it."
    )


# ---------------------------------------------------------------------------
# brain_session_start.py: the six slash-command emissions (loci #3/#4/#5 —
# main()'s service_available branches and the minimal-capture compile-pending
# fallback). check_for_updates() and check_service_version_drift() (loci
# #1/#2) are covered directly in tests/test_service_version_drift.py.
# ---------------------------------------------------------------------------

def _run_session_start_main(monkeypatch, capsys, tmp_path, *, service_available: bool) -> str:
    # Isolate from the live ~/.brain: brain_common.BRAIN_HOME/REGISTRY_PATH are
    # read internally by get_context_injection_level() via get_setting().
    monkeypatch.setattr(brain_common, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_common, "REGISTRY_PATH", tmp_path / "registry.yaml")
    monkeypatch.setattr(brain_session_start, "BRAIN_HOME", tmp_path)
    # Disable the two throttled nudges (covered elsewhere) and the PROV3
    # desired/operational forcing block so they don't add unrelated skill refs.
    monkeypatch.setattr(brain_session_start, "get_setting", lambda key, default=None, **kw: False)
    monkeypatch.setattr(brain_session_start, "get_local_brain_service_desired", lambda **kw: "local")
    monkeypatch.setattr(brain_session_start, "local_brain_service_operational", lambda **kw: False)
    monkeypatch.setattr(brain_session_start, "local_brain_service_available", lambda: service_available)
    monkeypatch.setattr(brain_session_start, "local_brain_service_configured", lambda: True)
    monkeypatch.setattr(brain_session_start, "local_brain_service_instructions", lambda: "STUB SERVICE INSTRUCTIONS")
    monkeypatch.setattr(brain_session_start, "resolve_project_vault", lambda cwd: (None, None, None, None))
    monkeypatch.setattr(brain_session_start, "load_registry", lambda: {"vaults": {}})

    hook_input = {"hook_event_name": "SessionStart", "cwd": str(tmp_path)}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook_input)))
    with pytest.raises(SystemExit):
        brain_session_start.main()
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


def test_session_start_service_available_claude_sanitizes_ingest_ref(monkeypatch, capsys, tmp_path):
    _set_claude(monkeypatch)
    ctx = _run_session_start_main(monkeypatch, capsys, tmp_path, service_available=True)
    assert "/fritz-brain:brain-ingest" in ctx
    assert "/fritz:brain-ingest" not in ctx


def test_session_start_service_available_non_claude_unchanged(monkeypatch, capsys, tmp_path):
    _clear_claude_markers(monkeypatch)
    ctx = _run_session_start_main(monkeypatch, capsys, tmp_path, service_available=True)
    assert "Use `/fritz:brain-ingest` for imports" in ctx


def test_session_start_service_unavailable_claude_sanitizes_refs(monkeypatch, capsys, tmp_path):
    _set_claude(monkeypatch)
    ctx = _run_session_start_main(monkeypatch, capsys, tmp_path, service_available=False)
    assert "/fritz-brain:brain-query" in ctx
    assert "/fritz-brain:brain-compile" in ctx
    assert "/fritz-brain:brain-ingest" in ctx
    assert "/fritz:brain-query" not in ctx
    assert "/fritz:brain-compile" not in ctx
    assert "/fritz:brain-ingest" not in ctx


def test_session_start_service_unavailable_non_claude_unchanged(monkeypatch, capsys, tmp_path):
    _clear_claude_markers(monkeypatch)
    ctx = _run_session_start_main(monkeypatch, capsys, tmp_path, service_available=False)
    assert (
        "Use `/fritz:brain-query` to search, `/fritz:brain-compile` to promote captures, "
        "`/fritz:brain-ingest` to import sources." in ctx
    )


def test_session_start_compile_pending_claude_sanitizes_compile_ref(monkeypatch, capsys, tmp_path):
    _set_claude(monkeypatch)
    (tmp_path / ".compile-needed").write_text(
        json.dumps({"since": "2026-01-01T00:00:00", "topics": 2, "processing_active": False}),
        encoding="utf-8",
    )
    ctx = _run_session_start_main(monkeypatch, capsys, tmp_path, service_available=False)
    assert "processing not active" in ctx
    assert "/fritz-brain:brain-compile" in ctx
    assert "run `/fritz:brain-compile` manually" not in ctx


def test_session_start_compile_pending_non_claude_unchanged(monkeypatch, capsys, tmp_path):
    _clear_claude_markers(monkeypatch)
    (tmp_path / ".compile-needed").write_text(
        json.dumps({"since": "2026-01-01T00:00:00", "topics": 2, "processing_active": False}),
        encoding="utf-8",
    )
    ctx = _run_session_start_main(monkeypatch, capsys, tmp_path, service_available=False)
    assert "processing not active" in ctx
    assert "run `/fritz:brain-compile` manually" in ctx


def test_session_start_surfaces_scheduler_compile_failure_alert(monkeypatch, capsys, tmp_path):
    _clear_claude_markers(monkeypatch)
    (tmp_path / ".scheduler-compile-failures.json").write_text(
        json.dumps(
            {
                "count": 3,
                "alert_threshold": 3,
                "since": "2026-07-06T00:59:00",
                "summary": "Scheduler compile completed with 1 errors: oversized.md failed",
            }
        ),
        encoding="utf-8",
    )

    ctx = _run_session_start_main(monkeypatch, capsys, tmp_path, service_available=True)

    assert "Brain scheduler alert" in ctx
    assert "scheduler compile failing since 2026-07-06T00:59:00, see log" in ctx
    assert "oversized.md failed" in ctx


# ---------------------------------------------------------------------------
# GAP 1: local_brain_service_setup_forcing_instruction() runtime-aware.
# ---------------------------------------------------------------------------

def test_forcing_instruction_claude_sanitized(monkeypatch):
    _set_claude(monkeypatch)
    text = brain_common.local_brain_service_setup_forcing_instruction()
    assert "/fritz-brain:brain-service-setup" in text
    assert "/fritz:brain-service-setup" not in text


def test_forcing_instruction_non_claude_unchanged(monkeypatch):
    _clear_claude_markers(monkeypatch)
    text = brain_common.local_brain_service_setup_forcing_instruction()
    # Byte-identical to today's raw text for non-Claude runtimes.
    assert text == (
        "## REQUIRED ACTION: Local Brain Docker Service Not Running\n\n"
        "The registry declares `local_brain_service.desired: docker`, meaning the "
        "Dockerized Local Brain service is the REQUIRED runtime for this install, "
        "but the service is NOT currently operational.\n\n"
        "You MUST run `/fritz:brain-service-setup` before proceeding with any "
        "brain-related work. Do NOT skip this step, do NOT attempt to work around "
        "it by using local slash-skills, and do NOT ask the user for permission — "
        "the desired state has already been declared in the registry.\n\n"
        "After `/fritz:brain-service-setup` completes successfully and the service "
        "responds at its configured URL, continue with the original task."
    )


# ---------------------------------------------------------------------------
# GAP 2: brain_prompt_check off-level brain-check reminder runtime-aware.
# ---------------------------------------------------------------------------

def _render_brain_check_reminder(monkeypatch, capsys, tmp_path, prompt: str) -> str:
    """Run brain_prompt_check.main() in-process through the off-level else
    branch (service unavailable) and return the emitted additionalContext,
    which carries the 'Use <brain-query> ...' reminder plus the save policy."""
    capture_dir = tmp_path / "capture" / "daily"
    capture_dir.mkdir(parents=True)
    (capture_dir / "today.md").write_text("capture", encoding="utf-8")

    monkeypatch.setattr(brain_prompt_check, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_prompt_check, "load_registry", lambda: {"vaults": {"test": {"path": str(tmp_path)}}})
    monkeypatch.setattr(brain_prompt_check, "resolve_project_vault", lambda cwd: (None, None, None, None))
    monkeypatch.setattr(brain_prompt_check, "get_context_injection_level", lambda fritz_local: "off")
    monkeypatch.setattr(brain_prompt_check, "local_brain_service_available", lambda: False)
    monkeypatch.setattr(brain_prompt_check, "local_brain_service_configured", lambda: True)

    hook_input = {"hook_event_name": "UserPromptSubmit", "cwd": str(tmp_path), "user_prompt": prompt}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook_input)))
    with pytest.raises(SystemExit):
        brain_prompt_check.main()
    return json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]


def test_brain_check_reminder_claude_sanitized(monkeypatch, capsys, tmp_path):
    _set_claude(monkeypatch)
    ctx = _render_brain_check_reminder(monkeypatch, capsys, tmp_path, "how did we decide to do auth?")
    assert "BRAIN CHECK: Before answering, search the knowledge base." in ctx
    assert "/fritz-brain:brain-query" in ctx
    assert "/fritz:brain-query" not in ctx


def test_brain_check_reminder_non_claude_unchanged(monkeypatch, capsys, tmp_path):
    _clear_claude_markers(monkeypatch)
    ctx = _render_brain_check_reminder(monkeypatch, capsys, tmp_path, "how did we decide to do auth?")
    assert "Use /fritz:brain-query or search knowledge/ directories" in ctx


# ---------------------------------------------------------------------------
# STRENGTHENED acceptance criterion 2 (comprehensive): with the Claude runtime,
# EVERY emitter of skill names into live Claude context must contain NO raw
# /fritz: colon-form skill command. Sanitized names use the /fritz-brain:
# plugin-qualified prefix, so a bare '/fritz:' substring is the signal of an
# unresolvable skill invocation leaking into Claude context.
#
# BRAIN_SERVICE_PHRASES is input-detection (matches the USER's prompt text), not
# an emission, so it is intentionally excluded here.
# ---------------------------------------------------------------------------

def test_no_raw_fritz_colon_skill_leaks_into_claude_context(monkeypatch, capsys, tmp_path):
    _set_claude(monkeypatch)
    monkeypatch.setattr(brain_common, "get_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_common, "get_local_brain_api_token", lambda: None)

    emitters = {
        "local_brain_service_instructions": brain_common.local_brain_service_instructions(),
        "local_brain_service_setup_forcing_instruction": brain_common.local_brain_service_setup_forcing_instruction(),
        "_save_policy": brain_prompt_check._save_policy(),
        "brain_check_reminder": _render_brain_check_reminder(
            monkeypatch, capsys, tmp_path, "how did we decide to do auth?"
        ),
    }
    registered = _registered_claude_skill_names()
    for name, text in emitters.items():
        assert "/fritz:" not in text, f"{name} leaks a raw /fritz: skill command into Claude context"
        for ref in _extract_skill_refs(text):
            assert ref in registered, f"{name} emits {ref}, which is not a registered Claude skill"
