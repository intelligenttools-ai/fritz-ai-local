"""Tests for render_article duplicate front-matter stripping (issue #122).

The compile agent sometimes emits a body that already begins with its own
``---\n...\n---\n`` YAML front-matter block.  render_article must strip it so
the rendered article has exactly ONE front-matter block whose authoritative
content comes from proposal.frontmatter.
"""

from __future__ import annotations

import yaml

from fritz_local_brain.knowledge import render_article
from fritz_local_brain.models import ArticleWriteProposal


def _make_proposal(body: str, **frontmatter_overrides) -> ArticleWriteProposal:
    fm: dict = {"status": "active"}
    fm.update(frontmatter_overrides)
    return ArticleWriteProposal(
        vault="common",
        relative_path="decisions/test-article.md",
        operation="create",
        title="Test Article",
        summary="A test article.",
        frontmatter=fm,
        body=body,
    )


# ---------------------------------------------------------------------------
# Body already contains a leading front-matter block → strip it
# ---------------------------------------------------------------------------


def test_duplicate_frontmatter_is_stripped() -> None:
    """When the body begins with its own FM block, render_article emits exactly one."""
    body_with_fm = (
        "---\n"
        "title: Agent-generated title\n"
        "status: active\n"
        "---\n\n"
        "This is the prose body.\n"
    )
    result = render_article(_make_proposal(body_with_fm))

    # A single FM block produces exactly two ``---`` lines (opening + closing).
    # The opening has no leading newline (it's the very first line), so count
    # all occurrences of the bare string "---" on its own line.
    fence_count = result.count("\n---") + (1 if result.startswith("---") else 0)
    assert fence_count == 2, (
        f"Expected 2 '---' fences (one FM block), got {fence_count}:\n{result!r}"
    )


def test_authoritative_header_comes_from_proposal_frontmatter() -> None:
    """The single FM block must reflect proposal.frontmatter, not the body's FM."""
    body_with_fm = (
        "---\n"
        "title: Wrong title from body\n"
        "status: deprecated\n"
        "---\n\n"
        "Prose content here.\n"
    )
    result = render_article(_make_proposal(body_with_fm, status="active"))

    # Parse the rendered front matter.
    assert result.startswith("---\n")
    parts = result.split("---\n", 2)
    # parts[0] == '', parts[1] == yaml text, parts[2] == body
    rendered_fm = yaml.safe_load(parts[1])

    assert rendered_fm.get("title") == "Test Article", "title must come from proposal.title"
    assert rendered_fm.get("status") == "active", "status must come from proposal.frontmatter"
    assert "Wrong title from body" not in result


def test_prose_body_is_preserved_after_stripping() -> None:
    """The prose that followed the stripped FM block must appear in the output."""
    prose = "This is the prose body.\n"
    body_with_fm = f"---\ntitle: Ignore me\n---\n\n{prose}"
    result = render_article(_make_proposal(body_with_fm))

    assert prose.strip() in result, "Prose body must be preserved after stripping"


# ---------------------------------------------------------------------------
# Clean body (no front matter) → no stripping, body unchanged
# ---------------------------------------------------------------------------


def test_clean_body_is_unchanged() -> None:
    """A body with no front matter must pass through intact."""
    clean_body = "Just some prose.\n\nNo front matter here.\n"
    result = render_article(_make_proposal(clean_body))

    assert "Just some prose." in result
    assert "No front matter here." in result

    fence_count = result.count("\n---") + (1 if result.startswith("---") else 0)
    assert fence_count == 2, (
        f"Expected exactly 2 fences for a clean body, got {fence_count}"
    )


# ---------------------------------------------------------------------------
# Malformed leading fence (no closing ``---``) → body preserved, not corrupted
# ---------------------------------------------------------------------------


def test_malformed_frontmatter_in_body_is_preserved() -> None:
    """A body with an unclosed opening fence must not be stripped or corrupted.

    _split_front_matter returns ({}, original_text) when there is no closing
    fence, so the malformed content must be preserved verbatim in the output.
    """
    malformed_body = "---\ntitle: No closing fence\n\nThis prose follows.\n"
    result = render_article(_make_proposal(malformed_body))

    # The key invariant: the body content is NOT lost.
    assert "No closing fence" in result
    assert "This prose follows." in result
    # The result must still start with the proposal's own FM block.
    assert result.startswith("---\n")
