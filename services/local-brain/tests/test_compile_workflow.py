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
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"

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
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"

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
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"
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


def test_compile_apply_archives_unrepresented_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Captures the agent ignores (no proposal AND no explicit skip) are auto-archived.

    This is the fix for issue #135: unrepresented captures must not stay pending
    forever — they are archived after the apply run so a second run sees 0 pending.
    """
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"

    capture_path.parent.mkdir(parents=True)
    capture_path.write_text("# Capture\n\nUseful fact.\n", encoding="utf-8")
    (vault_path / ".brain").mkdir(parents=True)
    (vault_path / "knowledge").mkdir()
    (vault_path / ".brain" / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\nexclude: []\n", encoding="utf-8")
    brain_home.mkdir(exist_ok=True)
    (brain_home / "registry.yaml").write_text(f"vaults:\n  test:\n    path: {vault_path}\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")

    # Agent returns no proposals and lists no skipped — the exact #135 scenario.
    agent = SequenceCompileAgent([CompileAgentOutput()])
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

    first = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=False),
        )
    )

    # Capture must no longer exist in inbox — it has been auto-archived.
    assert first.captures_considered == 1
    assert first.applied == []
    assert first.errors == []
    assert not capture_path.exists(), "Unrepresented capture must be archived out of inbox"

    # Second run must see 0 pending captures — backlog drains.
    second = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=True),
        )
    )
    assert second.captures_considered == 0, "Backlog must be empty after auto-archival"


def test_compile_apply_marks_explicitly_skipped_captures_processed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    capture_path = brain_home / "capture" / "inbox" / "noise.md"
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"

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
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"

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
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"

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


def test_compile_apply_rejects_missing_source_even_for_single_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"

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
        sources=[],
        body="Useful body.",
    )
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: FakeCompileAgent(proposal))

    result = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=False, max_captures=1),
        )
    )

    assert result.applied == []
    assert result.errors == ["test/facts/useful.md: Knowledge article proposal must include at least one capture source"]
    assert capture_path.exists()


def test_compile_apply_repairs_single_capture_source_mangled_by_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    capture_path = brain_home / "capture" / "inbox" / "2026-06-02-real-long-capture-name.md"
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"

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
        sources=[str(brain_home / "capture" / "inbox" / "2026-06-02-real-long-capture.md")],
        frontmatter={"sources": ["wrong"]},
        body="Useful body.",
    )
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: FakeCompileAgent(proposal))

    result = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=False, max_captures=1),
        )
    )

    assert result.errors == []
    assert len(result.applied) == 1
    assert result.proposals[0].sources == [str(capture_path.resolve())]
    assert not capture_path.exists()


def test_compile_apply_rejects_unrelated_hallucinated_single_capture_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    capture_path = brain_home / "capture" / "inbox" / "actual-capture.md"
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"

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
        sources=[str(brain_home / "capture" / "inbox" / "totally-unrelated.md")],
        body="Useful body.",
    )
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: FakeCompileAgent(proposal))

    result = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=False, max_captures=1),
        )
    )

    assert result.applied == []
    assert result.errors == [f"test/facts/useful.md: Source does not exist: {proposal.sources[0]}"]
    assert capture_path.exists()


def test_compile_apply_drains_backlog_when_agent_covers_only_one_of_many_captures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduction for issue #135: agent produces one proposal for a multi-capture batch,
    leaving the rest unaddressed.  All unaddressed captures must be auto-archived so a
    second run sees 0 pending — the backlog must drain even when agent coverage is partial.
    """
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"

    (brain_home / "capture" / "inbox").mkdir(parents=True)
    (vault_path / ".brain").mkdir(parents=True)
    (vault_path / "knowledge").mkdir()
    (vault_path / ".brain" / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\nexclude: []\n", encoding="utf-8")
    brain_home.mkdir(exist_ok=True)
    (brain_home / "registry.yaml").write_text(f"vaults:\n  test:\n    path: {vault_path}\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")

    # Three captures; the agent will produce a proposal only for the first one.
    capture_a = brain_home / "capture" / "inbox" / "a.md"
    capture_b = brain_home / "capture" / "inbox" / "b.md"
    capture_c = brain_home / "capture" / "inbox" / "c.md"
    capture_a.write_text("# Capture A\n\nFact A.\n", encoding="utf-8")
    capture_b.write_text("# Capture B\n\nFact B.\n", encoding="utf-8")
    capture_c.write_text("# Capture C\n\nFact C.\n", encoding="utf-8")

    proposal = ArticleWriteProposal(
        vault="test",
        relative_path="facts/a.md",
        operation="create",
        title="Fact A",
        summary="Only A was compiled.",
        sources=[str(capture_a)],
        body="Fact A body.",
    )
    # Agent covers capture_a only; capture_b and capture_c get no proposal and no skip.
    agent = SequenceCompileAgent([CompileAgentOutput(proposals=[proposal])])
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills")
    first = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))

    assert first.errors == []
    assert len(first.applied) == 1
    # All three captures must be gone from inbox (archived or processed).
    assert not capture_a.exists(), "capture_a (applied source) must be archived"
    assert not capture_b.exists(), "capture_b (unaddressed) must be auto-archived"
    assert not capture_c.exists(), "capture_c (unaddressed) must be auto-archived"

    # Second compile must see 0 pending — the backlog is drained.
    second_agent = SequenceCompileAgent([CompileAgentOutput()])
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: second_agent)
    second = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=True)))
    assert second.captures_considered == 0, "Backlog must be fully drained after first apply run"


