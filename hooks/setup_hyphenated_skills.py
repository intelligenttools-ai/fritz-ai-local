#!/usr/bin/env python3
"""Generate per-platform skill name variants from a single PLAIN source.

The repo's `skills/` directory is the single source of truth and uses PLAIN
names: every subdir that contains a `SKILL.md` is a portable skill, e.g.
`brain-compile`, `brain-query`, `handover`, `update`, `brain-save`.

Different agents accept different name shapes, so the generator emits a
per-platform variant by PREFIXING the plain base name:

  - claude / codex namespace -> `fritz:<plain>`  (colon prefix)
  - pi (installs to ~/.agents/skills) -> `fritz-<plain>`  (hyphen prefix)

Each emitted SKILL.md rewrites THREE things consistently:
  (a) the directory name        -> `<prefix><plain>`
  (b) the `name:` frontmatter    -> `<prefix><plain>`
  (c) intra-skill slash refs     -> `/<plain>` becomes `/<prefix><plain>`

A consistency validator (`validate_variant` / `validate_variants`) verifies
that a generated tree is internally consistent and carries no stale
wrong-platform references.

Usage:
    setup_hyphenated_skills.py <out_dir> --platform <claude|codex|pi> [--dry-run]
    setup_hyphenated_skills.py --validate <dir> --platform <claude|codex|pi>

Example:
    setup_hyphenated_skills.py ~/.agents/skills/ --platform pi
"""

import os
import re
import sys
from pathlib import Path

# Platform -> prefix applied to the plain base name. claude and codex share the
# colon namespace; pi uses the hyphen form because its runtime rejects colons.
PLATFORM_PREFIXES = {
    "claude": "fritz:",
    "codex": "fritz:",
    "pi": "fritz-",
}


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


def _platform_prefix(platform: str) -> str:
    """Return the name prefix for a platform, raising on an unknown one."""
    try:
        return PLATFORM_PREFIXES[platform]
    except KeyError:
        raise ValueError(
            f"unknown platform {platform!r}; expected one of "
            f"{sorted(PLATFORM_PREFIXES)}"
        )


def _iter_source_skills(repo_skills: Path):
    """Yield (plain_name, skill_md_path) for every plain source skill."""
    for skill_path in sorted(repo_skills.iterdir()):
        if not skill_path.is_dir():
            continue
        skill_file = skill_path / "SKILL.md"
        if not skill_file.exists():
            continue
        yield skill_path.name, skill_file


def _transform_content(content: str, plain_names: list[str], prefix: str) -> str:
    """Rewrite the `name:` field and intra-skill slash refs for one variant.

    - `name: <plain>` (first frontmatter occurrence) becomes `name: <prefix><plain>`.
    - Each `/<plain>` slash reference becomes `/<prefix><plain>`.

    Only the known plain skill names are rewritten so unrelated tokens (e.g.
    project folder names like `fritz-ai/`) are left untouched.
    """
    transformed = content

    # (b) name: frontmatter field. Rewrite the first matching plain name only.
    def _name_repl(match: re.Match) -> str:
        lead, value = match.group(1), match.group(2)
        if value in plain_names:
            return f"{lead}{prefix}{value}"
        return match.group(0)

    transformed = re.sub(
        r"^(name:\s+)([A-Za-z0-9:-]+)\s*$",
        _name_repl,
        transformed,
        count=1,
        flags=re.MULTILINE,
    )

    # (c) intra-skill slash references. Longest names first so e.g. brain-query
    # is matched before any shorter prefix could partially apply. The trailing
    # boundary stops `/brain-query` from matching inside `/brain-query-foo`.
    # The leading negative lookbehind `(?<![\w/])` ensures only genuine slash
    # COMMAND references are rewritten: a slash preceded by a word char or
    # another slash is part of a filesystem path (e.g. `skills/brain-setup`)
    # and must be left untouched.
    for plain in sorted(plain_names, key=len, reverse=True):
        transformed = re.sub(
            rf"(?<![\w/])/{re.escape(plain)}(?![A-Za-z0-9-])",
            f"/{prefix}{plain}",
            transformed,
        )

    return transformed


def generate_variants(out_dir: Path, platform: str, dry_run: bool = False) -> list[str]:
    """Generate per-platform name variants of every plain source skill.

    Reads the PLAIN source skills from `<repo>/skills/` (resolved via
    FRITZ_REPO_PATH or this file's location) and writes a variant of each into
    `out_dir`, with the directory name, `name:` frontmatter, and intra-skill
    slash references rewritten to carry the platform prefix.

    Args:
        out_dir: destination skills directory.
        platform: one of ``claude``, ``codex``, ``pi``.
        dry_run: when True, only describe what would be written.

    Returns a list of created file paths (or dry-run descriptions).
    """
    prefix = _platform_prefix(platform)

    repo_skills = _resolve_repo_root() / "skills"
    if not repo_skills.is_dir():
        print(f"Error: skill source directory not found: {repo_skills}", file=sys.stderr)
        sys.exit(1)

    sources = list(_iter_source_skills(repo_skills))
    plain_names = [name for name, _ in sources]

    created: list[str] = []
    for plain, skill_file in sources:
        variant_name = f"{prefix}{plain}"
        target_dir = out_dir / variant_name

        content = skill_file.read_text(encoding="utf-8")
        transformed = _transform_content(content, plain_names, prefix)

        if dry_run:
            created.append(f"  Would create: {target_dir}/SKILL.md")
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "SKILL.md").write_text(transformed, encoding="utf-8")
            created.append(str(target_dir / "SKILL.md"))

    return created


