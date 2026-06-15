"""Read-direction ingest adapters for mirror-as-ingest (WI11).

Each adapter fetches content from an external target and returns a list of
``MirroredEntry`` objects.  The entries are fed through the normal
capture -> compile -> index pipeline by ``mirror.py`` via
``captures.write_inbox_capture``.

This module is intentionally READ-ONLY w.r.t. the brain store: it only
produces ``MirroredEntry`` objects; no files are written here.

Adapter hierarchy
-----------------
``IngestAdapter`` (base class)
  └── ``LocalVaultIngestAdapter`` — reads Markdown files from a local dir
  └── (future) ``MCPIngestAdapter``, ``DriveIngestAdapter``, ...

Placement note
--------------
This module lives in the SERVICE package ``fritz_local_brain/`` and is NOT
related to the repo-root ``adapters/`` package (which handles agent-transcript
parsing).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .registry import ExternalTarget

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

# Maximum number of characters read from a file in "full" mode.
_FULL_CONTENT_CAP = 50_000

# Number of characters used for the summary snippet in "index-only" mode.
_SUMMARY_SNIPPET_CHARS = 200


@dataclass
class MirroredEntry:
    """A single piece of mirrored content ready to be written as an inbox capture.

    Attributes
    ----------
    pointer:
        Stable identifier for the source item.  Format depends on the adapter;
        for ``LocalVaultIngestAdapter`` it is ``"<target-name>:<relative-path>"``.
    title:
        Human-readable title extracted from the content or inferred from the
        filename.
    content:
        The body text to embed in the inbox capture.  For ``mode="full"`` this
        is the full (possibly truncated) file text; for ``mode="summary"`` it is
        a short snippet.
    mode:
        ``"full"`` (mirror_mode == "full-summary") or
        ``"summary"`` (mirror_mode == "index-only").
    """

    pointer: str
    title: str
    content: str
    mode: str  # "summary" | "full"


# ---------------------------------------------------------------------------
# Base adapter
# ---------------------------------------------------------------------------


class MirrorError(RuntimeError):
    """Raised for unrecoverable mirror-adapter errors."""


class IngestAdapter:
    """Base class for read-direction ingest adapters.

    Mirrors the sync-adapter idea (which pushes content OUT of the brain) but
    in the READ direction: it pulls content FROM an external system and returns
    a list of ``MirroredEntry`` objects for downstream capture ingestion.

    Subclasses must set the ``kind`` class attribute and override ``fetch``.
    """

    kind: str = ""

    def fetch(self, target: ExternalTarget) -> list[MirroredEntry]:
        """Fetch entries from *target* and return them as ``MirroredEntry`` objects.

        This method must be deterministic for the same target state — repeated
        calls with unchanged external content must return entries in the same
        order.  Implementations should handle missing/inaccessible sources
        gracefully by returning an empty list rather than raising.

        Parameters
        ----------
        target:
            The validated ``ExternalTarget`` describing the external system.

        Returns
        -------
        list[MirroredEntry]
            Zero or more mirrored entries.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Local-vault adapter
# ---------------------------------------------------------------------------

_H1_RE = re.compile(r"^#\s+(.+)", re.MULTILINE)


def _extract_title(text: str, stem: str) -> str:
    """Return the first ``# heading`` found in *text*, falling back to *stem*."""
    match = _H1_RE.search(text)
    if match:
        return match.group(1).strip()
    return stem


class LocalVaultIngestAdapter(IngestAdapter):
    """Ingest adapter for a local Markdown vault (a directory of ``.md`` files).

    Pointer format
    --------------
    ``"<target.name>:<relative-path>"``

    Example: ``"team-vault:projects/foo.md"``

    Mode mapping
    ------------
    - ``mirror_mode == "full-summary"`` → reads the full file text (capped at
      ``_FULL_CONTENT_CAP`` characters), ``mode = "full"``.
    - ``mirror_mode == "index-only"``  → title + first ``_SUMMARY_SNIPPET_CHARS``
      characters of the file, ``mode = "summary"``.

    Safety
    ------
    - Symlinks are skipped.
    - Files named ``index.md`` are skipped (they are auto-generated MOC files).
    - A missing or unreadable connection path returns ``[]`` without raising.
    """

    kind = "local-vault"

    def fetch(self, target: ExternalTarget) -> list[MirroredEntry]:  # type: ignore[override]
        """Fetch all ``.md`` files from the vault directory described by *target*.

        Returns an empty list when the ``connection`` path is absent, not a
        directory, or otherwise inaccessible.
        """
        if not target.connection:
            return []

        vault_path = Path(target.connection).expanduser()
        try:
            if not vault_path.is_dir():
                return []
        except OSError:
            return []

        entries: list[MirroredEntry] = []
        # Deterministic sorted order (resolved relative path string).
        try:
            vault_root = vault_path.resolve()
            md_files = sorted(
                vault_path.rglob("*.md"),
                key=lambda p: str(p.relative_to(vault_path)),
            )
        except OSError:
            return []

        for path in md_files:
            # Skip symlinks and index.md files.
            if path.is_symlink():
                continue
            if path.name == "index.md":
                continue
            if not path.is_file():
                continue

            # Defense-in-depth: verify resolved file stays within vault root.
            try:
                resolved_file = path.resolve()
                resolved_file.relative_to(vault_root)
            except (OSError, ValueError):
                continue

            try:
                rel = path.relative_to(vault_path)
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            pointer = f"{target.name}:{rel}"
            title = _extract_title(text, path.stem)

            if target.mirror_mode == "full-summary":
                content = text[:_FULL_CONTENT_CAP]
                mode = "full"
            else:
                # index-only: title + snippet
                snippet = text[:_SUMMARY_SNIPPET_CHARS]
                content = f"{title}\n\n{snippet}" if snippet else title
                mode = "summary"

            entries.append(MirroredEntry(pointer=pointer, title=title, content=content, mode=mode))

        return entries


# ---------------------------------------------------------------------------
# Adapter registry / factory
# ---------------------------------------------------------------------------

_NOT_YET_IMPLEMENTED_KINDS = frozenset({"mcp", "drive", "offsite"})


def get_ingest_adapter(kind: str) -> IngestAdapter:
    """Return the ``IngestAdapter`` instance for the given *kind*.

    Parameters
    ----------
    kind:
        One of the declared external-target kinds
        (``"local-vault"``, ``"mcp"``, ``"drive"``, ``"offsite"``).

    Returns
    -------
    IngestAdapter
        A ready-to-use adapter instance.

    Raises
    ------
    NotImplementedError
        For ``"mcp"``, ``"drive"``, and ``"offsite"`` — those adapters are
        scheduled for a future work item (WI12+).
    MirrorError
        For unknown / unrecognised kind strings.
    """
    if kind == "local-vault":
        return LocalVaultIngestAdapter()
    if kind in _NOT_YET_IMPLEMENTED_KINDS:
        raise NotImplementedError(
            f"Ingest adapter for kind {kind!r} is not yet implemented. "
            "Only 'local-vault' is available in this release; 'mcp', 'drive', "
            "and 'offsite' adapters are scheduled for a future work item."
        )
    raise MirrorError(
        f"Unknown ingest adapter kind: {kind!r}. "
        f"Valid kinds are: 'local-vault', 'mcp', 'drive', 'offsite'."
    )
