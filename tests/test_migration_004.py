"""Tests for migrations/004-wire-mcp-token-env.py (#259).

The live ``~/.zshenv``, ``~/Library/LaunchAgents`` and ``~/.brain`` are NEVER
touched: every test writes a synthetic HOME under ``tmp_path`` and injects a
fake ``launchctl`` recorder so no real launchd/env state changes.
"""

import importlib.util
import plistlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations" / "004-wire-mcp-token-env.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_004", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


migration = _load_migration()


def _recorder():
    calls: list[list[str]] = []

    def launchctl(argv):
        calls.append(list(argv))

    return launchctl, calls


def _write_registry(tmp_path: Path, svc: dict) -> Path:
    import yaml

    reg = tmp_path / ".brain" / "registry.yaml"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(yaml.safe_dump({"settings": {"local_brain_service": svc}}), encoding="utf-8")
    return reg


# --- zshenv block management ------------------------------------------------


def test_build_block_has_markers_and_export_but_no_secret():
    block = migration.build_zshenv_block("LOCAL_BRAIN_API_TOKEN")
    assert migration.BLOCK_START in block
    assert migration.BLOCK_END in block
    assert "export LOCAL_BRAIN_API_TOKEN=" in block
    assert "/usr/bin/sed -E -n" in block
    assert "python3 -c" not in block
    assert "import yaml" not in block
    # The block resolves the token at shell-init from registry.yaml — never the literal.
    assert "registry.yaml" in block


def test_build_block_mirrors_custom_env_to_claude_mcp_env():
    block = migration.build_zshenv_block("BRAIN_TOKEN")
    assert "export BRAIN_TOKEN=" in block
    assert "export LOCAL_BRAIN_API_TOKEN=" in block


def test_upsert_adds_block_once_and_is_idempotent():
    text = "export FOO=bar\n"
    once, changed1 = migration.upsert_zshenv_block(text, "LOCAL_BRAIN_API_TOKEN")
    assert changed1 is True
    assert once.count(migration.BLOCK_START) == 1
    assert "export FOO=bar" in once

    twice, changed2 = migration.upsert_zshenv_block(once, "LOCAL_BRAIN_API_TOKEN")
    assert twice == once
    assert changed2 is False
    assert twice.count(migration.BLOCK_START) == 1


def test_upsert_replaces_existing_hand_written_block_preserving_user_content():
    text = (
        "export FOO=bar\n\n"
        f"{migration.BLOCK_START}\n"
        'export LOCAL_BRAIN_API_TOKEN="hand-written-literal-secret"\n'
        f"{migration.BLOCK_END}\n\n"
        "export BAZ=qux\n"
    )
    new_text, changed = migration.upsert_zshenv_block(text, "LOCAL_BRAIN_API_TOKEN")
    assert changed is True
    assert new_text.count(migration.BLOCK_START) == 1
    assert "export FOO=bar" in new_text
    assert "export BAZ=qux" in new_text
    # The old hand-written literal must be gone (adopted/replaced by the managed block).
    assert "hand-written-literal-secret" not in new_text


# --- wire_token_env ---------------------------------------------------------


