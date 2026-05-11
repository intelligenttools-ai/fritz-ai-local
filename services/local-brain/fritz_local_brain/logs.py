"""Operation log helpers."""

from __future__ import annotations

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
