"""Tests for native/guided structured output wiring (issue #141, facets 1 & 2).

The compile and reconciliation agents must use ``NativeOutput`` (guided/json_schema
decoding) on ``openai-compatible`` endpoints, and fall back to the plain default
tool output on ``anthropic-compatible`` (AnthropicModel does not support
NativeOutput). Both agents must also carry a raised output-retry budget so the
model can self-repair from output-validation errors.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import NativeOutput

from fritz_local_brain.agents.compile_agent import build_compile_agent
from fritz_local_brain.agents.reconciliation_agent import build_reconciliation_agent
from fritz_local_brain.config import Settings
from fritz_local_brain.llm import OUTPUT_RETRIES, output_spec_for
from fritz_local_brain.models import CompileAgentOutput, ReconciliationVerdict


class _Model(BaseModel):
    x: int = 0


def _settings(tmp_path: Path, protocol: str) -> Settings:
    brain_home = tmp_path / "brain"
    brain_home.mkdir(parents=True, exist_ok=True)
    return Settings(_env_file=None, LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_LLM_PROTOCOL=protocol)


# ---------------------------------------------------------------------------
# Helper: protocol -> output spec (the stable, direct assertion)
# ---------------------------------------------------------------------------


def test_output_spec_openai_compatible_is_native() -> None:
    spec = output_spec_for("openai-compatible", _Model)
    assert isinstance(spec, NativeOutput)


def test_output_spec_anthropic_compatible_is_plain_model() -> None:
    spec = output_spec_for("anthropic-compatible", _Model)
    assert spec is _Model


# ---------------------------------------------------------------------------
# Agent wiring smoke tests (introspect the constructed agent)
# ---------------------------------------------------------------------------


def test_compile_agent_openai_uses_native_output(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "openai-compatible")
    agent = build_compile_agent(settings, skill_text="# Skill\n")
    assert type(agent._output_schema).__name__ == "NativeOutputSchema"


def test_compile_agent_anthropic_not_native(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "anthropic-compatible")
    agent = build_compile_agent(settings, skill_text="# Skill\n")
    assert type(agent._output_schema).__name__ != "NativeOutputSchema"


def test_reconciliation_agent_openai_uses_native_output(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "openai-compatible")
    agent = build_reconciliation_agent(settings)
    assert type(agent._output_schema).__name__ == "NativeOutputSchema"


def test_reconciliation_agent_anthropic_not_native(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "anthropic-compatible")
    agent = build_reconciliation_agent(settings)
    assert type(agent._output_schema).__name__ != "NativeOutputSchema"


# ---------------------------------------------------------------------------
# Facet 2: raised output-retry budget on both agents
# ---------------------------------------------------------------------------


def test_compile_agent_output_retry_budget(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "openai-compatible")
    agent = build_compile_agent(settings, skill_text="# Skill\n")
    assert agent._max_output_retries == OUTPUT_RETRIES
    assert OUTPUT_RETRIES >= 3


def test_reconciliation_agent_output_retry_budget(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "anthropic-compatible")
    agent = build_reconciliation_agent(settings)
    assert agent._max_output_retries == OUTPUT_RETRIES


# Reference the output models so the imports document the wired types.
assert CompileAgentOutput is not None
assert ReconciliationVerdict is not None
