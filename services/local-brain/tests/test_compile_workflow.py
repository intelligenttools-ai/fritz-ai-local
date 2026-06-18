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


def test_compile_apply_keeps_unrepresented_capture_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Captures the agent ignores (no proposal AND no explicit skip) stay PENDING.

    Issue #150: the old #135 fix auto-archived unrepresented captures, which is
    permanent silent data loss because the model routinely drops captures.  The
    new contract is retry-then-quarantine: after one run the unaccounted capture
    must NOT be archived — it stays in inbox and is re-considered next run.
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

    # Agent returns no proposals and lists no skipped — the dropped-capture scenario.
    # Two outputs: one for the apply run, one for the follow-up dry-run (the
    # capture is still pending, so the agent is invoked again).
    agent = SequenceCompileAgent([CompileAgentOutput(), CompileAgentOutput()])
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

    first = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=False),
        )
    )

    # Capture must STILL exist in inbox — it is retried, not archived (no data loss).
    assert first.captures_considered == 1
    assert first.applied == []
    assert first.errors == []
    assert capture_path.exists(), "Unrepresented capture must stay pending, not be archived"
    assert not (brain_home / "capture" / "inbox" / "archive").exists(), "Must not archive an unaccounted capture"

    # Second run must still see the capture as pending — it is re-read for a retry.
    second = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=True),
        )
    )
    assert second.captures_considered == 1, "Unaccounted capture must remain pending across runs"


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


def test_compile_apply_quarantines_uncovered_captures_after_n_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #150: partial agent coverage must NOT auto-archive the rest.

    The applied capture leaves immediately; the uncovered captures stay pending
    and are RETRIED.  Only after COMPILE_MAX_CAPTURE_ATTEMPTS (3) consecutive
    runs in which the agent keeps ignoring them are they quarantined — at which
    point the backlog drains (the #135 guarantee still holds, just deferred by N
    attempts) without ever silently discarding data.
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
    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills")

    # Run 1: agent covers capture_a only; capture_b/capture_c get no proposal, no skip.
    agent = SequenceCompileAgent([CompileAgentOutput(proposals=[proposal])])
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)
    first = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))

    assert first.errors == []
    assert len(first.applied) == 1
    assert not capture_a.exists(), "capture_a (applied source) must be archived"
    # Uncovered captures must stay pending after one run — NOT auto-archived.
    assert capture_b.exists(), "capture_b (uncovered) must stay pending, not be discarded"
    assert capture_c.exists(), "capture_c (uncovered) must stay pending, not be discarded"

    # Runs 2 and 3: agent keeps ignoring b and c (3 unaccounted attempts total).
    for _ in range(compile_workflow.COMPILE_MAX_CAPTURE_ATTEMPTS - 1):
        ignore_agent = SequenceCompileAgent([CompileAgentOutput()])
        monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: ignore_agent)
        run = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))
        assert run.errors == []

    # After N attempts the uncovered captures are quarantined (gone from inbox)...
    assert not capture_b.exists(), "capture_b must be quarantined after N attempts"
    assert not capture_c.exists(), "capture_c must be quarantined after N attempts"
    # ...landing in the distinct, visible quarantine location (NOT inbox/archive).
    quarantine_root = brain_home / "capture" / "quarantine"
    quarantined_names = {p.name for p in quarantine_root.glob("**/*.md")}
    assert {"b.md", "c.md"} <= quarantined_names, "Uncovered captures must land in capture/quarantine"
    # capture_a was legitimately archived (it had a proposal); b and c must NOT
    # be in inbox/archive — quarantine is a distinct location.
    inbox_archive = brain_home / "capture" / "inbox" / "archive"
    archived_names = {p.name for p in inbox_archive.glob("**/*.md")} if inbox_archive.exists() else set()
    assert "b.md" not in archived_names and "c.md" not in archived_names, "uncovered captures must not be archived"

    # The backlog drains — a fresh run sees 0 pending.
    drained_agent = SequenceCompileAgent([CompileAgentOutput()])
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: drained_agent)
    drained = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=True)))
    assert drained.captures_considered == 0, "Backlog must drain once captures are quarantined"


