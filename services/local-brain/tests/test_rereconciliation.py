"""Tests for the re-reconciliation sweep (WI13, Part C).

Scenarios covered:
1. Sweep is a no-op when the store root does not exist.
2. Sweep is a no-op when no article is flagged.
3. Dry-run (default): flagged article is processed by the agent but the
   needs_rereconciliation flag is NOT cleared and no writes are applied.
4. Non-dry-run: verdict is applied AND the flag is cleared on the processed
   article.

The reconciliation agent is monkeypatched via
``compile_workflow.build_reconciliation_agent`` (same seam used in
test_reconciliation.py).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from fritz_local_brain import compile_workflow
from fritz_local_brain.config import Settings
from fritz_local_brain.logs import read_reconciliation_undo
from fritz_local_brain.models import ReconciliationVerdict
from fritz_local_brain.rereconciliation import RereconciliationResult, run_rereconciliation_sweep


# ---------------------------------------------------------------------------
# Helpers — reused idioms from test_reconciliation.py
# ---------------------------------------------------------------------------


def _write_article(path: Path, frontmatter: dict, body: str = "Body.") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    path.write_text(f"---\n{yaml_text}\n---\n\n{body}\n", encoding="utf-8")


def _read_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, _, rest = text.partition("---\n")
    yaml_text, _, _ = rest.partition("\n---")
    return yaml.safe_load(yaml_text)


class FakeReconciliationAgent:
    """Fake that returns a fixed verdict without calling an LLM."""

    def __init__(self, verdict: ReconciliationVerdict) -> None:
        self.verdict = verdict
        self.call_count = 0

    async def run(self, prompt: str, *, deps: object, usage_limits: object) -> SimpleNamespace:
        self.call_count += 1
        return SimpleNamespace(output=self.verdict)


def _settings(tmp_path: Path, **kwargs) -> Settings:
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"
    (brain_home / "capture" / "inbox").mkdir(parents=True)
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")
    return Settings(
        _env_file=None,
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# No-op cases
# ---------------------------------------------------------------------------


def test_sweep_noop_when_store_absent(tmp_path: Path) -> None:
    """Sweep returns empty result when the store root does not exist."""
    settings = _settings(tmp_path)
    # Do not create the knowledge directory.
    result: RereconciliationResult = asyncio.run(
        run_rereconciliation_sweep(settings, dry_run=True)
    )
    assert result.flagged_count == 0
    assert result.processed_count == 0
    assert result.cleared_count == 0
    assert result.outcomes == []


def test_sweep_noop_when_nothing_flagged(tmp_path: Path) -> None:
    """Sweep returns empty result when no article has needs_rereconciliation: true."""
    settings = _settings(tmp_path)
    store_root = settings.resolve_brain_store_path()
    store_root.mkdir(parents=True)
    _write_article(store_root / "a.md", {"title": "A", "status": "active"})

    result = asyncio.run(run_rereconciliation_sweep(settings, dry_run=True))

    assert result.flagged_count == 0
    assert result.processed_count == 0


# ---------------------------------------------------------------------------
# Dry-run: processes article but does NOT clear flag or apply verdict
# ---------------------------------------------------------------------------


def test_sweep_dry_run_processes_but_does_not_clear_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dry_run=True: agent is invoked but flag stays set and no writes happen."""
    settings = _settings(tmp_path)
    store_root = settings.resolve_brain_store_path()

    flagged_article = store_root / "predecessor.md"
    related_article = store_root / "related.md"
    _write_article(
        flagged_article,
        {"title": "Predecessor", "status": "superseded", "needs_rereconciliation": True},
        body="Old knowledge.",
    )
    _write_article(
        related_article,
        {"title": "Related", "status": "active"},
        body="Current knowledge.",
    )

    fake = FakeReconciliationAgent(
        ReconciliationVerdict(verdict="orthogonal", reasoning="no overlap")
    )
    monkeypatch.setattr(compile_workflow, "build_reconciliation_agent", lambda s: fake)

    result = asyncio.run(run_rereconciliation_sweep(settings, dry_run=True))

    assert result.flagged_count == 1
    assert result.processed_count == 1
    assert result.cleared_count == 0  # flag must NOT be cleared in dry-run
    assert result.dry_run is True

    # Flag must still be present.
    fm = _read_frontmatter(flagged_article)
    assert fm.get("needs_rereconciliation") is True


