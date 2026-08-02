"""Agents must emit exactly ONE system message (#337).

pydantic-ai maps ``system_prompt=`` and ``instructions=`` to two SEPARATE system
messages at the front of the request. Models with a strict chat template reject
that: ``homelab/qwen3.6-27b-coder`` answers ``[system, user]`` with 200 but
``[system, system, user]`` with ``400 System message must be at the beginning.``
GPT- and Gemma-family templates tolerate it, which kept this latent.

The invariant below is model-agnostic: an agent may carry system content in
``system_prompt`` OR in ``instructions``, never both.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fritz_local_brain.agents.compile_agent import build_compile_agent
from fritz_local_brain.agents.mirror_agent import build_mirror_agent
from fritz_local_brain.agents.reconciliation_agent import build_reconciliation_agent
from fritz_local_brain.config import Settings


def _make_settings(tmp_path: Path) -> Settings:
    brain_home = tmp_path / "brain"
    (brain_home / "capture" / "inbox").mkdir(parents=True, exist_ok=True)
    return Settings(
        _env_file=None,
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills",
    )


def _as_texts(value) -> list[str]:
    """pydantic-ai keeps system prompts as a tuple and instructions as a list."""

    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    return [str(item) for item in items if str(item).strip()]


def _system_sources(agent) -> tuple[int, int]:
    """Return (static system_prompt count, non-empty instructions count)."""

    return len(_as_texts(agent._system_prompts)), len(_as_texts(agent._instructions))


def _all_prompt_text(agent) -> str:
    return "\n".join(_as_texts(agent._system_prompts) + _as_texts(agent._instructions))


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return _make_settings(tmp_path)


def _build_all(settings: Settings):
    return {
        "compile": build_compile_agent(settings, "SKILL TEXT"),
        "reconciliation": build_reconciliation_agent(settings),
        "mirror": build_mirror_agent(settings),
    }


@pytest.mark.parametrize("name", ["compile", "reconciliation", "mirror"])
def test_agent_emits_exactly_one_system_message(settings: Settings, name: str) -> None:
    agent = _build_all(settings)[name]
    system_count, instruction_count = _system_sources(agent)
    total = system_count + instruction_count
    assert total == 1, (
        f"{name} agent carries system content in {total} places "
        f"(system_prompt={system_count}, instructions={instruction_count}); "
        "pydantic-ai maps each to its own system message and strict templates 400"
    )


def test_compile_agent_keeps_all_prompt_text(settings: Settings) -> None:
    """Merging must not drop content — persona, task rules, and skill text."""

    from fritz_local_brain.prompts import COMPILE_MVP_INSTRUCTIONS, COMPILE_SYSTEM_PROMPT

    agent = build_compile_agent(settings, "UNIQUE-SKILL-MARKER")
    combined = _all_prompt_text(agent)

    assert COMPILE_SYSTEM_PROMPT in combined
    assert COMPILE_MVP_INSTRUCTIONS in combined
    assert "UNIQUE-SKILL-MARKER" in combined


def test_reconciliation_and_mirror_keep_all_prompt_text(settings: Settings) -> None:
    from fritz_local_brain.prompts import (
        MIRROR_INSTRUCTIONS,
        MIRROR_SYSTEM_PROMPT,
        RECONCILIATION_INSTRUCTIONS,
        RECONCILIATION_SYSTEM_PROMPT,
    )

    recon = build_reconciliation_agent(settings)
    recon_text = _all_prompt_text(recon)
    assert RECONCILIATION_SYSTEM_PROMPT in recon_text
    assert RECONCILIATION_INSTRUCTIONS in recon_text

    mirror = build_mirror_agent(settings)
    mirror_text = _all_prompt_text(mirror)
    assert MIRROR_SYSTEM_PROMPT in mirror_text
    assert MIRROR_INSTRUCTIONS in mirror_text