def test_compile_apply_marks_successful_captures_processed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"

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
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"

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
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"

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


# ---------------------------------------------------------------------------
# Store mode tests (no registry.yaml, no vault configured)
# ---------------------------------------------------------------------------


def _store_mode_settings(tmp_path: Path) -> tuple[Path, Path]:
    """Return (brain_home, skills_dir) for a registry-free store-mode setup."""
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"
    (brain_home / "capture" / "inbox").mkdir(parents=True)
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")
    return brain_home, tmp_path / "skills"


def test_store_mode_apply_writes_article_and_indexes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Apply run without registry writes article under store root and creates MOC indexes."""
    brain_home, skills_dir = _store_mode_settings(tmp_path)
    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    capture_path.write_text("# Capture\n\nDurable fact.\n", encoding="utf-8")

    proposal = ArticleWriteProposal(
        vault="brain",
        relative_path="common/decisions/foo.md",
        operation="create",
        title="Foo Decision",
        summary="A key decision.",
        sources=[str(capture_path)],
        body="We decided foo.",
    )
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: FakeCompileAgent(proposal))

    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=skills_dir)
    first = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False, max_captures=1)))

    assert first.errors == []
    assert len(first.applied) == 1

    store_root = brain_home / "knowledge"
    article_path = store_root / "common" / "decisions" / "foo.md"
    assert article_path.exists(), "Article must be written under store root"
    # Leaf index.md created in the decisions dir
    assert (store_root / "common" / "decisions" / "index.md").exists(), "Leaf index must be created"
    # Scope-level index.md for 'common'
    assert (store_root / "common" / "index.md").exists(), "Scope index must be created"
    # Global store index.md
    assert (store_root / "index.md").exists(), "Global MOC must be created"

    # Second run (dry-run) should see 0 pending captures — archival worked.
    second = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=True)))
    assert second.captures_considered == 0


def test_store_mode_dry_run_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dry-run in store mode returns proposals but does not write any files."""
    brain_home, skills_dir = _store_mode_settings(tmp_path)
    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    capture_path.write_text("# Capture\n\nDurable fact.\n", encoding="utf-8")

    proposal = ArticleWriteProposal(
        vault="brain",
        relative_path="common/decisions/dry.md",
        operation="create",
        title="Dry Decision",
        summary="Dry-run proposal.",
        sources=[str(capture_path)],
        body="Dry body.",
    )
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: FakeCompileAgent(proposal))

    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=skills_dir)
    result = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=True, max_captures=1)))

    assert result.dry_run is True
    assert result.proposals == [proposal]
    assert result.applied == []
    assert result.errors == []
    store_root = brain_home / "knowledge"
    assert not (store_root / "common" / "decisions" / "dry.md").exists(), "No file must be written in dry-run"
    assert not (brain_home / "log.md").exists(), "No log must be written in dry-run"


