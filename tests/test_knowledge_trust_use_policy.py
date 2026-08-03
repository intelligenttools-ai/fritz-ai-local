"""Tests for the `trust` / `use_policy` frontmatter fields (issue #342).

Both fields are optional additions to the knowledge article schema that let an
agent decide whether it may *act* on a claim. This mirrors the existing
``status`` / ``normalize_status`` precedent in ``knowledge.py``: unknown values
are treated as absent plus a warning — they never invalidate an article and
never raise.
"""

from __future__ import annotations

import logging

from fritz_local_brain.knowledge import (
    TRUST_VALUES,
    USE_POLICY_VALUES,
    ArticleWriteProposal,
    apply_frontmatter_update,
    normalize_trust,
    normalize_use_policy,
    render_article,
    split_front_matter,
)


def test_trust_values_vocabulary() -> None:
    assert TRUST_VALUES == (
        "observed",
        "inferred",
        "user_confirmed",
        "imported",
        "generated",
        "disputed",
    )


def test_use_policy_values_vocabulary() -> None:
    assert USE_POLICY_VALUES == (
        "instruction",
        "evidence",
        "requires_confirmation",
        "no_auto_inject",
    )


def test_normalize_trust_lowercases_and_strips() -> None:
    assert normalize_trust("  User_Confirmed  ") == "user_confirmed"


def test_normalize_trust_unknown_value_returns_none_and_warns(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        result = normalize_trust("bogus")
    assert result is None
    assert any("bogus" in record.message for record in caplog.records)


def test_normalize_trust_absent_returns_none() -> None:
    assert normalize_trust(None) is None
    assert normalize_trust("") is None


def test_normalize_use_policy_lowercases_and_strips() -> None:
    assert normalize_use_policy("  Requires_Confirmation  ") == "requires_confirmation"


def test_normalize_use_policy_unknown_value_returns_none_and_warns(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        result = normalize_use_policy("nonsense")
    assert result is None
    assert any("nonsense" in record.message for record in caplog.records)


def test_normalize_use_policy_absent_returns_none() -> None:
    assert normalize_use_policy(None) is None
    assert normalize_use_policy("") is None


def test_article_without_either_field_remains_valid_and_unchanged(tmp_path) -> None:
    """A fixture store article with neither field is untouched by the schema addition."""
    store = tmp_path / "knowledge"
    store.mkdir()
    article = store / "no-trust-fields.md"
    article.write_text(
        "---\n"
        "type: article\n"
        "title: Plain article\n"
        "status: active\n"
        "---\n\n"
        "Body text.\n",
        encoding="utf-8",
    )

    frontmatter, body = split_front_matter(article.read_text(encoding="utf-8"))

    assert "trust" not in frontmatter
    assert "use_policy" not in frontmatter
    assert normalize_trust(frontmatter.get("trust")) is None
    assert normalize_use_policy(frontmatter.get("use_policy")) is None
    assert body.strip() == "Body text."


def test_round_trip_preserves_trust_and_use_policy_exactly(tmp_path) -> None:
    proposal = ArticleWriteProposal(
        vault="common",
        relative_path="knowledge/example.md",
        operation="create",
        title="Example",
        summary="An example article",
        frontmatter={
            "type": "article",
            "title": "Example",
            "trust": "user_confirmed",
            "use_policy": "requires_confirmation",
        },
        body="Body text.",
    )

    rendered = render_article(proposal)
    frontmatter, _body = split_front_matter(rendered)

    assert frontmatter["trust"] == "user_confirmed"
    assert frontmatter["use_policy"] == "requires_confirmation"

    # Re-render from the parsed-back frontmatter to confirm a second round trip
    # is stable (idempotent) and exact.
    proposal_2 = ArticleWriteProposal(
        vault="common",
        relative_path="knowledge/example.md",
        operation="update",
        title="Example",
        summary="An example article",
        frontmatter=frontmatter,
        body=_body,
    )
    rendered_2 = render_article(proposal_2)
    frontmatter_2, _body_2 = split_front_matter(rendered_2)

    assert frontmatter_2["trust"] == "user_confirmed"
    assert frontmatter_2["use_policy"] == "requires_confirmation"


def test_render_article_drops_unknown_trust_and_warns(caplog) -> None:
    """AC: an unknown ``trust``/``use_policy`` value must be treated as absent
    (dropped from the rendered output) plus a logged warning, going through the
    REAL write path (``render_article``) rather than calling the normalizer
    functions directly."""
    proposal = ArticleWriteProposal(
        vault="common",
        relative_path="knowledge/example.md",
        operation="create",
        title="Example",
        summary="An example article",
        frontmatter={
            "type": "article",
            "title": "Example",
            "trust": "bogus",
            "use_policy": "nonsense",
        },
        body="Body text.",
    )

    with caplog.at_level(logging.WARNING):
        rendered = render_article(proposal)

    assert "trust" not in rendered
    assert "bogus" not in rendered
    assert "use_policy" not in rendered
    assert "nonsense" not in rendered
    assert any("bogus" in record.message for record in caplog.records)
    assert any("nonsense" in record.message for record in caplog.records)


def test_apply_frontmatter_update_drops_unknown_trust_and_warns(tmp_path, caplog) -> None:
    """Same AC, via the other real write path: ``apply_frontmatter_update``'s
    ``set_fields`` mechanism."""
    store = tmp_path / "knowledge"
    store.mkdir()
    article = store / "example.md"
    article.write_text(
        "---\ntype: article\ntitle: Example\nstatus: active\n---\n\nBody text.\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        apply_frontmatter_update(
            article,
            store_root=store,
            set_fields={"trust": "bogus", "use_policy": "nonsense"},
        )

    persisted = article.read_text(encoding="utf-8")
    assert "trust" not in persisted
    assert "bogus" not in persisted
    assert "use_policy" not in persisted
    assert "nonsense" not in persisted
    assert any("bogus" in record.message for record in caplog.records)
    assert any("nonsense" in record.message for record in caplog.records)
