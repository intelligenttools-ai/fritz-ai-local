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

    older_proposal = ArticleWriteProposal(
        vault="test",
        relative_path="facts/color.md",
        operation="create",
        title="Project Color",
        summary="Initial color.",
        sources=[str(older_capture)],
        body="Project color is blue.",
    )
    newer_proposal = ArticleWriteProposal(
        vault="test",
        relative_path="facts/color.md",
        operation="update",
        title="Project Color",
        summary="Later capture supersedes earlier color.",
        sources=[str(newer_capture)],
        body="Project color is green.",
    )
    # #153: one capture per agent.run — each run gets its own single-capture deps,
    # ordered oldest-first so the later capture can update the earlier article.
    agent = SequenceCompileAgent(
        [CompileAgentOutput(proposals=[older_proposal]), CompileAgentOutput(proposals=[newer_proposal])]
    )
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

    result = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=True),
        )
    )

    assert result.captures_considered == 2
    assert result.captures_by_source == {"inbox": 1, "daily": 1, "sessions": 0}
    assert result.proposals == [older_proposal, newer_proposal]
    # Exactly two runs, each fed ONE capture, oldest first.
    assert len(agent.deps) == 2
    assert agent.deps[0].capture_paths == [older_capture]
    assert agent.deps[1].capture_paths == [newer_capture]


def test_compile_default_caps_captures_processed_per_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    # #153: each capture skipped — one agent.run per capture, capped at 25 per run.
    agent = SequenceCompileAgent(
        [CompileAgentOutput(skipped=[f"{capture}: no durable knowledge"]) for capture in captures[:25]]
    )
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

    result = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=True),
        )
    )

    assert result.captures_considered == 25
    assert result.captures_by_source == {"inbox": 0, "daily": 25, "sessions": 0}
    # 25 agent.run calls, each fed exactly ONE capture, in chronological order.
    assert len(agent.deps) == 25
    assert [deps.capture_paths for deps in agent.deps] == [[capture] for capture in captures[:25]]


class CapturePathRecordingAgent:
    """Fake that records ``list(deps.capture_paths)`` seen on each ``agent.run``.

    Used to pin the #153 one-capture-per-run contract: each run's deps must carry
    exactly one capture. Returns one output per run, popped from ``outputs``.
    """

    def __init__(self, outputs: list[CompileAgentOutput]) -> None:
        self.outputs = outputs
        self.seen_paths: list[list[Path]] = []

    async def run(self, prompt: str, *, deps: object, usage_limits: object) -> SimpleNamespace:
        self.seen_paths.append(list(deps.capture_paths))
        return SimpleNamespace(output=self.outputs.pop(0))


