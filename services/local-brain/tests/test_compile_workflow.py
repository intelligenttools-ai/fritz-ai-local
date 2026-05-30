from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from fritz_local_brain import compile_workflow
from fritz_local_brain.config import Settings
from fritz_local_brain.models import ArticleWriteProposal, CompileAgentOutput, CompileRunRequest


class FakeCompileAgent:
    def __init__(self, proposal: ArticleWriteProposal) -> None:
        self.proposal = proposal
        self.prompts: list[str] = []
        self.deps: list[object] = []

    async def run(self, prompt: str, *, deps: object, usage_limits: object) -> SimpleNamespace:
        self.prompts.append(prompt)
        self.deps.append(deps)
        return SimpleNamespace(output=CompileAgentOutput(proposals=[self.proposal]))


class SequenceCompileAgent:
    def __init__(self, outputs: list[CompileAgentOutput]) -> None:
        self.outputs = outputs
        self.prompts: list[str] = []
        self.deps: list[object] = []

    async def run(self, prompt: str, *, deps: object, usage_limits: object) -> SimpleNamespace:
        self.prompts.append(prompt)
        self.deps.append(deps)
        return SimpleNamespace(output=self.outputs.pop(0))


class MutatingCompileAgent(FakeCompileAgent):
    def __init__(self, proposal: ArticleWriteProposal, path_to_mutate: Path) -> None:
        super().__init__(proposal)
        self.path_to_mutate = path_to_mutate

    async def run(self, prompt: str, *, deps: object, usage_limits: object) -> SimpleNamespace:
        self.path_to_mutate.write_text(
            self.path_to_mutate.read_text(encoding="utf-8") + "\nNew content during compile.\n",
            encoding="utf-8",
        )
        return await super().run(prompt, deps=deps, usage_limits=usage_limits)


def test_compile_dry_run_considers_inbox_capture_and_reports_source_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    skill_path = tmp_path / "skills" / "fritz:brain-compile" / "SKILL.md"

    capture_path.parent.mkdir(parents=True)
    capture_path.write_text("# Capture\n\nDurable fact.\n", encoding="utf-8")
    (vault_path / ".brain").mkdir(parents=True)
    (vault_path / "knowledge").mkdir()
    (vault_path / ".brain" / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\nexclude: []\n", encoding="utf-8")
    brain_home.mkdir(exist_ok=True)
    (brain_home / "registry.yaml").write_text(f"vaults:\n  test:\n    path: {vault_path}\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")

    proposal = ArticleWriteProposal(
        vault="test",
        relative_path="facts/durable.md",
        operation="create",
        title="Durable Fact",
        summary="Inbox proposal",
        sources=[str(capture_path)],
        body="Durable body.",
    )
    log_calls = []
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: FakeCompileAgent(proposal))
    monkeypatch.setattr(
        compile_workflow,
        "append_global_log",
        lambda brain_home, operation, summary, dry_run: log_calls.append((operation, summary, dry_run)),
    )

    result = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=True, max_captures=1),
        )
    )

    assert result.captures_considered == 1
    assert result.captures_by_source == {"inbox": 1, "daily": 0, "sessions": 0}
    assert result.proposals == [proposal]
    assert result.errors == []
    assert log_calls == [
        ("COMPILE", "Processed 1 captures (inbox=1, daily=0, sessions=0) -> 0 proposals applied (0 errors)", True)
    ]


def test_compile_considers_all_pending_captures_oldest_first_so_later_updates_can_win(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    older_capture = brain_home / "capture" / "daily" / "older.md"
    newer_capture = brain_home / "capture" / "inbox" / "newer.md"
    skill_path = tmp_path / "skills" / "fritz:brain-compile" / "SKILL.md"

    older_capture.parent.mkdir(parents=True)
    newer_capture.parent.mkdir(parents=True)
    older_capture.write_text("# Capture\n\nProject color is blue.\n", encoding="utf-8")
    newer_capture.write_text("# Capture\n\nCorrection: project color is green.\n", encoding="utf-8")
    os.utime(older_capture, (100, 100))
    os.utime(newer_capture, (200, 200))
    (vault_path / ".brain").mkdir(parents=True)
    (vault_path / "knowledge").mkdir()
    (vault_path / ".brain" / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\nexclude: []\n", encoding="utf-8")
    brain_home.mkdir(exist_ok=True)
    (brain_home / "registry.yaml").write_text(f"vaults:\n  test:\n    path: {vault_path}\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")

    proposal = ArticleWriteProposal(
        vault="test",
        relative_path="facts/color.md",
        operation="create",
        title="Project Color",
        summary="Later capture supersedes earlier color.",
        sources=[str(older_capture), str(newer_capture)],
        body="Project color is green.",
    )
    agent = FakeCompileAgent(proposal)
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

    result = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=True),
        )
    )

    assert result.captures_considered == 2
    assert result.captures_by_source == {"inbox": 1, "daily": 1, "sessions": 0}
    assert result.proposals == [proposal]
    assert agent.deps[0].capture_paths == [older_capture, newer_capture]


