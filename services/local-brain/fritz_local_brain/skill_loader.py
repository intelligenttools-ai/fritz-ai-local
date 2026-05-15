"""Load Fritz skill markdown from the repository skill directory."""

from __future__ import annotations

from pathlib import Path


def load_skill(skills_dir: Path, skill_name: str) -> str:
    skill_path = skills_dir / skill_name / "SKILL.md"
    if not skill_path.exists():
        raise FileNotFoundError(f"Skill not found: {skill_path}")
    return skill_path.read_text(encoding="utf-8")