def test_compile_apply_ac1_unaccounted_captures_in_partial_batch_stay_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1 (#150): a batch where the agent accounts for only SOME captures (one
    proposal, one explicit skip) leaves the remaining unaccounted capture PENDING
    — not archived — proven by re-running and seeing it still considered, with the
    inbox file still present.
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

    capture_a = brain_home / "capture" / "inbox" / "a.md"  # gets a proposal
    capture_b = brain_home / "capture" / "inbox" / "b.md"  # explicitly skipped
    capture_c = brain_home / "capture" / "inbox" / "c.md"  # unaccounted-for
    capture_a.write_text("# Capture A\n\nFact A.\n", encoding="utf-8")
    capture_b.write_text("# Capture B\n\nNo durable knowledge.\n", encoding="utf-8")
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
    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills")

    agent = SequenceCompileAgent(
        [CompileAgentOutput(proposals=[proposal], skipped=[f"{capture_b}: no durable knowledge"])]
    )
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)
    first = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))

    assert first.errors == []
    assert len(first.applied) == 1
    assert not capture_a.exists(), "accounted-for (proposal) capture is archived"
    assert not capture_b.exists(), "explicitly-skipped capture is archived (terminal state)"
    # The unaccounted capture must remain in the inbox — pending, not lost.
    assert capture_c.exists(), "unaccounted capture must stay pending in inbox"

    # Re-running still considers the unaccounted capture.
    second_agent = SequenceCompileAgent([CompileAgentOutput()])
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: second_agent)
    second = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=True)))
    assert second.captures_considered == 1, "only the unaccounted capture remains pending"


def test_compile_apply_ac2_quarantine_after_n_runs_logs_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC2 (#150): a capture unaccounted-for N consecutive runs lands in
    capture/quarantine/ (present there, absent from capture/inbox AND from
    capture/inbox/archive), a loud log line is written to the global log, and a
    subsequent run no longer sees it pending.
    """
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    capture_path = brain_home / "capture" / "inbox" / "dropped.md"
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"

    capture_path.parent.mkdir(parents=True)
    capture_path.write_text("# Capture\n\nUseful fact the agent keeps dropping.\n", encoding="utf-8")
    (vault_path / ".brain").mkdir(parents=True)
    (vault_path / "knowledge").mkdir()
    (vault_path / ".brain" / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\nexclude: []\n", encoding="utf-8")
    brain_home.mkdir(exist_ok=True)
    (brain_home / "registry.yaml").write_text(f"vaults:\n  test:\n    path: {vault_path}\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")

    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills")

    # N runs in which the agent keeps ignoring the capture entirely.
    for attempt in range(compile_workflow.COMPILE_MAX_CAPTURE_ATTEMPTS):
        agent = SequenceCompileAgent([CompileAgentOutput()])
        monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)
        run = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))
        assert run.errors == []
        if attempt < compile_workflow.COMPILE_MAX_CAPTURE_ATTEMPTS - 1:
            assert capture_path.exists(), "capture stays pending until the attempt cap is reached"

    # Quarantined: gone from inbox, NOT in inbox/archive, present in quarantine.
    assert not capture_path.exists(), "capture must be removed from inbox after N attempts"
    assert not (brain_home / "capture" / "inbox" / "archive").exists(), "quarantine must not use inbox/archive"
    quarantine_root = brain_home / "capture" / "quarantine"
    quarantined = list(quarantine_root.glob("**/dropped.md"))
    assert quarantined, "capture must be moved into capture/quarantine"

    # Loud log line naming the capture and the quarantine action.
    log_text = (brain_home / "log.md").read_text(encoding="utf-8")
    assert "quarantine" in log_text.lower(), "a loud quarantine log line must be written"
    assert "dropped.md" in log_text, "the quarantine log must name the capture"

    # Next run no longer sees it pending.
    final_agent = SequenceCompileAgent([CompileAgentOutput()])
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: final_agent)
    final = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=True)))
    assert final.captures_considered == 0, "quarantined capture is no longer pending"


def test_compile_apply_quarantines_uncovered_daily_capture_after_n_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix 1 (#150 regression): quarantine must cover ALL capture sources.

    A capture under ``capture/daily/`` (not inbox) that the agent keeps ignoring
    must, after COMPILE_MAX_CAPTURE_ATTEMPTS runs, be moved to capture/quarantine
    and then NO LONGER be rediscovered. The earlier inbox-only quarantine silently
    skipped daily/sessions captures, so they were never quarantined nor processed
    and the backlog grew forever (#135 broken). This proves it drains.
    """
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"

    (brain_home / "capture" / "daily").mkdir(parents=True)
    (vault_path / ".brain").mkdir(parents=True)
    (vault_path / "knowledge").mkdir()
    (vault_path / ".brain" / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\nexclude: []\n", encoding="utf-8")
    brain_home.mkdir(exist_ok=True)
    (brain_home / "registry.yaml").write_text(f"vaults:\n  test:\n    path: {vault_path}\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")

    # A daily capture discovered by list_all_captures but never accounted for.
    daily_capture = brain_home / "capture" / "daily" / "2026-06-18.md"
    daily_capture.write_text("# Daily\n\nA fact the agent keeps dropping.\n", encoding="utf-8")

    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills")

    for attempt in range(compile_workflow.COMPILE_MAX_CAPTURE_ATTEMPTS):
        agent = SequenceCompileAgent([CompileAgentOutput()])
        monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)
        run = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))
        assert run.errors == []
        if attempt < compile_workflow.COMPILE_MAX_CAPTURE_ATTEMPTS - 1:
            assert daily_capture.exists(), "daily capture stays pending until the attempt cap is reached"

    # Quarantined from the non-inbox source: gone from daily/, present in quarantine.
    assert not daily_capture.exists(), "daily capture must be quarantined after N attempts"
    quarantine_root = brain_home / "capture" / "quarantine"
    quarantined = list(quarantine_root.glob("**/2026-06-18.md"))
    assert quarantined, "an unaccounted daily capture must land in capture/quarantine"

    # Backlog drains: a fresh run no longer rediscovers the daily capture.
    drained_agent = SequenceCompileAgent([CompileAgentOutput()])
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: drained_agent)
    drained = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=True)))
    assert drained.captures_considered == 0, "quarantined daily capture must no longer be rediscovered"


