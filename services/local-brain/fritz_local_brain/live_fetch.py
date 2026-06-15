"""Live-fetch escape hatch for mirrored pointers (WI12).

A mirrored capture stores a stable ``pointer`` (format ``"<target.name>:<rest>"``)
in its front-matter. ``live_fetch`` resolves the LIVE content for that pointer on
demand — used to enrich index-only mirror hits at query time and for the rare
full-context need.

Deterministic; no LLM. Returns ``None`` whenever the target, pointer, or kind's
adapter cannot resolve live content (e.g. mcp/drive/offsite are not yet
implemented). Path-safe for local-vault: the resolved file must stay within the
connection directory and must not be a symlink.
"""

from __future__ import annotations

from pathlib import Path

from .config import Settings
from .registry import ExternalTarget, RegistryError, load_external_targets

# Cap on live-fetched content length (matches the full-content adapter cap).
_LIVE_FETCH_CAP = 50_000


def _split_pointer(pointer: str) -> tuple[str, str] | None:
    """Split ``"<target.name>:<rest>"`` into ``(name, rest)``.

    Returns ``None`` when the pointer has no ``":"`` separator or either side is
    empty.
    """
    if not pointer or ":" not in pointer:
        return None
    name, _, rest = pointer.partition(":")
    name = name.strip()
    rest = rest.strip()
    if not name or not rest:
        return None
    return name, rest


def _local_vault_live_fetch(target: ExternalTarget, relpath: str) -> str | None:
    """Read the live file at ``<connection>/<relpath>`` safely, or ``None``.

    Path-safety: the resolved file must stay within the resolved connection
    directory, must be a regular file, and must not be (or be reached through) a
    symlink.
    """
    if not target.connection:
        return None

    vault_path = Path(target.connection).expanduser()
    try:
        if not vault_path.is_dir():
            return None
        vault_root = vault_path.resolve()
    except OSError:
        return None

    candidate = vault_path / relpath

    # Reject symlinked files outright.
    try:
        if candidate.is_symlink():
            return None
    except OSError:
        return None

    # Containment: the resolved candidate must stay within the resolved root.
    try:
        resolved = candidate.resolve()
        resolved.relative_to(vault_root)
    except (OSError, ValueError):
        return None

    try:
        if not resolved.is_file():
            return None
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    return text[:_LIVE_FETCH_CAP]


def live_fetch(settings: Settings, pointer: str) -> str | None:
    """Resolve the live content for a mirrored *pointer*.

    Parameters
    ----------
    settings:
        Runtime settings (provides ``brain_home`` to locate external targets).
    pointer:
        A mirror pointer of the form ``"<target.name>:<rest>"``.

    Returns
    -------
    str | None
        The live content, or ``None`` when the target/pointer cannot be resolved
        or the target kind's adapter cannot live-fetch (mcp/drive/offsite).
    """
    parts = _split_pointer(pointer)
    if parts is None:
        return None
    target_name, rest = parts

    try:
        targets = load_external_targets(settings.brain_home)
    except RegistryError:
        return None

    target = next((t for t in targets if t.name == target_name), None)
    if target is None:
        return None

    if target.kind == "local-vault":
        return _local_vault_live_fetch(target, rest)

    # mcp / drive / offsite adapters cannot live-fetch yet.
    return None
