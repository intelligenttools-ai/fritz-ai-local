from __future__ import annotations

import pytest

from fritz_local_brain.models import ArticleWriteProposal
from fritz_local_brain.security import PolicyError, validate_article_write, validate_store_article_write


def test_validate_article_write_rejects_missing_sources(tmp_path) -> None:
    brain_home = tmp_path / "brain"
    vault = tmp_path / "vault"
    (brain_home / "capture" / "daily").mkdir(parents=True)
    (vault / "knowledge").mkdir(parents=True)

    proposal = ArticleWriteProposal(
        vault="test",
        relative_path="article.md",
        operation="create",
        title="Article",
        summary="Summary",
        sources=[],
        body="Body",
    )

    with pytest.raises(PolicyError, match="at least one capture source"):
        validate_article_write(
            proposal,
            {"test": vault},
            {"test": {"paths": {"knowledge": "knowledge"}, "exclude": []}},
            brain_home,
            allowed_sources=set(),
        )


# ---------------------------------------------------------------------------
# validate_store_article_write tests
# ---------------------------------------------------------------------------


def _make_store_proposal(
    *,
    relative_path: str = "common/decisions/foo.md",
    operation: str = "create",
    sources: list[str] | None = None,
    vault: str = "brain",
) -> ArticleWriteProposal:
    return ArticleWriteProposal(
        vault=vault,
        relative_path=relative_path,
        operation=operation,
        title="Foo",
        summary="Summary",
        sources=sources if sources is not None else [],
        body="Body",
    )


def test_validate_store_article_write_accepts_valid_proposal(tmp_path) -> None:
    brain_home = tmp_path / "brain"
    store_root = tmp_path / "store"
    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    capture_path.parent.mkdir(parents=True)
    capture_path.write_text("# Capture\n", encoding="utf-8")
    store_root.mkdir(parents=True)

    proposal = _make_store_proposal(sources=[str(capture_path)])
    allowed = {capture_path.resolve()}
    target = validate_store_article_write(proposal, store_root, brain_home, allowed_sources=allowed)
    assert target == (store_root / "common" / "decisions" / "foo.md").resolve()


def test_validate_store_article_write_rejects_absolute_path(tmp_path) -> None:
    brain_home = tmp_path / "brain"
    store_root = tmp_path / "store"
    store_root.mkdir(parents=True)
    (brain_home / "capture" / "inbox").mkdir(parents=True)
    capture = brain_home / "capture" / "inbox" / "x.md"
    capture.write_text("x", encoding="utf-8")

    proposal = _make_store_proposal(relative_path="/etc/passwd", sources=[str(capture)])
    with pytest.raises(PolicyError, match="Unsafe relative_path"):
        validate_store_article_write(proposal, store_root, brain_home, allowed_sources={capture.resolve()})


def test_validate_store_article_write_rejects_dotdot_path(tmp_path) -> None:
    brain_home = tmp_path / "brain"
    store_root = tmp_path / "store"
    store_root.mkdir(parents=True)
    (brain_home / "capture" / "inbox").mkdir(parents=True)
    capture = brain_home / "capture" / "inbox" / "x.md"
    capture.write_text("x", encoding="utf-8")

    proposal = _make_store_proposal(relative_path="../escape/foo.md", sources=[str(capture)])
    with pytest.raises(PolicyError, match="Unsafe relative_path"):
        validate_store_article_write(proposal, store_root, brain_home, allowed_sources={capture.resolve()})


def test_validate_store_article_write_rejects_non_md_target(tmp_path) -> None:
    brain_home = tmp_path / "brain"
    store_root = tmp_path / "store"
    store_root.mkdir(parents=True)
    (brain_home / "capture" / "inbox").mkdir(parents=True)
    capture = brain_home / "capture" / "inbox" / "x.md"
    capture.write_text("x", encoding="utf-8")

    proposal = _make_store_proposal(relative_path="common/decisions/foo.txt", sources=[str(capture)])
    with pytest.raises(PolicyError, match="markdown file"):
        validate_store_article_write(proposal, store_root, brain_home, allowed_sources={capture.resolve()})