def test_wire_writes_block_setenv_and_launchagent_without_leaking_secret(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    launchctl, calls = _recorder()

    actions = migration.wire_token_env(
        "super-secret-tok",
        token_env="LOCAL_BRAIN_API_TOKEN",
        home=home,
        launchctl=launchctl,
    )

    zpath = migration.zshenv_path(home)
    assert zpath.exists()
    ztext = zpath.read_text(encoding="utf-8")
    assert migration.BLOCK_START in ztext
    assert "super-secret-tok" not in ztext  # never written into the dotfile

    apath = migration.launch_agent_path(home)
    assert apath.exists()
    plist = plistlib.loads(apath.read_bytes())
    assert plist["Label"] == migration.LAUNCH_AGENT_LABEL
    assert "--apply-env" in plist["ProgramArguments"]
    assert plist.get("RunAtLoad") is True

    setenv_calls = [c for c in calls if c[:2] == ["launchctl", "setenv"]]
    assert setenv_calls == [["launchctl", "setenv", "LOCAL_BRAIN_API_TOKEN", "super-secret-tok"]]
    assert any(c[:2] == ["launchctl", "bootstrap"] for c in calls)
    assert actions  # non-empty reporting


def test_wire_respects_api_token_env_override(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    launchctl, calls = _recorder()

    migration.wire_token_env(
        "tok2", token_env="BRAIN_TOKEN", home=home, launchctl=launchctl
    )

    ztext = migration.zshenv_path(home).read_text(encoding="utf-8")
    assert "export BRAIN_TOKEN=" in ztext
    assert "export LOCAL_BRAIN_API_TOKEN=" in ztext
    assert any(c == ["launchctl", "setenv", "BRAIN_TOKEN", "tok2"] for c in calls)
    assert any(c == ["launchctl", "setenv", "LOCAL_BRAIN_API_TOKEN", "tok2"] for c in calls)


def test_wire_is_idempotent_single_block_on_repeat(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    launchctl, _ = _recorder()
    migration.wire_token_env("tok", home=home, launchctl=launchctl)
    migration.wire_token_env("tok", home=home, launchctl=launchctl)
    ztext = migration.zshenv_path(home).read_text(encoding="utf-8")
    assert ztext.count(migration.BLOCK_START) == 1


# --- run(): registry-driven migration entry point ---------------------------


def test_run_full_wiring_and_records_004(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "brain"
    root.mkdir()
    reg = _write_registry(tmp_path, {"api_token": "reg-tok", "enabled": True})
    launchctl, calls = _recorder()

    actions = migration.run(root, home=home, registry_path=reg, launchctl=launchctl)

    assert migration.zshenv_path(home).exists()
    assert migration.launch_agent_path(home).exists()
    assert (root / ".migrations-run").read_text(encoding="utf-8").splitlines() == ["004"]
    assert any(c[:2] == ["launchctl", "setenv"] for c in calls)
    assert actions


def test_run_skips_when_no_api_token_without_recording_completion(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "brain"
    root.mkdir()
    reg = _write_registry(tmp_path, {"enabled": False})
    launchctl, calls = _recorder()

    actions = migration.run(root, home=home, registry_path=reg, launchctl=launchctl)

    assert not migration.zshenv_path(home).exists()
    assert not migration.launch_agent_path(home).exists()
    assert calls == []
    assert not (root / ".migrations-run").exists()
    assert any("skipped" in a for a in actions)


def test_run_retries_and_records_after_token_is_later_configured(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "brain"
    root.mkdir()
    reg = _write_registry(tmp_path, {"enabled": False})
    launchctl, calls = _recorder()

    migration.run(root, home=home, registry_path=reg, launchctl=launchctl)
    assert calls == []
    assert not (root / ".migrations-run").exists()

    reg = _write_registry(tmp_path, {"enabled": False, "api_token": "later-tok"})
    actions = migration.run(root, home=home, registry_path=reg, launchctl=launchctl)

    assert migration.zshenv_path(home).exists()
    assert migration.launch_agent_path(home).exists()
    assert (root / ".migrations-run").read_text(encoding="utf-8").splitlines() == ["004"]
    assert ["launchctl", "setenv", "LOCAL_BRAIN_API_TOKEN", "later-tok"] in calls
    assert any("recorded migration 004" in a for a in actions)


def test_run_second_call_is_noop(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "brain"
    root.mkdir()
    reg = _write_registry(tmp_path, {"api_token": "reg-tok"})
    launchctl, _ = _recorder()
    migration.run(root, home=home, registry_path=reg, launchctl=launchctl)

    launchctl2, calls2 = _recorder()
    actions = migration.run(root, home=home, registry_path=reg, launchctl=launchctl2)
    assert calls2 == []
    assert any("already applied" in a for a in actions)


def test_apply_env_only_sets_env_from_registry(tmp_path):
    reg = _write_registry(tmp_path, {"api_token": "login-tok", "api_token_env": "LOCAL_BRAIN_API_TOKEN"})
    launchctl, calls = _recorder()
    actions = migration.apply_env(registry_path=reg, launchctl=launchctl)
    assert calls == [["launchctl", "setenv", "LOCAL_BRAIN_API_TOKEN", "login-tok"]]
    assert actions


def test_apply_env_mirrors_custom_env_to_claude_mcp_env(tmp_path):
    reg = _write_registry(tmp_path, {"api_token": "login-tok", "api_token_env": "BRAIN_TOKEN"})
    launchctl, calls = _recorder()
    actions = migration.apply_env(registry_path=reg, launchctl=launchctl)
    assert calls == [
        ["launchctl", "setenv", "BRAIN_TOKEN", "login-tok"],
        ["launchctl", "setenv", "LOCAL_BRAIN_API_TOKEN", "login-tok"],
    ]
    assert actions