def test_compile_default_uses_safe_chronological_batch_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    skill_path = tmp_path / "skills" / "fritz:brain-compile" / "SKILL.md"
    capture_dir = brain_home / "capture" / "daily"
    capture_dir.mkdir(parents=True)
    captures = []
    for index in range(30):
        capture = capture_dir / f"capture-{index:02d}.md"
        capture.write_text(f"# Capture {index}\n", encoding="utf-8")
        os.utime(capture, (100 + index, 100 + index))
        captures.append(capture)
    (vault_path / ".brain").mkdir(parents=True)
    (vault_path / "knowledge").mkdir()
    (vault_path / ".brain" / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\nexclude: []\n", encoding="utf-8")
    brain_home.mkdir(exist_ok=True)
    (brain_home / "registry.yaml").write_text(f"vaults:\n  test:\n    path: {vault_path}\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")

    proposal = ArticleWriteProposal(
        vault="test",
        relative_path="facts/capped.md",
        operation="create",
        title="Capped",
        summary="Default compile uses safe cap.",
        sources=[str(captures[0])],
        body="Capped body.",
    )
    agent = FakeCompileAgent(proposal)
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

    result = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=True),
        )
    )

    assert result.captures_considered == 25
    assert result.captures_by_source == {"inbox": 0, "daily": 25, "sessions": 0}
    assert agent.deps[0].capture_paths == captures[:25]


def test_compile_apply_leaves_unrepresented_capture_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    skill_path = tmp_path / "skills" / "fritz:brain-compile" / "SKILL.md"

    capture_path.parent.mkdir(parents=True)
    capture_path.write_text("# Capture\n\nUseful fact.\n", encoding="utf-8")
    (vault_path / ".brain").mkdir(parents=True)
    (vault_path / "knowledge").mkdir()
    (vault_path / ".brain" / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\nexclude: []\n", encoding="utf-8")
    brain_home.mkdir(exist_ok=True)
    (brain_home / "registry.yaml").write_text(f"vaults:\n  test:\n    path: {vault_path}\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")

    agent = SequenceCompileAgent([CompileAgentOutput(), CompileAgentOutput()])
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

    first = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=False),
        )
    )
    second = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=True),
        )
    )

    assert first.captures_considered == 1
    assert first.applied == []
    assert first.errors == []
    assert second.captures_considered == 1


def test_compile_apply_marks_explicitly_skipped_captures_processed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    capture_path = brain_home / "capture" / "inbox" / "noise.md"
    skill_path = tmp_path / "skills" / "fritz:brain-compile" / "SKILL.md"

    capture_path.parent.mkdir(parents=True)
    capture_path.write_text("# Capture\n\nNo durable knowledge here.\n", encoding="utf-8")
    (vault_path / ".brain").mkdir(parents=True)
    (vault_path / "knowledge").mkdir()
    (vault_path / ".brain" / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\nexclude: []\n", encoding="utf-8")
    brain_home.mkdir(exist_ok=True)
    (brain_home / "registry.yaml").write_text(f"vaults:\n  test:\n    path: {vault_path}\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")

    agent = SequenceCompileAgent(
        [CompileAgentOutput(skipped=[f"{capture_path}: no durable knowledge"]), CompileAgentOutput()]
    )
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

    first = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=False),
        )
    )
    second = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=True),
        )
    )

    assert first.captures_considered == 1
    assert first.applied == []
    assert first.errors == []
    assert second.captures_considered == 0


def test_compile_apply_marks_accounted_daily_capture_processed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    capture_path = brain_home / "capture" / "daily" / "2026-05-27.md"
    skill_path = tmp_path / "skills" / "fritz:brain-compile" / "SKILL.md"

    capture_path.parent.mkdir(parents=True)
    capture_path.write_text("# Daily Log\n\n- useful fact\n", encoding="utf-8")
    (vault_path / ".brain").mkdir(parents=True)
    (vault_path / "knowledge").mkdir()
    (vault_path / ".brain" / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\nexclude: []\n", encoding="utf-8")
    brain_home.mkdir(exist_ok=True)
    (brain_home / "registry.yaml").write_text(f"vaults:\n  test:\n    path: {vault_path}\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")

    proposal = ArticleWriteProposal(
        vault="test",
        relative_path="facts/useful.md",
        operation="create",
        title="Useful Fact",
        summary="Useful fact.",
        sources=[str(capture_path)],
        body="Useful body.",
    )
    agent = SequenceCompileAgent([CompileAgentOutput(proposals=[proposal]), CompileAgentOutput()])
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

    first = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=False),
        )
    )
    second = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=True),
        )
    )

    assert first.captures_considered == 1
    assert first.errors == []
    assert second.captures_considered == 0


