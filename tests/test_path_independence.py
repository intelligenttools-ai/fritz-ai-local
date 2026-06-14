"""Tests for location-independent repo resolution (issue #56).

Fritz must run from any clone location. The repo root is resolved either from
the FRITZ_REPO_PATH env var, or from the hook file's own location via
Path(__file__).resolve().parents[1] (which follows symlinks back to the real
repo when hooks are symlinked into ~/.brain/hooks/).
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path

import pytest


HOOKS_DIR = Path(__file__).resolve().parents[1] / "hooks"
REAL_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_brain_common(monkeypatch, *, env_value: str | None, file_path: Path):
    """Load a fresh copy of brain_common from a given file path.

    Loading from an explicit path lets us simulate the symlinked-into-~/.brain
    case: importlib uses the spec origin (the symlink path), and the module's
    own Path(__file__).resolve() should follow the symlink back to the real
    repo.
    """
    if env_value is None:
        monkeypatch.delenv("FRITZ_REPO_PATH", raising=False)
    else:
        monkeypatch.setenv("FRITZ_REPO_PATH", env_value)

    # Make sibling modules importable for the brain_common dependency graph.
    monkeypatch.syspath_prepend(str(file_path.parent))

    module_name = f"_brain_common_test_{id(file_path)}"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def test_fritz_repo_path_env_override(monkeypatch, tmp_path):
    """FRITZ_REPO_PATH env var takes precedence over file-based resolution."""
    override = tmp_path / "custom-repo-location"
    override.mkdir()
    module = _load_brain_common(
        monkeypatch, env_value=str(override), file_path=HOOKS_DIR / "brain_common.py"
    )
    assert module.FRITZ_REPO == override.resolve()


def test_file_based_resolution_points_to_real_repo(monkeypatch):
    """With no env override, FRITZ_REPO resolves to the actual repo root."""
    module = _load_brain_common(
        monkeypatch, env_value=None, file_path=HOOKS_DIR / "brain_common.py"
    )
    assert module.FRITZ_REPO == REAL_REPO_ROOT
    assert (module.FRITZ_REPO / "hooks" / "brain_common.py").exists()


def test_symlinked_hook_resolves_back_to_real_repo(monkeypatch, tmp_path):
    """A symlinked brain_common.py (e.g. ~/.brain/hooks/) must resolve to the
    real repo root via Path(__file__).resolve(), which follows symlinks."""
    fake_brain_hooks = tmp_path / ".brain" / "hooks"
    fake_brain_hooks.mkdir(parents=True)
    symlink = fake_brain_hooks / "brain_common.py"
    symlink.symlink_to(HOOKS_DIR / "brain_common.py")

    module = _load_brain_common(monkeypatch, env_value=None, file_path=symlink)

    # Even though the file was loaded from the symlink path, resolution must
    # point back to the real repo, not the tmp ~/.brain location.
    assert module.FRITZ_REPO == REAL_REPO_ROOT
    assert module.FRITZ_REPO != fake_brain_hooks.parent


def test_env_override_wins_for_symlinked_hook(monkeypatch, tmp_path):
    """FRITZ_REPO_PATH still wins even when loaded from a symlink."""
    fake_brain_hooks = tmp_path / ".brain" / "hooks"
    fake_brain_hooks.mkdir(parents=True)
    symlink = fake_brain_hooks / "brain_common.py"
    symlink.symlink_to(HOOKS_DIR / "brain_common.py")

    override = tmp_path / "elsewhere"
    override.mkdir()
    module = _load_brain_common(
        monkeypatch, env_value=str(override), file_path=symlink
    )
    assert module.FRITZ_REPO == override.resolve()


def test_no_hardcoded_home_path_in_brain_common():
    """brain_common.py must not hardcode the ~/.fritz-ai-local runtime path."""
    source = (HOOKS_DIR / "brain_common.py").read_text(encoding="utf-8")
    assert ".fritz-ai-local" not in source


def test_no_hardcoded_home_path_in_setup_hyphenated_skills():
    """setup_hyphenated_skills.py must not hardcode the ~/.fritz-ai-local path."""
    source = (HOOKS_DIR / "setup_hyphenated_skills.py").read_text(encoding="utf-8")
    assert ".fritz-ai-local" not in source


def test_setup_hyphenated_skills_resolves_repo_from_file(monkeypatch, tmp_path):
    """generate_variants should resolve skills from the real repo via __file__
    when FRITZ_REPO_PATH is not set, even when the module is symlinked."""
    fake_brain_hooks = tmp_path / ".brain" / "hooks"
    fake_brain_hooks.mkdir(parents=True)
    symlink = fake_brain_hooks / "setup_hyphenated_skills.py"
    symlink.symlink_to(HOOKS_DIR / "setup_hyphenated_skills.py")

    monkeypatch.delenv("FRITZ_REPO_PATH", raising=False)

    spec = importlib.util.spec_from_file_location("_setup_hyphenated_test", symlink)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # The resolved source must be the real repo's skills dir, not a
    # ~/.fritz-ai-local fallback (which does not exist in this test env).
    assert module._resolve_repo_root() == REAL_REPO_ROOT

    skills_out = tmp_path / "agent-skills"
    skills_out.mkdir()
    created = module.generate_variants(skills_out, "pi", dry_run=True)
    # Repo has plain source skills, so dry-run output is non-empty.
    # generate_variants would sys.exit(1) if the source dir were unresolved.
    assert created, "expected plain source skills resolved from the real repo"


def test_setup_hyphenated_skills_env_override(monkeypatch, tmp_path):
    """FRITZ_REPO_PATH override is honored by generate_variants (plain source)."""
    repo = tmp_path / "myrepo"
    skills_src = repo / "skills" / "demo"
    skills_src.mkdir(parents=True)
    (skills_src / "SKILL.md").write_text(
        "---\nname: demo\n---\nUse /demo here.\n", encoding="utf-8"
    )
    monkeypatch.setenv("FRITZ_REPO_PATH", str(repo))

    spec = importlib.util.spec_from_file_location(
        "_setup_hyphenated_test2", HOOKS_DIR / "setup_hyphenated_skills.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    skills_out = tmp_path / "agent-skills"
    skills_out.mkdir()

    # pi variant: hyphen prefix.
    created = module.generate_variants(skills_out, "pi", dry_run=False)
    assert len(created) == 1
    out_file = skills_out / "fritz-demo" / "SKILL.md"
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "name: fritz-demo" in content
    assert "/fritz-demo" in content

    # claude variant: colon prefix.
    created = module.generate_variants(skills_out, "claude", dry_run=False)
    assert len(created) == 1
    out_file = skills_out / "fritz:demo" / "SKILL.md"
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "name: fritz:demo" in content
    assert "/fritz:demo" in content
