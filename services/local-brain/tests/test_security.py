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


# ---------------------------------------------------------------------------
# Status / lifecycle vocabulary tests
# ---------------------------------------------------------------------------


def _capture_setup(tmp_path):
    """Return (brain_home, store_root, capture_path, allowed_sources)."""
    brain_home = tmp_path / "brain"
    store_root = tmp_path / "store"
    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    capture_path.parent.mkdir(parents=True)
    capture_path.write_text("# Capture\n", encoding="utf-8")
    store_root.mkdir(parents=True)
    allowed = {capture_path.resolve()}
    return brain_home, store_root, capture_path, allowed


def test_validate_store_article_write_defaults_status_to_active(tmp_path) -> None:
    """When status is absent from frontmatter it is set to 'active' by default."""
    brain_home, store_root, capture_path, allowed = _capture_setup(tmp_path)
    proposal = _make_store_proposal(sources=[str(capture_path)])
    assert "status" not in proposal.frontmatter
    validate_store_article_write(proposal, store_root, brain_home, allowed_sources=allowed)
    assert proposal.frontmatter["status"] == "active"


def test_validate_store_article_write_accepts_valid_status(tmp_path) -> None:
    """Explicit valid status values are accepted and preserved."""
    brain_home, store_root, capture_path, allowed = _capture_setup(tmp_path)
    for status in ("active", "corroborated", "deprecated", "superseded", "historical"):
        p = _make_store_proposal(relative_path=f"common/decisions/{status}.md", sources=[str(capture_path)])
        p.frontmatter["status"] = status
        validate_store_article_write(p, store_root, brain_home, allowed_sources=allowed)
        assert p.frontmatter["status"] == status


def test_validate_store_article_write_normalizes_status_case(tmp_path) -> None:
    """Status value is lowercased and stripped during validation."""
    brain_home, store_root, capture_path, allowed = _capture_setup(tmp_path)
    proposal = _make_store_proposal(sources=[str(capture_path)])
    proposal.frontmatter["status"] = "Active"
    validate_store_article_write(proposal, store_root, brain_home, allowed_sources=allowed)
    assert proposal.frontmatter["status"] == "active"


def test_validate_store_article_write_rejects_invalid_status(tmp_path) -> None:
    """An unrecognized status value raises PolicyError."""
    brain_home, store_root, capture_path, allowed = _capture_setup(tmp_path)
    proposal = _make_store_proposal(sources=[str(capture_path)])
    proposal.frontmatter["status"] = "draft"
    with pytest.raises(PolicyError, match="Invalid status"):
        validate_store_article_write(proposal, store_root, brain_home, allowed_sources=allowed)


def test_validate_store_article_write_accepts_supersedes_as_list(tmp_path) -> None:
    """Optional 'supersedes' field is accepted when it is a list."""
    brain_home, store_root, capture_path, allowed = _capture_setup(tmp_path)
    proposal = _make_store_proposal(sources=[str(capture_path)])
    proposal.frontmatter["supersedes"] = ["common/decisions/old.md"]
    validate_store_article_write(proposal, store_root, brain_home, allowed_sources=allowed)
    assert proposal.frontmatter["supersedes"] == ["common/decisions/old.md"]


def test_validate_store_article_write_accepts_superseded_by_as_list(tmp_path) -> None:
    """Optional 'superseded_by' field is accepted when it is a list."""
    brain_home, store_root, capture_path, allowed = _capture_setup(tmp_path)
    proposal = _make_store_proposal(sources=[str(capture_path)])
    proposal.frontmatter["superseded_by"] = ["common/decisions/new.md"]
    validate_store_article_write(proposal, store_root, brain_home, allowed_sources=allowed)
    assert proposal.frontmatter["superseded_by"] == ["common/decisions/new.md"]


def test_validate_store_article_write_rejects_supersedes_non_list(tmp_path) -> None:
    """'supersedes' must be a list; a string value raises PolicyError."""
    brain_home, store_root, capture_path, allowed = _capture_setup(tmp_path)
    proposal = _make_store_proposal(sources=[str(capture_path)])
    proposal.frontmatter["supersedes"] = "common/decisions/old.md"
    with pytest.raises(PolicyError, match="supersedes must be a list"):
        validate_store_article_write(proposal, store_root, brain_home, allowed_sources=allowed)


def test_validate_store_article_write_rejects_superseded_by_non_list(tmp_path) -> None:
    """'superseded_by' must be a list; a string value raises PolicyError."""
    brain_home, store_root, capture_path, allowed = _capture_setup(tmp_path)
    proposal = _make_store_proposal(sources=[str(capture_path)])
    proposal.frontmatter["superseded_by"] = "common/decisions/new.md"
    with pytest.raises(PolicyError, match="superseded_by must be a list"):
        validate_store_article_write(proposal, store_root, brain_home, allowed_sources=allowed)