def test_compile_apply_quarantines_uncovered_sessions_capture_after_n_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix 1 (#150 regression): quarantine must cover the sessions source too.

    Mirrors the daily test for ``capture/sessions/``: an unaccounted sessions
    capture must, after COMPILE_MAX_CAPTURE_ATTEMPTS runs, be moved to
    capture/quarantine and then NO LONGER be rediscovered. Proves the all-sources
    fix holds for sessions, not just inbox/daily.
    """
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"

    (brain_home / "capture" / "sessions").mkdir(parents=True)
    (vault_path / ".brain").mkdir(parents=True)
    (vault_path / "knowledge").mkdir()
    (vault_path / ".brain" / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\nexclude: []\n", encoding="utf-8")
    brain_home.mkdir(exist_ok=True)
    (brain_home / "registry.yaml").write_text(f"vaults:\n  test:\n    path: {vault_path}\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")

    # A sessions capture discovered by list_all_captures but never accounted for.
    sessions_capture = brain_home / "capture" / "sessions" / "session-001.md"
    sessions_capture.write_text("# Session\n\nA fact the agent keeps dropping.\n", encoding="utf-8")

    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills")

    for attempt in range(compile_workflow.COMPILE_MAX_CAPTURE_ATTEMPTS):
        agent = SequenceCompileAgent([CompileAgentOutput()])
        monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)
        run = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))
        assert run.errors == []
        if attempt < compile_workflow.COMPILE_MAX_CAPTURE_ATTEMPTS - 1:
            assert sessions_capture.exists(), "sessions capture stays pending until the attempt cap is reached"

    # Quarantined from the sessions source: gone from sessions/, present in quarantine.
    assert not sessions_capture.exists(), "sessions capture must be quarantined after N attempts"
    quarantine_root = brain_home / "capture" / "quarantine"
    quarantined = list(quarantine_root.glob("**/session-001.md"))
    assert quarantined, "an unaccounted sessions capture must land in capture/quarantine"

    # Backlog drains: a fresh run no longer rediscovers the sessions capture.
    drained_agent = SequenceCompileAgent([CompileAgentOutput()])
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: drained_agent)
    drained = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=True)))
    assert drained.captures_considered == 0, "quarantined sessions capture must no longer be rediscovered"


def test_compile_absolute_ceiling_quarantines_content_mutating_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix A (#135/#150): the absolute lifetime ceiling fires.

    A capture whose CONTENT CHANGES every run keeps resetting the hash-bound
    ``count`` to 1, so it would never hit COMPILE_MAX_CAPTURE_ATTEMPTS and would
    be rediscovered forever — reopening the "backlog cannot grow forever" (#135)
    guarantee. The lifetime ``total`` counter (never reset on content change)
    must eventually quarantine it once total reaches
    COMPILE_MAX_CAPTURE_ATTEMPTS_ABSOLUTE.
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

    capture_path = brain_home / "capture" / "inbox" / "mutating.md"

    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills")

    absolute = compile_workflow.COMPILE_MAX_CAPTURE_ATTEMPTS_ABSOLUTE
    # Run ABSOLUTE times; rewrite the capture's content BEFORE each run so the
    # hash differs every time and ``count`` resets to 1 every run (never reaching
    # COMPILE_MAX_CAPTURE_ATTEMPTS). Only ``total`` accumulates.
    for run_index in range(absolute):
        capture_path.write_text(f"# Capture\n\nMutating content revision {run_index}.\n", encoding="utf-8")
        assert capture_path.exists(), "content-mutating capture must NOT be quarantined before the absolute ceiling"
        agent = SequenceCompileAgent([CompileAgentOutput()])
        monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)
        run = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))
        assert run.errors == []

    # After total reaches the absolute ceiling the capture is quarantined despite
    # the content changing (so count never reached the per-content cap).
    assert not capture_path.exists(), "content-mutating capture must be quarantined once total hits the absolute ceiling"
    quarantine_root = brain_home / "capture" / "quarantine"
    assert list(quarantine_root.glob("**/mutating.md")), "capture must land in capture/quarantine via the absolute ceiling"

    # Backlog drains.
    drained_agent = SequenceCompileAgent([CompileAgentOutput()])
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: drained_agent)
    drained = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=True)))
    assert drained.captures_considered == 0, "quarantined content-mutating capture must no longer be rediscovered"


def test_compile_quarantine_collision_keeps_both_same_basename_captures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#150: two unaccounted captures with the SAME basename from different
    sources, quarantined the same day, must both survive as distinct files under
    capture/quarantine/<date>/ (e.g. ``x.md`` and ``x-1.md``) — neither lost to a
    name collision.
    """
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"

    (brain_home / "capture" / "inbox").mkdir(parents=True)
    (brain_home / "capture" / "daily").mkdir(parents=True)
    (vault_path / ".brain").mkdir(parents=True)
    (vault_path / "knowledge").mkdir()
    (vault_path / ".brain" / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\nexclude: []\n", encoding="utf-8")
    brain_home.mkdir(exist_ok=True)
    (brain_home / "registry.yaml").write_text(f"vaults:\n  test:\n    path: {vault_path}\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")

    # Same basename, different sources, distinct content.
    inbox_x = brain_home / "capture" / "inbox" / "x.md"
    daily_x = brain_home / "capture" / "daily" / "x.md"
    inbox_x.write_text("# Inbox X\n\nInbox fact the agent drops.\n", encoding="utf-8")
    daily_x.write_text("# Daily X\n\nDaily fact the agent drops.\n", encoding="utf-8")

    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills")

    for _ in range(compile_workflow.COMPILE_MAX_CAPTURE_ATTEMPTS):
        agent = SequenceCompileAgent([CompileAgentOutput()])
        monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)
        run = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))
        assert run.errors == []

    assert not inbox_x.exists() and not daily_x.exists(), "both same-basename captures must be quarantined"
    quarantine_root = brain_home / "capture" / "quarantine"
    quarantined = sorted(p.name for p in quarantine_root.glob("**/*.md"))
    # Two distinct files under the same date dir: x.md and x-1.md — neither lost.
    assert quarantined == ["x-1.md", "x.md"], f"collision must keep both files, got {quarantined}"


