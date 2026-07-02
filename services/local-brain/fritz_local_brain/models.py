"""Shared Local Brain data models."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

    @field_validator("proposals", mode="before")
    @classmethod
    def _coerce_stringified_proposals(cls, v: object) -> object:
        if not isinstance(v, str):
            return v
        try:
            parsed = json.loads(v)
        except (ValueError, TypeError):
            return []  # fall back to field default on unparseable string
        return parsed if isinstance(parsed, list) else []


class AppliedArticleWrite(BaseModel):
    """A validated proposal that was applied to disk."""

    vault: str
    path: str
    operation: Literal["create", "update"]
    title: str


class ReconciliationVerdict(BaseModel):
    """Structured verdict for one (new article, related-old article) pair."""

    verdict: Literal[
        "corroborates",
        "refines",
        "contradicts_supersedes",
        "context_split",
        "orthogonal",
    ]
    reasoning: str
    evidence_strength: float = 0.0  # agent's weighting inputs (0..1)
    source_authority: float = 0.0
    anchor_strength: float = 0.0
    confidence: float = 0.0
    scope_qualifier: str | None = None  # used for context_split

    @field_validator("evidence_strength", "source_authority", "anchor_strength", "confidence", mode="before")
    @classmethod
    def _coerce_float_string(cls, v: object) -> object:
        if not isinstance(v, str):
            return v
        cleaned = v.strip().strip("'\"").rstrip(",").strip()
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0  # fall back to field default on unparseable string


class ReconciliationOutcome(BaseModel):
    """Record of one applied reconciliation verdict for visibility."""

    new_path: str
    old_path: str
    verdict: str
    actions: list[str] = Field(default_factory=list)
    reasoning: str = ""
    applied: bool = True
    prior_status: str | None = None
    disposition: str = "applied"  # "applied" | "proposed" | "escalated"


class MirrorSummary(BaseModel):
    """Structured output from the mirror summarizer agent (WI12).

    A faithful, concise summary of a single piece of EXTERNAL mirrored content,
    produced for ingestion into the brain. No fabrication: it must preserve the
    key facts of the source without inventing claims.
    """

    title: str
    summary: str
    key_points: list[str] = Field(default_factory=list)


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
    reconciliations: list[ReconciliationOutcome] = Field(default_factory=list)


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


class EmbeddingRefreshScheduleResult(BaseModel):
    """Result of scheduling a background vector refresh."""

    enabled: bool
    status: str | None = None
    reason: str = "ingest"


class QueryRunRequest(BaseModel):
    """Request body for a read-only brain query.

    Scope semantics for brain-store retrieval:

    - ``"active"`` (default): primary results (active/corroborated/no-status)
      followed by demoted (deprecated).  Archived (superseded/historical)
      articles are EXCLUDED.
    - ``"include_archive"``: same as active, but archived articles are appended
      AFTER the active+demoted results.
    - ``"all"``: everything in natural (sorted) order; no status filtering.
    """

    query: str = Field(min_length=1)
    vault: str | None = None
    limit: int = Field(default=10, ge=1, le=50)
    scope: str = "active"
    # WI12: when True, index-only mirrored hits are enriched in-place via a
    # live-fetch of their stored ``pointer`` (retrieval-synthesis). Default off
    # so existing query behaviour is unchanged.
    live_fetch: bool = False
    agent: str | None = None


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


class UsageActivityResult(BaseModel):
    """Time-series of event counts (#181).

    ``buckets`` maps a bucket key (``YYYY-MM-DD`` for bucket=day) to a
    ``{dimension_value: count}`` dict, where the dimension is ``by``
    (type|agent|vault). A vault/agent of None is keyed under ``"(none)"``.
    """

    bucket: str = "day"
    by: str = "type"
    buckets: dict[str, dict[str, int]] = Field(default_factory=dict)


class UsageLatency(BaseModel):
    """Latency percentiles in milliseconds; nulls when no data."""

    p50: float | None = None
    p95: float | None = None
    p99: float | None = None


class UsageTopQuery(BaseModel):
    """One frequent query text and its count."""

    query: str
    count: int


class UsageQueriesResult(BaseModel):
    """Query/search aggregates over the date range (#181)."""

    total: int = 0
    hit_rate: float | None = None
    latency_ms: UsageLatency = Field(default_factory=UsageLatency)
    by_agent: dict[str, int] = Field(default_factory=dict)
    top_queries: list[UsageTopQuery] = Field(default_factory=list)


class UsageKnowledgeResult(BaseModel):
    """KB-health snapshot from ``compute_kb_health`` (#180/#181).

    ``extra="allow"`` so a future key added to ``compute_kb_health`` is passed
    through the response rather than silently dropped by response_model
    serialization.
    """

    model_config = ConfigDict(extra="allow")

    articles_total: int = 0
    articles_by_status: dict[str, int] = Field(default_factory=dict)
    articles_by_vault: dict[str, int] = Field(default_factory=dict)
    growth_by_day: dict[str, int] = Field(default_factory=dict)
    embedding: dict[str, Any] = Field(default_factory=dict)
    compile: dict[str, Any] = Field(default_factory=dict)
    backlog: dict[str, Any] = Field(default_factory=dict)


class UsageProject(BaseModel):
    """Per-vault rollup combining event activity and KB article counts."""

    vault: str
    event_count: int = 0
    events_by_type: dict[str, int] = Field(default_factory=dict)
    article_count: int = 0


class UsageProjectsResult(BaseModel):
    """Per-vault rollup list (#181)."""

    projects: list[UsageProject] = Field(default_factory=list)


class UsageSummaryResult(BaseModel):
    """Headline usage numbers for the dashboard landing page (#181)."""

    total_events: int = 0
    events_by_type: dict[str, int] = Field(default_factory=dict)
    total_queries: int = 0
    hit_rate: float | None = None
    total_articles: int = 0
    backlog_pending: int = 0
    distinct_agents: int = 0


class UsageAgent(BaseModel):
    """One distinct agent discovered in the telemetry store (#199).

    Data-driven: any agent value present in the events appears here with no
    code change (never a hardcoded enum).
    """

    agent: str
    count: int = 0
    first_seen: str | None = None
    last_seen: str | None = None


class UsageAgentsResult(BaseModel):
    """Per-agent discovery list for the dashboard drill-down (#199)."""

    agents: list[UsageAgent] = Field(default_factory=list)


class UsageSystemTypeStat(BaseModel):
    """Per-event-type SYSTEM counts (#205)."""

    total: int = 0
    ok: int = 0
    error: int = 0


class UsageSystemResult(BaseModel):
    """SYSTEM activity (the service's own work) for the System panel (#205).

    ``by_type`` maps each system event_type to its ``{total, ok, error}`` counts;
    the overall ``total``/``ok``/``error`` and ``success_rate`` roll up all system
    events. ``success_rate`` is None when there are no system events.
    """

    by_type: dict[str, UsageSystemTypeStat] = Field(default_factory=dict)
    total: int = 0
    ok: int = 0
    error: int = 0
    success_rate: float | None = None


class StatusResult(BaseModel):
    """Service status without secrets."""

    service: str = "local-brain"
    service_running: bool = True
    version: str | None = None
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


# ---------------------------------------------------------------------------
# Live configuration API (#208)
# ---------------------------------------------------------------------------


class ConfigField(BaseModel):
    """One configurable setting as surfaced by GET /v1/config.

    ``value`` is API-safe: secret fields (llm_api_key) return a bool indicating
    whether a key is set, never the key itself.
    """

    value: Any
    mutable: bool
    requires: Literal["runtime", "rebuild"]


class ConfigResult(BaseModel):
    """Effective configuration keyed by field name."""

    fields: dict[str, ConfigField]


class ConfigPatchResult(BaseModel):
    """Result of a PATCH /v1/config apply attempt."""

    applied: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    config: dict[str, ConfigField]


# ---------------------------------------------------------------------------
# Read-only knowledge browse API (#221)
# ---------------------------------------------------------------------------


class KnowledgeTreeNode(BaseModel):
    """One directory node of the store tree with recursive per-node rollups."""

    name: str
    path: str  # relative to store root ("" for the root node)
    article_count: int
    status_counts: dict[str, int]  # keyed by all STATUS_VALUES
    children: list["KnowledgeTreeNode"] = Field(default_factory=list)


class KnowledgeArticleSummary(BaseModel):
    """A single article as it appears in the flat list (no body)."""

    path: str
    title: str
    status: str
    created: str | None = None
    updated: str | None = None
    tags: list[str] = Field(default_factory=list)


class KnowledgeArticlesResult(BaseModel):
    """Paginated flat article list."""

    total: int
    limit: int
    offset: int
    articles: list[KnowledgeArticleSummary] = Field(default_factory=list)


class KnowledgeLink(BaseModel):
    """A resolved supersession link and whether its target exists on disk."""

    relation: Literal["supersedes", "superseded_by"]
    target: str
    exists: bool


class KnowledgeArticleDetail(BaseModel):
    """Full detail for one article incl. raw body and resolved links."""

    path: str
    title: str
    status: str
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    body: str
    supersedes: list[str] = Field(default_factory=list)
    superseded_by: list[str] = Field(default_factory=list)
    links: list[KnowledgeLink] = Field(default_factory=list)