def test_store_mode_repairs_single_capture_source_mangled_by_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Single-capture source repair works in store mode (registry-free)."""
    brain_home, skills_dir = _store_mode_settings(tmp_path)
    capture_path = brain_home / "capture" / "inbox" / "2026-06-02-real-long-capture-name.md"
    capture_path.write_text("# Capture\n\nUseful fact.\n", encoding="utf-8")

    # Proposal has a mangled (truncated) source name that looks similar to the real file.
    proposal = ArticleWriteProposal(
        vault="brain",
        relative_path="common/lessons/useful.md",
        operation="create",
        title="Useful Lesson",
        summary="Useful fact.",
        sources=[str(brain_home / "capture" / "inbox" / "2026-06-02-real-long-capture.md")],
        frontmatter={"sources": ["wrong"]},
        body="Useful body.",
    )
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: FakeCompileAgent(proposal))

    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=skills_dir)
    result = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False, max_captures=1)))

    assert result.errors == []
    assert len(result.applied) == 1
    assert result.proposals[0].sources == [str(capture_path.resolve())]
    # Capture must be archived (not exist in inbox after compile).
    assert not capture_path.exists()


# ---------------------------------------------------------------------------
# Issue #123: update accepts archived/processed source (store mode)
# ---------------------------------------------------------------------------


def test_store_mode_update_accepts_archived_source_from_prior_compile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: second compile proposes update re-listing an archived source.

    Sequence:
    1. First compile: create store article from inbox capture → capture is
       compiled, marked processed, and archived out of inbox.
    2. Second compile: a NEW inbox capture triggers compile.  The agent enriches
       the existing article and re-lists the ORIGINAL inbox path (now archived)
       as the source for the update proposal.  The validator must accept this
       because the original source is recorded as already-processed.
    """
    brain_home, skills_dir = _store_mode_settings(tmp_path)
    capture_path = brain_home / "capture" / "inbox" / "decision-001.md"
    capture_path.write_text("# Capture\n\nInitial decision content.\n", encoding="utf-8")

    # ---- First compile: create the article ----
    create_proposal = ArticleWriteProposal(
        vault="brain",
        relative_path="common/decisions/decision-001.md",
        operation="create",
        title="Decision 001",
        summary="Initial decision.",
        sources=[str(capture_path)],
        body="Initial body.",
    )
    monkeypatch.setattr(
        compile_workflow,
        "build_compile_agent",
        lambda settings, skill_text: FakeCompileAgent(create_proposal),
    )

    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=skills_dir)
    first = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False, max_captures=1)))

    assert first.errors == [], f"First compile should succeed, got: {first.errors}"
    assert len(first.applied) == 1
    # Capture is now archived — no longer in inbox.
    assert not capture_path.exists(), "Capture should have been archived after first compile"

    # ---- Second compile: a new capture triggers compile, but the update
    # proposal references the ORIGINAL (now-archived) inbox path. ----
    new_capture_path = brain_home / "capture" / "inbox" / "enrichment-001.md"
    new_capture_path.write_text("# Capture\n\nEnrichment detail.\n", encoding="utf-8")

    # The update proposal lists the ORIGINAL archived path as source — this is
    # the exact failure mode from issue #123: the source no longer exists in
    # inbox and is not in the current batch's allowed_sources.
    update_proposal = ArticleWriteProposal(
        vault="brain",
        relative_path="common/decisions/decision-001.md",
        operation="update",
        title="Decision 001",
        summary="Enriched decision.",
        sources=[str(capture_path)],  # original inbox path — file no longer exists there
        body="Enriched body.",
    )
    update_agent = SequenceCompileAgent([CompileAgentOutput(proposals=[update_proposal])])
    monkeypatch.setattr(
        compile_workflow,
        "build_compile_agent",
        lambda settings, skill_text: update_agent,
    )

    second = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))

    # The update must apply without errors (this was the failing case before the fix).
    assert second.errors == [], f"Second compile should succeed, got: {second.errors}"
    assert len(second.applied) == 1
    assert second.applied[0].operation == "update"

    store_root = brain_home / "knowledge"
    article_path = store_root / "common" / "decisions" / "decision-001.md"
    assert "Enriched body." in article_path.read_text(encoding="utf-8")
