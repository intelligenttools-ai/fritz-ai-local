from __future__ import annotations

import pytest

from fritz_local_brain.models import ArticleWriteProposal
from fritz_local_brain.security import PolicyError, validate_article_write


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