def _manifest_vault(tmp_path: Path) -> tuple[Path, Path]:
    """Create a registry+manifest vault and return (brain_home, skills_dir)."""
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"
    (brain_home / "capture" / "inbox").mkdir(parents=True)
    (vault_path / ".brain").mkdir(parents=True)
    (vault_path / "knowledge").mkdir()
    (vault_path / ".brain" / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\nexclude: []\n", encoding="utf-8")
    (brain_home / "registry.yaml").write_text(f"vaults:\n  test:\n    path: {vault_path}\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")
    return brain_home, tmp_path / "skills"


def test_compile_ac1_each_run_processes_exactly_one_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#153 AC1: a 3-capture backlog yields exactly 3 agent.run calls, each whose
    deps carry exactly ONE capture (no batching)."""
    brain_home, skills_dir = _manifest_vault(tmp_path)
    captures = []
    for index, name in enumerate(("a", "b", "c")):
        capture = brain_home / "capture" / "inbox" / f"{name}.md"
        capture.write_text(f"# Capture {name}\n\nFact {name}.\n", encoding="utf-8")
        os.utime(capture, (100 + index, 100 + index))
        captures.append(capture)

    # Each run skips its one capture (terminal, no apply side effects to reason about).
    agent = CapturePathRecordingAgent(
        [CompileAgentOutput(skipped=[f"{capture}: no durable knowledge"]) for capture in captures]
    )
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

    result = asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=skills_dir),
            CompileRunRequest(dry_run=True),
        )
    )

    assert result.captures_considered == 3
    # Exactly 3 runs, each with EXACTLY ONE capture in its deps.
    assert len(agent.seen_paths) == 3, "one agent.run per capture"
    assert all(len(paths) == 1 for paths in agent.seen_paths), "each run's deps must carry exactly one capture"
    assert [paths[0] for paths in agent.seen_paths] == captures, "captures processed oldest-first, one at a time"


def test_compile_ac2_ten_capture_backlog_reaches_full_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#153 AC2: a 10+ capture backlog reaches 100% coverage in one run — every
    capture is applied, skipped, or routed to the retry counter; none silently
    dropped. A re-run then only reconsiders the legitimately-pending (dropped) one.

    Mix: 5 captures get a proposal (applied), 4 are explicitly skipped (archived),
    1 is dropped (no proposal/skip) → routed to the #150 retry counter and stays
    pending. 0 vanish.
    """
    brain_home, skills_dir = _manifest_vault(tmp_path)
    captures = []
    for index in range(10):
        capture = brain_home / "capture" / "inbox" / f"cap-{index:02d}.md"
        capture.write_text(f"# Capture {index}\n\nFact {index}.\n", encoding="utf-8")
        os.utime(capture, (100 + index, 100 + index))
        captures.append(capture)

    outputs: list[CompileAgentOutput] = []
    applied_sources: list[Path] = []
    skipped_sources: list[Path] = []
    for index, capture in enumerate(captures):
        if index < 5:  # applied
            outputs.append(
                CompileAgentOutput(
                    proposals=[
                        ArticleWriteProposal(
                            vault="test",
                            relative_path=f"facts/cap-{index:02d}.md",
                            operation="create",
                            title=f"Fact {index}",
                            summary="s",
                            sources=[str(capture)],
                            body="body",
                        )
                    ]
                )
            )
            applied_sources.append(capture)
        elif index < 9:  # explicitly skipped
            outputs.append(CompileAgentOutput(skipped=[f"{capture}: no durable knowledge"]))
            skipped_sources.append(capture)
        else:  # dropped (no proposal, no skip) → retry counter
            outputs.append(CompileAgentOutput())

    agent = SequenceCompileAgent(outputs)
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=skills_dir)
    result = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))

    assert result.errors == []
    assert len(result.applied) == 5, "the 5 proposal captures applied"
    # 5 applied + 4 skipped = 9 captures archived out of inbox; only the dropped one remains.
    for capture in applied_sources + skipped_sources:
        assert not capture.exists(), f"{capture.name} reached a terminal state and must be archived"
    dropped = captures[9]
    assert dropped.exists(), "the dropped capture must stay pending (routed to retry), not vanish"

    # The dropped capture is tracked by the #150 retry counter — proof it was not
    # silently lost.
    from fritz_local_brain.captures import _load_capture_attempts

    attempts = _load_capture_attempts(brain_home)
    attempt_keys = {compile_workflow._resolve_capture_source(brain_home, key) for key in attempts}
    assert dropped.resolve() in attempt_keys, "dropped capture must be tracked by the retry counter"

    # A re-run (dry) reflects ONLY the legitimately-pending capture: full coverage,
    # nothing silently dropped.
    rerun_agent = SequenceCompileAgent([CompileAgentOutput()])
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: rerun_agent)
    rerun = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=True)))
    assert rerun.captures_considered == 1, "only the legitimately-pending capture remains; none vanished"