# ---------------------------------------------------------------------------
# Issue #123: processed_sources relaxation for update ops
# ---------------------------------------------------------------------------


def test_validate_store_article_write_update_accepts_processed_archived_source(tmp_path) -> None:
    """update + processed_sources: archived source (not on disk, not in allowed) passes."""
    brain_home = tmp_path / "brain"
    store_root = tmp_path / "store"
    # Build a source path inside capture root that does NOT exist on disk.
    archived_source = brain_home / "capture" / "inbox" / "archived.md"
    (brain_home / "capture" / "inbox").mkdir(parents=True)
    store_root.mkdir(parents=True)

    # Article target must exist for update.
    target_file = store_root / "common" / "decisions" / "foo.md"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("existing content", encoding="utf-8")
    known = {target_file.resolve()}

    proposal = _make_store_proposal(
        operation="update",
        sources=[str(archived_source)],
    )
    # Source is NOT on disk, NOT in allowed_sources — but IS in processed_sources.
    processed = {archived_source.resolve()}
    # Must not raise.
    result = validate_store_article_write(
        proposal,
        store_root,
        brain_home,
        allowed_sources=set(),
        known_existing_targets=known,
        processed_sources=processed,
    )
    assert result == target_file.resolve()


def test_validate_store_article_write_create_still_rejects_processed_archived_source(tmp_path) -> None:
    """create + processed_sources: archived source that is not on disk still raises."""
    brain_home = tmp_path / "brain"
    store_root = tmp_path / "store"
    archived_source = brain_home / "capture" / "inbox" / "archived.md"
    (brain_home / "capture" / "inbox").mkdir(parents=True)
    store_root.mkdir(parents=True)

    proposal = _make_store_proposal(
        operation="create",
        sources=[str(archived_source)],
    )
    processed = {archived_source.resolve()}
    # For create, processed relaxation must NOT apply — source must exist.
    with pytest.raises(PolicyError, match="Source does not exist"):
        validate_store_article_write(
            proposal,
            store_root,
            brain_home,
            allowed_sources=set(),
            known_existing_targets=set(),
            processed_sources=processed,
        )


def test_validate_store_article_write_update_rejects_unknown_nonexistent_source(tmp_path) -> None:
    """update + processed_sources: source neither on disk nor processed still raises."""
    brain_home = tmp_path / "brain"
    store_root = tmp_path / "store"
    ghost_source = brain_home / "capture" / "inbox" / "ghost.md"
    (brain_home / "capture" / "inbox").mkdir(parents=True)
    store_root.mkdir(parents=True)

    target_file = store_root / "common" / "decisions" / "foo.md"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("existing content", encoding="utf-8")
    known = {target_file.resolve()}

    proposal = _make_store_proposal(
        operation="update",
        sources=[str(ghost_source)],
    )
    # processed_sources is empty — the ghost source is not recorded anywhere.
    with pytest.raises(PolicyError, match="Source does not exist"):
        validate_store_article_write(
            proposal,
            store_root,
            brain_home,
            allowed_sources=set(),
            known_existing_targets=known,
            processed_sources=set(),
        )


def test_validate_store_article_write_capture_root_check_preserved_for_update_processed(tmp_path) -> None:
    """capture_root containment check fires even when source is in processed_sources."""
    brain_home = tmp_path / "brain"
    store_root = tmp_path / "store"
    # A source path OUTSIDE the capture root — even if we listed it in
    # processed_sources the containment guard must still reject it.
    outside_source = tmp_path / "outside" / "secret.md"
    outside_source.parent.mkdir(parents=True)
    outside_source.write_text("secret", encoding="utf-8")
    (brain_home / "capture" / "inbox").mkdir(parents=True)
    store_root.mkdir(parents=True)

    target_file = store_root / "common" / "decisions" / "foo.md"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("existing content", encoding="utf-8")
    known = {target_file.resolve()}

    proposal = _make_store_proposal(
        operation="update",
        sources=[str(outside_source)],
    )
    # Even with the out-of-root path listed in processed_sources the
    # containment check must fire first and reject it.
    processed = {outside_source.resolve()}
    with pytest.raises(PolicyError, match="not in capture root"):
        validate_store_article_write(
            proposal,
            store_root,
            brain_home,
            allowed_sources=processed,
            known_existing_targets=known,
            processed_sources=processed,
        )
