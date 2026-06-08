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
    approval_token: str | None = None


class CompileRunResult(BaseModel):
    """Structured compile run result."""

    run_id: str
    started_at: datetime
    finished_at: datetime
    dry_run: bool
    captures_considered: int
    captures_by_source: dict[str, int] = Field(default_factory=dict)
    proposals: list[ArticleWriteProposal] = Field(default_factory=list)
    applied: list[AppliedArticleWrite] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SyncRunRequest(BaseModel):
    """Request body for a manual sync run."""

    dry_run: bool = True
    vault: str | None = None
    approval_token: str | None = None


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


class EmbeddingMetadata(BaseModel):
    """Stored embedding model metadata."""

    protocol: str
    base_url: str
    model: str
    dimensions: int
    probed_at: datetime


class EmbeddingStatusResult(BaseModel):
    """Embedding configuration and last probe metadata without secrets."""

    enabled: bool
    protocol: str
    base_url: str
    model: str
    metadata: EmbeddingMetadata | None = None


class EmbeddingProbeRequest(BaseModel):
    """Request body for embedding dimension probing."""

    dry_run: bool = True


class EmbeddingProbeResult(BaseModel):
    """Result of probing an embedding endpoint for vector dimensions."""

    enabled: bool
    dry_run: bool
    protocol: str
    base_url: str
    model: str
    dimensions: int | None = None
    metadata_path: str | None = None
    stored: bool = False
    error: str | None = None


class EmbeddingIndexRequest(BaseModel):
    """Request body for container-owned vector index refresh."""

    force: bool = False


class EmbeddingIndexResult(BaseModel):
    """Result of refreshing the local vector index."""

    enabled: bool
    indexed: bool = False
    documents_indexed: int = 0
    index_path: str | None = None
    updated_at: datetime | None = None
    error: str | None = None


class QueryRunRequest(BaseModel):
    """Request body for a read-only brain query."""

    query: str = Field(min_length=1)
    vault: str | None = None
    limit: int = Field(default=10, ge=1, le=50)


class QueryMatch(BaseModel):
    """Knowledge article match for a query."""

    vault: str
    path: str
    title: str
    snippet: str


class QueryRunResult(BaseModel):
    """Structured read-only query result."""

    run_id: str
    started_at: datetime
    finished_at: datetime
    query: str
    matches: list[QueryMatch] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class LintRunRequest(BaseModel):
    """Request body for a brain lint run."""

    dry_run: bool = True
    vault: str | None = None


class LintFinding(BaseModel):
    """Health finding from a lint run."""

    vault: str
    severity: Literal["error", "warning"]
    path: str
    message: str


class LintRunResult(BaseModel):
    """Structured lint result."""

    run_id: str
    started_at: datetime
    finished_at: datetime
    dry_run: bool
    findings: list[LintFinding] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class StatusResult(BaseModel):
    """Service status without secrets."""

    service: str = "local-brain"
    service_running: bool = True
    scheduler_enabled: bool
    scheduler_dry_run: bool
    processing_mode: Literal["dry-run", "apply"]
    processing_active: bool
    processing_note: str
    interval_minutes: int
    brain_home: str
    skills_dir: str
    allow_first_external_sync: bool
    last_successful_compile_at: datetime | None = None
    pending_captures_by_source: dict[str, int] = Field(default_factory=dict)
    oldest_pending_capture_path: str | None = None
    oldest_pending_capture_at: datetime | None = None
    status_warnings: list[str] = Field(default_factory=list)