def test_compile_error_isolation_one_bad_capture_does_not_block_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#153 error isolation (folds in the deferred #150/#151 finding): when ONE
    capture yields an invalid proposal (validation error) and the others are valid,
    the valid captures' articles ARE applied (the bad one no longer aborts the run)
    and the bad capture is routed to the retry counter / surfaced — not dropped.
    """
    brain_home, skills_dir = _manifest_vault(tmp_path)
    good_a = brain_home / "capture" / "inbox" / "good-a.md"
    bad = brain_home / "capture" / "inbox" / "bad.md"
    good_b = brain_home / "capture" / "inbox" / "good-b.md"
    for index, capture in enumerate((good_a, bad, good_b)):
        capture.write_text(f"# {capture.stem}\n\nContent.\n", encoding="utf-8")
        os.utime(capture, (100 + index, 100 + index))

    proposal_a = ArticleWriteProposal(
        vault="test", relative_path="facts/a.md", operation="create",
        title="A", summary="s", sources=[str(good_a)], body="A body",
    )
    # Invalid proposal: cites no source → validation rejects it.
    bad_proposal = ArticleWriteProposal(
        vault="test", relative_path="facts/bad.md", operation="create",
        title="Bad", summary="s", sources=[], body="bad body",
    )
    proposal_b = ArticleWriteProposal(
        vault="test", relative_path="facts/b.md", operation="create",
        title="B", summary="s", sources=[str(good_b)], body="B body",
    )
    agent = SequenceCompileAgent(
        [
            CompileAgentOutput(proposals=[proposal_a]),
            CompileAgentOutput(proposals=[bad_proposal]),
            CompileAgentOutput(proposals=[proposal_b]),
        ]
    )
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=skills_dir)
    result = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))

    # The bad capture's error is surfaced but the valid captures still applied.
    assert {write.title for write in result.applied} == {"A", "B"}, "valid captures must apply despite a bad one"
    assert any("facts/bad.md" in err for err in result.errors), "the bad proposal's error must be surfaced"
    assert not good_a.exists() and not good_b.exists(), "applied captures are archived"
    # The bad capture is NOT silently dropped — it stays pending, tracked by retry.
    assert bad.exists(), "the bad capture must stay pending for the retry path"
    from fritz_local_brain.captures import _load_capture_attempts

    attempts = _load_capture_attempts(brain_home)
    attempt_keys = {compile_workflow._resolve_capture_source(brain_home, key) for key in attempts}
    assert bad.resolve() in attempt_keys, "the bad capture must be routed to the retry counter"


def test_compile_capture_count_approval_gate_blocks_run_without_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#153 approval gate (adapted to the per-capture model): an apply run whose
    capture count exceeds ``large_batch_threshold`` without a matching approval
    token blocks the WHOLE run and applies nothing; the captures stay pending. A
    matching token lets it through.

    SHIFT FROM PRIOR SEMANTICS: the gate now counts CAPTURES this run will process
    (known up-front), not proposals (which are only known after each per-capture
    agent.run). This is the simplest correct adaptation for one-capture-per-run.
    """
    brain_home, skills_dir = _manifest_vault(tmp_path)
    captures = []
    for index in range(3):  # threshold = 2 below → 3 > 2 triggers the gate
        capture = brain_home / "capture" / "inbox" / f"cap-{index}.md"
        capture.write_text(f"# Cap {index}\n\nFact {index}.\n", encoding="utf-8")
        os.utime(capture, (100 + index, 100 + index))
        captures.append(capture)

    def _agent() -> SequenceCompileAgent:
        return SequenceCompileAgent(
            [
                CompileAgentOutput(
                    proposals=[
                        ArticleWriteProposal(
                            vault="test", relative_path=f"facts/cap-{i}.md", operation="create",
                            title=f"F{i}", summary="s", sources=[str(captures[i])], body="b",
                        )
                    ]
                )
                for i in range(3)
            ]
        )

    settings = Settings(
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_SKILLS_DIR=skills_dir,
        LOCAL_BRAIN_LARGE_BATCH_THRESHOLD=2,
        APPROVAL_TOKEN="sekret",
    )

    # No token → whole run blocked, nothing applied, captures stay pending.
    blocked_agent = _agent()
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: blocked_agent)
    blocked = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))
    assert blocked.applied == []
    assert any("requires approval" in err and "captures" in err for err in blocked.errors)
    assert all(capture.exists() for capture in captures), "blocked run must apply nothing; captures stay pending"

    # Matching token → run proceeds and applies.
    approved_agent = _agent()
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: approved_agent)
    approved = asyncio.run(
        compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False, approval_token="sekret"))
    )
    assert len(approved.applied) == 3
    assert approved.errors == []


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
    # Each run now does first + one #151 repair call (the agent drops the capture
    # both times): 2 outputs for the apply run + 2 for the follow-up dry-run.
    agent = SequenceCompileAgent(
        [CompileAgentOutput(), CompileAgentOutput(), CompileAgentOutput(), CompileAgentOutput()]
    )
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
    # Second-run agent: the capture changed content so it is still pending; the
    # agent drops it on both the first and the #151 repair call.
    agent = SequenceCompileAgent([CompileAgentOutput(), CompileAgentOutput()])
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
    # #153: one capture, one agent.run. The proposal cites no source, so validation
    # rejects it (surfaced as an error) and the capture stays pending for #150 retry.
    agent = FakeCompileAgent(proposal)
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

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
    # #153: one capture, one agent.run. The proposal cites an unrelated hallucinated
    # source, so validation rejects it (Source does not exist) and the real capture
    # stays pending for the #150 retry path.
    agent = FakeCompileAgent(proposal)
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

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

    # Run 1: agent covers capture_a only; capture_b/capture_c get no proposal, no
    # skip. #153: one agent.run per capture (3 runs) — A proposed, B and C dropped.
    agent = SequenceCompileAgent(
        [CompileAgentOutput(proposals=[proposal]), CompileAgentOutput(), CompileAgentOutput()]
    )
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)
    first = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))

    assert first.errors == []
    assert len(first.applied) == 1
    assert not capture_a.exists(), "capture_a (applied source) must be archived"
    # Uncovered captures must stay pending after one run — NOT auto-archived.
    assert capture_b.exists(), "capture_b (uncovered) must stay pending, not be discarded"
    assert capture_c.exists(), "capture_c (uncovered) must stay pending, not be discarded"

    # Runs 2 and 3: agent keeps ignoring b and c (3 unaccounted attempts total).
    # #153: one run per remaining capture (2 runs each), all dropped.
    for _ in range(compile_workflow.COMPILE_MAX_CAPTURE_ATTEMPTS - 1):
        ignore_agent = SequenceCompileAgent([CompileAgentOutput(), CompileAgentOutput()])
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

    # #153: one agent.run per capture (A, B, C in mtime order). A → proposal,
    # B → explicit skip, C → dropped (no proposal, no skip).
    agent = SequenceCompileAgent(
        [
            CompileAgentOutput(proposals=[proposal]),
            CompileAgentOutput(skipped=[f"{capture_b}: no durable knowledge"]),
            CompileAgentOutput(),
        ]
    )
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)
    first = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))

    assert first.errors == []
    assert len(first.applied) == 1
    assert not capture_a.exists(), "accounted-for (proposal) capture is archived"
    assert not capture_b.exists(), "explicitly-skipped capture is archived (terminal state)"
    # The unaccounted capture must remain in the inbox — pending, not lost.
    assert capture_c.exists(), "unaccounted capture must stay pending in inbox"

    # Re-running still considers the unaccounted capture (one run, dropped again).
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
        # Pending capture(s) dropped on both the first and the #151 repair call.
        agent = SequenceCompileAgent([CompileAgentOutput(), CompileAgentOutput()])
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
        # Pending capture(s) dropped on both the first and the #151 repair call.
        agent = SequenceCompileAgent([CompileAgentOutput(), CompileAgentOutput()])
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
        # Pending capture(s) dropped on both the first and the #151 repair call.
        agent = SequenceCompileAgent([CompileAgentOutput(), CompileAgentOutput()])
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
        # Pending capture(s) dropped on both the first and the #151 repair call.
        agent = SequenceCompileAgent([CompileAgentOutput(), CompileAgentOutput()])
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
        # Pending capture(s) dropped on both the first and the #151 repair call.
        agent = SequenceCompileAgent([CompileAgentOutput(), CompileAgentOutput()])
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
        # Pending capture(s) dropped on both the first and the #151 repair call.
        agent = SequenceCompileAgent([CompileAgentOutput(), CompileAgentOutput()])
        monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)
        run = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))
        assert run.errors == []
        assert capture_path.exists()

    # The user edits the capture: new content => fresh retry budget.
    capture_path.write_text("# Capture\n\nCorrected content the user fixed.\n", encoding="utf-8")

    # One more failed run. With the budget reset to 1, this must NOT quarantine.
    # The capture is pending and is dropped on both the first and the #151 repair call.
    agent = SequenceCompileAgent([CompileAgentOutput(), CompileAgentOutput()])
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
    # The update proposal's source is the OLD archived path, so the NEW batch
    # capture (enrichment-001) is uncovered → #151 fires one repair call; the
    # repair returns nothing (enrichment falls to the #150 retry path).
    update_agent = SequenceCompileAgent(
        [CompileAgentOutput(proposals=[update_proposal]), CompileAgentOutput()]
    )
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


