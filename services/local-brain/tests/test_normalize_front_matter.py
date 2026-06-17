"""Tests for normalize_front_matter (issue #139).

Ensures that articles with stacked/duplicate YAML front-matter blocks are
collapsed to a single block (first block wins, body preserved), that the
function is idempotent, and that clean articles are returned unchanged.
"""

from __future__ import annotations

import yaml

from fritz_local_brain.knowledge import normalize_front_matter, render_article
from fritz_local_brain.models import ArticleWriteProposal


# ---------------------------------------------------------------------------
# Core: stacked front-matter collapses to single block
# ---------------------------------------------------------------------------


def test_stacked_front_matter_collapses() -> None:
    """Two stacked FM blocks → single block (first block kept, body preserved)."""
    stacked = (
        "---\n"
        "title: First Block\n"
        "status: active\n"
        "---\n"
        "---\n"
        "title: Second Block\n"
        "status: deprecated\n"
        "---\n\n"
        "The real body content.\n"
    )
    result = normalize_front_matter(stacked)

    fence_count = result.count("\n---") + (1 if result.startswith("---") else 0)
    assert fence_count == 2, f"Expected 2 fences (single FM block), got {fence_count}:\n{result!r}"

    # First block's metadata must be kept.
    fm = yaml.safe_load(result.split("---\n", 2)[1])
    assert fm.get("title") == "First Block"
    assert fm.get("status") == "active"

    # Second block's data must not appear as front matter.
    assert "Second Block" not in result.split("---\n", 2)[1]

    # Body must be preserved.
    assert "The real body content." in result


def test_stacked_front_matter_with_blank_lines_between() -> None:
    """Stacked FM blocks separated by blank lines are also collapsed."""
    stacked = (
        "---\n"
        "title: First\n"
        "---\n\n\n"
        "---\n"
        "title: Second\n"
        "---\n\n"
        "Body here.\n"
    )
    result = normalize_front_matter(stacked)

    fence_count = result.count("\n---") + (1 if result.startswith("---") else 0)
    assert fence_count == 2, f"Expected 2 fences, got {fence_count}:\n{result!r}"
    assert "Body here." in result


def test_first_block_metadata_is_kept_not_second() -> None:
    """Verify specifically that second-block YAML is dropped from the FM section."""
    stacked = (
        "---\n"
        "title: Keep Me\n"
        "tags:\n"
        "- python\n"
        "---\n"
        "---\n"
        "title: Drop Me\n"
        "extra: value\n"
        "---\n\n"
        "Prose.\n"
    )
    result = normalize_front_matter(stacked)
    fm_section = result.split("---\n", 2)[1]
    fm = yaml.safe_load(fm_section)

    assert fm.get("title") == "Keep Me"
    assert "extra" not in fm
    assert "Drop Me" not in fm_section


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_on_stacked_input() -> None:
    """normalize(normalize(stacked)) == normalize(stacked)."""
    stacked = (
        "---\ntitle: A\nstatus: active\n---\n"
        "---\ntitle: B\nstatus: deprecated\n---\n\nBody.\n"
    )
    once = normalize_front_matter(stacked)
    twice = normalize_front_matter(once)
    assert once == twice


def test_idempotent_on_clean_input() -> None:
    """normalize(normalize(clean)) == normalize(clean) == clean."""
    clean = "---\ntitle: Clean\nstatus: active\n---\n\nClean body.\n"
    once = normalize_front_matter(clean)
    twice = normalize_front_matter(once)
    assert once == twice


# ---------------------------------------------------------------------------
# Single-block article → unchanged
# ---------------------------------------------------------------------------


def test_single_block_unchanged() -> None:
    """A well-formed single-block article must be returned unchanged."""
    article = "---\ntitle: Single\nstatus: active\n---\n\nBody text.\n"
    assert normalize_front_matter(article) == article


# ---------------------------------------------------------------------------
# No front matter → unchanged
# ---------------------------------------------------------------------------


def test_no_front_matter_unchanged() -> None:
    """An article with no front matter at all must be returned unchanged."""
    plain = "Just plain markdown.\n\nNo YAML here.\n"
    assert normalize_front_matter(plain) == plain


# ---------------------------------------------------------------------------
# Malformed leading fence → unchanged
# ---------------------------------------------------------------------------


def test_malformed_fm_unchanged() -> None:
    """An unclosed opening fence must not be stripped — return text unchanged."""
    malformed = "---\ntitle: No closing fence\n\nProse follows.\n"
    assert normalize_front_matter(malformed) == malformed


# ---------------------------------------------------------------------------
# MUST-FIX 1 regression: body thematic break must NOT be consumed
# ---------------------------------------------------------------------------


def test_body_thematic_break_not_consumed() -> None:
    """Body opening with a bare ``---`` thematic break must be left intact.

    The glossary example from the #139 review: the body's ``---`` separator
    and its ``Key: Value`` glossary entries share no identity keys with the
    front matter, so the normalizer must return the article unchanged.
    """
    article = (
        "---\n"
        "title: Glossary\n"
        "status: active\n"
        "---\n\n"
        "---\n\n"
        "API: Application Programming Interface\n"
        "REST: Representational State Transfer\n\n"
        "---\n"
    )
    assert normalize_front_matter(article) == article


def test_body_thematic_break_with_server_block_not_consumed() -> None:
    """Body section with a horizontal rule above a ``Server: prod-01`` block.

    ``Server`` is not a front-matter identity key (type/title/status), so the
    block must be treated as body prose, not a duplicate front-matter block.
    """
    article = (
        "---\n"
        "title: Infra Overview\n"
        "type: article\n"
        "status: active\n"
        "---\n\n"
        "Some introductory prose.\n\n"
        "---\n\n"
        "Server: prod-01\n"
        "Region: eu-central-1\n\n"
        "---\n"
    )
    assert normalize_front_matter(article) == article


# ---------------------------------------------------------------------------
# MUST-FIX 2 regression: compile write path (render_article) normalizes output
# ---------------------------------------------------------------------------


def test_render_article_collapses_stacked_fm_in_body() -> None:
    """render_article must emit single-block output even when the body passed
    by the compile agent contains a stacked front-matter block.

    This covers the compile write path (apply_article_write → render_article).
    """
    # Simulate a compile agent that accidentally included a second FM block in
    # the body field — a real straggler that shares identity keys with the
    # proposal frontmatter.
    body_with_stacked = (
        "---\n"
        "title: Keep Me\n"
        "type: article\n"
        "status: active\n"
        "---\n\n"
        "Actual prose content.\n"
    )
    proposal = ArticleWriteProposal(
        vault="test",
        relative_path="test.md",
        operation="create",
        title="Keep Me",
        summary="test",
        frontmatter={"type": "article", "title": "Keep Me", "status": "active"},
        body=body_with_stacked,
    )
    result = render_article(proposal)

    # render_article strips a leading FM block from body before assembling, so
    # the body_with_stacked is already handled by _split_front_matter.  Confirm
    # the output is a single FM block regardless.
    fence_count = result.count("\n---") + (1 if result.startswith("---") else 0)
    assert fence_count == 2, f"Expected single FM block (2 fences), got {fence_count}:\n{result!r}"
    assert "Actual prose content." in result
