"""Tests for the agent registry / shared factory (WI13, Part B).

Every fleet agent is built via llm.build_model(settings) inside its builder.
The registry exposes AGENT_KINDS, get_agent_builder, and build_agent so
callers can construct agents uniformly without importing each module directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fritz_local_brain.agents import (
    AGENT_BUILDERS,
    AGENT_KINDS,
    build_agent,
    build_mirror_agent,
    build_reconciliation_agent,
    get_agent_builder,
)
from fritz_local_brain.agents.compile_agent import build_compile_agent
from fritz_local_brain.config import Settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(tmp_path: Path) -> Settings:
    brain_home = tmp_path / "brain"
    brain_home.mkdir(parents=True, exist_ok=True)
    return Settings(_env_file=None, LOCAL_BRAIN_HOME=brain_home)


# ---------------------------------------------------------------------------
# Registry membership
# ---------------------------------------------------------------------------


def test_agent_kinds_contains_all_three() -> None:
    assert "compile" in AGENT_KINDS
    assert "reconciliation" in AGENT_KINDS
    assert "mirror" in AGENT_KINDS


def test_agent_kinds_is_tuple() -> None:
    assert isinstance(AGENT_KINDS, tuple)


def test_agent_builders_keys_match_kinds() -> None:
    assert set(AGENT_BUILDERS.keys()) == set(AGENT_KINDS)


# ---------------------------------------------------------------------------
# get_agent_builder
# ---------------------------------------------------------------------------


def test_get_agent_builder_compile() -> None:
    assert get_agent_builder("compile") is build_compile_agent


def test_get_agent_builder_reconciliation() -> None:
    assert get_agent_builder("reconciliation") is build_reconciliation_agent


def test_get_agent_builder_mirror() -> None:
    assert get_agent_builder("mirror") is build_mirror_agent


def test_get_agent_builder_unknown_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown agent"):
        get_agent_builder("nonexistent")


def test_get_agent_builder_empty_string_raises_value_error() -> None:
    with pytest.raises(ValueError):
        get_agent_builder("")


# ---------------------------------------------------------------------------
# build_agent smoke tests
# ---------------------------------------------------------------------------


def test_build_agent_reconciliation_returns_agent(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    from pydantic_ai import Agent

    agent = build_agent("reconciliation", settings)
    assert isinstance(agent, Agent)


def test_build_agent_mirror_returns_agent(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    from pydantic_ai import Agent

    agent = build_agent("mirror", settings)
    assert isinstance(agent, Agent)


def test_build_agent_compile_with_skill_text(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    from pydantic_ai import Agent

    agent = build_agent("compile", settings, skill_text="# Compile Skill\n")
    assert isinstance(agent, Agent)


def test_build_agent_unknown_raises_value_error(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(ValueError, match="Unknown agent"):
        build_agent("unknown-agent", settings)


# ---------------------------------------------------------------------------
# No circular imports
# ---------------------------------------------------------------------------


def test_agent_modules_do_not_import_agents_init() -> None:
    """The agent sub-modules must not import agents/__init__ (circular import guard)."""
    import importlib
    import sys

    # Clear any cached modules to get a fresh import.
    for key in list(sys.modules.keys()):
        if key.startswith("fritz_local_brain.agents."):
            del sys.modules[key]

    # Re-importing each sub-module should succeed without raising ImportError.
    for mod_name in (
        "fritz_local_brain.agents.compile_agent",
        "fritz_local_brain.agents.reconciliation_agent",
        "fritz_local_brain.agents.mirror_agent",
    ):
        importlib.import_module(mod_name)
