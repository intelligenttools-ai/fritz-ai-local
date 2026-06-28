"""Regression guard: VERSION must be allowlisted in .dockerignore.

Without this, `COPY VERSION /app/VERSION` in the Dockerfile fails because the
deny-all build context excludes VERSION from the Docker build context.
"""

from __future__ import annotations

from pathlib import Path


def test_dockerignore_allowlists_version() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    dockerignore = repo_root / ".dockerignore"
    lines = dockerignore.read_text(encoding="utf-8").splitlines()
    assert "!VERSION" in lines, (
        "'.dockerignore' must contain '!VERSION' so the VERSION file is included "
        "in the Docker build context for `COPY VERSION /app/VERSION`."
    )
