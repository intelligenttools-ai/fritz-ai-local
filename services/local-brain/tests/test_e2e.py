"""End-to-end tests covering the full brain pipeline (WI14).

Phases covered:
  1. Local-only E2E: inbox capture → registry-free compile (store mode) →
     reconciliation (contradicts_supersedes) → archive → query scope filtering.
  2. Federation happy-path E2E: external local-vault target → mirror_target →
     provenance-tagged inbox capture → compile → query; plus a live_fetch
     assertion for an index-only pointer.

All tests use fake agents (monkeypatched via the same seam as the existing unit
tests), temporary brain homes, and make no real network / LLM calls.  Nothing
touches the live ~/.brain directory.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from fritz_local_brain import compile_workflow
from fritz_local_brain.config import Settings
from fritz_local_brain.live_fetch import live_fetch
from fritz_local_brain.mirror import mirror_target
from fritz_local_brain.models import (
    ArticleWriteProposal,
    CompileAgentOutput,
    CompileRunRequest,
    QueryRunRequest,
    ReconciliationVerdict,
)
from fritz_local_brain.query_workflow import run_query
from fritz_local_brain.registry import ExternalTarget


# ---------------------------------------------------------------------------
# Helpers shared across both E2E scenarios
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path, **overrides) -> Settings:
    """Create a Settings instance pointing at a fresh tmp brain home."""
    brain_home = tmp_path / "brain"
    (brain_home / "capture" / "inbox").mkdir(parents=True, exist_ok=True)
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")
    query_skill_path = tmp_path / "skills" / "brain-query" / "SKILL.md"
    query_skill_path.parent.mkdir(parents=True, exist_ok=True)
    query_skill_path.write_text("# Query Skill\n", encoding="utf-8")
    return Settings(
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills",
        **overrides,
    )


def _write_article(path: Path, frontmatter: dict, body: str = "Body.") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    path.write_text(f"---\n{yaml_text}\n---\n\n{body}\n", encoding="utf-8")


def _read_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"Expected frontmatter in {path}"
    _, _, rest = text.partition("---\n")
    yaml_text, _, _ = rest.partition("\n---")
    return yaml.safe_load(yaml_text) or {}


class FakeCompileAgent:
    """Returns a fixed CompileAgentOutput every time it is called."""

    def __init__(self, output: CompileAgentOutput) -> None:
        self.output = output

    async def run(self, prompt: str, *, deps: object, usage_limits: object) -> SimpleNamespace:
        return SimpleNamespace(output=self.output)


class FakeReconciliationAgent:
    """Returns a fixed ReconciliationVerdict every time it is called."""

    def __init__(self, verdict: ReconciliationVerdict) -> None:
        self.verdict = verdict

    async def run(self, prompt: str, *, deps: object, usage_limits: object) -> SimpleNamespace:
        return SimpleNamespace(output=self.verdict)


# ===========================================================================
# 1. Local-only E2E
#    capture → compile → reconcile → archive → query
# ===========================================================================


def test_local_only_e2e_compile_creates_store_article(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An inbox capture compiled in store mode produces a new article on disk.

    No registry, no vault manifests.  compile in non-dry-run mode writes an
    article under ~/.brain/knowledge/<scope>/<section>/<slug>.md.
    """
    settings = _make_settings(tmp_path)
    brain_home = settings.brain_home

    # Write one inbox capture.
    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    capture_path.write_text(
        "# Session Capture\n\nRedis streams are ordered, append-only logs.\n",
        encoding="utf-8",
    )

    # The fake compile agent proposes one new store article.
    proposal = ArticleWriteProposal(
        vault="brain",
        relative_path="common/context/redis-streams.md",
        operation="create",
        title="Redis Streams",
        summary="Redis streams are ordered append-only logs.",
        sources=[str(capture_path)],
        body="Redis streams are ordered, append-only logs useful for event sourcing.",
    )
    monkeypatch.setattr(
        compile_workflow,
        "build_compile_agent",
        lambda s, skill: FakeCompileAgent(CompileAgentOutput(proposals=[proposal])),
    )
    # No reconciliation for this basic creation test.
    monkeypatch.setattr(
        compile_workflow,
        "build_reconciliation_agent",
        lambda s: FakeReconciliationAgent(ReconciliationVerdict(verdict="orthogonal", reasoning="no related")),
    )

    result = asyncio.run(
        compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False, max_captures=10))
    )

    assert result.errors == [], result.errors
    assert len(result.applied) == 1
    assert result.applied[0].operation == "create"
    assert result.applied[0].vault == "brain"

    # The article must exist on disk under the brain store.
    store_root = brain_home / "knowledge"
    article_path = store_root / "common" / "context" / "redis-streams.md"
    assert article_path.exists(), f"Expected article at {article_path}"

    # The article must contain the body text.
    body = article_path.read_text(encoding="utf-8")
    assert "ordered, append-only logs" in body