def test_sweep_dry_run_does_not_apply_verdict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dry_run=True: even a supersession verdict is not written to disk."""
    settings = _settings(tmp_path)
    store_root = settings.resolve_brain_store_path()

    old = store_root / "old.md"
    new = store_root / "new.md"
    _write_article(
        old,
        {"title": "Old", "status": "superseded", "needs_rereconciliation": True},
        body="Old.",
    )
    _write_article(new, {"title": "New", "status": "active"}, body="New.")

    fake = FakeReconciliationAgent(
        ReconciliationVerdict(verdict="contradicts_supersedes", reasoning="new wins")
    )
    monkeypatch.setattr(compile_workflow, "build_reconciliation_agent", lambda s: fake)

    asyncio.run(run_rereconciliation_sweep(settings, dry_run=True))

    # In dry-run the article status must not be mutated.
    old_fm = _read_frontmatter(old)
    assert old_fm["status"] == "superseded"
    assert old_fm.get("needs_rereconciliation") is True  # flag not cleared


# ---------------------------------------------------------------------------
# Non-dry-run: applies verdict AND clears the flag
# ---------------------------------------------------------------------------


def test_sweep_non_dry_run_clears_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """non-dry-run: needs_rereconciliation flag is cleared after processing."""
    settings = _settings(tmp_path)
    store_root = settings.resolve_brain_store_path()

    flagged = store_root / "predecessor.md"
    _write_article(
        flagged,
        {"title": "Predecessor", "status": "superseded", "needs_rereconciliation": True},
    )

    fake = FakeReconciliationAgent(
        ReconciliationVerdict(verdict="orthogonal", reasoning="nothing to do")
    )
    monkeypatch.setattr(compile_workflow, "build_reconciliation_agent", lambda s: fake)

    result = asyncio.run(run_rereconciliation_sweep(settings, dry_run=False))

    assert result.flagged_count == 1
    assert result.processed_count == 1
    assert result.cleared_count == 1
    assert result.dry_run is False

    fm = _read_frontmatter(flagged)
    # Flag is cleared (set to False or removed).
    assert fm.get("needs_rereconciliation") is not True


def test_sweep_non_dry_run_applies_verdict_and_clears_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """non-dry-run with a real verdict: verdict applied + flag cleared."""
    settings = _settings(
        tmp_path,
        LOCAL_BRAIN_RECONCILIATION_AUTONOMY="apply",
        LOCAL_BRAIN_CORRELATION_TOP_K=5,
    )
    store_root = settings.resolve_brain_store_path()

    # OLD is flagged (its superseder was invalidated).
    old = store_root / "old.md"
    new = store_root / "new.md"
    _write_article(
        old,
        {"title": "Old", "status": "active", "needs_rereconciliation": True},
        body="Old knowledge.",
    )
    _write_article(new, {"title": "New", "status": "active"}, body="New knowledge.")

    fake = FakeReconciliationAgent(
        ReconciliationVerdict(verdict="corroborates", reasoning="new confirms old")
    )
    monkeypatch.setattr(compile_workflow, "build_reconciliation_agent", lambda s: fake)

    result = asyncio.run(run_rereconciliation_sweep(settings, dry_run=False))

    assert result.processed_count == 1
    assert result.cleared_count == 1

    # Flag must be cleared.
    old_fm = _read_frontmatter(old)
    assert old_fm.get("needs_rereconciliation") is not True


# ---------------------------------------------------------------------------
# Multiple flagged articles
# ---------------------------------------------------------------------------


def test_sweep_processes_multiple_flagged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All flagged articles are processed and cleared in non-dry-run."""
    settings = _settings(tmp_path)
    store_root = settings.resolve_brain_store_path()

    for name in ("a.md", "b.md", "c.md"):
        _write_article(
            store_root / name,
            {"title": name, "status": "superseded", "needs_rereconciliation": True},
        )

    fake = FakeReconciliationAgent(
        ReconciliationVerdict(verdict="orthogonal", reasoning="no pairs")
    )
    monkeypatch.setattr(compile_workflow, "build_reconciliation_agent", lambda s: fake)

    result = asyncio.run(run_rereconciliation_sweep(settings, dry_run=False))

    assert result.flagged_count == 3
    assert result.processed_count == 3
    assert result.cleared_count == 3

    for name in ("a.md", "b.md", "c.md"):
        fm = _read_frontmatter(store_root / name)
        assert fm.get("needs_rereconciliation") is not True


# ---------------------------------------------------------------------------
# Correlated-pair dry-run guard: the bug this test hunts
# ---------------------------------------------------------------------------
# The articles below share the keyword "knowledge" (and others) so
# find_related_articles's keyword-ranker returns new.md as related to old.md.
# The fake agent returns contradicts_supersedes, which would mutate old.md's
# frontmatter AND write a reconciliation-undo.jsonl entry — unless dry_run is
# correctly threaded through to the write calls.


