"""Tests for tolerant model coercion (issue #141, facet 3 — proposals).

Mirrors the existing float-coercion pattern on ``ReconciliationVerdict``
(see ``tests/test_reconciliation.py``): a structured-output model must absorb
a stringified-JSON value emitted by a weaker model rather than hard-failing.
"""

from __future__ import annotations

import json

from fritz_local_brain.models import ArticleWriteProposal, CompileAgentOutput


def _proposal_dict() -> dict:
    return {
        "vault": "personal",
        "relative_path": "notes/x.md",
        "operation": "create",
        "title": "X",
        "summary": "a summary",
        "sources": ["capture/inbox/x.md"],
        "frontmatter": {"type": "article"},
        "body": "# X\n\nbody",
    }


def test_compile_output_coerces_stringified_proposals() -> None:
    """A JSON-string array of proposals is parsed back into a real list."""
    out = CompileAgentOutput.model_validate(
        {"proposals": json.dumps([_proposal_dict()])}
    )
    assert isinstance(out.proposals, list)
    assert len(out.proposals) == 1
    assert isinstance(out.proposals[0], ArticleWriteProposal)
    assert out.proposals[0].title == "X"


def test_compile_output_unparseable_proposals_string_falls_back_to_empty() -> None:
    """An unparseable string for proposals falls back to the default [] (no raise)."""
    out = CompileAgentOutput.model_validate({"proposals": "not-json-at-all"})
    assert out.proposals == []


def test_compile_output_normal_list_proposals_unchanged() -> None:
    """A real list of proposals passes through untouched."""
    out = CompileAgentOutput.model_validate({"proposals": [_proposal_dict()]})
    assert len(out.proposals) == 1
    assert out.proposals[0].title == "X"
