"""Minimal markdown index maintenance."""

from __future__ import annotations

from pathlib import Path



def update_directory_index(target: Path, title: str, summary: str, dry_run: bool) -> None:
    if dry_run:
        return
    if target.name == "index.md":
        return
    index_path = target.parent / "index.md"
    rel = target.name
    line = f"- [{title}]({rel}) -- {summary.strip()}"
    if index_path.exists():
        existing = index_path.read_text(encoding="utf-8")
        if f"]({rel})" in existing:
            return
        content = existing.rstrip() + "\n" + line + "\n"
    else:
        content = f"# {target.parent.name}\n\n{line}\n"
    index_path.write_text(content, encoding="utf-8")
