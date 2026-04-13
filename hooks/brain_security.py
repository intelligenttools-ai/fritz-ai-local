#!/usr/bin/env python3
"""Brain security enforcement — validates operations against vault security tiers.

Can be called by other hooks or skills to check if an operation is allowed.
Not registered as a standalone hook — it's a library used by brain_capture.py
and brain_prompt_check.py.

Security tiers:
- Tier 0 (Read): Any agent can read any file. Default.
- Tier 1 (Capture): Write to capture paths, append to log.md.
- Tier 2 (Knowledge): Create/update knowledge articles, update index.
- Tier 3 (Structure): Modify manifest, schema, identity files. Human only.
"""

import sys
from pathlib import Path
from fnmatch import fnmatch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brain_common import load_manifest


def is_excluded(file_path: str, vault_path: Path, manifest: dict) -> bool:
    """Check if a file path matches any exclusion pattern in the manifest."""
    excludes = manifest.get("exclude", [])
    if not excludes:
        return False

    # Normalize to relative path within vault
    try:
        rel = Path(file_path).resolve().relative_to(vault_path.resolve())
        rel_str = str(rel)
    except ValueError:
        return False

    for pattern in excludes:
        if fnmatch(rel_str, pattern) or fnmatch(rel_str, f"*/{pattern}"):
            return True
        # Also check if any parent directory matches
        for part in Path(rel_str).parts:
            if fnmatch(part, pattern.rstrip("/")):
                return True

    return False


def check_tier(operation: str, file_path: str, vault_path: Path, manifest: dict) -> tuple[bool, str]:
    """Check if an operation is allowed on a file.

    Returns (allowed, reason).
    """
    if is_excluded(file_path, vault_path, manifest):
        return False, f"File matches exclusion pattern in manifest"

    # Resolve paths from manifest
    paths = manifest.get("paths", {})

    try:
        rel = str(Path(file_path).resolve().relative_to(vault_path.resolve()))
    except ValueError:
        return True, "File outside vault — no tier enforcement"

    # Tier 3: structure files — human only
    structure_files = ["manifest.yaml", "schema.md"]
    for sf in structure_files:
        if rel.endswith(sf) and ".brain/" in rel:
            if operation in ("write", "edit", "delete"):
                return False, f"Tier 3: {sf} can only be modified by human or admin agent"

    # Identity files
    for key in ("soul", "user", "memory"):
        identity_path = paths.get(key, "")
        if identity_path and rel == identity_path:
            if operation in ("write", "edit", "delete"):
                return False, f"Tier 3: identity file {key} can only be modified by human or admin"

    # Tier 2: knowledge — trusted agents
    knowledge_path = paths.get("knowledge", "")
    if knowledge_path and rel.startswith(knowledge_path):
        if operation in ("write", "edit"):
            return True, "Tier 2: knowledge write allowed for trusted agents"

    # Tier 1: capture — any agent
    for capture_key in ("capture_daily", "capture_sessions", "capture_inbox"):
        capture_path = paths.get(capture_key, "")
        if capture_path and rel.startswith(capture_path):
            return True, "Tier 1: capture write allowed"

    # Tier 0: read — always allowed
    if operation == "read":
        return True, "Tier 0: read always allowed"

    # Default: allow with warning
    return True, "No tier restriction matched"
