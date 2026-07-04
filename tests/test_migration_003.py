"""Tests for migrations/003-claude-plugin-owns-hooks.py.

The live ``~/.claude/settings.json`` is NEVER touched: every test writes a
synthetic settings file under ``tmp_path`` and passes it directly to the
migration.
"""

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations" / "003-claude-plugin-owns-hooks.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_003", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


migration = _load_migration()


def _fritz_group(script: str) -> dict:
    return {"hooks": [{"type": "command", "command": f"/opt/homebrew/bin/python3 ~/.brain/hooks/{script}", "timeout": 5000}]}


def _user_group(name: str) -> dict:
    return {"hooks": [{"type": "command", "command": f"/usr/bin/true # {name}", "timeout": 1000}]}


def _write_settings(path: Path, settings: dict) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def _read_settings(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_plugin_enabled_removes_only_fritz_hook_groups_and_records_003(tmp_path):
    root = tmp_path / "brain"
    root.mkdir()
    settings_path = tmp_path / "home" / ".claude" / "settings.json"
    _write_settings(
        settings_path,
        {
            "enabledPlugins": ["other-plugin", "fritz-brain@fritz-local"],
            "hooks": {
                "SessionStart": [_user_group("session"), _fritz_group("brain_session_start.py")],
                "UserPromptSubmit": [_fritz_group("brain_prompt_check.py"), _user_group("prompt")],
                "PreCompact": [_fritz_group("brain_capture.py")],
                "Stop": [_fritz_group("brain_capture.py"), _user_group("stop")],
                "Notification": [_user_group("notify")],
            },
        },
    )

    actions = migration.run(root, settings_path=settings_path)

    settings = _read_settings(settings_path)
    assert settings["hooks"]["SessionStart"] == [_user_group("session")]
    assert settings["hooks"]["UserPromptSubmit"] == [_user_group("prompt")]
    assert "PreCompact" not in settings["hooks"]
    assert settings["hooks"]["Stop"] == [_user_group("stop")]
    assert settings["hooks"]["Notification"] == [_user_group("notify")]
    assert (root / ".migrations-run").read_text(encoding="utf-8").splitlines() == ["003"]
    assert any("removed 4 fritz hook group" in action for action in actions)


def test_plugin_enabled_as_dict_is_supported(tmp_path):
    root = tmp_path / "brain"
    root.mkdir()
    settings_path = tmp_path / "home" / ".claude" / "settings.json"
    _write_settings(
        settings_path,
        {
            "enabledPlugins": {"fritz-brain@fritz-local": True},
            "hooks": {"SessionStart": [_fritz_group("brain_session_start.py")]},
        },
    )

    migration.run(root, settings_path=settings_path)

    assert _read_settings(settings_path)["hooks"] == {}


def test_plugin_not_enabled_leaves_settings_unchanged(tmp_path):
    root = tmp_path / "brain"
    root.mkdir()
    settings_path = tmp_path / "home" / ".claude" / "settings.json"
    settings = {
        "enabledPlugins": ["other-plugin"],
        "hooks": {"SessionStart": [_fritz_group("brain_session_start.py"), _user_group("session")]},
    }
    _write_settings(settings_path, settings)
    before = settings_path.read_text(encoding="utf-8")

    actions = migration.run(root, settings_path=settings_path)

    assert settings_path.read_text(encoding="utf-8") == before
    assert (root / ".migrations-run").read_text(encoding="utf-8").splitlines() == ["003"]
    assert any("plugin not enabled" in action for action in actions)


def test_malformed_settings_is_skipped_without_recording_003(tmp_path):
    root = tmp_path / "brain"
    root.mkdir()
    settings_path = tmp_path / "home" / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    malformed = b'{"enabledPlugins": ["fritz-brain@fritz-local"],'
    settings_path.write_bytes(malformed)

    actions = migration.run(root, settings_path=settings_path)

    assert settings_path.read_bytes() == malformed
    assert not (root / ".migrations-run").exists()
    assert any("not valid JSON" in action for action in actions)
    assert any("refusing to rewrite" in action for action in actions)


def test_second_run_is_noop(tmp_path):
    root = tmp_path / "brain"
    root.mkdir()
    settings_path = tmp_path / "home" / ".claude" / "settings.json"
    _write_settings(
        settings_path,
        {
            "enabledPlugins": ["fritz-brain@fritz-local"],
            "hooks": {"SessionStart": [_fritz_group("brain_session_start.py")]},
        },
    )
    migration.run(root, settings_path=settings_path)
    before = settings_path.read_text(encoding="utf-8")

    actions = migration.run(root, settings_path=settings_path)

    assert settings_path.read_text(encoding="utf-8") == before
    assert (root / ".migrations-run").read_text(encoding="utf-8").splitlines() == ["003"]
    assert any("already applied" in action for action in actions)
