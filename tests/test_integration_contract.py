"""Contract / documentation tests for issue #63.

The integration contract + binding kit must be self-sufficient: a non-first-class
runtime's agent could produce a conformant binding from them alone. We can't run
a real external agent loop, so instead we assert the kit is demonstrably complete
— the contract covers every required section, the canonical-event mapping names
all four first-class runtimes, the template kit references the contract /
checklist / installer, and each guidance note states mechanism, an event mapping,
and open unknowns.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT = REPO_ROOT / "docs" / "integration-contract.md"
TEMPLATE_DIR = REPO_ROOT / "bindings" / "_template"
TEMPLATE_PROMPT = TEMPLATE_DIR / "INITIAL_PROMPT.md"
OPENCLAW_NOTE = REPO_ROOT / "docs" / "bindings" / "openclaw.md"
ANTIGRAVITY_NOTE = REPO_ROOT / "docs" / "bindings" / "antigravity.md"


@pytest.fixture(scope="module")
def contract_text() -> str:
    assert CONTRACT.exists(), f"missing {CONTRACT}"
    return CONTRACT.read_text(encoding="utf-8")


# --- The contract exists and covers every required section -----------------

def test_contract_exists(contract_text: str) -> None:
    assert contract_text.strip(), "integration-contract.md is empty"


def test_contract_has_canonical_events_section(contract_text: str) -> None:
    lower = contract_text.lower()
    assert "canonical event" in lower
    # The five canonical events must be named.
    for phrase in ("session start", "brain check", "agent end",
                   "session end", "explicit save"):
        assert phrase in lower, f"canonical events missing '{phrase}'"


def test_contract_documents_hook_json_protocol(contract_text: str) -> None:
    # The actual on-the-wire field names the hooks read/emit.
    assert "additionalContext" in contract_text
    assert "hook_event_name" in contract_text
    assert "transcript_path" in contract_text
    assert "stdin" in contract_text.lower() and "stdout" in contract_text.lower()


def test_contract_documents_adapter_interface(contract_text: str) -> None:
    assert "CaptureEntry" in contract_text
    assert "parse" in contract_text
    assert "detect" in contract_text
    assert "TranscriptAdapter" in contract_text


def test_contract_documents_config_precedence(contract_text: str) -> None:
    lower = contract_text.lower()
    assert "precedence" in lower
    assert ".fritz-local.json" in contract_text
    assert "registry.yaml" in contract_text
    # project > central > default ordering must be expressed.
    assert "project" in lower and "central" in lower and "default" in lower


def test_contract_documents_skill_naming_rule(contract_text: str) -> None:
    lower = contract_text.lower()
    assert "skill" in lower and "naming" in lower
    # plain source -> per-platform prefixes.
    assert "fritz:" in contract_text  # claude / codex
    assert "fritz-" in contract_text  # pi


def test_contract_has_nine_capability_checklist(contract_text: str) -> None:
    lower = contract_text.lower()
    assert "capability checklist" in lower
    # All nine capability keywords must appear in the checklist.
    keywords = [
        "context injection",
        "guardrail",
        "save",
        "auto-capture",
        "capture",
        "mode detection",
        "bootstrap",
        "skills",
        "config",
    ]
    for kw in keywords:
        assert kw in lower, f"capability keyword missing: {kw!r}"
    assert len(keywords) == 9
    # Links back to the source spec.
    assert "capability-spec.md" in contract_text


def test_canonical_mapping_references_all_first_class_runtimes(contract_text: str) -> None:
    lower = contract_text.lower()
    for runtime in ("claude", "codex", "pi", "hermes"):
        assert runtime in lower, f"mapping does not reference runtime: {runtime}"


# --- The binding template kit ----------------------------------------------

def test_template_dir_exists_with_readme_and_prompt() -> None:
    assert TEMPLATE_DIR.is_dir(), f"missing {TEMPLATE_DIR}"
    readme = TEMPLATE_DIR / "README.md"
    assert readme.exists(), "bindings/_template/README.md missing"
    assert readme.read_text(encoding="utf-8").strip()
    assert TEMPLATE_PROMPT.exists(), "bindings/_template/INITIAL_PROMPT.md missing"


def test_initial_prompt_references_contract_checklist_and_installer() -> None:
    text = TEMPLATE_PROMPT.read_text(encoding="utf-8")
    assert "integration-contract.md" in text, "INITIAL_PROMPT must reference the contract"
    lower = text.lower()
    assert "capability checklist" in lower or "capability-spec.md" in text, \
        "INITIAL_PROMPT must reference the capability checklist"
    assert "scripts/install.py" in text, "INITIAL_PROMPT must reference the installer"


# --- Guidance notes for non-first-class runtimes ---------------------------

@pytest.mark.parametrize("note_path", [OPENCLAW_NOTE, ANTIGRAVITY_NOTE])
def test_binding_note_states_mechanism_mapping_and_unknowns(note_path: Path) -> None:
    assert note_path.exists(), f"missing guidance note {note_path}"
    text = note_path.read_text(encoding="utf-8")
    lower = text.lower()
    assert "mechanism" in lower, f"{note_path.name} must state the native mechanism"
    # An event mapping must be present (references the canonical events).
    assert "canonical event" in lower and "mapping" in lower, \
        f"{note_path.name} must contain a canonical-event mapping"
    assert "open unknown" in lower, f"{note_path.name} must state open unknowns"
