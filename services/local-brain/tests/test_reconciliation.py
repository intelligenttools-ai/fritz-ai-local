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
)
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