# ---------------------------------------------------------------------------
# Adversarial-review fixes (#153 / epic #149)
# ---------------------------------------------------------------------------


def test_compile_apply_does_not_account_capture_when_proposal_cites_only_prior_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix 1 (#149 BLOCKER — silent capture loss): an applied UPDATE proposal that
    cites ONLY a previously-processed source (not THIS run's capture) must NOT make
    the current capture "accounted".  The capture stays pending and is routed to the
    #150 retry counter — never silently marked processed/archived without being
    captured into any article.

    Sequence mirrors the #123 path: first compile creates an article from capture P
    (P is archived/processed).  Second compile sees a NEW capture C; the agent emits
    an update proposal whose only source is the archived P — the update applies, but
    C is never cited.  C must remain pending.
    """
    brain_home, skills_dir = _store_mode_settings(tmp_path)
    prior = brain_home / "capture" / "inbox" / "prior.md"
    prior.write_text("# Capture\n\nInitial decision content.\n", encoding="utf-8")

    create_proposal = ArticleWriteProposal(
        vault="brain",
        relative_path="common/decisions/decision-001.md",
        operation="create",
        title="Decision 001",
        summary="Initial decision.",
        sources=[str(prior)],
        body="Initial body.",
    )
    monkeypatch.setattr(
        compile_workflow, "build_compile_agent", lambda settings, skill_text: FakeCompileAgent(create_proposal)
    )
    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=skills_dir)
    first = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False, max_captures=1)))
    assert first.errors == []
    assert not prior.exists(), "prior capture archived after first compile"

    # ---- Second compile: a NEW capture C, but the proposal cites ONLY the prior
    # (archived) source — never C. ----
    current = brain_home / "capture" / "inbox" / "current.md"
    current.write_text("# Capture\n\nUnrelated new fact the agent ignored.\n", encoding="utf-8")

    update_proposal = ArticleWriteProposal(
        vault="brain",
        relative_path="common/decisions/decision-001.md",
        operation="update",
        title="Decision 001",
        summary="Enriched decision.",
        sources=[str(prior)],  # only the prior, archived source — NOT the current capture
        body="Enriched body.",
    )
    monkeypatch.setattr(
        compile_workflow, "build_compile_agent", lambda settings, skill_text: FakeCompileAgent(update_proposal)
    )
    second = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))

    # The update applies (cites a legitimately-processed source), but the CURRENT
    # capture was never cited, so it must NOT be archived/processed.
    assert len(second.applied) == 1, "the update still applies"
    assert current.exists(), "current capture must stay pending — not silently archived"

    from fritz_local_brain.captures import _load_capture_attempts

    attempts = _load_capture_attempts(brain_home)
    attempt_keys = {compile_workflow._resolve_capture_source(brain_home, key) for key in attempts}
    assert current.resolve() in attempt_keys, "uncited current capture must be routed to the retry counter"

    # A re-run still considers the current capture — proof it was not lost.
    rerun_agent = FakeCompileAgent(update_proposal)
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: rerun_agent)
    rerun = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=True)))
    assert rerun.captures_considered == 1, "the uncited capture remains pending across runs"


class RaisingNthCompileAgent:
    """Fake whose Nth ``run`` (0-indexed) raises, simulating an unstable LLM endpoint.

    All other runs return the matching output from ``outputs`` (popped in order).
    """

    def __init__(self, outputs: list[CompileAgentOutput], raise_on_index: int, exc: Exception) -> None:
        self.outputs = outputs
        self.raise_on_index = raise_on_index
        self.exc = exc
        self.calls = 0

    async def run(self, prompt: str, *, deps: object, usage_limits: object) -> SimpleNamespace:
        index = self.calls
        self.calls += 1
        if index == self.raise_on_index:
            raise self.exc
        return SimpleNamespace(output=self.outputs.pop(0))


def test_compile_apply_isolates_agent_run_exception_to_one_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix 2 (#153 MAJOR — robustness): an exception from ``agent.run`` on ONE
    capture (unstable LLM endpoint) must not abort ``run_compile``.  The other
    captures' articles are still applied and their sources marked processed (the
    post-loop mark/archive runs), the failing capture's attempt counter is
    incremented, and an error is recorded.
    """
    brain_home, skills_dir = _manifest_vault(tmp_path)
    good_a = brain_home / "capture" / "inbox" / "good-a.md"
    boom = brain_home / "capture" / "inbox" / "boom.md"
    good_b = brain_home / "capture" / "inbox" / "good-b.md"
    for index, capture in enumerate((good_a, boom, good_b)):
        capture.write_text(f"# {capture.stem}\n\nContent.\n", encoding="utf-8")
        os.utime(capture, (100 + index, 100 + index))

    proposal_a = ArticleWriteProposal(
        vault="test", relative_path="facts/a.md", operation="create",
        title="A", summary="s", sources=[str(good_a)], body="A body",
    )
    proposal_b = ArticleWriteProposal(
        vault="test", relative_path="facts/b.md", operation="create",
        title="B", summary="s", sources=[str(good_b)], body="B body",
    )
    # Middle run (index 1, the boom capture) raises; the surrounding runs return A and B.
    agent = RaisingNthCompileAgent(
        [CompileAgentOutput(proposals=[proposal_a]), CompileAgentOutput(proposals=[proposal_b])],
        raise_on_index=1,
        exc=RuntimeError("model transport error"),
    )
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=skills_dir)
    result = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))

    # The run did NOT raise out; the good captures applied and were archived.
    assert {write.title for write in result.applied} == {"A", "B"}, "good captures apply despite a mid-run exception"
    assert not good_a.exists() and not good_b.exists(), "good captures' sources marked processed + archived"
    # The failing capture is surfaced and stays pending, routed to the retry counter.
    assert any("boom.md" in err for err in result.errors), "the failing capture must be named in an error"
    assert boom.exists(), "the failing capture must stay pending, not be lost"
    from fritz_local_brain.captures import _load_capture_attempts

    attempts = _load_capture_attempts(brain_home)
    attempt_keys = {compile_workflow._resolve_capture_source(brain_home, key) for key in attempts}
    assert boom.resolve() in attempt_keys, "the failing capture must be routed to the retry counter"