def test_sweep_dry_run_correlated_pair_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dry_run=True with a genuinely correlated pair: no frontmatter written, no undo log.

    This is the regression test for the bug where _reconcile_applied_articles
    called apply_reconciliation_verdict(..., dry_run=False) and
    append_reconciliation_undo(..., dry_run=False) unconditionally, ignoring
    request.dry_run.  With a correlated pair the agent IS invoked and the
    verdict path is exercised — so this test would have FAILED before the fix.
    """
    settings = _settings(
        tmp_path,
        LOCAL_BRAIN_RECONCILIATION_AUTONOMY="apply",
        LOCAL_BRAIN_BULK_SUPERSESSION_THRESHOLD=5,
        LOCAL_BRAIN_CORRELATION_TOP_K=5,
    )
    store_root = settings.resolve_brain_store_path()

    # old.md: flagged article — the sweep treats it as the "new_target".
    # Uses "knowledge" + "python" + "project" so keyword-ranker finds new.md.
    old = store_root / "old.md"
    _write_article(
        old,
        {"title": "Old Knowledge", "status": "active", "needs_rereconciliation": True},
        body="This article describes knowledge about python project configuration.",
    )

    # new.md: related article sharing the same distinctive keywords.
    new = store_root / "new.md"
    _write_article(
        new,
        {"title": "New Knowledge", "status": "active"},
        body="Updated knowledge about python project configuration settings.",
    )

    fake = FakeReconciliationAgent(
        ReconciliationVerdict(verdict="contradicts_supersedes", reasoning="new supersedes old")
    )
    monkeypatch.setattr(compile_workflow, "build_reconciliation_agent", lambda s: fake)

    result = asyncio.run(run_rereconciliation_sweep(settings, dry_run=True))

    # Agent must have been invoked (pair was actually correlated).
    assert fake.call_count >= 1, "agent was not invoked — articles are not correlated enough"

    # --- Nothing may be written ---

    # In the sweep, old.md is the "new_target" (the flagged/newer article).
    # new.md is the related "old_path" found by find_related_articles.
    # contradicts_supersedes would mutate new.md (status → superseded) in a real run.
    # In dry-run, new.md must be UNCHANGED.
    new_fm = _read_frontmatter(new)
    assert new_fm["status"] == "active", "dry-run must not mutate the related article's status"
    assert "superseded_by" not in new_fm, "dry-run must not add superseded_by to related article"

    # 2. Undo log must not exist (or be empty).
    undo_log = settings.brain_home / "reconciliation-undo.jsonl"
    assert not undo_log.exists() or undo_log.read_text(encoding="utf-8").strip() == "", (
        "dry-run must not write to reconciliation-undo.jsonl"
    )

    # 3. needs_rereconciliation flag on old.md must NOT be cleared.
    old_fm = _read_frontmatter(old)
    assert old_fm.get("needs_rereconciliation") is True, "dry-run must not clear flag"

    # 4. Sweep result reflects dry-run.
    assert result.dry_run is True
    assert result.cleared_count == 0


def test_sweep_non_dry_run_correlated_pair_applies_verdict_and_clears_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """non-dry-run with a correlated pair: verdict applied, undo log written, flag cleared.

    This is the positive counterpart: the same setup but dry_run=False must
    mutate old.md and write the undo log entry.
    """
    settings = _settings(
        tmp_path,
        LOCAL_BRAIN_RECONCILIATION_AUTONOMY="apply",
        LOCAL_BRAIN_BULK_SUPERSESSION_THRESHOLD=5,
        LOCAL_BRAIN_CORRELATION_TOP_K=5,
    )
    store_root = settings.resolve_brain_store_path()

    old = store_root / "old.md"
    _write_article(
        old,
        {"title": "Old Knowledge", "status": "active", "needs_rereconciliation": True},
        body="This article describes knowledge about python project configuration.",
    )

    new = store_root / "new.md"
    _write_article(
        new,
        {"title": "New Knowledge", "status": "active"},
        body="Updated knowledge about python project configuration settings.",
    )

    fake = FakeReconciliationAgent(
        ReconciliationVerdict(verdict="contradicts_supersedes", reasoning="new supersedes old")
    )
    monkeypatch.setattr(compile_workflow, "build_reconciliation_agent", lambda s: fake)

    result = asyncio.run(run_rereconciliation_sweep(settings, dry_run=False))

    # Agent must have been invoked.
    assert fake.call_count >= 1, "agent was not invoked — articles are not correlated enough"

    # --- Verdict must be applied ---

    # In the sweep, old.md is the "new_target" (the flagged article = the newer one).
    # new.md is the related "old_path" found by find_related_articles.
    # contradicts_supersedes mutates the OLD article in the pair → new.md gets
    # status=superseded, while old.md (the new_target) gets a supersedes link.
    new_fm = _read_frontmatter(new)
    assert new_fm["status"] == "superseded", "non-dry-run must apply supersession to the related article"

    # 2. Undo log must contain the record.
    records = read_reconciliation_undo(settings.brain_home)
    assert len(records) >= 1, "undo log must be written in non-dry-run"
    assert records[0]["verdict"] == "contradicts_supersedes"

    # 3. needs_rereconciliation flag must be cleared on the flagged article.
    old_fm = _read_frontmatter(old)
    assert old_fm.get("needs_rereconciliation") is not True, "flag must be cleared in non-dry-run"

    # 4. Sweep result reflects non-dry-run.
    assert result.dry_run is False
    assert result.cleared_count == 1


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


def test_rereconciliation_result_defaults() -> None:
    r = RereconciliationResult()
    assert r.flagged_count == 0
    assert r.processed_count == 0
    assert r.cleared_count == 0
    assert r.dry_run is True
    assert r.outcomes == []
    assert r.flagged_paths == []
