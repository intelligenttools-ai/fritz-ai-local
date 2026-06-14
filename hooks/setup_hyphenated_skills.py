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


def _resolve_repo_root() -> Path:
    """Resolve the Fritz repo root, independent of clone location.

    Honors FRITZ_REPO_PATH if set, else derives the root from this file's
    location. Path(__file__).resolve() follows symlinks, so a hook symlinked
    into ~/.brain/hooks/ still resolves back to the real repo root (the parent
    of the hooks/ directory).
    """
    env_path = os.environ.get("FRITZ_REPO_PATH")
    if env_path and env_path.strip():
        return Path(env_path).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def generate_hyphenated(repo_skills: Path, skills_dir: Path, dry_run: bool = False) -> list[str]:
    """Generate hyphenated copies of all fritz:* skills.

    For each skill in <repo>/skills/fritz:*/SKILL.md, create a local copy at
    <skills_dir>/fritz-*/SKILL.md with:
      - Directory name: colon replaced by hyphen
      - SKILL.md frontmatter: `name:` field uses hyphens
      - Slash commands inside text: `/fritz:*` → `/fritz-*`

    The repo_skills argument is ignored; the source skills directory is resolved
    from FRITZ_REPO_PATH or this file's location. The parameter is retained for
    backward-compatible call signatures.

    Returns a list of created file paths (or dry-run descriptions).
    """
    # Resolve the repo path independent of clone location. FRITZ_REPO_PATH wins
    # if set; otherwise derive the repo root from this file's own location.
    # Path(__file__).resolve() follows symlinks, so this works even when the
    # hook is symlinked into ~/.brain/hooks/ — it resolves back to the real repo.
    repo_skills = _resolve_repo_root() / "skills"

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

    # repo_skills is resolved inside generate_hyphenated() from FRITZ_REPO_PATH
    # or this file's location; the argument is kept for signature stability.
    created = generate_hyphenated(None, skills_dir, dry_run)

    if created:
        print(f"{'[DRY RUN] ' if dry_run else ''}Created {len(created)} hyphenated skill(s):")
        for path in created:
            print(f"  {path}")
    else:
        print("No fritz:* skills found to copy.")


if __name__ == "__main__":
    main()