def test_compile_dry_run_detects_duplicate_create_against_earlier_same_run_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix 3 (#153 MAJOR — dry-run fidelity): two captures that both propose
    CREATING the same target in a single dry-run must be detected as a duplicate
    create on the second — matching real apply semantics.  Dry-run is the DEFAULT
    request mode, so the preview must not let two creates of one target slip
    through.
    """
    brain_home, skills_dir = _store_mode_settings(tmp_path)
    cap_a = brain_home / "capture" / "inbox" / "a.md"
    cap_b = brain_home / "capture" / "inbox" / "b.md"
    cap_a.write_text("# A\n\nFact A.\n", encoding="utf-8")
    cap_b.write_text("# B\n\nFact B.\n", encoding="utf-8")
    os.utime(cap_a, (100, 100))
    os.utime(cap_b, (200, 200))

    same_target = "common/decisions/dup.md"
    proposal_a = ArticleWriteProposal(
        vault="brain", relative_path=same_target, operation="create",
        title="Dup A", summary="s", sources=[str(cap_a)], body="A body",
    )
    proposal_b = ArticleWriteProposal(
        vault="brain", relative_path=same_target, operation="create",
        title="Dup B", summary="s", sources=[str(cap_b)], body="B body",
    )
    agent = SequenceCompileAgent(
        [CompileAgentOutput(proposals=[proposal_a]), CompileAgentOutput(proposals=[proposal_b])]
    )
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=skills_dir)
    result = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=True)))

    # The second create of the same target must be rejected in dry-run, exactly as apply.
    assert any(
        "already exists" in err and same_target in err for err in result.errors
    ), f"duplicate create must be rejected in dry-run, got errors: {result.errors}"


def test_compile_approval_blocked_run_does_not_create_store_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix 4 (#153 MINOR): an approval-blocked run must not create the store root
    on disk — the store dir is only materialised when the run actually proceeds.
    """
    brain_home, skills_dir = _store_mode_settings(tmp_path)
    captures = []
    for index in range(3):  # threshold = 2 below → 3 > 2 triggers the gate
        capture = brain_home / "capture" / "inbox" / f"cap-{index}.md"
        capture.write_text(f"# Cap {index}\n\nFact {index}.\n", encoding="utf-8")
        os.utime(capture, (100 + index, 100 + index))
        captures.append(capture)

    proposal = ArticleWriteProposal(
        vault="brain", relative_path="common/decisions/x.md", operation="create",
        title="X", summary="s", sources=[str(captures[0])], body="b",
    )
    monkeypatch.setattr(
        compile_workflow, "build_compile_agent", lambda settings, skill_text: FakeCompileAgent(proposal)
    )
    settings = Settings(
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_SKILLS_DIR=skills_dir,
        LOCAL_BRAIN_LARGE_BATCH_THRESHOLD=2,
        APPROVAL_TOKEN="sekret",
    )

    blocked = asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False)))
    assert blocked.applied == []
    assert any("requires approval" in err for err in blocked.errors)
    assert not (brain_home / "knowledge").exists(), "blocked run must not create the store root on disk"


