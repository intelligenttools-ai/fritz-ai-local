"""Tests for the cross-agent skill naming model (issue #59).

The repo's `skills/` directory is the single source of truth and uses PLAIN
names (e.g. `brain-query`, `handover`, `update`). The generator emits a
per-platform name and slash-command variant:

  - claude plugin            -> plain dir/name, `/fritz-brain:<plain>` refs
  - codex namespace          -> `fritz:<plain>`  (colon)
  - pi (~/.agents/skills)    -> `fritz-<plain>`  (hyphen)

A generated variant rewrites three things consistently:
  (a) the directory name
  (b) the SKILL.md `name:` frontmatter field
  (c) intra-skill slash references

A validator checks that consistency and fails on a deliberately-broken variant.
"""

from __future__ import annotations

import importlib.util
import re
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
    "platform,name_prefix",
    [("claude", ""), ("codex", "fritz:"), ("pi", "fritz-")],
)
def test_generate_brain_skills_get_platform_name_prefix(tmp_path, platform, name_prefix):
    module = _load_module()
    out = tmp_path / "out"
    out.mkdir()

    created = module.generate_variants(out, platform, dry_run=False)
    assert created, "expected generated skills"

    # brain-* dirs should appear with the platform name prefix.
    for base in ("brain-compile", "brain-query", "handover", "update"):
        variant_dir = out / f"{name_prefix}{base}"
        assert variant_dir.is_dir(), f"missing {variant_dir}"
        assert (variant_dir / "SKILL.md").exists()


@pytest.mark.parametrize(
    "platform,name_prefix,slash_prefix",
    [
        ("claude", "", "fritz-brain:"),
        ("codex", "fritz:", "fritz:"),
        ("pi", "fritz-", "fritz-"),
    ],
)
def test_name_dir_slash_consistency(tmp_path, platform, name_prefix, slash_prefix):
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
        # dir name must carry the platform name prefix, when the platform uses one.
        if name_prefix:
            assert dir_name.startswith(name_prefix), f"{dir_name} missing prefix {name_prefix}"

        # No stale wrong-platform slash refs to a known skill base. (Scoped to
        # real skill slash commands so unrelated tokens like a `fritz-ai/`
        # example folder are not false positives.)
        base = dir_name[len(name_prefix):] if name_prefix else dir_name
        expected_ref = f"/{slash_prefix}{base}"
        assert expected_ref in content
        for stale_prefix in {"", "fritz:", "fritz-", "fritz-brain:"} - {slash_prefix}:
            stale_ref = f"/{stale_prefix}{base}"
            pattern = rf"(?<![\w/]){re.escape(stale_ref)}(?![A-Za-z0-9-])"
            assert not re.search(pattern, content), f"stale {stale_ref} in {dir_name}"


def test_pi_uses_hyphen_not_colon(tmp_path):
    module = _load_module()
    out = tmp_path / "out"
    out.mkdir()
    module.generate_variants(out, "pi", dry_run=False)
    assert (out / "fritz-brain-query").is_dir()
    assert not (out / "fritz:brain-query").exists()


def test_claude_uses_plain_plugin_skill_names(tmp_path):
    module = _load_module()
    out = tmp_path / "out"
    out.mkdir()
    module.generate_variants(out, "claude", dry_run=False)
    assert (out / "brain-query").is_dir()
    assert not (out / "fritz:brain-query").exists()
    assert not (out / "fritz-brain-query").exists()


def test_unknown_platform_rejected(tmp_path):
    module = _load_module()
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises((ValueError, SystemExit)):
        module.generate_variants(out, "bogus", dry_run=False)


# --- Round-trip a representative skill --------------------------------------


@pytest.mark.parametrize(
    "platform,name_prefix,slash_prefix",
    [
        ("claude", "", "fritz-brain:"),
        ("codex", "fritz:", "fritz:"),
        ("pi", "fritz-", "fritz-"),
    ],
)
def test_round_trip_brain_query(tmp_path, platform, name_prefix, slash_prefix):
    module = _load_module()
    out = tmp_path / "out"
    out.mkdir()
    module.generate_variants(out, platform, dry_run=False)

    variant_dir = out / f"{name_prefix}brain-query"
    content = (variant_dir / "SKILL.md").read_text(encoding="utf-8")

    assert f"name: {name_prefix}brain-query" in content
    # brain-query references brain-query and brain-compile slash commands.
    assert f"/{slash_prefix}brain-query" in content
    assert f"/{slash_prefix}brain-compile" in content
    # The plain (unprefixed) slash forms must be gone.
    assert "/brain-query" not in content.replace(f"/{slash_prefix}brain-query", "")


def test_codex_generated_skills_use_http_service_not_native_brain_mcp(tmp_path):
    module = _load_module()
    out = tmp_path / "out"
    out.mkdir()
    module.generate_variants(out, "codex", dry_run=False)

    query = (out / "fritz:brain-query" / "SKILL.md").read_text(encoding="utf-8")
    compile_ = (out / "fritz:brain-compile" / "SKILL.md").read_text(encoding="utf-8")
    sync = (out / "fritz:brain-sync" / "SKILL.md").read_text(encoding="utf-8")
    lint = (out / "fritz:brain-lint" / "SKILL.md").read_text(encoding="utf-8")
    handover = (out / "fritz:handover" / "SKILL.md").read_text(encoding="utf-8")

    for content in (query, compile_, sync, lint, handover):
        assert "Codex bindings do not register the Brain MCP tools natively" in content
        assert "prefer the registered MCP" not in content

    assert "call `POST <base_url>/v1/search/run` from the host" in query
    assert "call `POST <base_url>/v1/compile/run` from the host" in compile_
    assert "call `POST <base_url>/v1/sync/run` from the host" in sync
    assert "call `POST <base_url>/v1/lint/run` from the host" in lint
    assert "call `POST <base_url>/v1/compile/run` and `POST <base_url>/v1/sync/run` from the host" in handover


def test_claude_generated_skills_keep_native_brain_mcp_guidance(tmp_path):
    module = _load_module()
    out = tmp_path / "out"
    out.mkdir()
    module.generate_variants(out, "claude", dry_run=False)

    query = (out / "brain-query" / "SKILL.md").read_text(encoding="utf-8")

    assert "prefer the registered MCP tool `brain_search`" in query
    assert "Codex bindings do not register the Brain MCP tools natively" not in query


@pytest.mark.parametrize(
    "platform,name_prefix,slash_prefix",
    [
        ("claude", "", "fritz-brain:"),
        ("codex", "fritz:", "fritz:"),
        ("pi", "fritz-", "fritz-"),
    ],
)
def test_update_skill_filesystem_path_not_rewritten(tmp_path, platform, name_prefix, slash_prefix):
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

    content = (out / f"{name_prefix}update" / "SKILL.md").read_text(encoding="utf-8")

    # Filesystem path stays plain; the over-rewrite would have produced
    # `skills/fritz:brain-setup/`, `skills/fritz-brain:brain-setup/`, or
    # `skills/fritz-brain-setup/`.
    assert "skills/brain-setup/SKILL.md" in content
    if name_prefix:
        assert f"skills/{name_prefix}brain-setup/SKILL.md" not in content

    # A genuine slash-command ref (e.g. a backtick-wrapped `/brain-setup`) is
    # still rewritten to the platform variant.
    assert f"`/{slash_prefix}brain-setup`" in content
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

    good = out / "brain-query"
    assert module.validate_variant(good, "claude") == []