def _bases_in_tree(out_dir: Path, prefix: str) -> list[str]:
    """Return the plain skill bases present in a generated tree.

    Derived from the variant directory names by stripping the platform prefix.
    Used to scope stale-reference detection to real skill slash commands and
    avoid false positives on unrelated tokens (e.g. a `Projects/fritz-ai/`
    example folder).
    """
    bases: list[str] = []
    for d in out_dir.iterdir():
        if d.is_dir() and d.name.startswith(prefix) and (d / "SKILL.md").exists():
            bases.append(d.name[len(prefix):])
    return bases


def validate_variant(
    variant_dir: Path, platform: str, known_bases: list[str] | None = None
) -> list[str]:
    """Validate a single generated variant directory.

    Checks that the directory name equals the `name:` frontmatter and that both
    carry the platform prefix, and that no stale wrong-platform slash reference
    to a known skill remains. Returns a list of human-readable error strings
    (empty == valid).

    ``known_bases`` is the set of plain skill bases to scope stale-reference
    detection. When omitted, it is derived from this variant directory plus its
    siblings so a single-dir call still works.
    """
    prefix = _platform_prefix(platform)
    errors: list[str] = []

    dir_name = variant_dir.name
    skill_file = variant_dir / "SKILL.md"
    if not skill_file.exists():
        return [f"{dir_name}: missing SKILL.md"]

    if not dir_name.startswith(prefix):
        errors.append(f"{dir_name}: directory name missing prefix {prefix!r}")

    content = skill_file.read_text(encoding="utf-8")

    match = re.search(r"^name:\s+(.+?)\s*$", content, flags=re.MULTILINE)
    if not match:
        errors.append(f"{dir_name}: no name: frontmatter field")
    else:
        name_value = match.group(1)
        if name_value != dir_name:
            errors.append(
                f"{dir_name}: name: {name_value!r} does not match directory name"
            )

    if known_bases is None:
        known_bases = _bases_in_tree(variant_dir.parent, prefix)
        if dir_name.startswith(prefix):
            known_bases.append(dir_name[len(prefix):])

    # No stale wrong-platform slash reference to a known skill. The wrong prefix
    # is the one this platform does NOT use.
    wrong_prefix = "fritz-" if prefix == "fritz:" else "fritz:"
    for base in set(known_bases):
        stale_ref = f"/{wrong_prefix}{base}"
        if stale_ref in content:
            errors.append(
                f"{dir_name}: stale wrong-platform slash reference {stale_ref!r}"
            )

    return errors


def validate_variants(out_dir: Path, platform: str) -> list[str]:
    """Validate every variant directory under ``out_dir``.

    Returns the concatenation of all per-variant errors (empty == all valid).
    """
    prefix = _platform_prefix(platform)
    known_bases = _bases_in_tree(out_dir, prefix)
    errors: list[str] = []
    for variant_dir in sorted(out_dir.iterdir()):
        if not variant_dir.is_dir():
            continue
        if not (variant_dir / "SKILL.md").exists():
            continue
        errors.extend(validate_variant(variant_dir, platform, known_bases))
    return errors


def _parse_args(argv: list[str]) -> dict:
    args = {"out_dir": None, "platform": None, "dry_run": False, "validate": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--dry-run":
            args["dry_run"] = True
        elif a == "--platform":
            i += 1
            args["platform"] = argv[i] if i < len(argv) else None
        elif a == "--validate":
            i += 1
            args["validate"] = argv[i] if i < len(argv) else None
        elif args["out_dir"] is None and not a.startswith("--"):
            args["out_dir"] = a
        i += 1
    return args


def _usage() -> None:
    print(
        "Usage:\n"
        "  setup_hyphenated_skills.py <out_dir> --platform <claude|codex|pi> [--dry-run]\n"
        "  setup_hyphenated_skills.py --validate <dir> --platform <claude|codex|pi>",
        file=sys.stderr,
    )


def main() -> None:
    args = _parse_args(sys.argv[1:])

    platform = args["platform"]
    if platform is not None and platform not in PLATFORM_PREFIXES:
        print(
            f"Error: unknown platform {platform!r}; expected one of "
            f"{sorted(PLATFORM_PREFIXES)}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Validation mode.
    if args["validate"] is not None:
        if not platform:
            _usage()
            sys.exit(1)
        target = Path(args["validate"]).expanduser().resolve()
        if not target.is_dir():
            print(f"Error: directory not found: {target}", file=sys.stderr)
            sys.exit(1)
        errors = validate_variants(target, platform)
        if errors:
            print(f"Validation FAILED ({len(errors)} issue(s)):", file=sys.stderr)
            for err in errors:
                print(f"  {err}", file=sys.stderr)
            sys.exit(1)
        print(f"Validation OK: all variants in {target} are consistent.")
        return

    # Generation mode.
    if not args["out_dir"] or not platform:
        _usage()
        sys.exit(1)

    out_dir = Path(args["out_dir"]).expanduser().resolve()
    if not out_dir.is_dir():
        print(f"Error: output directory not found: {out_dir}", file=sys.stderr)
        sys.exit(1)

    created = generate_variants(out_dir, platform, args["dry_run"])

    if created:
        prefix = " [DRY RUN] " if args["dry_run"] else " "
        print(f"{prefix.strip()} Created {len(created)} {platform} skill variant(s):")
        for path in created:
            print(f"  {path}")
    else:
        print("No source skills found to generate.")


if __name__ == "__main__":
    main()
