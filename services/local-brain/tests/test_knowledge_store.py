"""Tests for the brain-owned knowledge store resolution (WI1, issue #86).

The store is relocatable and registry-free: its root resolves from
``brain_store_path`` (or the ``<brain_home>/knowledge`` default), never from a
registry. These tests cover default + relocated path resolution and the
directory-ensuring helper.
"""

from __future__ import annotations

from pathlib import Path

from fritz_local_brain import knowledge
from fritz_local_brain.config import Settings


def test_store_defaults_to_knowledge_under_brain_home(tmp_path) -> None:
    settings = Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path)

    assert settings.resolve_brain_store_path() == tmp_path / "knowledge"
    assert knowledge.store_root(settings) == tmp_path / "knowledge"


def test_relocating_setting_moves_the_store(tmp_path) -> None:
    elsewhere = tmp_path / "somewhere" / "else"
    settings = Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path, BRAIN_STORE_PATH=elsewhere)

    assert settings.resolve_brain_store_path() == elsewhere
    assert knowledge.store_root(settings) == elsewhere


def test_store_path_expands_user_home() -> None:
    settings = Settings(_env_file=None, BRAIN_STORE_PATH="~/custom-brain-store")

    assert settings.resolve_brain_store_path() == Path.home() / "custom-brain-store"


def test_empty_store_path_falls_back_to_default(tmp_path) -> None:
    settings = Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path, BRAIN_STORE_PATH="")

    assert settings.brain_store_path is None
    assert settings.resolve_brain_store_path() == tmp_path / "knowledge"


def test_ensure_store_root_creates_directory(tmp_path) -> None:
    root = tmp_path / "knowledge"
    settings = Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path)
    assert not root.exists()

    created = knowledge.ensure_store_root(settings)

    assert created == root
    assert root.is_dir()


def test_store_resolves_without_a_registry(tmp_path) -> None:
    """The store root resolves with no registry.yaml present at all."""
    assert not (tmp_path / "registry.yaml").exists()
    settings = Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path)

    assert knowledge.ensure_store_root(settings) == tmp_path / "knowledge"
    assert (tmp_path / "knowledge").is_dir()