# ---------------------------------------------------------------------------
# #158 — per-capture compile prompt regression guard
# ---------------------------------------------------------------------------

def test_compile_capture_prompt_wording_store_mode() -> None:
    """Wording lock (guards #158 regression): the per-capture store-mode prompt must
    use the proven lead line from before #153 and must NOT use the #153 wording that
    produced zero proposals on hermes-qwen36-35b-a3b.

    This test MUST FAIL before the #158 fix (current wording = 'Compile exactly one
    capture') and PASS after (restored wording = 'Run one chronological compile
    batch').  Any future prompt edit MUST consciously update this test.
    """
    prompt = compile_workflow._compile_capture_prompt(
        store_mode=True, vault_names=["brain"]
    )
    assert "Run one chronological compile batch" in prompt, (
        "store-mode prompt must use the proven pre-#153 lead line"
    )
    assert "Compile exactly one capture" not in prompt, (
        "store-mode prompt must NOT use the #153 wording that produced zero proposals"
    )
    assert "Later batches may update knowledge created by earlier batches" in prompt, (
        "store-mode prompt must use the proven second sentence"
    )
    assert "You may update knowledge created by earlier captures in this run" not in prompt, (
        "store-mode prompt must NOT use the #153 regressing second sentence"
    )


def test_compile_capture_prompt_wording_non_store_mode() -> None:
    """Wording lock (guards #158 regression): the per-capture non-store-mode prompt
    must use the proven lead line and must NOT use the #153 wording.

    Same pass/fail criteria as the store-mode variant.
    """
    prompt = compile_workflow._compile_capture_prompt(
        store_mode=False, vault_names=["project-a", "common"]
    )
    assert "Run one chronological compile batch" in prompt, (
        "non-store-mode prompt must use the proven pre-#153 lead line"
    )
    assert "Compile exactly one capture" not in prompt, (
        "non-store-mode prompt must NOT use the #153 wording that produced zero proposals"
    )
    assert "Later batches may update knowledge created by earlier batches" in prompt, (
        "non-store-mode prompt must use the proven second sentence"
    )
    assert "You may update knowledge created by earlier captures in this run" not in prompt, (
        "non-store-mode prompt must NOT use the #153 regressing second sentence"
    )


