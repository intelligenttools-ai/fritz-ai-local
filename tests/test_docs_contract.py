"""Docs-match-shipped-behavior tests for issue #68.

The rewritten docs must describe the contract-first, location-independent,
four-platform model that actually ships. These tests assert the load-bearing
facts a reader depends on:

  - a per-platform install walkthrough exists for all four first-class runtimes
    (Claude plugin/marketplace with NO settings.json hook edit, pi extension,
    Codex `codex plugin`, Hermes YAML profile merge / HERMES_HOME),
  - README documents the four platforms, the nine-capability bar, and location
    independence,
  - the adopter path (integration contract + template + INITIAL_PROMPT) is
    documented,
  - the capture layout (inbox / daily / auto + log.md) is referenced with roles,
  - config precedence (project > central > defaults) is referenced,
  - no manual-hook-edit instruction for the plugin/extension platforms,
  - `.fritz-ai-local` only ever appears in an optional/example/default context,
    never stated as a required path.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
SETUP = REPO_ROOT / "SETUP.md"
DOCS = REPO_ROOT / "docs"

DOC_GLOB = [README, SETUP] + sorted(DOCS.glob("*.md"))


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def setup() -> str:
    return SETUP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def setup_and_readme(readme: str, setup: str) -> str:
    return readme + "\n" + setup


# --- Per-platform install walkthroughs (all four) --------------------------


def test_claude_walkthrough_plugin_marketplace(setup: str) -> None:
    low = setup.lower()
    assert "claude code" in low
    # Plugin / marketplace mechanism, not manual settings edits.
    assert "plugin" in low
    assert "marketplace" in low
    assert "/plugin marketplace add" in setup
    assert "fritz-brain@fritz-local" in setup


def test_claude_walkthrough_no_settings_json_hook_edit(setup: str) -> None:
    """The Claude section must NOT instruct editing settings.json hooks."""
    # Isolate the Claude Code section.
    m = re.search(
        r"## Platform: Claude Code\n(.*?)(?:\n## |\Z)", setup, re.DOTALL
    )
    assert m, "no '## Platform: Claude Code' section found in SETUP.md"
    section = m.group(1).lower()
    # It should tell the reader to enable the plugin.
    assert "enable the plugin" in section or "enabling the plugin" in section
    # It must NOT instruct editing settings.json hooks by hand. Any mention of
    # settings.json in the Claude section must be a negative ("no manual edits" /
    # "do not edit by hand"), checked over a small surrounding window so phrasing
    # split across lines still resolves.
    lines = section.splitlines()
    for i, line in enumerate(lines):
        if "settings.json" not in line:
            continue
        window = " ".join(lines[max(0, i - 1) : i + 2])
        negated = (
            "no manual" in window
            or ("not" in window and ("edit" in window or "hand" in window))
        )
        assert negated, (
            "Claude section appears to instruct editing settings.json "
            f"hooks: {window!r}"
        )


def test_pi_walkthrough_extension(setup: str) -> None:
    low = setup.lower()
    assert re.search(r"## platform: pi", low)
    assert "extension" in low
    # The pi extension bootstrap command.
    assert "/fritz" in setup


def test_codex_walkthrough_plugin(setup: str) -> None:
    low = setup.lower()
    assert "## platform: codex" in low
    assert "codex plugin marketplace add" in setup
    assert "codex plugin add" in setup
    # Hooks honestly marked as requiring in-Codex verification (not overstated).
    assert "requires-in-codex-verification" in low


def test_hermes_walkthrough_yaml_merge_and_home(setup: str) -> None:
    low = setup.lower()
    assert "## platform: hermes" in low
    # YAML profile merge.
    assert "hermes-hooks.yaml" in setup
    assert "config.yaml" in setup
    assert "hooks_auto_accept: true" in setup
    # HERMES_HOME honored.
    assert "HERMES_HOME" in setup


def test_hermes_is_not_an_agent_flag(setup: str) -> None:
    """Hermes is bootstrapped via YAML merge, not `install.py --agent hermes`."""
    low = setup.lower()
    assert "hermes is not an" in low or "not an `--agent`" in low


def test_shared_bootstrap_install_commands(setup: str) -> None:
    assert "scripts/install.py install --agent" in setup
    for sub in ("status", "smoke-test", "--dry-run"):
        assert sub in setup, f"missing install.py surface: {sub}"


# --- README: four platforms, capability bar, location independence ---------


def test_readme_documents_four_platforms(readme: str) -> None:
    low = readme.lower()
    for platform in ("claude code", "pi", "codex", "hermes"):
        assert platform in low, f"README missing platform: {platform}"


def test_readme_documents_nine_capability_bar(readme: str) -> None:
    low = readme.lower()
    assert "nine" in low or "9-capability" in low or "9 capability" in low
    # Capability keywords for the bar.
    for kw in (
        "context injection",
        "guardrail",
        "auto-capture",
        "mode detection",
        "skills",
        "per-project",
    ):
        assert kw in low, f"README missing capability keyword: {kw}"
    # Explicit save target.
    assert "brain_save_fact" in readme


def test_readme_references_location_independence(readme: str) -> None:
    low = readme.lower()
    assert "location independence" in low or "location-independent" in low
    assert "FRITZ_REPO_PATH" in readme
    assert "anywhere" in low


# --- Adopter path ----------------------------------------------------------


def test_adopter_path_documented(setup_and_readme: str) -> None:
    assert "docs/integration-contract.md" in setup_and_readme
    assert "bindings/_template/INITIAL_PROMPT.md" in setup_and_readme


# --- Capture layout reference ----------------------------------------------


def test_capture_layout_referenced(setup_and_readme: str) -> None:
    text = setup_and_readme
    low = text.lower()
    for sub in ("inbox", "daily", "auto"):
        assert sub in low, f"capture layout missing: {sub}"
    assert "log.md" in low
    # Roles: explicit, automatic/session, dedup.
    assert "explicit" in low
    assert "dedup" in low


# --- Config precedence -----------------------------------------------------


def test_config_precedence_referenced(setup_and_readme: str) -> None:
    text = setup_and_readme.lower()
    # project > central > defaults, in that order.
    assert "project" in text and "central" in text and "default" in text
    # The precedence chain stated explicitly somewhere.
    assert re.search(r"project.*>.*central.*>.*default", text), (
        "precedence 'project > central > defaults' not stated"
    )
    assert "configuration.md" in setup_and_readme


# --- Location independence: .fritz-ai-local only as optional example -------


_OK_CONTEXT = ("optional", "example", "default", "e.g.", "anywhere")
_REQUIRED_WORDS = ("required", "must be cloned", "clone to", "canonical location")


def test_fritz_ai_local_never_stated_as_required() -> None:
    """Every `.fritz-ai-local` mention must be optional/example, not required."""
    offenders: list[str] = []
    for doc in DOC_GLOB:
        if not doc.exists():
            continue
        for i, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            if ".fritz-ai-local" not in line:
                continue
            low = line.lower()
            # A line may not POSITIVELY assert it is a required path. A line that
            # uses a "required"-ish word while also negating it ("not required",
            # "only an optional example") is fine.
            asserts_required = any(w in low for w in _REQUIRED_WORDS)
            negates = any(w in low for w in _OK_CONTEXT) or "not " in low
            if asserts_required and not negates:
                offenders.append(f"{doc.name}:{i}: {line.strip()}")
    assert not offenders, (
        "`.fritz-ai-local` stated as required:\n" + "\n".join(offenders)
    )


def test_fritz_ai_local_appears_in_optional_context() -> None:
    """Each `.fritz-ai-local` occurrence sits near an optional/example marker.

    The marker may be on the same line or within a small window of surrounding
    lines (so a labeled 'optional example' paragraph counts).
    """
    offenders: list[str] = []
    for doc in DOC_GLOB:
        if not doc.exists():
            continue
        lines = doc.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if ".fritz-ai-local" not in line:
                continue
            window = "\n".join(lines[max(0, i - 3) : i + 4]).lower()
            if not any(w in window for w in _OK_CONTEXT):
                offenders.append(f"{doc.name}:{i + 1}: {line.strip()}")
    assert not offenders, (
        "`.fritz-ai-local` not in an optional/example context:\n"
        + "\n".join(offenders)
    )
