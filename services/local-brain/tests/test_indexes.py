"""Tests for the typed brain-store layout and index maintenance (WI2, issue #87).

Covers:
- ``update_directory_index``: creation on first write, dedup on subsequent
  writes, dry_run no-op.
- ``update_indexes_for_article``: leaf + scope + global MOC all updated.
- ``build_global_moc``: global MOC links all known scopes.
- Per-project and ``common`` scope both handled.
- ``backfill_indexes``: rebuilds indexes for a pre-populated store.
- No live ``~/.brain`` touched — only ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fritz_local_brain.indexes import (
    COMMON_SCOPE,
    SECTIONS,
    _ensure_scope_index,
    backfill_indexes,
    build_global_moc,
    update_directory_index,
    update_indexes_for_article,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_article(directory: Path, filename: str, title: str = "", summary: str = "") -> Path:
    """Create a minimal markdown file and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    article = directory / filename
    if title or summary:
        fm = "---\n"
        if title:
            fm += f"title: {title}\n"
        if summary:
            fm += f"summary: {summary}\n"
        fm += "---\n\nBody text.\n"
        article.write_text(fm, encoding="utf-8")
    else:
        article.write_text("# stub\n", encoding="utf-8")
    return article


# ---------------------------------------------------------------------------
# update_directory_index
# ---------------------------------------------------------------------------


class TestUpdateDirectoryIndex:
    def test_creates_index_on_first_write(self, tmp_path: Path) -> None:
        section_dir = tmp_path / "decisions"
        section_dir.mkdir()
        article = _make_article(section_dir, "adr-001.md")

        update_directory_index(article, "First Decision", "Why we chose X", dry_run=False)

        index = section_dir / "index.md"
        assert index.exists()
        content = index.read_text(encoding="utf-8")
        assert "# decisions" in content
        assert "[First Decision](adr-001.md)" in content
        assert "Why we chose X" in content

    def test_deduplicates_on_subsequent_write(self, tmp_path: Path) -> None:
        section_dir = tmp_path / "decisions"
        section_dir.mkdir()
        article = _make_article(section_dir, "adr-001.md")

        update_directory_index(article, "First Decision", "Why we chose X", dry_run=False)
        update_directory_index(article, "First Decision", "Why we chose X", dry_run=False)

        index = section_dir / "index.md"
        count = index.read_text(encoding="utf-8").count("](adr-001.md)")
        assert count == 1

    def test_appends_second_article_without_duplication(self, tmp_path: Path) -> None:
        section_dir = tmp_path / "decisions"
        section_dir.mkdir()
        a1 = _make_article(section_dir, "adr-001.md")
        a2 = _make_article(section_dir, "adr-002.md")

        update_directory_index(a1, "First", "s1", dry_run=False)
        update_directory_index(a2, "Second", "s2", dry_run=False)

        content = (section_dir / "index.md").read_text(encoding="utf-8")
        assert "](adr-001.md)" in content
        assert "](adr-002.md)" in content

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        section_dir = tmp_path / "decisions"
        section_dir.mkdir()
        article = _make_article(section_dir, "adr-001.md")

        update_directory_index(article, "First", "s", dry_run=True)

        assert not (section_dir / "index.md").exists()

    def test_skips_when_target_is_index_md(self, tmp_path: Path) -> None:
        section_dir = tmp_path / "decisions"
        section_dir.mkdir()
        index_file = section_dir / "index.md"
        index_file.write_text("# decisions\n", encoding="utf-8")

        update_directory_index(index_file, "Index", "meta", dry_run=False)

        # Content should not have gained a self-referencing entry.
        content = index_file.read_text(encoding="utf-8")
        assert "](index.md)" not in content


# ---------------------------------------------------------------------------
# build_global_moc
# ---------------------------------------------------------------------------


