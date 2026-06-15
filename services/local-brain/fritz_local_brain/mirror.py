"""Mirror-as-ingest writer (WI11).

Resolves a read-direction ingest adapter for each external target, fetches
content, and writes provenance-tagged inbox captures so that the normal
compile -> index pipeline can ingest them.

NO scheduling, background loops, or agent orchestration here — that is WI12.
All public functions are purely callable (synchronous, no async).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .captures import write_inbox_capture
from .config import Settings
from .ingest_adapters import MirroredEntry, get_ingest_adapter
from .registry import ExternalTarget


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