@pytest.mark.skipif(
    not os.environ.get("LOCAL_BRAIN_SMOKE_LLM"),
    reason="real-model smoke test; set LOCAL_BRAIN_SMOKE_LLM=1 and configure a live LLM endpoint to run",
)
def test_compile_capture_prompt_real_model_produces_proposals(tmp_path: Path) -> None:
    """Real-model smoke guard (AC2 intent from #158, updated for #160).

    Unit-test mocks cannot detect a prompt that causes the model to return zero
    proposals — that is the root cause of #158.  This test exercises the actual
    ``build_compile_agent`` → ``agent.run`` path with a substantive capture and
    asserts that at least one proposal is returned.

    The agent is built with COMPILE_POLICY (the in-service policy), matching the
    instructions now used by the service (#160 fix).

    Skipped in normal CI runs (no live LLM).  Run manually against a configured
    endpoint to verify the restored prompt wording on the real model:

        LOCAL_BRAIN_SMOKE_LLM=1 .venv/bin/python -m pytest tests/test_compile_workflow.py \
            -k test_compile_capture_prompt_real_model_produces_proposals -v

    This is the guard the mocked unit tests cannot provide.
    """
    from fritz_local_brain.agents.compile_agent import CompileDeps, build_compile_agent
    from fritz_local_brain.prompts import COMPILE_POLICY

    brain_home = tmp_path / "brain"
    skills_dir = tmp_path / "skills"
    capture_path = brain_home / "capture" / "inbox" / "smoke.md"
    capture_path.parent.mkdir(parents=True)
    capture_path.write_text(
        "# Smoke Test Capture\n\n"
        "Decision: always use UTC timestamps in log entries to avoid timezone confusion.\n"
        "Rationale: avoids DST edge cases and makes log correlation trivial.\n",
        encoding="utf-8",
    )
    skill_dir = skills_dir / "brain-compile"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Compile Skill\n", encoding="utf-8")

    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=skills_dir)
    vault_names = ["brain"]
    prompt = compile_workflow._compile_capture_prompt(store_mode=True, vault_names=vault_names)
    deps = CompileDeps(
        capture_paths=[capture_path],
        vault_names=vault_names,
        article_paths={},
        capture_max_chars=settings.capture_max_chars,
    )

    agent = build_compile_agent(settings, COMPILE_POLICY)

    from pydantic_ai.usage import UsageLimits
    from fritz_local_brain.llm import AGENT_REQUEST_LIMIT

    result = asyncio.run(
        agent.run(prompt, deps=deps, usage_limits=UsageLimits(request_limit=AGENT_REQUEST_LIMIT))
    )
    assert result.output.proposals, (
        "real model must return at least one proposal for a substantive capture; "
        "zero proposals → prompt regression"
    )


