"""Shared helpers for Local Brain MCP token shell wiring.

The managed ``~/.zshenv`` block must work in a virgin login environment. Keep
the shell snippet dependency-free: absolute system tools only, no Python, no
PyYAML, and no assumptions about PATH beyond ``/usr/bin``.
"""

from __future__ import annotations

import re


BLOCK_START = "# >>> fritz-local brain token >>>"
BLOCK_END = "# <<< fritz-local brain token <<<"
CLAUDE_MCP_TOKEN_ENV = "LOCAL_BRAIN_API_TOKEN"


def valid_env_name(name: str) -> bool:
    return bool(isinstance(name, str) and re.fullmatch(r"[A-Z_][A-Z0-9_]*", name))


def token_env_names(token_env: str) -> list[str]:
    """Return env vars to wire.

    ``api_token_env`` remains supported for generic service callers, but the
    Claude plugin MCP header is fixed to ``LOCAL_BRAIN_API_TOKEN``. Mirror the
    same registry-backed token into both when an install uses a custom env var.
    """
    primary = token_env if valid_env_name(token_env) else CLAUDE_MCP_TOKEN_ENV
    names = [primary]
    if CLAUDE_MCP_TOKEN_ENV not in names:
        names.append(CLAUDE_MCP_TOKEN_ENV)
    return names


REGISTRY_TOKEN_READER = (
    "$(/usr/bin/sed -E -n "
    "'/^[[:space:]]*local_brain_service:[[:space:]]*$/,/^([^[:space:]]|  [^[:space:]])/ "
    "s/^[[:space:]][[:space:]][[:space:]][[:space:]]*api_token:[[:space:]]*//p' "
    "\"$HOME/.brain/registry.yaml\" 2>/dev/null | /usr/bin/head -n 1 | "
    "/usr/bin/tr -d \"\\\"'\\r\")"
)


def _export_from_registry_lines(name: str) -> list[str]:
    return [
        f'if [ -z "${{{name}:-}}" ]; then',
        f'  export {name}="{REGISTRY_TOKEN_READER}"',
        "fi",
        f'if [ -z "${{{name}:-}}" ]; then unset {name}; fi',
    ]


def build_zshenv_block(token_env: str) -> str:
    """Return the managed ``~/.zshenv`` block (no trailing newline, no secret)."""
    lines = [
        BLOCK_START,
        "# Managed by fritz-local. Exports the Local Brain MCP token from",
        "# ~/.brain/registry.yaml so plugin MCP headers can authenticate.",
        "# Do not edit by hand; re-run /fritz-brain:update.",
    ]
    for name in token_env_names(token_env):
        lines.extend(_export_from_registry_lines(name))
    lines.append(BLOCK_END)
    return "\n".join(lines)


def upsert_zshenv_block(text: str, token_env: str) -> tuple[str, bool]:
    """Insert or replace the managed block, preserving all other user content."""
    block = build_zshenv_block(token_env)
    lines = text.splitlines()
    kept: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == BLOCK_START:
            j = i + 1
            while j < len(lines) and lines[j].strip() != BLOCK_END:
                j += 1
            i = j + 1  # skip the end marker too
            continue
        kept.append(lines[i])
        i += 1
    while kept and kept[-1].strip() == "":
        kept.pop()
    body = "\n".join(kept)
    new_text = (body + "\n\n" + block + "\n") if body else (block + "\n")
    return new_text, new_text != text