def test_compile_attempt_budget_resets_when_capture_content_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix 2 (#150): the attempt counter is bound to the capture's content hash.

    A capture that fails once, then has its CONTENT CHANGED by the user, gets a
    FRESH retry budget — it must NOT be quarantined on the next failure as if it
    already had a prior attempt. Without hash-binding the stale count would carry
    over and quarantine the edited capture prematurely.
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

    capture_path = brain_home / "capture" / "inbox" / "edited.md"
    capture_path.write_text("# Capture\n\nOriginal content.\n", encoding="utf-8")

    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills")

    # Accumulate (N - 1) failed attempts against the ORIGINAL content, so one
    # more failure on the same content would quarantine it.
    for _ in range(compile_workflow.COMPILE_MAX_CAPTURE_ATTEMPTS - 1):
        agent = SequenceCompileAgent([CompileAgentOutput()])
        monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)
        run = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))
        assert run.errors == []
        assert capture_path.exists()

    # The user edits the capture: new content => fresh retry budget.
    capture_path.write_text("# Capture\n\nCorrected content the user fixed.\n", encoding="utf-8")

    # One more failed run. With the budget reset to 1, this must NOT quarantine.
    agent = SequenceCompileAgent([CompileAgentOutput()])
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)
    run = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))
    assert run.errors == []
    assert capture_path.exists(), "edited capture must NOT be quarantined on a single fresh-content failure"
    quarantine_root = brain_home / "capture" / "quarantine"
    assert not list(quarantine_root.glob("**/edited.md")), "edited capture must not be quarantined prematurely"


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
