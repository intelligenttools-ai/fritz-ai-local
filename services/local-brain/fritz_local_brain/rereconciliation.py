"""Re-reconciliation sweep (WI13).

Processes store articles that are flagged ``needs_rereconciliation: true``
(set by WI9's resurrection-flagging logic when a superseder is itself
invalidated).  The sweep re-runs the standard reconciliation agent over each
flagged article against its current related content, then either applies the
resulting verdict (non-dry-run) or reports what would happen (dry-run).

Key design choices:
- Dry-run by default: nothing is written or cleared unless ``dry_run=False``.
- The reconciliation agent is monkeypatchable via
  ``compile_workflow.build_reconciliation_agent`` (same seam used in tests).
- Flag clearing uses ``knowledge.apply_frontmatter_update(...,
  set_fields={"needs_rereconciliation": False})`` so processed articles are
  not reprocessed forever.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from pydantic_ai.usage import UsageLimits

from . import compile_workflow
from .config import Settings
from .knowledge import apply_frontmatter_update, find_rereconciliation_flagged, store_root
from .logs import append_global_log
from .models import CompileRunRequest, ReconciliationOutcome


@dataclass
class RereconciliationResult:
    """Counts and outcomes for one re-reconciliation sweep pass."""

    flagged_count: int = 0
    """Number of articles found with needs_rereconciliation: true."""

    processed_count: int = 0
    """Number of articles for which the agent was actually invoked."""

    cleared_count: int = 0
    """Number of articles whose flag was cleared (non-dry-run only)."""

    dry_run: bool = True
    """Whether this sweep ran in dry-run mode."""

    outcomes: list[ReconciliationOutcome] = field(default_factory=list)
    """Per-pair reconciliation outcomes produced during the sweep."""

    flagged_paths: list[str] = field(default_factory=list)
    """Relative paths of articles that were flagged at sweep start."""


async def run_rereconciliation_sweep(
    settings: Settings,
    *,
    dry_run: bool = True,
) -> RereconciliationResult:
    """Scan the store for resurrection-flagged articles and re-reconcile them.

    Parameters
    ----------
    settings:
        Service settings; used to resolve the store root and to build the
        reconciliation agent.
    dry_run:
        When ``True`` (the default), no frontmatter mutations are written and
        the ``needs_rereconciliation`` flag is not cleared.  The agent is still
        invoked so callers can inspect what would happen.

    Returns
    -------
    RereconciliationResult
        Aggregated counts and per-pair outcomes for this sweep pass.
    """
    brain_store_root = store_root(settings)
    result = RereconciliationResult(dry_run=dry_run)

    if not brain_store_root.exists():
        return result

    flagged_relpaths = find_rereconciliation_flagged(brain_store_root)
    result.flagged_count = len(flagged_relpaths)
    result.flagged_paths = list(flagged_relpaths)

    if not flagged_relpaths:
        return result

    # Build a fake CompileRunRequest carrying dry_run so _reconcile_applied_articles
    # honours it for its own internal writes.  No approval_token needed here
    # because we are not initiating a bulk compile — just re-running per-pair
    # reconciliation on already-stored articles.
    request = CompileRunRequest(dry_run=dry_run)

    for rel_path in flagged_relpaths:
        abs_path = (brain_store_root / rel_path).resolve()
        if not abs_path.exists():
            continue

        result.processed_count += 1

        # Treat the flagged article as the "new" article in a reconciliation
        # call — _reconcile_applied_articles finds related content internally.
        outcomes: list[ReconciliationOutcome] = await compile_workflow._reconcile_applied_articles(
            settings,
            brain_store_root,
            [abs_path],
            request,
        )
        result.outcomes.extend(outcomes)

        # Clear the flag on processed articles so they are not swept again.
        # Only do this when NOT in dry-run AND reconciliation ran (even if it
        # produced no pairs — the article was processed and the flag is stale).
        if not dry_run:
            apply_frontmatter_update(
                abs_path,
                store_root=brain_store_root,
                set_fields={"needs_rereconciliation": False},
                dry_run=False,
            )
            result.cleared_count += 1

    # Summarise to the global log (no-op when dry_run).
    applied_count = sum(1 for o in result.outcomes if o.applied)
    append_global_log(
        settings.brain_home,
        "RERECONCILE",
        (
            f"Re-reconciliation sweep: {result.flagged_count} flagged, "
            f"{result.processed_count} processed, {result.cleared_count} cleared, "
            f"{len(result.outcomes)} pairs evaluated ({applied_count} applied)"
        ),
        dry_run,
    )

    return result
