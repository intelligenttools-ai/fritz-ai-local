"""Mirror-as-ingest writer (WI11 + WI12).

Resolves a read-direction ingest adapter for each external target, fetches
content, and writes provenance-tagged inbox captures so that the normal
compile -> index pipeline can ingest them.

WI11 functions (``mirror_target`` / ``mirror_targets``) are deterministic and
synchronous (no agent). WI12 adds ``run_mirror`` — an async, agent-backed pass
that summarizes full-summary targets and writes minimal index-only captures.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .agents.mirror_agent import MirrorSummaryDeps, build_mirror_agent
from .captures import write_inbox_capture
from .config import Settings
from .ingest_adapters import MirroredEntry, get_ingest_adapter
from .models import MirrorSummary
from .registry import ExternalTarget, load_external_targets

# A summarizer takes (pointer, title, raw_content) and returns a MirrorSummary.
SummarizeFn = Callable[[str, str, str], Awaitable[MirrorSummary]]

# Marker body for index-only captures (the full content is fetched live on hit).
_INDEX_ONLY_MARKER = "Content available via live-fetch on hit."


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass
class MirrorResult:
    """Summary of one ``mirror_target`` run.

    Attributes
    ----------
    target:
        ``ExternalTarget.name`` of the mirrored target.
    kind:
        ``ExternalTarget.kind`` (e.g. ``"local-vault"``).
    entries_mirrored:
        Number of ``MirroredEntry`` objects returned by the adapter.
    written_paths:
        Intended (or actually written) inbox-capture paths as strings.
    dry_run:
        Whether the run was a dry-run (no files written).
    """

    target: str
    kind: str
    entries_mirrored: int
    written_paths: list[str] = field(default_factory=list)
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def mirror_target(
    settings: Settings,
    target: ExternalTarget,
    *,
    mirrored_at: str | None = None,
    dry_run: bool = False,
) -> MirrorResult:
    """Fetch *target* via its ingest adapter and write inbox captures.

    For each ``MirroredEntry`` returned by the adapter, an inbox capture is
    written under ``<brain_home>/capture/inbox/`` via
    ``captures.write_inbox_capture``.  Each capture's YAML front-matter
    contains:

    - ``title`` — entry title.
    - ``source`` — ``"<target.name> (<target.kind>)"``.
    - ``mirrored_at`` — ISO-8601 timestamp (override for deterministic tests).
    - ``mode`` — ``"full"`` or ``"summary"``.
    - ``pointer`` — stable adapter-specific identifier for the source item.

    Parameters
    ----------
    settings:
        Runtime settings (provides ``brain_home``).
    target:
        The external target to mirror.
    mirrored_at:
        ISO-8601 timestamp to embed in every capture's front-matter.  When
        ``None`` (default), ``datetime.now().isoformat()`` is used.  Pass an
        explicit value in tests to make assertions deterministic.
    dry_run:
        When ``True``, adapters are still called and paths are computed, but
        no files are written to disk.

    Returns
    -------
    MirrorResult
        A summary including the count of mirrored entries and the intended
        (or written) inbox paths.
    """
    if mirrored_at is None:
        mirrored_at = datetime.now().isoformat()

    adapter = get_ingest_adapter(target.kind)
    entries: list[MirroredEntry] = adapter.fetch(target)

    written_paths: list[str] = []
    brain_home = settings.brain_home

    for entry in entries:
        frontmatter: dict[str, Any] = {
            "title": entry.title,
            "source": f"{target.name} ({target.kind})",
            "mirrored_at": mirrored_at,
            "mode": entry.mode,
            "pointer": entry.pointer,
        }
        # Derive a slug from the pointer so filenames are stable and readable.
        # pointer format for local-vault: "<name>:<relpath>" e.g. "vault:a/b.md"
        pointer_stem = entry.pointer.replace(":", "-").replace("/", "-")
        slug = f"mirror-{target.name}-{pointer_stem}"

        path = write_inbox_capture(
            brain_home,
            slug,
            frontmatter,
            entry.content,
            dry_run=dry_run,
        )
        written_paths.append(str(path))

    return MirrorResult(
        target=target.name,
        kind=target.kind,
        entries_mirrored=len(entries),
        written_paths=written_paths,
        dry_run=dry_run,
    )


def mirror_targets(
    settings: Settings,
    targets: list[ExternalTarget],
    *,
    mirrored_at: str | None = None,
    dry_run: bool = False,
) -> list[MirrorResult]:
    """Mirror each target in *targets* sequentially.

    This is a thin loop over ``mirror_target`` with no scheduling, concurrency,
    or agent orchestration (those are WI12).

    Parameters
    ----------
    settings:
        Runtime settings.
    targets:
        List of external targets to mirror.
    mirrored_at:
        Passed through to each ``mirror_target`` call (shared timestamp for a
        single mirror pass).
    dry_run:
        Passed through to each ``mirror_target`` call.

    Returns
    -------
    list[MirrorResult]
        One result per target, in the same order as *targets*.
    """
    ts = mirrored_at if mirrored_at is not None else datetime.now().isoformat()
    return [
        mirror_target(settings, target, mirrored_at=ts, dry_run=dry_run)
        for target in targets
    ]


# ---------------------------------------------------------------------------
# WI12: agent-backed mirror pass (per-target mode + summarization)
# ---------------------------------------------------------------------------


def _slug_for(target: ExternalTarget, pointer: str) -> str:
    """Derive a stable, readable inbox-capture slug from a pointer."""
    pointer_stem = pointer.replace(":", "-").replace("/", "-")
    return f"mirror-{target.name}-{pointer_stem}"


def _make_default_summarizer(settings: Settings) -> SummarizeFn:
    """Build a summarizer backed by the real mirror agent (built once).

    ``build_mirror_agent`` is referenced via the module global so tests can
    monkeypatch ``mirror.build_mirror_agent`` (mirrors how compile tests
    monkeypatch ``compile_workflow.build_compile_agent``).
    """
    from pydantic_ai.usage import UsageLimits

    agent = build_mirror_agent(settings)

    async def _summarize(pointer: str, title: str, raw_content: str) -> MirrorSummary:
        deps = MirrorSummaryDeps(pointer=pointer, title=title, raw_content=raw_content)
        result = await agent.run(
            "Summarize the mirrored external content. Call load_mirror_context once, then return final output.",
            deps=deps,
            usage_limits=UsageLimits(request_limit=3),
        )
        return result.output

    return _summarize


async def run_mirror(
    settings: Settings,
    *,
    targets: list[ExternalTarget] | None = None,
    mirrored_at: str | None = None,
    dry_run: bool = False,
    summarize: SummarizeFn | None = None,
) -> list[MirrorResult]:
    """Agent-backed mirror pass honoring each target's ``mirror_mode`` (WI12).

    For each target the adapter fetches entries, then per mode:

    - **full-summary**: each entry's full content is summarized by the mirror
      agent (or an injected *summarize* callable). The inbox capture body is the
      summary; front-matter ``mode`` is ``"full"``.
    - **index-only**: a MINIMAL capture is written (``mode: "index-only"``,
      ``pointer`` present, body = title + a one-line live-fetch marker). The full
      external content is NOT stored — it is fetched live on a query hit.

    Parameters
    ----------
    settings:
        Runtime settings (provides ``brain_home``).
    targets:
        Targets to mirror. Defaults to ``load_external_targets(brain_home)``.
    mirrored_at:
        Shared ISO-8601 timestamp for the pass. Defaults to ``now()``.
    dry_run:
        When ``True``, no files are written (paths are still computed/returned).
    summarize:
        Optional injected summarizer ``(pointer, title, content) -> MirrorSummary``
        (for tests). When ``None``, the real mirror agent is built once and used
        for full-summary targets only.

    Returns
    -------
    list[MirrorResult]
        One result per target, in target order.
    """
    if targets is None:
        targets = load_external_targets(settings.brain_home)
    if mirrored_at is None:
        mirrored_at = datetime.now().isoformat()

    brain_home = settings.brain_home
    results: list[MirrorResult] = []

    # Build the summarizer lazily — only when a full-summary target exists and no
    # summarizer was injected — so index-only-only passes never touch the LLM.
    summarizer: SummarizeFn | None = summarize
    needs_summarizer = any(t.mirror_mode == "full-summary" for t in targets)
    if summarizer is None and needs_summarizer:
        summarizer = _make_default_summarizer(settings)

    for target in targets:
        adapter = get_ingest_adapter(target.kind)
        entries: list[MirroredEntry] = adapter.fetch(target)
        written_paths: list[str] = []

        for entry in entries:
            slug = _slug_for(target, entry.pointer)
            source = f"{target.name} ({target.kind})"

            if target.mirror_mode == "full-summary":
                assert summarizer is not None  # set above when any full-summary target exists
                summary = await summarizer(entry.pointer, entry.title, entry.content)
                body = summary.summary
                if summary.key_points:
                    points = "\n".join(f"- {point}" for point in summary.key_points)
                    body = f"{body}\n\nKey points:\n{points}"
                frontmatter: dict[str, Any] = {
                    "title": summary.title or entry.title,
                    "source": source,
                    "mirrored_at": mirrored_at,
                    "mode": "full",
                    "pointer": entry.pointer,
                }
            else:
                # index-only: minimal body, full content fetched live on hit.
                frontmatter = {
                    "title": entry.title,
                    "source": source,
                    "mirrored_at": mirrored_at,
                    "mode": "index-only",
                    "pointer": entry.pointer,
                }
                body = f"{entry.title}\n\n{_INDEX_ONLY_MARKER}"

            path = write_inbox_capture(brain_home, slug, frontmatter, body, dry_run=dry_run)
            written_paths.append(str(path))

        results.append(
            MirrorResult(
                target=target.name,
                kind=target.kind,
                entries_mirrored=len(entries),
                written_paths=written_paths,
                dry_run=dry_run,
            )
        )

    return results