def test_compile_apply_leaves_changed_capture_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    skill_path = tmp_path / "skills" / "fritz:brain-compile" / "SKILL.md"

    capture_path.parent.mkdir(parents=True)
    capture_path.write_text("# Capture\n\nUseful fact.\n", encoding="utf-8")
    (vault_path / ".brain").mkdir(parents=True)
    (vault_path / "knowledge").mkdir()
    (vault_path / ".brain" / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\nexclude: []\n", encoding="utf-8")
    brain_home.mkdir(exist_ok=True)
    (brain_home / "registry.yaml").write_text(f"vaults:\n  test:\n    path: {vault_path}\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")

    proposal = ArticleWriteProposal(
        vault="test",
        relative_path="facts/useful.md",
        operation="create",
        title="Useful Fact",
        summary="Useful fact.",
        sources=[str(capture_path)],
        body="Useful body.",
    )
    agent = SequenceCompileAgent([CompileAgentOutput()])
    monkeypatch.setattr(
        compile_workflow,
        "build_compile_agent",
        lambda settings, skill_text: MutatingCompileAgent(proposal, capture_path)
        if not (vault_path / "knowledge" / "facts" / "useful.md").exists()
        else agent,
    )

    first = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=False),
        )
    )
    second = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=True),
        )
    )

    assert first.captures_considered == 1
    assert first.errors == []
    assert second.captures_considered == 1


def test_compile_apply_marks_successful_captures_processed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    skill_path = tmp_path / "skills" / "fritz:brain-compile" / "SKILL.md"

    capture_path.parent.mkdir(parents=True)
    capture_path.write_text("# Capture\n\nUseful fact.\n", encoding="utf-8")
    (vault_path / ".brain").mkdir(parents=True)
    (vault_path / "knowledge").mkdir()
    (vault_path / ".brain" / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\nexclude: []\n", encoding="utf-8")
    brain_home.mkdir(exist_ok=True)
    (brain_home / "registry.yaml").write_text(f"vaults:\n  test:\n    path: {vault_path}\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")

    proposal = ArticleWriteProposal(
        vault="test",
        relative_path="facts/useful.md",
        operation="create",
        title="Useful Fact",
        summary="Useful fact.",
        sources=[str(capture_path)],
        body="Useful body.",
    )
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: FakeCompileAgent(proposal))

    first = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=False),
        )
    )
    second = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=True),
        )
    )

    assert first.captures_considered == 1
    assert second.captures_considered == 0


def test_compile_apply_allows_later_batch_to_update_article_created_earlier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    older_capture = brain_home / "capture" / "daily" / "older.md"
    newer_capture = brain_home / "capture" / "inbox" / "newer.md"
    skill_path = tmp_path / "skills" / "fritz:brain-compile" / "SKILL.md"

    older_capture.parent.mkdir(parents=True)
    newer_capture.parent.mkdir(parents=True)
    older_capture.write_text("# Capture\n\nProject color is blue.\n", encoding="utf-8")
    newer_capture.write_text("# Capture\n\nCorrection: project color is green.\n", encoding="utf-8")
    os.utime(older_capture, (100, 100))
    os.utime(newer_capture, (200, 200))
    (vault_path / ".brain").mkdir(parents=True)
    (vault_path / "knowledge").mkdir()
    (vault_path / ".brain" / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\nexclude: []\n", encoding="utf-8")
    brain_home.mkdir(exist_ok=True)
    (brain_home / "registry.yaml").write_text(f"vaults:\n  test:\n    path: {vault_path}\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")

    create = ArticleWriteProposal(
        vault="test",
        relative_path="facts/color.md",
        operation="create",
        title="Project Color",
        summary="Initial color.",
        sources=[str(older_capture)],
        body="Project color is blue.",
    )
    update = ArticleWriteProposal(
        vault="test",
        relative_path="facts/color.md",
        operation="update",
        title="Project Color",
        summary="Corrected color.",
        sources=[str(newer_capture)],
        body="Project color is green.",
    )
    agent = SequenceCompileAgent([CompileAgentOutput(proposals=[create]), CompileAgentOutput(proposals=[update])])
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

    result = asyncio.run(
        compile_workflow.run_compile(
            Settings(
                LOCAL_BRAIN_HOME=brain_home,
                LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills",
                LOCAL_BRAIN_COMPILE_MAX_CAPTURES=1,
            ),
            CompileRunRequest(dry_run=False, max_captures=2),
        )
    )

    assert result.errors == []
    assert [write.operation for write in result.applied] == ["create", "update"]
    assert "Project color is green." in (vault_path / "knowledge" / "facts" / "color.md").read_text(encoding="utf-8")


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
