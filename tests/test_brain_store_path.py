"""Tests for the brain-owned knowledge store resolver in the hooks (WI1, #86).

Precedence under test: project (.fritz-local.json) > central (registry.yaml
settings:) > default <BRAIN_HOME>/knowledge, via the registry-free
get_brain_store_path() helper.

GUARDRAIL: every test monkeypatches BRAIN_HOME / REGISTRY_PATH onto tmp_path
and uses temp .fritz-local.json files. The live ~/.brain is never touched.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import brain_common  # noqa: E402


def _point_at_tmp(monkeypatch, tmp_path, settings: dict | None):
    """Point brain_common at a temp BRAIN_HOME / registry.yaml."""
    monkeypatch.setattr(brain_common, "BRAIN_HOME", tmp_path)
    registry_path = tmp_path / "registry.yaml"
    monkeypatch.setattr(brain_common, "REGISTRY_PATH", registry_path)
    if settings is not None:
        import yaml

        registry_path.write_text(
            yaml.safe_dump({"version": 1, "vaults": {}, "settings": settings}),
            encoding="utf-8",
        )
    return registry_path


def _write_fritz_local(tmp_path, data: dict) -> str:
    project_dir = tmp_path / "project"
    project_dir.mkdir(exist_ok=True)
    (project_dir / ".fritz-local.json").write_text(json.dumps(data), encoding="utf-8")
    return str(project_dir)


def test_default_is_knowledge_under_brain_home(monkeypatch, tmp_path):
    """No setting anywhere -> <BRAIN_HOME>/knowledge, no registry required."""
    _point_at_tmp(monkeypatch, tmp_path, None)  # no registry.yaml written
    assert not (tmp_path / "registry.yaml").exists()

    assert brain_common.get_brain_store_path() == tmp_path / "knowledge"


def test_central_setting_relocates_the_store(monkeypatch, tmp_path):
    elsewhere = tmp_path / "vault" / "store"
    _point_at_tmp(monkeypatch, tmp_path, {"brain_store_path": str(elsewhere)})

    assert brain_common.get_brain_store_path() == elsewhere


def test_project_setting_wins_over_central(monkeypatch, tmp_path):
    central = tmp_path / "central-store"
    project = tmp_path / "project-store"
    _point_at_tmp(monkeypatch, tmp_path, {"brain_store_path": str(central)})

    fritz_local = {"brain_store_path": str(project)}
    assert brain_common.get_brain_store_path(fritz_local=fritz_local) == project


def test_path_is_user_home_expanded(monkeypatch, tmp_path):
    _point_at_tmp(monkeypatch, tmp_path, {"brain_store_path": "~/custom-store"})

    assert brain_common.get_brain_store_path() == Path.home() / "custom-store"


def test_resolves_via_cwd_loaded_project_file(monkeypatch, tmp_path):
    relocated = tmp_path / "from-cwd"
    _point_at_tmp(monkeypatch, tmp_path, None)
    cwd = _write_fritz_local(tmp_path, {"brain_store_path": str(relocated)})

    assert brain_common.get_brain_store_path(cwd=cwd) == relocated


def test_ensure_brain_store_path_creates_directory(monkeypatch, tmp_path):
    _point_at_tmp(monkeypatch, tmp_path, None)
    root = tmp_path / "knowledge"
    assert not root.exists()

    created = brain_common.ensure_brain_store_path()

    assert created == root
    assert root.is_dir()
