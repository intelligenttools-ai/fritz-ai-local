"""Tests for the centralized configuration resolver (issue #60).

Precedence under test: project (.fritz-local.json) > central
(registry.yaml settings:) > defaults, via the single get_setting() path.

GUARDRAIL: every test monkeypatches BRAIN_HOME / REGISTRY_PATH onto tmp_path
and uses temp .fritz-local.json files. The live ~/.brain is never touched.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import brain_common  # noqa: E402


def _set_central(monkeypatch, tmp_path, settings: dict | None):
    """Point brain_common at a temp registry.yaml with the given settings."""
    monkeypatch.setattr(brain_common, "BRAIN_HOME", tmp_path)
    registry_path = tmp_path / "registry.yaml"
    monkeypatch.setattr(brain_common, "REGISTRY_PATH", registry_path)
    lines = ["version: 1", "vaults: {}"]
    if settings is not None:
        import yaml

        registry_path.write_text(
            yaml.safe_dump({"version": 1, "vaults": {}, "settings": settings}),
            encoding="utf-8",
        )
    else:
        registry_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return registry_path


def _write_fritz_local(tmp_path, data: dict) -> str:
    project_dir = tmp_path / "project"
    project_dir.mkdir(exist_ok=True)
    (project_dir / ".fritz-local.json").write_text(json.dumps(data), encoding="utf-8")
    return str(project_dir)


# --- get_setting: core precedence -----------------------------------------


def test_get_setting_project_wins_over_central(monkeypatch, tmp_path):
    """A per-project value overrides a conflicting central value."""
    _set_central(monkeypatch, tmp_path, {"max_injection_chars": 1000})
    fritz_local = {"max_injection_chars": 5000}
    assert brain_common.get_setting("max_injection_chars", 8000, fritz_local=fritz_local) == 5000


def test_get_setting_central_used_when_no_project_value(monkeypatch, tmp_path):
    """With no project value, the central value is used."""
    _set_central(monkeypatch, tmp_path, {"max_injection_chars": 1000})
    assert brain_common.get_setting("max_injection_chars", 8000) == 1000


def test_get_setting_central_used_when_project_lacks_key(monkeypatch, tmp_path):
    """A present .fritz-local.json that omits the key falls through to central."""
    _set_central(monkeypatch, tmp_path, {"max_injection_chars": 1000})
    fritz_local = {"project": "demo"}  # present but no max_injection_chars
    assert brain_common.get_setting("max_injection_chars", 8000, fritz_local=fritz_local) == 1000


def test_get_setting_default_when_neither(monkeypatch, tmp_path):
    """With neither project nor central value, the default is returned."""
    _set_central(monkeypatch, tmp_path, {})
    assert brain_common.get_setting("max_injection_chars", 8000) == 8000


def test_get_setting_default_when_no_registry(monkeypatch, tmp_path):
    """No registry file at all -> default."""
    _set_central(monkeypatch, tmp_path, None)
    # Remove the file written by helper to simulate truly missing registry.
    (tmp_path / "registry.yaml").unlink()
    assert brain_common.get_setting("anything", "fallback") == "fallback"


def test_get_setting_null_value_treated_as_missing(monkeypatch, tmp_path):
    """A key explicitly set to null in a layer falls through to the next layer."""
    _set_central(monkeypatch, tmp_path, {"context_injection": "full"})
    fritz_local = {"context_injection": None}  # present but null -> falls through
    assert brain_common.get_setting("context_injection", "off", fritz_local=fritz_local) == "full"


def test_get_setting_loads_fritz_local_from_cwd(monkeypatch, tmp_path):
    """When fritz_local is None but cwd is given, .fritz-local.json is loaded."""
    _set_central(monkeypatch, tmp_path, {"context_injection": "light"})
    cwd = _write_fritz_local(tmp_path, {"context_injection": "full"})
    assert brain_common.get_setting("context_injection", "off", cwd=cwd) == "full"


def test_get_setting_cwd_falls_through_to_central(monkeypatch, tmp_path):
    """cwd-loaded project file lacking the key falls through to central."""
    _set_central(monkeypatch, tmp_path, {"context_injection": "light"})
    cwd = _write_fritz_local(tmp_path, {"project": "demo"})
    assert brain_common.get_setting("context_injection", "off", cwd=cwd) == "light"


# --- regression: get_context_injection_level ------------------------------


def test_context_injection_project_wins(monkeypatch, tmp_path):
    _set_central(monkeypatch, tmp_path, {"context_injection": "full"})
    assert brain_common.get_context_injection_level({"context_injection": "light"}) == "light"


def test_context_injection_present_file_without_key_is_off(monkeypatch, tmp_path):
    """Historical edge case: a present .fritz-local.json without the key -> off.

    This must NOT fall through to a central value of 'full'.
    """
    _set_central(monkeypatch, tmp_path, {"context_injection": "full"})
    assert brain_common.get_context_injection_level({"project": "demo"}) == "off"


def test_context_injection_invalid_project_value_is_off(monkeypatch, tmp_path):
    _set_central(monkeypatch, tmp_path, {"context_injection": "full"})
    assert brain_common.get_context_injection_level({"context_injection": "loud"}) == "off"


def test_context_injection_no_project_uses_central(monkeypatch, tmp_path):
    _set_central(monkeypatch, tmp_path, {"context_injection": "light"})
    assert brain_common.get_context_injection_level(None) == "light"


def test_context_injection_no_project_invalid_central_is_off(monkeypatch, tmp_path):
    _set_central(monkeypatch, tmp_path, {"context_injection": "loud"})
    assert brain_common.get_context_injection_level(None) == "off"


def test_context_injection_default_off(monkeypatch, tmp_path):
    _set_central(monkeypatch, tmp_path, {})
    assert brain_common.get_context_injection_level(None) == "off"


# --- regression: get_max_injection_chars ----------------------------------


def test_max_injection_chars_project_wins(monkeypatch, tmp_path):
    _set_central(monkeypatch, tmp_path, {"max_injection_chars": 2000})
    assert brain_common.get_max_injection_chars({"max_injection_chars": 4000}) == 4000


def test_max_injection_chars_project_missing_key_uses_central(monkeypatch, tmp_path):
    _set_central(monkeypatch, tmp_path, {"max_injection_chars": 2000})
    assert brain_common.get_max_injection_chars({"project": "demo"}) == 2000


def test_max_injection_chars_central_used_when_no_project(monkeypatch, tmp_path):
    _set_central(monkeypatch, tmp_path, {"max_injection_chars": 2000})
    assert brain_common.get_max_injection_chars(None) == 2000


def test_max_injection_chars_default(monkeypatch, tmp_path):
    _set_central(monkeypatch, tmp_path, {})
    assert brain_common.get_max_injection_chars(None) == 8000


def test_max_injection_chars_coerces_to_int(monkeypatch, tmp_path):
    """String values resolve and are coerced to int (historical behavior)."""
    _set_central(monkeypatch, tmp_path, {"max_injection_chars": "3000"})
    assert brain_common.get_max_injection_chars(None) == 3000