def test_local_only_e2e_reconciliation_supersedes_old_article(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compile with a pre-existing related article triggers reconciliation.

    When the reconciliation agent returns contradicts_supersedes, the OLD
    article is marked superseded and excluded from an active-scope query, but
    returned under include_archive scope.  The NEW article is found in active.
    """
    settings = _make_settings(tmp_path)
    brain_home = settings.brain_home
    store_root = brain_home / "knowledge"

    # Pre-existing article with active status.
    old_article = store_root / "common" / "decisions" / "deploy-strategy-old.md"
    _write_article(
        old_article,
        {
            "type": "article",
            "title": "Deploy Strategy",
            "status": "active",
        },
        body="We deploy by pushing to main.  Blue/green is overkill.",
    )

    # Inbox capture describing the new approach.
    capture_path = brain_home / "capture" / "inbox" / "deploy-update.md"
    capture_path.write_text(
        "# Deploy update\n\nWe now use canary releases for all services.\n",
        encoding="utf-8",
    )

    new_article_rel = "common/decisions/deploy-strategy-new.md"
    proposal = ArticleWriteProposal(
        vault="brain",
        relative_path=new_article_rel,
        operation="create",
        title="Deploy Strategy (Canary)",
        summary="Switched to canary releases.",
        sources=[str(capture_path)],
        body="We now use canary releases for all services. Blue/green retired.",
    )
    monkeypatch.setattr(
        compile_workflow,
        "build_compile_agent",
        lambda s, skill: FakeCompileAgent(CompileAgentOutput(proposals=[proposal])),
    )
    # Reconciliation agent says the new article supersedes the old one.
    monkeypatch.setattr(
        compile_workflow,
        "build_reconciliation_agent",
        lambda s: FakeReconciliationAgent(
            ReconciliationVerdict(
                verdict="contradicts_supersedes",
                reasoning="canary supersedes blue-green approach",
            )
        ),
    )

    compile_result = asyncio.run(
        compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False, max_captures=10))
    )

    assert compile_result.errors == [], compile_result.errors
    assert len(compile_result.applied) == 1

    # At least one reconciliation outcome with contradicts_supersedes.
    superseded_outcomes = [
        o for o in compile_result.reconciliations if o.verdict == "contradicts_supersedes"
    ]
    assert superseded_outcomes, "Expected at least one contradicts_supersedes reconciliation"

    # The old article must now have status=superseded on disk.
    old_fm = _read_frontmatter(old_article)
    assert old_fm.get("status") == "superseded", (
        f"Expected old article status='superseded', got {old_fm.get('status')!r}"
    )

    # ---- Query: active scope excludes the superseded article ----
    active_result = asyncio.run(
        run_query(
            settings,
            QueryRunRequest(query="deploy strategy", limit=20, scope="active"),
        )
    )
    assert active_result.errors == []
    active_paths = {m.path for m in active_result.matches if m.vault == "brain"}
    assert "common/decisions/deploy-strategy-old.md" not in active_paths, (
        "Superseded article must NOT appear in active scope"
    )
    assert new_article_rel in active_paths, (
        "New article must appear in active scope"
    )

    # ---- Query: include_archive scope returns the superseded article too ----
    archive_result = asyncio.run(
        run_query(
            settings,
            QueryRunRequest(query="deploy strategy", limit=20, scope="include_archive"),
        )
    )
    assert archive_result.errors == []
    archive_paths = {m.path for m in archive_result.matches if m.vault == "brain"}
    assert "common/decisions/deploy-strategy-old.md" in archive_paths, (
        "Superseded article MUST appear under include_archive scope"
    )
    assert new_article_rel in archive_paths, (
        "New article must also appear under include_archive scope"
    )


# ===========================================================================
# 2. Federation happy-path E2E
#    external local-vault target → mirror → inbox → compile → query
# ===========================================================================


def test_federation_mirror_then_compile_then_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full federation path: mirror external vault → compile → query.

    Assertions:
    - mirror_target writes a provenance-tagged inbox capture (source, mirrored_at,
      mode, pointer all present in frontmatter).
    - run_compile (store mode) discovers the mirrored capture in the inbox.
    - run_query finds the compiled article in the brain store.
    """
    settings = _make_settings(tmp_path)
    brain_home = settings.brain_home

    # Build an external local-vault with one content file.
    ext_vault = tmp_path / "ext-vault"
    ext_vault.mkdir()
    (ext_vault / "performance-tips.md").write_text(
        "# Performance Tips\n\nUse connection pooling to reduce latency.\n",
        encoding="utf-8",
    )

    target = ExternalTarget(
        name="ext-vault",
        kind="local-vault",
        connection=str(ext_vault),
        mirror_mode="index-only",
    )

    # Step 1: mirror the external vault.
    mirror_result = mirror_target(settings, target, mirrored_at="2026-06-15T10:00:00")
    assert mirror_result.entries_mirrored == 1
    assert len(mirror_result.written_paths) == 1

    # Step 2: assert provenance fields on the written capture.
    written_path = Path(mirror_result.written_paths[0])
    assert written_path.exists(), f"Mirror capture not written at {written_path}"
    fm = _read_frontmatter(written_path)
    assert fm["source"] == "ext-vault (local-vault)", (
        f"Expected provenance source field, got {fm.get('source')!r}"
    )
    assert fm["mirrored_at"] == "2026-06-15T10:00:00"
    assert "pointer" in fm, "Mirrored capture must carry a pointer field"
    assert "mode" in fm, "Mirrored capture must carry a mode field"

    # Step 3: compile (store mode) — fake agent promotes the mirrored capture.
    article_rel = "common/context/performance-tips.md"
    proposal = ArticleWriteProposal(
        vault="brain",
        relative_path=article_rel,
        operation="create",
        title="Performance Tips",
        summary="Use connection pooling to reduce latency.",
        sources=[str(written_path)],
        body="Use connection pooling to reduce latency.",
    )
    monkeypatch.setattr(
        compile_workflow,
        "build_compile_agent",
        lambda s, skill: FakeCompileAgent(CompileAgentOutput(proposals=[proposal])),
    )
    monkeypatch.setattr(
        compile_workflow,
        "build_reconciliation_agent",
        lambda s: FakeReconciliationAgent(ReconciliationVerdict(verdict="orthogonal", reasoning="nothing related")),
    )

    compile_result = asyncio.run(
        compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False, max_captures=10))
    )

    assert compile_result.errors == [], compile_result.errors
    assert compile_result.captures_by_source.get("inbox", 0) >= 1, (
        "Compile must see at least one inbox capture (the mirrored one)"
    )
    assert len(compile_result.applied) == 1

    # Step 4: run_query finds the compiled article.
    query_result = asyncio.run(
        run_query(
            settings,
            QueryRunRequest(query="connection pooling", limit=10, scope="active"),
        )
    )
    assert query_result.errors == []
    store_paths = {m.path for m in query_result.matches if m.vault == "brain"}
    assert article_rel in store_paths, (
        f"Expected compiled article {article_rel!r} in query results; got {store_paths}"
    )


