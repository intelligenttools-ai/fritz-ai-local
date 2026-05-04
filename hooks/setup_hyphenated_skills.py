#!/usr/bin/env python3
"""Generate hyphenated skill copies for agents that cannot handle colons.

The repo uses `fritz:*` (colon) in directory names and SKILL.md frontmatter.
This is the single source of truth — changing it would break every existing
installation.

Agents whose runtime requires skill names to match the parent directory name
and only accept `lowercase a-z, 0-9, hyphens` (no colons) must create local
hyphenated copies during setup.

Usage:
    python3 setup_hyphenated_skills.py <skills_dir> [--dry-run]

Example:
    python3 setup_hyphenated_skills.py ~/.agents/skills/
"""

import os
import re
import shutil
import sys
from pathlib import Path


def generate_hyphenated(repo_skills: Path, skills_dir: Path, dry_run: bool = False) -> list[str]:
    """Generate hyphenated copies of all fritz:* skills.

    For each skill in <repo>/skills/fritz:*/SKILL.md, create a local copy at
    <skills_dir>/fritz-*/SKILL.md with:
      - Directory name: colon replaced by hyphen
      - SKILL.md frontmatter: `name:` field uses hyphens
      - Slash commands inside text: `/fritz:*` → `/fritz-*`

    Returns a list of created file paths (or dry-run descriptions).
    """
    # Resolve the repo path explicitly — __file__ resolves through symlinks,
    # so we can't rely on parent traversal. Use the canonical path.
    import os
    repo_path = os.environ.get("FRITZ_REPO_PATH", str(Path.home() / ".fritz-ai-local"))
    repo_skills = Path(repo_path).resolve() / "skills"

    if not repo_skills.is_dir():
        print(f"Error: skill source directory not found: {repo_skills}", file=sys.stderr)
        sys.exit(1)

    created = []

    for skill_path in sorted(repo_skills.iterdir()):
        if not skill_path.is_dir():
            continue

        name = skill_path.name
        # Only process fritz:* prefixed directories (colon variant)
        if not name.startswith("fritz:"):
            continue

        skill_file = skill_path / "SKILL.md"
        if not skill_file.exists():
            continue

        hyphen_name = name.replace(":", "-")
        target_dir = skills_dir / hyphen_name

        # Read the source SKILL.md
        content = skill_file.read_text()

        # Transform: name field and slash commands
        transformed = content
        transformed = re.sub(
            r"^(name:\s+)fritz:",
            rf"\g<1>fritz-",
            transformed,
            flags=re.MULTILINE,
        )
        # Replace all /fritz:brain-* references with hyphenated versions
        # Replace all /fritz:* slash commands with hyphenated versions
        transformed = re.sub(
            r"/fritz:(brain-|update|handover)",
            r"/fritz-\1",
            transformed,
        )

        if dry_run:
            created.append(f"  Would create: {target_dir}/SKILL.md")
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "SKILL.md").write_text(transformed)
            created.append(str(target_dir / "SKILL.md"))

    return created


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args

    if len(args) < 1 or (len(args) == 1 and args[0] == "--dry-run"):
        print("Usage: setup_hyphenated_skills.py <skills_dir> [--dry-run]", file=sys.stderr)
        sys.exit(1)

    skills_dir = Path(args[0]).expanduser().resolve()
    if not skills_dir.is_dir():
        print(f"Error: skills directory not found: {skills_dir}", file=sys.stderr)
        sys.exit(1)

    repo_skills = Path.home() / ".fritz-ai-local"
    created = generate_hyphenated(repo_skills, skills_dir, dry_run)

    if created:
        print(f"{'[DRY RUN] ' if dry_run else ''}Created {len(created)} hyphenated skill(s):")
        for path in created:
            print(f"  {path}")
    else:
        print("No fritz:* skills found to copy.")


if __name__ == "__main__":
    main()
