"""Tests for the cross-agent skill naming model (issue #59).

The repo's `skills/` directory is the single source of truth and uses PLAIN
names (e.g. `brain-query`, `handover`, `update`). The generator emits a
per-platform name variant by prefixing the plain base:

  - claude / codex namespace -> `fritz:<plain>`  (colon)
  - pi (~/.agents/skills)    -> `fritz-<plain>`  (hyphen)

A generated variant rewrites three things consistently:
  (a) the directory name
  (b) the SKILL.md `name:` frontmatter field
  (c) intra-skill slash references (`/<plain>` -> `/<prefix><plain>`)

A validator checks that consistency and fails on a deliberately-broken variant.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


HOOKS_DIR = Path(__file__).resolve().parents[1] / "hooks"
REAL_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "_setup_hyphenated_skills_naming", HOOKS_DIR / "setup_hyphenated_skills.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- Generation: prefix per platform ---------------------------------------


@pytest.mark.parametrize(
    "platform,prefix",
    [("claude", "fritz:"), ("codex", "fritz:"), ("pi", "fritz-")],
)
def test_generate_brain_skills_get_platform_prefix(tmp_path, platform, prefix):
    module = _load_module()
    out = tmp_path / "out"
    out.mkdir()

    created = module.generate_variants(out, platform, dry_run=False)
    assert created, "expected generated skills"

    # brain-* dirs should appear with the platform prefix.
    for base in ("brain-compile", "brain-query", "handover", "update"):
        variant_dir = out / f"{prefix}{base}"
        assert variant_dir.is_dir(), f"missing {variant_dir}"
        assert (variant_dir / "SKILL.md").exists()


@pytest.mark.parametrize(
    "platform,prefix",
    [("claude", "fritz:"), ("codex", "fritz:"), ("pi", "fritz-")],
)
def test_name_dir_slash_consistency(tmp_path, platform, prefix):
    module = _load_module()
    out = tmp_path / "out"
    out.mkdir()
    module.generate_variants(out, platform, dry_run=False)

    for variant_dir in sorted(out.iterdir()):
        if not variant_dir.is_dir():
            continue
        content = (variant_dir / "SKILL.md").read_text(encoding="utf-8")
        dir_name = variant_dir.name
        # name: frontmatter must equal the dir name.
        assert f"name: {dir_name}" in content, f"name mismatch in {dir_name}"
        # dir name must carry the platform prefix.
        assert dir_name.startswith(prefix), f"{dir_name} missing prefix {prefix}"

        # No stale wrong-platform slash refs to a known skill base. (Scoped to
        # real skill slash commands so unrelated tokens like a `fritz-ai/`
        # example folder are not false positives.)
        base = dir_name[len(prefix):]
        if prefix == "fritz:":
            assert f"/fritz-{base}" not in content, f"stale /fritz-{base} in {dir_name}"
        else:
            assert f"/fritz:{base}" not in content, f"stale /fritz:{base} in {dir_name}"


def test_pi_uses_hyphen_not_colon(tmp_path):
    module = _load_module()
    out = tmp_path / "out"
    out.mkdir()
    module.generate_variants(out, "pi", dry_run=False)
    assert (out / "fritz-brain-query").is_dir()
    assert not (out / "fritz:brain-query").exists()


def test_claude_uses_colon_not_hyphen(tmp_path):
    module = _load_module()
    out = tmp_path / "out"
    out.mkdir()
    module.generate_variants(out, "claude", dry_run=False)
    assert (out / "fritz:brain-query").is_dir()
    assert not (out / "fritz-brain-query").exists()


def test_unknown_platform_rejected(tmp_path):
    module = _load_module()
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises((ValueError, SystemExit)):
        module.generate_variants(out, "bogus", dry_run=False)


# --- Round-trip a representative skill --------------------------------------


@pytest.mark.parametrize(
    "platform,prefix",
    [("claude", "fritz:"), ("codex", "fritz:"), ("pi", "fritz-")],
)
def test_round_trip_brain_query(tmp_path, platform, prefix):
    module = _load_module()
    out = tmp_path / "out"
    out.mkdir()
    module.generate_variants(out, platform, dry_run=False)

    variant_dir = out / f"{prefix}brain-query"
    content = (variant_dir / "SKILL.md").read_text(encoding="utf-8")

    assert f"name: {prefix}brain-query" in content
    # brain-query references brain-query and brain-compile slash commands.
    assert f"/{prefix}brain-query" in content
    assert f"/{prefix}brain-compile" in content
    # The plain (unprefixed) slash forms must be gone.
    assert "/brain-query" not in content.replace(f"/{prefix}brain-query", "")


@pytest.mark.parametrize(
    "platform,prefix",
    [("claude", "fritz:"), ("codex", "fritz:"), ("pi", "fritz-")],
)
def test_update_skill_filesystem_path_not_rewritten(tmp_path, platform, prefix):
    """Regression: the slash-rewrite must not touch filesystem path refs.

    `skills/update/SKILL.md` contains a real path
    `<REPO>/skills/brain-setup/SKILL.md`. A `/brain-setup` preceded by a word
    char or another slash is part of a path, not a slash COMMAND, so it must be
    left PLAIN. A genuine command-style ref like `` `/brain-setup` `` must still
    be prefixed.
    """
    module = _load_module()
    out = tmp_path / "out"
    out.mkdir()
    module.generate_variants(out, platform, dry_run=False)

    content = (out / f"{prefix}update" / "SKILL.md").read_text(encoding="utf-8")

    # Filesystem path stays plain; the over-rewrite would have produced
    # `skills/fritz:brain-setup/` or `skills/fritz-brain-setup/`.
    assert "skills/brain-setup/SKILL.md" in content
    assert f"skills/{prefix}brain-setup/SKILL.md" not in content

    # A genuine slash-command ref (e.g. a backtick-wrapped `/brain-setup`) is
    # still rewritten to the platform variant.
    assert f"`/{prefix}brain-setup`" in content
    assert "`/brain-setup`" not in content


# --- Validator -------------------------------------------------------------


@pytest.mark.parametrize("platform", ["claude", "codex", "pi"])
def test_validator_passes_on_good_output(tmp_path, platform):
    module = _load_module()
    out = tmp_path / "out"
    out.mkdir()
    module.generate_variants(out, platform, dry_run=False)

    errors = module.validate_variants(out, platform)
    assert errors == [], f"validator unexpectedly failed: {errors}"


def test_validator_fails_on_broken_name_field(tmp_path):
    module = _load_module()
    out = tmp_path / "out"
    out.mkdir()
    module.generate_variants(out, "pi", dry_run=False)

    # Break the name: frontmatter so it no longer matches the dir name.
    broken = out / "fritz-brain-query" / "SKILL.md"
    text = broken.read_text(encoding="utf-8")
    broken.write_text(text.replace("name: fritz-brain-query", "name: brain-query"), encoding="utf-8")

    errors = module.validate_variants(out, "pi")
    assert errors, "validator should fail on broken name field"


def test_validator_fails_on_stale_slash_ref(tmp_path):
    module = _load_module()
    out = tmp_path / "out"
    out.mkdir()
    module.generate_variants(out, "pi", dry_run=False)

    # Inject a stale /fritz: ref into a pi (hyphen) variant.
    broken = out / "fritz-brain-query" / "SKILL.md"
    text = broken.read_text(encoding="utf-8")
    broken.write_text(text + "\nRun /fritz:brain-compile now.\n", encoding="utf-8")

    errors = module.validate_variants(out, "pi")
    assert errors, "validator should fail on stale /fritz: ref in pi variant"


def test_validate_single_variant(tmp_path):
    module = _load_module()
    out = tmp_path / "out"
    out.mkdir()
    module.generate_variants(out, "claude", dry_run=False)

    good = out / "fritz:brain-query"
    assert module.validate_variant(good, "claude") == []
