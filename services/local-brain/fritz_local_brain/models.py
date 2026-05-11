"""Shared Local Brain data models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ArticleWriteProposal(BaseModel):
    """Agent proposal for a safe knowledge article create or update."""

    vault: str
    relative_path: str
    operation: Literal["create", "update"]
    title: str
    summary: str
    sources: list[str] = Field(default_factory=list)
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    body: str


class CompileAgentOutput(BaseModel):
    """Structured output expected from the compile agent."""

    proposals: list[ArticleWriteProposal] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)


class AppliedArticleWrite(BaseModel):
    """A validated proposal that was applied to disk."""

    vault: str
    path: str
    operation: Literal["create", "update"]
    title: str


class CompileRunRequest(BaseModel):
    """Request body for a manual compile run."""

    dry_run: bool = True
    max_captures: int | None = Field(default=None, ge=1)


class CompileRunResult(BaseModel):
    """Structured compile run result."""

    run_id: str
    started_at: datetime
    finished_at: datetime
    dry_run: bool
    captures_considered: int
    proposals: list[ArticleWriteProposal] = Field(default_factory=list)
    applied: list[AppliedArticleWrite] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SyncRunRequest(BaseModel):
    """Request body for a manual sync run."""

    dry_run: bool = True
    vault: str | None = None


class SyncVaultResult(BaseModel):
    """Sync outcome for one vault."""

    vault: str
    target: str
    first_sync: bool
    articles_considered: int = 0
    articles_to_sync: list[str] = Field(default_factory=list)
    pushed: bool = False
    skipped: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SyncRunResult(BaseModel):
    """Structured sync run result."""

    run_id: str
    started_at: datetime
    finished_at: datetime
    dry_run: bool
    results: list[SyncVaultResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class RecentRun(BaseModel):
    """Bounded in-process record of a service run."""

    kind: Literal["compile", "sync"]
    run_id: str
    started_at: datetime
    finished_at: datetime
    dry_run: bool
    status: Literal["ok", "error"]
    summary: str


class RecentRunsResult(BaseModel):
    """Recent service runs."""

    runs: list[RecentRun] = Field(default_factory=list)


class StatusResult(BaseModel):
    """Service status without secrets."""

    service: str = "local-brain"
    scheduler_enabled: bool
    interval_minutes: int
    brain_home: str
    skills_dir: str
    allow_first_external_sync: bool