class TestBuildGlobalMoc:
    def test_creates_global_index_linking_scopes(self, tmp_path: Path) -> None:
        common_decisions = tmp_path / COMMON_SCOPE / "decisions"
        common_decisions.mkdir(parents=True)
        _make_article(common_decisions, "a.md")

        myproject = tmp_path / "myproject" / "lessons"
        myproject.mkdir(parents=True)
        _make_article(myproject, "b.md")

        # Scope indexes must exist for build_global_moc to link them (Fix 3).
        _ensure_scope_index(tmp_path, COMMON_SCOPE, dry_run=False)
        _ensure_scope_index(tmp_path, "myproject", dry_run=False)
        build_global_moc(tmp_path, dry_run=False)

        moc = tmp_path / "index.md"
        assert moc.exists()
        content = moc.read_text(encoding="utf-8")
        assert "[common]" in content
        assert "[myproject]" in content

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        (tmp_path / COMMON_SCOPE / "decisions").mkdir(parents=True)

        build_global_moc(tmp_path, dry_run=True)

        assert not (tmp_path / "index.md").exists()

    def test_skips_when_no_scopes(self, tmp_path: Path) -> None:
        build_global_moc(tmp_path, dry_run=False)

        assert not (tmp_path / "index.md").exists()

    def test_idempotent_on_repeated_calls(self, tmp_path: Path) -> None:
        (tmp_path / COMMON_SCOPE / "decisions").mkdir(parents=True)
        _make_article(tmp_path / COMMON_SCOPE / "decisions", "x.md")

        # Scope index must exist for build_global_moc to link it (Fix 3).
        _ensure_scope_index(tmp_path, COMMON_SCOPE, dry_run=False)
        build_global_moc(tmp_path, dry_run=False)
        content_first = (tmp_path / "index.md").read_text(encoding="utf-8")

        build_global_moc(tmp_path, dry_run=False)
        content_second = (tmp_path / "index.md").read_text(encoding="utf-8")

        assert content_first == content_second


# ---------------------------------------------------------------------------
# update_indexes_for_article
# ---------------------------------------------------------------------------


