"""Operation log helpers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def append_global_log(brain_home: Path, operation: str, summary: str, dry_run: bool) -> None:
    if dry_run:
        return
    log_path = brain_home / "log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} | {operation} | local-brain | {summary}\n")


def append_vault_log(vault_path: Path, operation: str, summary: str, dry_run: bool) -> None:
    if dry_run:
        return
    log_path = vault_path / ".brain" / "log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} | {operation} | local-brain | {summary}\n")


def append_reconciliation_undo(brain_home: Path, record: dict, dry_run: bool) -> None:
    """Append one JSON line to ``<brain_home>/reconciliation-undo.jsonl``.

    The *record* must contain enough to undo the operation, at minimum::

        {
            "ts": "<iso-timestamp>",
            "verdict": "<verdict-type>",
            "new_path": "<rel-path>",
            "old_path": "<rel-path>",
            "old_prior_status": "<status-before>",
            "links_added": {"<fm-key>": ["<value>", ...]},
        }

    No-op when ``dry_run`` is True.
    """
    if dry_run:
        return
    undo_path = brain_home / "reconciliation-undo.jsonl"
    undo_path.parent.mkdir(parents=True, exist_ok=True)
    with undo_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_reconciliation_undo(brain_home: Path) -> list[dict]:
    """Read all undo records from ``<brain_home>/reconciliation-undo.jsonl``.

    Returns an empty list if the file does not exist or is empty.
    """
    undo_path = brain_home / "reconciliation-undo.jsonl"
    if not undo_path.exists():
        return []
    records: list[dict] = []
    for line in undo_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records
