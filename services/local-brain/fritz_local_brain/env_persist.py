"""Durable .env persistence for runtime config changes (#208).

Runtime-mutable config changes are applied to the live ``get_settings()``
singleton immediately, but the container reads its ``.env`` only at startup.
Persisting the changed keys here keeps a live edit durable across a restart.

This mirrors the parse/merge/write behaviour of the provisioning engine
(``scripts/provision_engine.py``) but is package-internal and dependency-free so
the running service can persist without importing the out-of-package script.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# The set of env-file candidates the Settings model reads, in precedence order.
# We persist to the FIRST candidate that already exists; otherwise the first
# candidate is created. This matches Settings.model_config env_file tuple.
_ENV_FILE_CANDIDATES = (".env", "../../.env")

_ENV_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def resolve_env_path() -> Path:
    """Resolve the .env file to persist to (first existing candidate, else first)."""
    for candidate in _ENV_FILE_CANDIDATES:
        p = Path(candidate)
        if p.exists():
            return p
    return Path(_ENV_FILE_CANDIDATES[0])


def _dotenv_line(key: str, value: str) -> str:
    if not _ENV_KEY_PATTERN.fullmatch(key):
        raise ValueError(f"Unsafe .env key: {key}")
    if any(char in value for char in "\r\n\0"):
        raise ValueError(f"Unsafe newline/NUL in .env value for {key}")
    if value == "" or re.fullmatch(r"[A-Za-z0-9_./:@%+=,-]+", value):
        return f"{key}={value}"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{key}="{escaped}"'


def merge_dotenv(existing_text: str, updates: dict[str, str]) -> str:
    """Merge ``updates`` into ``existing_text``, replacing in-place / appending new."""
    lines = existing_text.splitlines()
    replaced: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or not stripped or "=" not in stripped:
            out.append(line)
            continue
        key, _, _val = stripped.partition("=")
        key = key.strip()
        if key in updates:
            out.append(_dotenv_line(key, updates[key]))
            replaced.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in replaced:
            out.append(_dotenv_line(key, value))
    result = "\n".join(out)
    if not result.endswith("\n"):
        result += "\n"
    return result


def persist_env_updates(updates: dict[str, str], env_path: Path | None = None) -> Path:
    """Merge ``updates`` into the resolved .env and write it back (mode 0600).

    Returns the path written. A no-op (empty ``updates``) still resolves and
    returns the path without writing.
    """
    path = env_path or resolve_env_path()
    if not updates:
        return path
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    merged = merge_dotenv(existing, updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(merged, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # best-effort on filesystems that reject chmod
    return path