def test_validate_store_article_write_rejects_missing_sources(tmp_path) -> None:
    brain_home = tmp_path / "brain"
    store_root = tmp_path / "store"
    store_root.mkdir(parents=True)
    (brain_home / "capture" / "inbox").mkdir(parents=True)

    proposal = _make_store_proposal(sources=[])
    with pytest.raises(PolicyError, match="at least one capture source"):
        validate_store_article_write(proposal, store_root, brain_home)


def test_validate_store_article_write_rejects_source_outside_capture(tmp_path) -> None:
    brain_home = tmp_path / "brain"
    store_root = tmp_path / "store"
    store_root.mkdir(parents=True)
    (brain_home / "capture" / "inbox").mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("x", encoding="utf-8")

    proposal = _make_store_proposal(sources=[str(outside)])
    with pytest.raises(PolicyError, match="not in capture root"):
        validate_store_article_write(proposal, store_root, brain_home)


def test_validate_store_article_write_rejects_create_when_target_exists(tmp_path) -> None:
    brain_home = tmp_path / "brain"
    store_root = tmp_path / "store"
    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    capture_path.parent.mkdir(parents=True)
    capture_path.write_text("x", encoding="utf-8")
    store_root.mkdir(parents=True)

    existing = store_root / "common" / "decisions" / "foo.md"
    existing_set = {existing.resolve()}

    proposal = _make_store_proposal(sources=[str(capture_path)])
    with pytest.raises(PolicyError, match="already exists"):
        validate_store_article_write(
            proposal, store_root, brain_home, allowed_sources={capture_path.resolve()}, known_existing_targets=existing_set
        )


def test_validate_store_article_write_rejects_update_when_target_missing(tmp_path) -> None:
    brain_home = tmp_path / "brain"
    store_root = tmp_path / "store"
    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    capture_path.parent.mkdir(parents=True)
    capture_path.write_text("x", encoding="utf-8")
    store_root.mkdir(parents=True)

    proposal = _make_store_proposal(operation="update", sources=[str(capture_path)])
    with pytest.raises(PolicyError, match="does not exist"):
        validate_store_article_write(
            proposal, store_root, brain_home, allowed_sources={capture_path.resolve()}, known_existing_targets=set()
        )


def test_validate_store_article_write_rejects_forbidden_parts_in_path(tmp_path) -> None:
    """Reject paths containing forbidden parts like registry.yaml, manifest.yaml, schema.md."""
    brain_home = tmp_path / "brain"
    store_root = tmp_path / "store"
    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    capture_path.parent.mkdir(parents=True)
    capture_path.write_text("x", encoding="utf-8")
    store_root.mkdir(parents=True)

    # Test with manifest.yaml in the path (check happens before .md suffix check)
    proposal = _make_store_proposal(relative_path="common/manifest.yaml/article.md", sources=[str(capture_path)])
    with pytest.raises(PolicyError, match="Forbidden target path"):
        validate_store_article_write(proposal, store_root, brain_home, allowed_sources={capture_path.resolve()})


def test_validate_store_article_write_rejects_source_not_in_allowed_sources(tmp_path) -> None:
    """Reject sources that exist under capture root but are not in allowed_sources."""
    brain_home = tmp_path / "brain"
    store_root = tmp_path / "store"
    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    capture_path.parent.mkdir(parents=True)
    capture_path.write_text("x", encoding="utf-8")
    store_root.mkdir(parents=True)

    proposal = _make_store_proposal(sources=[str(capture_path)])
    # Pass allowed_sources as empty set, so the source is not allowed
    with pytest.raises(PolicyError, match="Source was not provided to the compile agent"):
        validate_store_article_write(proposal, store_root, brain_home, allowed_sources=set())


def test_validate_store_article_write_rejects_path_escape_via_dotdot(tmp_path) -> None:
    """Reject paths that escape store_root using .. components."""
    brain_home = tmp_path / "brain"
    store_root = tmp_path / "store"
    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    capture_path.parent.mkdir(parents=True)
    capture_path.write_text("x", encoding="utf-8")
    store_root.mkdir(parents=True)

    # Note: The ".." check happens in line 135 before the .md check,
    # so this should raise "Unsafe relative_path" even though it doesn't end in .md
    proposal = _make_store_proposal(relative_path="../outside.md", sources=[str(capture_path)])
    with pytest.raises(PolicyError, match="Unsafe relative_path"):
        validate_store_article_write(proposal, store_root, brain_home, allowed_sources={capture_path.resolve()})
