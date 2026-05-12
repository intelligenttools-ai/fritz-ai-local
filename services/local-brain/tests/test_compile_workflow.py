from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from fritz_local_brain import compile_workflow
from fritz_local_brain.config import Settings
from fritz_local_brain.models import ArticleWriteProposal, CompileAgentOutput, CompileRunRequest


class FakeCompileAgent:
    def __init__(self, proposal: ArticleWriteProposal) -> None:
        self.proposal = proposal

    async def run(self, prompt: str, *, deps: object, usage_limits: object) -> SimpleNamespace:
        return SimpleNamespace(output=CompileAgentOutput(proposals=[self.proposal]))


def test_compile_dry_run_returns_proposals_without_applied_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    capture_path = brain_home / "capture" / "daily" / "2026-05-12.md"
    skill_path = tmp_path / "skills" / "fritz:brain-compile" / "SKILL.md"

    capture_path.parent.mkdir(parents=True)
    capture_path.write_text("# Capture\n\nUseful session detail.\n", encoding="utf-8")
    (vault_path / ".brain").mkdir(parents=True)
    (vault_path / "knowledge").mkdir()
    (vault_path / ".brain" / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\nexclude: []\n", encoding="utf-8")
    brain_home.mkdir(exist_ok=True)
    (brain_home / "registry.yaml").write_text(f"vaults:\n  test:\n    path: {vault_path}\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")

    target_path = vault_path / "knowledge" / "patterns" / "dry-run.md"
    proposal = ArticleWriteProposal(
        vault="test",
        relative_path="patterns/dry-run.md",
        operation="create",
        title="Dry Run",
        summary="Dry-run proposal",
        sources=[str(capture_path)],
        body="Dry-run body.",
    )
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: FakeCompileAgent(proposal))

    result = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=True, max_captures=1),
        )
    )

    assert result.dry_run is True
    assert result.proposals == [proposal]
    assert result.applied == []
    assert result.errors == []
    assert not target_path.exists()
    assert not (target_path.parent / "index.md").exists()
    assert not (brain_home / "log.md").exists()
