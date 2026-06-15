from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from fritz_local_brain import compile_workflow
from fritz_local_brain.config import Settings
from fritz_local_brain.knowledge import (
    apply_frontmatter_update,
    apply_reconciliation_verdict,
    find_rereconciliation_flagged,
    mark_for_rereconciliation,
    revert_reconciliation,
)
from fritz_local_brain.logs import read_reconciliation_undo
from fritz_local_brain.models import (
    ArticleWriteProposal,
    CompileAgentOutput,
    CompileRunRequest,
    ReconciliationVerdict,
)


# ---------------------------------------------------------------------------
# Helpers
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
    def __init__(self, verdict: ReconciliationVerdict) -> None:
        self.verdict = verdict
        self.prompts: list[str] = []
        self.deps: list[object] = []

    async def run(self, prompt: str, *, deps: object, usage_limits: object) -> SimpleNamespace:
        self.prompts.append(prompt)
        self.deps.append(deps)
        return SimpleNamespace(output=self.verdict)


class FakeCompileAgent:
    def __init__(self, output: CompileAgentOutput) -> None:
        self.output = output

    async def run(self, prompt: str, *, deps: object, usage_limits: object) -> SimpleNamespace:
        return SimpleNamespace(output=self.output)


# ---------------------------------------------------------------------------
# Pure mapping tests
# ---------------------------------------------------------------------------