# ---------------------------------------------------------------------------
# #160 — compile agent policy: in-service agent must NOT receive host runbook
# ---------------------------------------------------------------------------


def test_compile_policy_does_not_contain_host_orchestration_markers() -> None:
    """AC5a (#160): COMPILE_POLICY must not carry host-orchestration instructions.

    The in-service compile agent is only responsible for grounding (calling
    load_compile_context) and producing proposals.  Host steps — service gate,
    finding captures, updating indexes, logging — are done by the Python harness
    and must NOT appear in the agent's instructions.
    """
    from fritz_local_brain.prompts import COMPILE_POLICY

    forbidden = [
        "Service-first gate",
        "Find unprocessed captures",
        "Update indexes",
        "### 7. Log",
    ]
    for marker in forbidden:
        assert marker not in COMPILE_POLICY, (
            f"COMPILE_POLICY must not contain host-orchestration marker {marker!r}"
        )


def test_run_compile_builds_agent_with_compile_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC5b (#160): run_compile must pass COMPILE_POLICY (not the SKILL.md text)
    to build_compile_agent.

    Monkeypatches build_compile_agent to capture the skill_text argument and
    asserts it equals COMPILE_POLICY and does not contain host-orchestration markers.
    """
    from fritz_local_brain.prompts import COMPILE_POLICY

    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"
    capture_path = brain_home / "capture" / "inbox" / "fact.md"

    capture_path.parent.mkdir(parents=True)
    capture_path.write_text("# Capture\n\nDurable fact.\n", encoding="utf-8")
    brain_home.mkdir(exist_ok=True)
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Full host SKILL.md with Service-first gate\n", encoding="utf-8")

    received_skill_texts: list[str] = []
    proposal = ArticleWriteProposal(
        vault="brain",
        relative_path="common/decisions/fact.md",
        operation="create",
        title="Fact",
        summary="s",
        sources=[str(capture_path)],
        body="body",
    )

    def capturing_build(settings: object, skill_text: str) -> object:
        received_skill_texts.append(skill_text)
        return FakeCompileAgent(proposal)

    monkeypatch.setattr(compile_workflow, "build_compile_agent", capturing_build)

    asyncio.run(
        compile_workflow.run_compile(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            CompileRunRequest(dry_run=True, max_captures=1),
        )
    )

    assert received_skill_texts, "build_compile_agent must have been called"
    for skill_text in received_skill_texts:
        assert skill_text == COMPILE_POLICY, (
            "run_compile must pass COMPILE_POLICY to build_compile_agent, "
            f"not the SKILL.md text; got: {skill_text[:120]!r}"
        )
        for marker in ("Service-first gate", "Find unprocessed captures", "Update indexes", "### 7. Log"):
            assert marker not in skill_text, (
                f"skill_text passed to build_compile_agent must not contain {marker!r}"
            )
