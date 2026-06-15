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
