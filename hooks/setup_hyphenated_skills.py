#!/usr/bin/env python3
"""Generate per-platform skill name variants from a single PLAIN source.

The repo's `skills/` directory is the single source of truth and uses PLAIN
names: every subdir that contains a `SKILL.md` is a portable skill, e.g.
`brain-compile`, `brain-query`, `handover`, `update`, `brain-save`.

Different agents accept different name shapes, so the generator emits a
per-platform variant by rewriting the plain base name and slash references:

  - claude plugin -> directory/name `<plain>`, slash refs `/fritz-brain:<plain>`
  - codex namespace -> `fritz:<plain>`  (colon prefix)
  - pi (installs to ~/.agents/skills) -> `fritz-<plain>`  (hyphen prefix)

Each emitted SKILL.md rewrites THREE things consistently:
  (a) the directory name
  (b) the `name:` frontmatter
  (c) intra-skill slash refs

A consistency validator (`validate_variant` / `validate_variants`) verifies
that a generated tree is internally consistent and carries no stale
wrong-platform references.

Usage:
    setup_hyphenated_skills.py <out_dir> --platform <claude|codex|pi> [--dry-run]
    setup_hyphenated_skills.py --validate <dir> --platform <claude|codex|pi>

Example:
    setup_hyphenated_skills.py ~/.agents/skills/ --platform pi
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Platform -> directory/name prefix and slash-command prefix. Claude plugin
# skills are already namespaced by the plugin name (`fritz-brain`), so their
# on-disk skill names stay plain while emitted slash refs are plugin-qualified.
PLATFORM_NAMING = {
    "claude": {"name_prefix": "", "slash_prefix": "fritz-brain:"},
    "codex": {"name_prefix": "fritz:", "slash_prefix": "fritz:"},
    "pi": {"name_prefix": "fritz-", "slash_prefix": "fritz-"},
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


def _platform_naming(platform: str) -> dict[str, str]:
    """Return naming settings for a platform, raising on an unknown one."""
    try:
        return PLATFORM_NAMING[platform]
    except KeyError:
        raise ValueError(
            f"unknown platform {platform!r}; expected one of "
            f"{sorted(PLATFORM_NAMING)}"
        )


def _platform_prefix(platform: str) -> str:
    """Return the directory/name prefix for a platform."""
    return _platform_naming(platform)["name_prefix"]


def _iter_source_skills(repo_skills: Path):
    """Yield (plain_name, skill_md_path) for every plain source skill."""
    for skill_path in sorted(repo_skills.iterdir()):
        if not skill_path.is_dir():
            continue
        skill_file = skill_path / "SKILL.md"
        if not skill_file.exists():
            continue
        yield skill_path.name, skill_file


def _transform_content(
    content: str,
    plain_names: list[str],
    *,
    name_prefix: str,
    slash_prefix: str,
) -> str:
    """Rewrite the `name:` field and intra-skill slash refs for one variant.

    - `name: <plain>` (first frontmatter occurrence) becomes
      `name: <name_prefix><plain>`.
    - Each `/<plain>` slash reference becomes `/<slash_prefix><plain>`.

    Only the known plain skill names are rewritten so unrelated tokens (e.g.
    project folder names like `fritz-ai/`) are left untouched.
    """
    transformed = content

    # (b) name: frontmatter field. Rewrite the first matching plain name only.
    def _name_repl(match: re.Match) -> str:
        lead, value = match.group(1), match.group(2)
        if value in plain_names:
            return f"{lead}{name_prefix}{value}"
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
            f"/{slash_prefix}{plain}",
            transformed,
        )

    return transformed


def _apply_platform_guidance(content: str, platform: str) -> str:
    """Apply platform-specific instruction wording after name rewrites."""

    if platform != "codex":
        return content

    replacements = {
        (
            "use the service-backed semantic search path first: prefer the registered MCP tool "
            "`brain_search` when available and authorized, otherwise use `POST <base_url>/v1/search/run` "
            "from the host. This is the default brain-check path and uses the container-managed vector index "
            "when embeddings are enabled. Use `brain_query` or `POST <base_url>/v1/query/run` only for "
            "exact/read-only compatibility lookup when semantic search is unavailable, returns insufficient "
            "results, or the human explicitly asks for exact/raw lookup."
        ): (
            "use the service-backed semantic search path first: call `POST <base_url>/v1/search/run` "
            "from the host. Codex bindings do not register the Brain MCP tools natively; use `brain_search` "
            "only if the human explicitly configured that MCP tool in this session. This is the default "
            "brain-check path and uses the container-managed vector index when embeddings are enabled. "
            "Use `POST <base_url>/v1/query/run` only for exact/read-only compatibility lookup when semantic "
            "search is unavailable, returns insufficient results, or the human explicitly asks for exact/raw "
            "lookup; use `brain_query` only if it was explicitly configured in this session."
        ),
        (
            "use the service-backed compile path first: prefer the registered MCP tool `brain_compile` "
            "when available and authorized, otherwise use `POST <base_url>/v1/compile/run` from the host."
        ): (
            "use the service-backed compile path first: call `POST <base_url>/v1/compile/run` from the host. "
            "Codex bindings do not register the Brain MCP tools natively; use `brain_compile` only if the "
            "human explicitly configured that MCP tool in this session."
        ),
        (
            "use the service-backed lint path first: prefer the registered MCP tool `brain_lint` when "
            "available and authorized, otherwise use `POST <base_url>/v1/lint/run` from the host."
        ): (
            "use the service-backed lint path first: call `POST <base_url>/v1/lint/run` from the host. "
            "Codex bindings do not register the Brain MCP tools natively; use `brain_lint` only if the "
            "human explicitly configured that MCP tool in this session."
        ),
        (
            "use the service-backed sync path first: prefer the registered MCP tool `brain_sync` when "
            "available and authorized, otherwise use `POST <base_url>/v1/sync/run` from the host."
        ): (
            "use the service-backed sync path first: call `POST <base_url>/v1/sync/run` from the host. "
            "Codex bindings do not register the Brain MCP tools natively; use `brain_sync` only if the "
            "human explicitly configured that MCP tool in this session."
        ),
        (
            "use it for supported preservation steps: prefer the registered MCP tools `brain_compile` and "
            "`brain_sync` when available and authorized, otherwise use `POST <base_url>/v1/compile/run` and "
            "`POST <base_url>/v1/sync/run` from the host."
        ): (
            "use it for supported preservation steps: call `POST <base_url>/v1/compile/run` and "
            "`POST <base_url>/v1/sync/run` from the host. Codex bindings do not register the Brain MCP "
            "tools natively; use `brain_compile` or `brain_sync` only if the human explicitly configured "
            "those MCP tools in this session."
        ),
    }

    transformed = content
    for source, replacement in replacements.items():
        transformed = transformed.replace(source, replacement)
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
    naming = _platform_naming(platform)
    name_prefix = naming["name_prefix"]
    slash_prefix = naming["slash_prefix"]

    repo_skills = _resolve_repo_root() / "skills"
    if not repo_skills.is_dir():
        print(f"Error: skill source directory not found: {repo_skills}", file=sys.stderr)
        sys.exit(1)

    sources = list(_iter_source_skills(repo_skills))
    plain_names = [name for name, _ in sources]

    created: list[str] = []
    for plain, skill_file in sources:
        variant_name = f"{name_prefix}{plain}"
        target_dir = out_dir / variant_name

        content = skill_file.read_text(encoding="utf-8")
        transformed = _transform_content(
            content,
            plain_names,
            name_prefix=name_prefix,
            slash_prefix=slash_prefix,
        )
        transformed = _apply_platform_guidance(transformed, platform)

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

    if prefix and not dir_name.startswith(prefix):
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
        if prefix:
            known_bases.append(dir_name[len(prefix):])
        else:
            known_bases.append(dir_name)

    # No stale wrong-platform slash reference to a known skill command.
    slash_prefix = _platform_naming(platform)["slash_prefix"]
    candidate_slash_prefixes = {"", "fritz:", "fritz-", "fritz-brain:"}
    for base in set(known_bases):
        for candidate in candidate_slash_prefixes - {slash_prefix}:
            stale_ref = f"/{candidate}{base}"
            pattern = rf"(?<![\w/]){re.escape(stale_ref)}(?![A-Za-z0-9-])"
            if re.search(pattern, content):
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
    if platform is not None and platform not in PLATFORM_NAMING:
        print(
            f"Error: unknown platform {platform!r}; expected one of "
            f"{sorted(PLATFORM_NAMING)}",
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