def test_federation_live_fetch_index_only_pointer(tmp_path: Path) -> None:
    """index-only mirror target: live_fetch returns current content via pointer.

    Sets up:
    - An external vault with a file.
    - A registry.yaml with that file listed as an external_target.
    - Calls live_fetch(settings, pointer) and asserts the live content is returned.

    This covers the live-fetch escape hatch used at query time for index-only
    mirrored capture hits (retrieval-synthesis, WI12).
    """
    settings = _make_settings(tmp_path)
    brain_home = settings.brain_home

    # External vault content.
    ext_vault = tmp_path / "ext-vault"
    ext_vault.mkdir()
    (ext_vault / "db-schema.md").write_text(
        "# DB Schema\n\nThe users table has columns: id, email, created_at.\n",
        encoding="utf-8",
    )

    # registry.yaml must list the external_target so live_fetch can resolve it.
    # external_targets is a dict keyed by target name (same schema as the registry).
    registry_content = {
        "external_targets": {
            "ext-vault": {
                "kind": "local-vault",
                "connection": str(ext_vault),
                "mirror_mode": "index-only",
            }
        }
    }
    (brain_home / "registry.yaml").write_text(
        yaml.dump(registry_content), encoding="utf-8"
    )

    # The pointer format written by mirror_target is "<target-name>:<relpath>".
    pointer = "ext-vault:db-schema.md"

    content = live_fetch(settings, pointer)

    assert content is not None, (
        "live_fetch must return content for a valid local-vault pointer"
    )
    assert "users table" in content, (
        f"live_fetch must return the actual file content; got {content!r}"
    )
    assert "id, email, created_at" in content