def test_apply_frontmatter_update_sets_status_links_scope_and_updated(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    article = store_root / "common" / "decisions" / "a.md"
    _write_article(article, {"type": "article", "title": "A", "status": "active"})

    apply_frontmatter_update(
        article,
        store_root=store_root,
        status="corroborated",
        append_links={"corroborated_by": ["common/decisions/b.md", "common/decisions/b.md"]},
        scope_qualifier="staging-only",
    )

    fm = _read_frontmatter(article)
    assert fm["status"] == "corroborated"
    assert fm["corroborated_by"] == ["common/decisions/b.md"]  # dedup
    assert fm["scope"] == "staging-only"
    assert "updated" in fm
    assert "Body." in article.read_text(encoding="utf-8")


def test_apply_frontmatter_update_rejects_path_outside_store_root(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    store_root.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    _write_article(outside, {"type": "article", "title": "X"})

    with pytest.raises(ValueError):
        apply_frontmatter_update(outside, store_root=store_root, status="superseded")


def test_apply_frontmatter_update_dry_run_is_noop(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    article = store_root / "a.md"
    _write_article(article, {"type": "article", "title": "A", "status": "active"})
    before = article.read_text(encoding="utf-8")

    apply_frontmatter_update(article, store_root=store_root, status="superseded", dry_run=True)
    assert article.read_text(encoding="utf-8") == before


def test_apply_frontmatter_update_handles_missing_frontmatter(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    article = store_root / "a.md"
    article.parent.mkdir(parents=True)
    article.write_text("Just a plain body with no front matter.\n", encoding="utf-8")

    apply_frontmatter_update(article, store_root=store_root, status="active")
    fm = _read_frontmatter(article)
    assert fm["status"] == "active"
    assert "Just a plain body" in article.read_text(encoding="utf-8")


def _verdict(kind: str, scope: str | None = None) -> ReconciliationVerdict:
    return ReconciliationVerdict(verdict=kind, reasoning="r", scope_qualifier=scope)


def test_verdict_corroborates(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    old = store_root / "common" / "decisions" / "old.md"
    new = store_root / "common" / "decisions" / "new.md"
    _write_article(old, {"type": "article", "title": "Old", "status": "active"})
    _write_article(new, {"type": "article", "title": "New", "status": "active"})

    apply_reconciliation_verdict(
        _verdict("corroborates"), new_path=new, old_path=old, store_root=store_root, dry_run=False
    )

    old_fm = _read_frontmatter(old)
    assert old_fm["status"] == "corroborated"
    assert old_fm["corroborated_by"] == ["common/decisions/new.md"]


def test_verdict_corroborates_never_downgrades_superseded(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    old = store_root / "old.md"
    new = store_root / "new.md"
    _write_article(old, {"type": "article", "title": "Old", "status": "superseded"})
    _write_article(new, {"type": "article", "title": "New", "status": "active"})

    apply_reconciliation_verdict(
        _verdict("corroborates"), new_path=new, old_path=old, store_root=store_root, dry_run=False
    )

    old_fm = _read_frontmatter(old)
    assert old_fm["status"] == "superseded"  # not downgraded
    assert old_fm["corroborated_by"] == ["new.md"]


def test_verdict_refines(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    old = store_root / "old.md"
    new = store_root / "new.md"
    _write_article(old, {"type": "article", "title": "Old", "status": "active"})
    _write_article(new, {"type": "article", "title": "New", "status": "active"})

    apply_reconciliation_verdict(
        _verdict("refines"), new_path=new, old_path=old, store_root=store_root, dry_run=False
    )

    old_fm = _read_frontmatter(old)
    new_fm = _read_frontmatter(new)
    assert old_fm["status"] == "active"  # no status change
    assert new_fm["status"] == "active"
    assert old_fm["refined_by"] == ["new.md"]
    assert new_fm["refines"] == ["old.md"]


def test_verdict_contradicts_supersedes(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    old = store_root / "old.md"
    new = store_root / "new.md"
    _write_article(old, {"type": "article", "title": "Old", "status": "active"})
    _write_article(new, {"type": "article", "title": "New", "status": "active"})

    apply_reconciliation_verdict(
        _verdict("contradicts_supersedes"), new_path=new, old_path=old, store_root=store_root, dry_run=False
    )

    old_fm = _read_frontmatter(old)
    new_fm = _read_frontmatter(new)
    assert old_fm["status"] == "superseded"
    assert old_fm["superseded_by"] == ["new.md"]
    assert new_fm["supersedes"] == ["old.md"]


def test_verdict_context_split_sets_scope_on_both(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    old = store_root / "old.md"
    new = store_root / "new.md"
    _write_article(old, {"type": "article", "title": "Old", "status": "active"})
    _write_article(new, {"type": "article", "title": "New", "status": "active"})

    apply_reconciliation_verdict(
        _verdict("context_split", scope="v1-behavior"),
        new_path=new,
        old_path=old,
        store_root=store_root,
        dry_run=False,
    )

    old_fm = _read_frontmatter(old)
    new_fm = _read_frontmatter(new)
    assert old_fm["status"] == "active"
    assert new_fm["status"] == "active"
    assert old_fm["scope"] == "v1-behavior"
    assert new_fm["scope"] == "v1-behavior"


def test_verdict_orthogonal_no_change(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    old = store_root / "old.md"
    new = store_root / "new.md"
    _write_article(old, {"type": "article", "title": "Old", "status": "active"})
    _write_article(new, {"type": "article", "title": "New", "status": "active"})
    old_before = old.read_text(encoding="utf-8")
    new_before = new.read_text(encoding="utf-8")

    outcome = apply_reconciliation_verdict(
        _verdict("orthogonal"), new_path=new, old_path=old, store_root=store_root, dry_run=False
    )

    assert old.read_text(encoding="utf-8") == old_before
    assert new.read_text(encoding="utf-8") == new_before
    assert outcome.actions == []


# ---------------------------------------------------------------------------
# Store-mode compile integration
# ---------------------------------------------------------------------------


def _store_mode_settings(tmp_path: Path, **kwargs) -> Settings:
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"
    (brain_home / "capture" / "inbox").mkdir(parents=True)
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")
    return Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills", **kwargs)


def test_store_mode_compile_runs_reconciliation_and_supersedes_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _store_mode_settings(tmp_path)
    brain_home = settings.brain_home
    store_root = brain_home / "knowledge"

    # Existing related store article.
    existing = store_root / "common" / "decisions" / "color.md"
    _write_article(
        existing,
        {"type": "article", "title": "Project Color", "status": "active"},
        body="Project color is blue.",
    )

    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    capture_path.write_text("# Capture\n\nProject color is now green.\n", encoding="utf-8")

    proposal = ArticleWriteProposal(
        vault="brain",
        relative_path="common/decisions/color-new.md",
        operation="create",
        title="Project Color Updated",
        summary="Color changed.",
        sources=[str(capture_path)],
        body="Project color is green.",
    )

    monkeypatch.setattr(
        compile_workflow,
        "build_compile_agent",
        lambda settings, skill_text: FakeCompileAgent(CompileAgentOutput(proposals=[proposal])),
    )
    fake = FakeReconciliationAgent(
        ReconciliationVerdict(verdict="contradicts_supersedes", reasoning="newer green wins")
    )
    monkeypatch.setattr(compile_workflow, "build_reconciliation_agent", lambda settings: fake)

    result = asyncio.run(
        compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False, max_captures=1))
    )

    assert result.errors == []
    assert len(result.applied) == 1
    assert len(result.reconciliations) >= 1
    superseded = [o for o in result.reconciliations if o.verdict == "contradicts_supersedes"]
    assert superseded, "expected a contradicts_supersedes outcome"

    existing_fm = _read_frontmatter(existing)
    assert existing_fm["status"] == "superseded"
    assert "common/decisions/color-new.md" in existing_fm["superseded_by"]


def test_store_mode_compile_reconciliation_disabled_does_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _store_mode_settings(tmp_path, LOCAL_BRAIN_RECONCILIATION_ENABLED=False)
    brain_home = settings.brain_home
    store_root = brain_home / "knowledge"

    existing = store_root / "common" / "decisions" / "color.md"
    _write_article(
        existing,
        {"type": "article", "title": "Project Color", "status": "active"},
        body="Project color is blue.",
    )

    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    capture_path.write_text("# Capture\n\nProject color is now green.\n", encoding="utf-8")

    proposal = ArticleWriteProposal(
        vault="brain",
        relative_path="common/decisions/color-new.md",
        operation="create",
        title="Project Color Updated",
        summary="Color changed.",
        sources=[str(capture_path)],
        body="Project color is green.",
    )
    monkeypatch.setattr(
        compile_workflow,
        "build_compile_agent",
        lambda settings, skill_text: FakeCompileAgent(CompileAgentOutput(proposals=[proposal])),
    )

    def _boom(settings):  # pragma: no cover - must not be called
        raise AssertionError("reconciliation agent must not be built when disabled")

    monkeypatch.setattr(compile_workflow, "build_reconciliation_agent", _boom)

    result = asyncio.run(
        compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False, max_captures=1))
    )

    assert result.errors == []
    assert result.reconciliations == []
    assert _read_frontmatter(existing)["status"] == "active"


def test_store_mode_compile_reconciliation_topk_zero_does_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _store_mode_settings(tmp_path, LOCAL_BRAIN_CORRELATION_TOP_K=0)
    brain_home = settings.brain_home
    store_root = brain_home / "knowledge"

    existing = store_root / "common" / "decisions" / "color.md"
    _write_article(
        existing,
        {"type": "article", "title": "Project Color", "status": "active"},
        body="Project color is blue.",
    )

    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    capture_path.write_text("# Capture\n\nProject color is now green.\n", encoding="utf-8")

    proposal = ArticleWriteProposal(
        vault="brain",
        relative_path="common/decisions/color-new.md",
        operation="create",
        title="Project Color Updated",
        summary="Color changed.",
        sources=[str(capture_path)],
        body="Project color is green.",
    )
    monkeypatch.setattr(
        compile_workflow,
        "build_compile_agent",
        lambda settings, skill_text: FakeCompileAgent(CompileAgentOutput(proposals=[proposal])),
    )

    def _boom(settings):  # pragma: no cover - must not be called
        raise AssertionError("reconciliation agent must not be built when top_k == 0")

    monkeypatch.setattr(compile_workflow, "build_reconciliation_agent", _boom)

    result = asyncio.run(
        compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False, max_captures=1))
    )

    assert result.errors == []
    assert result.reconciliations == []
    assert _read_frontmatter(existing)["status"] == "active"


# ---------------------------------------------------------------------------
# WI8: Config validators
# ---------------------------------------------------------------------------


def test_reconciliation_autonomy_defaults_to_apply(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path)
    assert settings.reconciliation_autonomy == "apply"


def test_reconciliation_autonomy_accepts_propose(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path, RECONCILIATION_AUTONOMY="propose")
    assert settings.reconciliation_autonomy == "propose"


def test_reconciliation_autonomy_normalizes_case_and_whitespace(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path, RECONCILIATION_AUTONOMY="  Apply  ")
    assert settings.reconciliation_autonomy == "apply"


def test_reconciliation_autonomy_rejects_invalid_value(tmp_path: Path) -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path, RECONCILIATION_AUTONOMY="auto")


def test_reconciliation_autonomy_empty_string_coerces_to_apply(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path, RECONCILIATION_AUTONOMY="")
    assert settings.reconciliation_autonomy == "apply"


def test_bulk_supersession_threshold_default(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path)
    assert settings.bulk_supersession_threshold == 5


def test_bulk_supersession_threshold_configurable(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path, BULK_SUPERSESSION_THRESHOLD="3")
    assert settings.bulk_supersession_threshold == 3


# ---------------------------------------------------------------------------
# WI8: remove_links in apply_frontmatter_update
# ---------------------------------------------------------------------------


def test_apply_frontmatter_update_remove_links_removes_entries(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    article = store_root / "a.md"
    _write_article(article, {
        "type": "article",
        "title": "A",
        "status": "superseded",
        "superseded_by": ["b.md", "c.md"],
    })

    apply_frontmatter_update(
        article,
        store_root=store_root,
        remove_links={"superseded_by": ["b.md"]},
    )

    fm = _read_frontmatter(article)
    assert fm["superseded_by"] == ["c.md"]


def test_apply_frontmatter_update_remove_links_prunes_empty_list(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    article = store_root / "a.md"
    _write_article(article, {
        "type": "article",
        "title": "A",
        "status": "superseded",
        "superseded_by": ["b.md"],
    })

    apply_frontmatter_update(
        article,
        store_root=store_root,
        remove_links={"superseded_by": ["b.md"]},
    )

    fm = _read_frontmatter(article)
    assert "superseded_by" not in fm


def test_apply_frontmatter_update_remove_links_noop_if_key_absent(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    article = store_root / "a.md"
    _write_article(article, {"type": "article", "title": "A", "status": "active"})

    apply_frontmatter_update(
        article,
        store_root=store_root,
        remove_links={"superseded_by": ["x.md"]},
    )
    fm = _read_frontmatter(article)
    assert "superseded_by" not in fm


def test_apply_frontmatter_update_remove_links_dry_run_is_noop(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    article = store_root / "a.md"
    _write_article(article, {
        "type": "article",
        "title": "A",
        "status": "superseded",
        "superseded_by": ["b.md"],
    })
    before = article.read_text(encoding="utf-8")

    apply_frontmatter_update(
        article,
        store_root=store_root,
        remove_links={"superseded_by": ["b.md"]},
        dry_run=True,
    )
    assert article.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# WI8: apply_reconciliation_verdict captures prior_status
# ---------------------------------------------------------------------------


def test_apply_reconciliation_verdict_captures_prior_status_on_supersedes(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    old = store_root / "old.md"
    new = store_root / "new.md"
    _write_article(old, {"title": "Old", "status": "active"})
    _write_article(new, {"title": "New", "status": "active"})

    outcome = apply_reconciliation_verdict(
        _verdict("contradicts_supersedes"), new_path=new, old_path=old, store_root=store_root, dry_run=False
    )

    assert outcome.prior_status == "active"
    assert outcome.applied is True
    assert outcome.disposition == "applied"


def test_apply_reconciliation_verdict_captures_prior_status_on_corroborates(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    old = store_root / "old.md"
    new = store_root / "new.md"
    _write_article(old, {"title": "Old", "status": "active"})
    _write_article(new, {"title": "New", "status": "active"})

    outcome = apply_reconciliation_verdict(
        _verdict("corroborates"), new_path=new, old_path=old, store_root=store_root, dry_run=False
    )

    assert outcome.prior_status == "active"
    assert outcome.applied is True
    assert outcome.disposition == "applied"


# ---------------------------------------------------------------------------
# WI8: revert_reconciliation (undo affordance)
# ---------------------------------------------------------------------------


def test_revert_reconciliation_restores_status_and_removes_links(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    old = store_root / "old.md"
    new = store_root / "new.md"
    _write_article(old, {"title": "Old", "status": "active"})
    _write_article(new, {"title": "New", "status": "active"})

    outcome = apply_reconciliation_verdict(
        _verdict("contradicts_supersedes"), new_path=new, old_path=old, store_root=store_root, dry_run=False
    )
    assert _read_frontmatter(old)["status"] == "superseded"
    assert "new.md" in _read_frontmatter(old).get("superseded_by", [])
    assert "old.md" in _read_frontmatter(new).get("supersedes", [])

    revert_reconciliation(outcome, store_root=store_root, dry_run=False)

    old_fm = _read_frontmatter(old)
    new_fm = _read_frontmatter(new)
    assert old_fm["status"] == "active"
    assert "superseded_by" not in old_fm
    assert "supersedes" not in new_fm


def test_revert_reconciliation_dry_run_does_not_write(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    old = store_root / "old.md"
    new = store_root / "new.md"
    _write_article(old, {"title": "Old", "status": "active"})
    _write_article(new, {"title": "New", "status": "active"})

    outcome = apply_reconciliation_verdict(
        _verdict("contradicts_supersedes"), new_path=new, old_path=old, store_root=store_root, dry_run=False
    )
    state_before_revert = old.read_text(encoding="utf-8")

    revert_reconciliation(outcome, store_root=store_root, dry_run=True)

    assert old.read_text(encoding="utf-8") == state_before_revert


def test_revert_reconciliation_corroborates(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    old = store_root / "old.md"
    new = store_root / "new.md"
    _write_article(old, {"title": "Old", "status": "active"})
    _write_article(new, {"title": "New", "status": "active"})

    outcome = apply_reconciliation_verdict(
        _verdict("corroborates"), new_path=new, old_path=old, store_root=store_root, dry_run=False
    )
    assert _read_frontmatter(old)["status"] == "corroborated"

    revert_reconciliation(outcome, store_root=store_root, dry_run=False)

    old_fm = _read_frontmatter(old)
    assert old_fm["status"] == "active"
    assert "corroborated_by" not in old_fm


# ---------------------------------------------------------------------------
# WI8: undo log (logs.py)
# ---------------------------------------------------------------------------


def test_append_and_read_reconciliation_undo(tmp_path: Path) -> None:
    from fritz_local_brain.logs import append_reconciliation_undo

    record = {
        "ts": "2026-06-15T10:00:00",
        "verdict": "contradicts_supersedes",
        "new_path": "common/decisions/new.md",
        "old_path": "common/decisions/old.md",
        "old_prior_status": "active",
        "links_added": {"superseded_by": ["common/decisions/new.md"]},
    }
    append_reconciliation_undo(tmp_path, record, dry_run=False)

    records = read_reconciliation_undo(tmp_path)
    assert len(records) == 1
    assert records[0]["verdict"] == "contradicts_supersedes"
    assert records[0]["old_prior_status"] == "active"


def test_append_reconciliation_undo_dry_run_writes_nothing(tmp_path: Path) -> None:
    from fritz_local_brain.logs import append_reconciliation_undo

    append_reconciliation_undo(tmp_path, {"ts": "x", "verdict": "v"}, dry_run=True)

    assert not (tmp_path / "reconciliation-undo.jsonl").exists()


def test_read_reconciliation_undo_returns_empty_when_file_absent(tmp_path: Path) -> None:
    records = read_reconciliation_undo(tmp_path)
    assert records == []


# ---------------------------------------------------------------------------
# WI8: Autonomy + gate integration tests via run_compile
# ---------------------------------------------------------------------------


def _store_mode_settings_wi8(tmp_path: Path, **kwargs) -> Settings:
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"
    (brain_home / "capture" / "inbox").mkdir(parents=True)
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")
    return Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills", **kwargs)


def _make_compile_run_setup(tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch,
                             verdict_kind: str = "contradicts_supersedes") -> tuple:
    """Set up a store-mode compile run with one existing article."""
    brain_home = settings.brain_home
    store_root = brain_home / "knowledge"

    existing = store_root / "common" / "decisions" / "color.md"
    _write_article(existing, {"title": "Project Color", "status": "active"}, body="Project color is blue.")

    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    capture_path.write_text("# Capture\n\nProject color is now green.\n", encoding="utf-8")

    proposal = ArticleWriteProposal(
        vault="brain",
        relative_path="common/decisions/color-new.md",
        operation="create",
        title="Project Color Updated",
        summary="Color changed.",
        sources=[str(capture_path)],
        body="Project color is green.",
    )

    monkeypatch.setattr(
        compile_workflow,
        "build_compile_agent",
        lambda s, st: FakeCompileAgent(CompileAgentOutput(proposals=[proposal])),
    )

    fake = FakeReconciliationAgent(
        ReconciliationVerdict(verdict=verdict_kind, reasoning="test")
    )
    monkeypatch.setattr(compile_workflow, "build_reconciliation_agent", lambda s: fake)

    return brain_home, existing


def test_apply_mode_within_threshold_applies_and_writes_undo_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apply mode, supersession within threshold → applied + undo-log written."""
    settings = _store_mode_settings_wi8(
        tmp_path,
        LOCAL_BRAIN_RECONCILIATION_AUTONOMY="apply",
        LOCAL_BRAIN_BULK_SUPERSESSION_THRESHOLD=5,
    )
    brain_home, existing = _make_compile_run_setup(tmp_path, settings, monkeypatch)

    result = asyncio.run(
        compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False, max_captures=1))
    )

    assert result.errors == []
    superseded_outcomes = [o for o in result.reconciliations if o.verdict == "contradicts_supersedes"]
    assert superseded_outcomes, "expected a supersession outcome"
    outcome = superseded_outcomes[0]
    assert outcome.applied is True
    assert outcome.disposition == "applied"
    assert outcome.prior_status == "active"

    assert _read_frontmatter(existing)["status"] == "superseded"

    records = read_reconciliation_undo(brain_home)
    assert len(records) >= 1
    assert records[0]["verdict"] == "contradicts_supersedes"
    assert records[0]["old_prior_status"] == "active"


def test_propose_mode_no_token_does_not_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """propose mode without approval token → supersession NOT applied, disposition=proposed."""
    settings = _store_mode_settings_wi8(
        tmp_path,
        LOCAL_BRAIN_RECONCILIATION_AUTONOMY="propose",
        LOCAL_BRAIN_APPROVAL_TOKEN="secret",
    )
    brain_home, existing = _make_compile_run_setup(tmp_path, settings, monkeypatch)

    result = asyncio.run(
        compile_workflow.run_compile(
            settings, CompileRunRequest(dry_run=False, max_captures=1, approval_token=None)
        )
    )

    assert result.errors == []
    superseded_outcomes = [o for o in result.reconciliations if o.verdict == "contradicts_supersedes"]
    assert superseded_outcomes
    outcome = superseded_outcomes[0]
    assert outcome.applied is False
    assert outcome.disposition == "proposed"

    assert _read_frontmatter(existing)["status"] == "active"
    assert read_reconciliation_undo(brain_home) == []


def test_propose_mode_with_matching_token_applies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """propose mode WITH matching approval token → supersession applied."""
    settings = _store_mode_settings_wi8(
        tmp_path,
        LOCAL_BRAIN_RECONCILIATION_AUTONOMY="propose",
        LOCAL_BRAIN_APPROVAL_TOKEN="secret",
    )
    brain_home, existing = _make_compile_run_setup(tmp_path, settings, monkeypatch)

    result = asyncio.run(
        compile_workflow.run_compile(
            settings, CompileRunRequest(dry_run=False, max_captures=1, approval_token="secret")
        )
    )

    assert result.errors == []
    superseded_outcomes = [o for o in result.reconciliations if o.verdict == "contradicts_supersedes"]
    assert superseded_outcomes
    outcome = superseded_outcomes[0]
    assert outcome.applied is True
    assert outcome.disposition == "applied"
    assert _read_frontmatter(existing)["status"] == "superseded"


def test_bulk_escalation_without_approval_blocks_supersessions_but_applies_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apply mode, supersession_count > threshold without approval → supersessions escalated, others applied."""
    settings = _store_mode_settings_wi8(
        tmp_path,
        LOCAL_BRAIN_RECONCILIATION_AUTONOMY="apply",
        LOCAL_BRAIN_BULK_SUPERSESSION_THRESHOLD=1,
        LOCAL_BRAIN_APPROVAL_TOKEN="secret",
    )
    brain_home = settings.brain_home
    store_root = brain_home / "knowledge"

    existing1 = store_root / "common" / "decisions" / "color.md"
    existing2 = store_root / "common" / "decisions" / "shade.md"
    extra_corr = store_root / "common" / "decisions" / "extra.md"
    _write_article(existing1, {"title": "Color", "status": "active"}, body="Color is blue.")
    _write_article(existing2, {"title": "Shade", "status": "active"}, body="Shade is dark.")
    _write_article(extra_corr, {"title": "Extra", "status": "active"}, body="Extra corr.")

    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    capture_path.write_text("# Capture\n\nColor is green, shade is light.\n", encoding="utf-8")

    proposal = ArticleWriteProposal(
        vault="brain",
        relative_path="common/decisions/color-new.md",
        operation="create",
        title="Project Color Updated",
        summary="Color changed.",
        sources=[str(capture_path)],
        body="Color is green, shade is light.",
    )

    monkeypatch.setattr(
        compile_workflow,
        "build_compile_agent",
        lambda s, st: FakeCompileAgent(CompileAgentOutput(proposals=[proposal])),
    )

    # Return: supersede, supersede, corroborate (non-supersession).
    verdicts = [
        ReconciliationVerdict(verdict="contradicts_supersedes", reasoning="sup1"),
        ReconciliationVerdict(verdict="contradicts_supersedes", reasoning="sup2"),
        ReconciliationVerdict(verdict="corroborates", reasoning="corr1"),
    ]
    verdict_iter = iter(verdicts)

    class MultiVerdict:
        async def run(self, prompt, *, deps, usage_limits):
            try:
                v = next(verdict_iter)
            except StopIteration:
                v = ReconciliationVerdict(verdict="orthogonal", reasoning="no more")
            return SimpleNamespace(output=v)

    monkeypatch.setattr(compile_workflow, "build_reconciliation_agent", lambda s: MultiVerdict())

    result = asyncio.run(
        compile_workflow.run_compile(
            settings, CompileRunRequest(dry_run=False, max_captures=1, approval_token=None)
        )
    )

    assert result.errors == []

    escalated = [o for o in result.reconciliations if o.disposition == "escalated"]
    applied_outcomes = [o for o in result.reconciliations if o.disposition == "applied"]
    assert len(escalated) == 2, f"Expected 2 escalated, got {escalated}"
    assert len(applied_outcomes) >= 1, f"Expected at least 1 applied (the corroborate), got {applied_outcomes}"

    assert _read_frontmatter(existing1)["status"] == "active"
    assert _read_frontmatter(existing2)["status"] == "active"

    undo_records = read_reconciliation_undo(brain_home)
    supersession_undo = [r for r in undo_records if r["verdict"] == "contradicts_supersedes"]
    assert supersession_undo == [], "No undo log for escalated verdicts"


def test_bulk_escalation_with_approval_applies_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apply mode, supersession_count > threshold WITH approval → all applied."""
    settings = _store_mode_settings_wi8(
        tmp_path,
        LOCAL_BRAIN_RECONCILIATION_AUTONOMY="apply",
        LOCAL_BRAIN_BULK_SUPERSESSION_THRESHOLD=1,
        LOCAL_BRAIN_APPROVAL_TOKEN="secret",
    )
    brain_home = settings.brain_home
    store_root = brain_home / "knowledge"

    existing1 = store_root / "common" / "decisions" / "color.md"
    existing2 = store_root / "common" / "decisions" / "shade.md"
    _write_article(existing1, {"title": "Color", "status": "active"}, body="Color is blue.")
    _write_article(existing2, {"title": "Shade", "status": "active"}, body="Shade is dark.")

    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    capture_path.write_text("# Capture\n\nUpdated colors.\n", encoding="utf-8")

    proposal = ArticleWriteProposal(
        vault="brain",
        relative_path="common/decisions/new.md",
        operation="create",
        title="New",
        summary="New.",
        sources=[str(capture_path)],
        body="Updated.",
    )

    monkeypatch.setattr(
        compile_workflow,
        "build_compile_agent",
        lambda s, st: FakeCompileAgent(CompileAgentOutput(proposals=[proposal])),
    )

    verdicts = [
        ReconciliationVerdict(verdict="contradicts_supersedes", reasoning="sup1"),
        ReconciliationVerdict(verdict="contradicts_supersedes", reasoning="sup2"),
    ]
    verdict_iter = iter(verdicts)

    class MultiVerdict:
        async def run(self, prompt, *, deps, usage_limits):
            try:
                v = next(verdict_iter)
            except StopIteration:
                v = ReconciliationVerdict(verdict="orthogonal", reasoning="done")
            return SimpleNamespace(output=v)

    monkeypatch.setattr(compile_workflow, "build_reconciliation_agent", lambda s: MultiVerdict())

    result = asyncio.run(
        compile_workflow.run_compile(
            settings, CompileRunRequest(dry_run=False, max_captures=1, approval_token="secret")
        )
    )

    assert result.errors == []
    applied_outcomes = [o for o in result.reconciliations if o.applied is True]
    assert len(applied_outcomes) == 2, f"Expected 2 applied, got {applied_outcomes}"

    assert _read_frontmatter(existing1)["status"] == "superseded"
    assert _read_frontmatter(existing2)["status"] == "superseded"

    records = read_reconciliation_undo(brain_home)
    assert len(records) == 2


# ---------------------------------------------------------------------------
# WI9: Resurrection flagging (issue #94)
# ---------------------------------------------------------------------------


def test_mark_for_rereconciliation_sets_flag(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    article = store_root / "predecessor.md"
    _write_article(article, {"title": "Predecessor", "status": "superseded"})

    mark_for_rereconciliation(article, store_root=store_root)

    fm = _read_frontmatter(article)
    assert fm.get("needs_rereconciliation") is True


def test_mark_for_rereconciliation_dry_run_is_noop(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    article = store_root / "predecessor.md"
    _write_article(article, {"title": "Predecessor", "status": "superseded"})
    before = article.read_text(encoding="utf-8")

    mark_for_rereconciliation(article, store_root=store_root, dry_run=True)

    assert article.read_text(encoding="utf-8") == before


def test_find_rereconciliation_flagged_returns_flagged_paths(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    flagged_a = store_root / "a.md"
    _write_article(flagged_a, {"title": "A", "status": "superseded", "needs_rereconciliation": True})
    not_flagged = store_root / "b.md"
    _write_article(not_flagged, {"title": "B", "status": "active"})
    # flagged but value is not True (should not be returned)
    flagged_wrong = store_root / "c.md"
    _write_article(flagged_wrong, {"title": "C", "needs_rereconciliation": False})

    result = find_rereconciliation_flagged(store_root)

    assert result == ["a.md"]


def test_find_rereconciliation_flagged_empty_when_none_flagged(tmp_path: Path) -> None:
    store_root = tmp_path / "knowledge"
    _write_article(store_root / "a.md", {"title": "A", "status": "active"})

    result = find_rereconciliation_flagged(store_root)

    assert result == []


def test_find_rereconciliation_flagged_returns_empty_for_missing_store(tmp_path: Path) -> None:
    result = find_rereconciliation_flagged(tmp_path / "no-such-store")
    assert result == []


def test_contradicts_supersedes_flags_predecessors_of_old(tmp_path: Path) -> None:
    """When B supersedes A and C supersedes B, A gets needs_rereconciliation: true."""
    store_root = tmp_path / "knowledge"
    # A: an old article that was previously superseded by B.
    article_a = store_root / "a.md"
    _write_article(article_a, {"title": "A", "status": "superseded", "superseded_by": ["b.md"]})
    # B: an article that superseded A and is now being superseded by C.
    article_b = store_root / "b.md"
    _write_article(article_b, {"title": "B", "status": "active", "supersedes": ["a.md"]})
    # C: the new article that supersedes B.
    article_c = store_root / "c.md"
    _write_article(article_c, {"title": "C", "status": "active"})

    # Apply contradicts_supersedes to B (B is the OLD, C is the NEW).
    outcome = apply_reconciliation_verdict(
        _verdict("contradicts_supersedes"),
        new_path=article_c,
        old_path=article_b,
        store_root=store_root,
        dry_run=False,
    )

    # B should now be superseded by C.
    b_fm = _read_frontmatter(article_b)
    assert b_fm["status"] == "superseded"
    assert "c.md" in b_fm["superseded_by"]

    # A (B's predecessor) should be flagged for re-reconciliation.
    a_fm = _read_frontmatter(article_a)
    assert a_fm.get("needs_rereconciliation") is True, "A must be flagged because its superseder (B) was invalidated"

    # find_rereconciliation_flagged should return A.
    flagged = find_rereconciliation_flagged(store_root)
    assert "a.md" in flagged

    # The action record should mention the resurrection flagging.
    assert any("needs_rereconciliation" in action or "a.md" in action for action in outcome.actions)


def test_contradicts_supersedes_no_predecessor_flagging_when_no_supersedes_list(tmp_path: Path) -> None:
    """When OLD has no supersedes list, no predecessor is flagged."""
    store_root = tmp_path / "knowledge"
    old = store_root / "old.md"
    new = store_root / "new.md"
    _write_article(old, {"title": "Old", "status": "active"})
    _write_article(new, {"title": "New", "status": "active"})

    apply_reconciliation_verdict(
        _verdict("contradicts_supersedes"),
        new_path=new,
        old_path=old,
        store_root=store_root,
        dry_run=False,
    )

    flagged = find_rereconciliation_flagged(store_root)
    assert flagged == []


def test_contradicts_supersedes_dry_run_does_not_flag_predecessors(tmp_path: Path) -> None:
    """dry_run=True: no predecessor flagging written to disk."""
    store_root = tmp_path / "knowledge"
    article_a = store_root / "a.md"
    _write_article(article_a, {"title": "A", "status": "superseded"})
    article_b = store_root / "b.md"
    _write_article(article_b, {"title": "B", "status": "active", "supersedes": ["a.md"]})
    article_c = store_root / "c.md"
    _write_article(article_c, {"title": "C", "status": "active"})

    before = article_a.read_text(encoding="utf-8")

    apply_reconciliation_verdict(
        _verdict("contradicts_supersedes"),
        new_path=article_c,
        old_path=article_b,
        store_root=store_root,
        dry_run=True,
    )

    assert article_a.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# WI9: Compile index rebuild after reconciliation archives an article
# ---------------------------------------------------------------------------


def test_compile_rebuilds_indexes_after_supersession(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a compile run that produces a supersession, backfill_indexes is called to
    update the archive index.  The superseded article appears in archive.index.md."""
    settings = _store_mode_settings(tmp_path)
    brain_home = settings.brain_home
    store_root = brain_home / "knowledge"

    existing = store_root / "common" / "decisions" / "color.md"
    _write_article(
        existing,
        {"type": "article", "title": "Project Color", "status": "active"},
        body="Project color is blue.",
    )

    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    capture_path.write_text("# Capture\n\nProject color is now green.\n", encoding="utf-8")

    proposal = ArticleWriteProposal(
        vault="brain",
        relative_path="common/decisions/color-new.md",
        operation="create",
        title="Project Color Updated",
        summary="Color changed.",
        sources=[str(capture_path)],
        body="Project color is green.",
    )

    monkeypatch.setattr(
        compile_workflow,
        "build_compile_agent",
        lambda settings, skill_text: FakeCompileAgent(CompileAgentOutput(proposals=[proposal])),
    )
    fake = FakeReconciliationAgent(
        ReconciliationVerdict(verdict="contradicts_supersedes", reasoning="newer green wins")
    )
    monkeypatch.setattr(compile_workflow, "build_reconciliation_agent", lambda settings: fake)

    result = asyncio.run(
        compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False, max_captures=1))
    )

    assert result.errors == []
    superseded_outcomes = [o for o in result.reconciliations if o.verdict == "contradicts_supersedes" and o.applied]
    assert superseded_outcomes, "expected an applied supersession"

    # The archive index should exist and list the superseded article.
    archive_index = store_root / "archive.index.md"
    assert archive_index.exists(), "archive.index.md must exist after supersession"
    archive_content = archive_index.read_text(encoding="utf-8")
    assert "color.md" in archive_content or "Project Color" in archive_content
