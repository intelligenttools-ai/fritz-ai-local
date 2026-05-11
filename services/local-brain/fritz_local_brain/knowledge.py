"""Knowledge article write helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .models import ArticleWriteProposal


def render_article(proposal: ArticleWriteProposal) -> str:
    frontmatter = dict(proposal.frontmatter)
    frontmatter.setdefault("title", proposal.title)
    frontmatter.setdefault("updated", datetime.now().strftime("%Y-%m-%d"))
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    body = proposal.body.strip() + "\n"
    return f"---\n{yaml_text}\n---\n\n{body}"


def apply_article_write(target: Path, proposal: ArticleWriteProposal, dry_run: bool) -> None:
    if dry_run:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_article(proposal), encoding="utf-8")