class TestUpdateIndexesForArticle:
    def _store(self, tmp_path: Path) -> Path:
        store = tmp_path / "store"
        store.mkdir()
        return store

    def test_updates_leaf_and_scope_and_global_moc(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        section_dir = store / COMMON_SCOPE / "decisions"
        section_dir.mkdir(parents=True)
        article = _make_article(section_dir, "adr-001.md")

        update_indexes_for_article(store, article, "ADR 001", "Some decision", dry_run=False)

        # Leaf index
        assert (section_dir / "index.md").exists()
        leaf_content = (section_dir / "index.md").read_text(encoding="utf-8")
        assert "[ADR 001](adr-001.md)" in leaf_content

        # Scope index
        scope_index = store / COMMON_SCOPE / "index.md"
        assert scope_index.exists()
        scope_content = scope_index.read_text(encoding="utf-8")
        assert "decisions" in scope_content

        # Global MOC
        global_moc = store / "index.md"
        assert global_moc.exists()
        global_content = global_moc.read_text(encoding="utf-8")
        assert "common" in global_content

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        section_dir = store / COMMON_SCOPE / "decisions"
        section_dir.mkdir(parents=True)
        article = _make_article(section_dir, "adr-001.md")

        update_indexes_for_article(store, article, "ADR 001", "Some decision", dry_run=True)

        assert not (section_dir / "index.md").exists()
        assert not (store / COMMON_SCOPE / "index.md").exists()
        assert not (store / "index.md").exists()

    def test_per_project_scope(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        section_dir = store / "acme-app" / "lessons"
        section_dir.mkdir(parents=True)
        article = _make_article(section_dir, "lesson-1.md")

        update_indexes_for_article(store, article, "Lesson 1", "Learned X", dry_run=False)

        # Global MOC should mention the project slug.
        global_content = (store / "index.md").read_text(encoding="utf-8")
        assert "acme-app" in global_content

        # Scope MOC should mention the lessons section.
        scope_content = (store / "acme-app" / "index.md").read_text(encoding="utf-8")
        assert "lessons" in scope_content

    def test_both_common_and_project_scopes(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)

        common_dir = store / COMMON_SCOPE / "runbooks"
        common_dir.mkdir(parents=True)
        common_article = _make_article(common_dir, "runbook-1.md")

        proj_dir = store / "proj-x" / "decisions"
        proj_dir.mkdir(parents=True)
        proj_article = _make_article(proj_dir, "adr-42.md")

        update_indexes_for_article(store, common_article, "Runbook 1", "How to deploy", dry_run=False)
        update_indexes_for_article(store, proj_article, "ADR 42", "Switch DB", dry_run=False)

        global_content = (store / "index.md").read_text(encoding="utf-8")
        assert "common" in global_content
        assert "proj-x" in global_content

    def test_article_outside_store_root_is_safe(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        outside = tmp_path / "other" / "note.md"
        outside.parent.mkdir(parents=True)
        outside.write_text("# note\n", encoding="utf-8")

        # Should not raise, just silently do nothing beyond the leaf index.
        update_indexes_for_article(store, outside, "Note", "outside", dry_run=False)

    def test_article_outside_store_writes_nothing(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        outside_dir = tmp_path / "sibling"
        outside_dir.mkdir()
        outside = outside_dir / "note.md"
        outside.write_text("# note\n", encoding="utf-8")

        update_indexes_for_article(store, outside, "Note", "external article", dry_run=False)

        # Nothing should be written to the sibling directory.
        assert not (outside_dir / "index.md").exists()
        # Store root should remain empty or only have directories, no indexes.
        store_indexes = list(store.rglob("index.md"))
        assert len(store_indexes) == 0


# ---------------------------------------------------------------------------
# backfill_indexes
# ---------------------------------------------------------------------------


class TestBackfillIndexes:
    def _populated_store(self, tmp_path: Path) -> Path:
        store = tmp_path / "store"

        # common/decisions
        d = store / COMMON_SCOPE / "decisions"
        d.mkdir(parents=True)
        _make_article(d, "adr-001.md", title="ADR 001", summary="Chose Postgres")
        _make_article(d, "adr-002.md", title="ADR 002", summary="Chose Redis")

        # common/lessons
        l = store / COMMON_SCOPE / "lessons"
        l.mkdir(parents=True)
        _make_article(l, "retro-q1.md", title="Q1 Retro", summary="What went well")

        # myproject/runbooks
        r = store / "myproject" / "runbooks"
        r.mkdir(parents=True)
        _make_article(r, "deploy.md", title="Deploy Runbook", summary="How to deploy")

        return store

    def test_rebuilds_leaf_indexes(self, tmp_path: Path) -> None:
        store = self._populated_store(tmp_path)

        backfill_indexes(store, dry_run=False)

        decisions_index = store / COMMON_SCOPE / "decisions" / "index.md"
        assert decisions_index.exists()
        content = decisions_index.read_text(encoding="utf-8")
        assert "adr-001.md" in content
        assert "adr-002.md" in content

    def test_rebuilds_scope_indexes(self, tmp_path: Path) -> None:
        store = self._populated_store(tmp_path)

        backfill_indexes(store, dry_run=False)

        scope_index = store / COMMON_SCOPE / "index.md"
        assert scope_index.exists()
        content = scope_index.read_text(encoding="utf-8")
        assert "decisions" in content
        assert "lessons" in content

    def test_rebuilds_global_moc(self, tmp_path: Path) -> None:
        store = self._populated_store(tmp_path)

        backfill_indexes(store, dry_run=False)

        moc = store / "index.md"
        assert moc.exists()
        content = moc.read_text(encoding="utf-8")
        assert "common" in content
        assert "myproject" in content

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        store = self._populated_store(tmp_path)

        backfill_indexes(store, dry_run=True)

        # No index.md files should have been created.
        for p in store.rglob("index.md"):
            pytest.fail(f"dry_run=True should not have written {p}")

    def test_idempotent_on_double_run(self, tmp_path: Path) -> None:
        store = self._populated_store(tmp_path)

        backfill_indexes(store, dry_run=False)
        snap_before = {
            p: p.read_text(encoding="utf-8")
            for p in store.rglob("index.md")
        }

        backfill_indexes(store, dry_run=False)
        snap_after = {
            p: p.read_text(encoding="utf-8")
            for p in store.rglob("index.md")
        }

        assert set(snap_before.keys()) == set(snap_after.keys())
        for p in snap_before:
            assert snap_before[p] == snap_after[p], f"Content changed for {p}"

    def test_noop_for_missing_store(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "no-such-store"
        # Should not raise.
        backfill_indexes(nonexistent, dry_run=False)

    def test_extracts_title_from_front_matter(self, tmp_path: Path) -> None:
        store = tmp_path / "store"
        d = store / COMMON_SCOPE / "context"
        d.mkdir(parents=True)
        _make_article(d, "glossary.md", title="Glossary", summary="Key terms")

        backfill_indexes(store, dry_run=False)

        content = (d / "index.md").read_text(encoding="utf-8")
        assert "Glossary" in content
        assert "Key terms" in content

    def test_falls_back_to_stem_when_no_front_matter(self, tmp_path: Path) -> None:
        store = tmp_path / "store"
        d = store / COMMON_SCOPE / "context"
        d.mkdir(parents=True)
        plain = d / "plain-note.md"
        plain.write_text("No front matter here.\n", encoding="utf-8")

        backfill_indexes(store, dry_run=False)

        content = (d / "index.md").read_text(encoding="utf-8")
        assert "plain-note" in content


# ---------------------------------------------------------------------------
# Constants sanity-check
# ---------------------------------------------------------------------------


def test_sections_constant_contains_expected_names() -> None:
    assert "decisions" in SECTIONS
    assert "lessons" in SECTIONS
    assert "runbooks" in SECTIONS
    assert "context" in SECTIONS


def test_common_scope_constant() -> None:
    assert COMMON_SCOPE == "common"


# ---------------------------------------------------------------------------
# WI9: Archive-aware index building (issue #94)
# ---------------------------------------------------------------------------


def _make_article_with_status(directory: Path, filename: str, title: str, status: str | None = None) -> Path:
    """Create a minimal markdown file with optional status frontmatter."""
    directory.mkdir(parents=True, exist_ok=True)
    article = directory / filename
    if status:
        fm = f"---\ntitle: {title}\nstatus: {status}\nsummary: summary for {title}\n---\n\nBody.\n"
    else:
        fm = f"---\ntitle: {title}\nsummary: summary for {title}\n---\n\nBody.\n"
    article.write_text(fm, encoding="utf-8")
    return article


class TestArchiveAwareBackfill:
    def test_backfill_excludes_superseded_from_active_leaf_index(self, tmp_path: Path) -> None:
        store = tmp_path / "store"
        d = store / COMMON_SCOPE / "context"
        active = _make_article_with_status(d, "active.md", "Active Article")
        superseded = _make_article_with_status(d, "old.md", "Old Article", status="superseded")

        backfill_indexes(store, dry_run=False)

        leaf_index = d / "index.md"
        assert leaf_index.exists()
        content = leaf_index.read_text(encoding="utf-8")
        assert "Active Article" in content
        assert "Old Article" not in content

    def test_backfill_excludes_historical_from_active_leaf_index(self, tmp_path: Path) -> None:
        store = tmp_path / "store"
        d = store / COMMON_SCOPE / "lessons"
        _make_article_with_status(d, "active.md", "Active Lesson")
        _make_article_with_status(d, "hist.md", "Historical Lesson", status="historical")

        backfill_indexes(store, dry_run=False)

        leaf_content = (d / "index.md").read_text(encoding="utf-8")
        assert "Active Lesson" in leaf_content
        assert "Historical Lesson" not in leaf_content

    def test_backfill_creates_archive_index_with_archived_article(self, tmp_path: Path) -> None:
        store = tmp_path / "store"
        d = store / COMMON_SCOPE / "context"
        _make_article_with_status(d, "active.md", "Active Article")
        _make_article_with_status(d, "old.md", "Old Article", status="superseded")

        backfill_indexes(store, dry_run=False)

        archive_index = store / "archive.index.md"
        assert archive_index.exists(), "archive.index.md should be created when archived articles exist"
        archive_content = archive_index.read_text(encoding="utf-8")
        assert "Old Article" in archive_content
        assert "superseded" in archive_content
        # Active article must NOT appear in the archive index.
        assert "Active Article" not in archive_content

    def test_backfill_archive_index_lists_both_superseded_and_historical(self, tmp_path: Path) -> None:
        store = tmp_path / "store"
        d = store / COMMON_SCOPE / "context"
        _make_article_with_status(d, "sup.md", "Superseded Article", status="superseded")
        _make_article_with_status(d, "hist.md", "Historical Article", status="historical")
        _make_article_with_status(d, "active.md", "Active Article")

        backfill_indexes(store, dry_run=False)

        archive_content = (store / "archive.index.md").read_text(encoding="utf-8")
        assert "Superseded Article" in archive_content
        assert "Historical Article" in archive_content
        assert "Active Article" not in archive_content

    def test_backfill_no_archive_index_when_no_archived_articles(self, tmp_path: Path) -> None:
        store = tmp_path / "store"
        d = store / COMMON_SCOPE / "context"
        _make_article_with_status(d, "active.md", "Active Article")

        backfill_indexes(store, dry_run=False)

        assert not (store / "archive.index.md").exists(), "archive.index.md should not be created when no archived articles exist"

    def test_backfill_active_index_and_archive_index_are_disjoint(self, tmp_path: Path) -> None:
        store = tmp_path / "store"
        d = store / COMMON_SCOPE / "decisions"
        _make_article_with_status(d, "active.md", "Active Decision")
        _make_article_with_status(d, "old.md", "Old Decision", status="superseded")

        backfill_indexes(store, dry_run=False)

        # Global MOC should not include the superseded article directly.
        global_moc = store / "index.md"
        assert global_moc.exists()
        # The archive index should exist.
        archive_index = store / "archive.index.md"
        assert archive_index.exists()
        archive_content = archive_index.read_text(encoding="utf-8")
        leaf_content = (d / "index.md").read_text(encoding="utf-8")
        assert "Old Decision" in archive_content
        assert "Old Decision" not in leaf_content

    def test_backfill_idempotent_with_archive(self, tmp_path: Path) -> None:
        store = tmp_path / "store"
        d = store / COMMON_SCOPE / "context"
        _make_article_with_status(d, "active.md", "Active Article")
        _make_article_with_status(d, "old.md", "Old Article", status="superseded")

        backfill_indexes(store, dry_run=False)
        snap1 = {p: p.read_text(encoding="utf-8") for p in store.rglob("*.md") if "index" in p.name}
        backfill_indexes(store, dry_run=False)
        snap2 = {p: p.read_text(encoding="utf-8") for p in store.rglob("*.md") if "index" in p.name}

        assert snap1 == snap2

    def test_backfill_dry_run_does_not_write_archive_index(self, tmp_path: Path) -> None:
        store = tmp_path / "store"
        d = store / COMMON_SCOPE / "context"
        _make_article_with_status(d, "old.md", "Old Article", status="superseded")

        backfill_indexes(store, dry_run=True)

        assert not (store / "archive.index.md").exists()


# ---------------------------------------------------------------------------
# Disjointness regression tests (Fix 1 / Fix 2 / Fix 3 — issue #94)
# ---------------------------------------------------------------------------


class TestArchiveDisjointness:
    """Verify that active and archive indexes are strictly disjoint when a
    section or scope becomes 100 % archived."""

    # -- Fix 1 / Fix 2: all-archived section ----------------------------------

    def test_all_archived_section_no_dangling_leaf_index(self, tmp_path: Path) -> None:
        """When every article in a section is archived, backfill_indexes must
        remove the section leaf index.md (no dangling link)."""
        store = tmp_path / "store"
        d = store / COMMON_SCOPE / "decisions"
        _make_article_with_status(d, "adr-old.md", "Old ADR", status="superseded")

        backfill_indexes(store, dry_run=False)

        # No leaf index should exist for the all-archived section.
        assert not (d / "index.md").exists(), (
            "decisions/index.md must not exist when all articles are archived"
        )

    def test_all_archived_section_not_in_scope_moc(self, tmp_path: Path) -> None:
        """The scope MOC must NOT link the all-archived section (Fix 1 / Fix 2)."""
        store = tmp_path / "store"
        d = store / COMMON_SCOPE / "decisions"
        _make_article_with_status(d, "adr-old.md", "Old ADR", status="superseded")
        # Add an active article in a different section so the scope MOC is created.
        _make_article_with_status(
            store / COMMON_SCOPE / "lessons", "lesson.md", "Active Lesson"
        )

        backfill_indexes(store, dry_run=False)

        scope_moc = store / COMMON_SCOPE / "index.md"
        assert scope_moc.exists(), "scope index must exist (has active lessons)"
        content = scope_moc.read_text(encoding="utf-8")
        assert "decisions" not in content, (
            "scope MOC must not link decisions when all its articles are archived"
        )
        assert "lessons" in content

    def test_all_archived_section_article_in_archive_index(self, tmp_path: Path) -> None:
        """Archived articles in an all-archived section appear in archive.index.md."""
        store = tmp_path / "store"
        d = store / COMMON_SCOPE / "decisions"
        _make_article_with_status(d, "adr-old.md", "Old ADR", status="superseded")

        backfill_indexes(store, dry_run=False)

        archive = store / "archive.index.md"
        assert archive.exists()
        assert "Old ADR" in archive.read_text(encoding="utf-8")

    # -- Fix 3: all-archived scope --------------------------------------------

    def test_all_archived_scope_not_in_global_moc(self, tmp_path: Path) -> None:
        """A scope whose every article is archived must not appear in the
        global active index.md (Fix 3)."""
        store = tmp_path / "store"
        # proj-x: all articles historical → scope must be absent from global MOC.
        _make_article_with_status(
            store / "proj-x" / "decisions", "adr.md", "Old ADR", status="historical"
        )
        # common: has an active article → must still appear.
        _make_article_with_status(
            store / COMMON_SCOPE / "lessons", "lesson.md", "Active Lesson"
        )

        backfill_indexes(store, dry_run=False)

        global_moc = store / "index.md"
        assert global_moc.exists(), "global MOC should exist (common has active content)"
        content = global_moc.read_text(encoding="utf-8")
        assert "common" in content
        assert "proj-x" not in content, (
            "global MOC must not link proj-x when all its articles are archived"
        )

    def test_all_archived_scope_article_in_archive_index(self, tmp_path: Path) -> None:
        """The archived article from an all-archived scope appears in archive.index.md."""
        store = tmp_path / "store"
        _make_article_with_status(
            store / "proj-x" / "decisions", "adr.md", "Old ADR", status="historical"
        )

        backfill_indexes(store, dry_run=False)

        archive = store / "archive.index.md"
        assert archive.exists()
        assert "Old ADR" in archive.read_text(encoding="utf-8")

    # -- Fix 2: stale scope index cleanup -------------------------------------

    def test_stale_scope_index_removed_when_scope_goes_all_archived(
        self, tmp_path: Path
    ) -> None:
        """When a scope transitions from active to all-archived, the stale
        scope index.md must be removed on the next backfill_indexes run."""
        store = tmp_path / "store"
        d = store / COMMON_SCOPE / "decisions"
        active_article = _make_article_with_status(d, "adr-001.md", "Active ADR")

        # First run: scope is active → scope index.md should exist.
        backfill_indexes(store, dry_run=False)
        scope_index = store / COMMON_SCOPE / "index.md"
        assert scope_index.exists(), "scope index must exist while articles are active"

        # Transition: mark the only article as superseded.
        active_article.write_text(
            "---\ntitle: Active ADR\nstatus: superseded\nsummary: now archived\n---\n\nBody.\n",
            encoding="utf-8",
        )

        # Second run: scope is now all-archived → stale scope index.md removed.
        backfill_indexes(store, dry_run=False)
        assert not scope_index.exists(), (
            "stale scope index.md must be removed when scope goes all-archived"
        )

    def test_idempotency_all_archived_section(self, tmp_path: Path) -> None:
        """Running backfill_indexes twice yields identical files even when a
        section is 100 % archived."""
        store = tmp_path / "store"
        _make_article_with_status(
            store / COMMON_SCOPE / "decisions", "adr.md", "Old ADR", status="superseded"
        )
        _make_article_with_status(
            store / COMMON_SCOPE / "lessons", "lesson.md", "Active Lesson"
        )

        backfill_indexes(store, dry_run=False)
        snap1 = {p: p.read_text(encoding="utf-8") for p in store.rglob("*.md") if "index" in p.name}

        backfill_indexes(store, dry_run=False)
        snap2 = {p: p.read_text(encoding="utf-8") for p in store.rglob("*.md") if "index" in p.name}

        assert set(snap1.keys()) == set(snap2.keys()), "Set of index files must be stable"
        for p in snap1:
            assert snap1[p] == snap2[p], f"Content changed for {p}"

    # -- Mixed content: active + archived in same section ---------------------

    def test_mixed_section_links_correctly_and_excludes_archived(
        self, tmp_path: Path
    ) -> None:
        """A section with both active and archived articles must link only the
        active ones in the leaf index and still appear in the scope MOC."""
        store = tmp_path / "store"
        d = store / COMMON_SCOPE / "decisions"
        _make_article_with_status(d, "adr-active.md", "Active ADR")
        _make_article_with_status(d, "adr-old.md", "Superseded ADR", status="superseded")

        backfill_indexes(store, dry_run=False)

        leaf_index = d / "index.md"
        assert leaf_index.exists(), "leaf index must exist (section has active articles)"
        leaf_content = leaf_index.read_text(encoding="utf-8")
        assert "Active ADR" in leaf_content
        assert "Superseded ADR" not in leaf_content

        scope_moc = store / COMMON_SCOPE / "index.md"
        assert scope_moc.exists()
        scope_content = scope_moc.read_text(encoding="utf-8")
        assert "decisions" in scope_content

        global_moc = store / "index.md"
        assert global_moc.exists()
        assert "common" in global_moc.read_text(encoding="utf-8")

        archive = store / "archive.index.md"
        assert archive.exists()
        assert "Superseded ADR" in archive.read_text(encoding="utf-8")
