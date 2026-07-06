"""Tests for migrations/005-dependency-free-token-zshenv.py (#274).

The live ``~/.zshenv`` and ``~/.brain`` are NEVER touched. Every test uses a
synthetic HOME/root under ``tmp_path`` and sources only that generated block.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations" / "005-dependency-free-token-zshenv.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_005", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


migration = _load_migration()


ORIGINAL_004_BLOCK = "\n".join(
    [
        migration.BLOCK_START,
        "# Managed by fritz-local (migration 004). Exports the Local Brain MCP",
        "# token from ~/.brain/registry.yaml so Claude Code's plugin MCP header",
        "# can authenticate. Do not edit by hand; re-run /fritz-brain:update.",
        "export LOCAL_BRAIN_API_TOKEN=\"$(python3 -c 'import pathlib,yaml; "
        "r=yaml.safe_load(pathlib.Path.home().joinpath(\".brain\",\"registry.yaml\").read_text()) or {}; "
        "c=(r.get(\"settings\") or {}).get(\"local_brain_service\") or {}; "
        "print((c.get(\"api_token\") or \"\").strip())' 2>/dev/null)\"",
        migration.BLOCK_END,
    ]
)


HAND_HARDENED_BLOCK = "\n".join(
    [
        migration.BLOCK_START,
        'if [ -z "${LOCAL_BRAIN_API_TOKEN:-}" ]; then',
        "  export LOCAL_BRAIN_API_TOKEN=\"$(/usr/bin/sed -n 's/^[[:space:]]*api_token:[[:space:]]*//p' \"$HOME/.brain/registry.yaml\" 2>/dev/null | /usr/bin/head -n 1 | /usr/bin/tr -d '\"\\r')\"",
        "fi",
        'if [ -z "${LOCAL_BRAIN_API_TOKEN:-}" ]; then unset LOCAL_BRAIN_API_TOKEN; fi',
        migration.BLOCK_END,
    ]
)


def _write_registry(
    home: Path,
    svc: dict,
    *,
    leading_api_token: str | None = None,
    trailing_api_token: str | None = None,
) -> Path:
    reg = home / ".brain" / "registry.yaml"
    reg.parent.mkdir(parents=True, exist_ok=True)
    data = {"settings": {"local_brain_service": svc}}
    text = yaml.safe_dump(data)
    if leading_api_token is not None:
        text = f"other:\n  api_token: {leading_api_token}\n" + text
    if trailing_api_token is not None:
        text += f"other:\n  api_token: {trailing_api_token}\n"
    reg.write_text(text, encoding="utf-8")
    return reg


def _source_zshenv(home: Path, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "/usr/bin/env",
            "-i",
            "PATH=/usr/bin:/bin",
            f"HOME={home}",
            "/bin/sh",
            "-c",
            f'. "$HOME/.zshenv"; {command}',
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_run_replaces_original_004_block_records_005_and_preserves_004(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "brain"
    home.mkdir()
    root.mkdir()
    migration.zshenv_path(home).write_text(f"export FOO=bar\n\n{ORIGINAL_004_BLOCK}\n", encoding="utf-8")
    (root / ".migrations-run").write_text("004\n", encoding="utf-8")
    reg = _write_registry(home, {"api_token": "registry-token", "api_token_env": "LOCAL_BRAIN_API_TOKEN"})

    actions = migration.run(root, home=home, registry_path=reg)

    ztext = migration.zshenv_path(home).read_text(encoding="utf-8")
    assert "export FOO=bar" in ztext
    assert ztext.count(migration.BLOCK_START) == 1
    assert "/usr/bin/sed -E -n" in ztext
    assert "/usr/bin/head -n 1" in ztext
    assert "/usr/bin/tr -d" in ztext
    assert "python3 -c" not in ztext
    assert "import yaml" not in ztext
    assert (root / ".migrations-run").read_text(encoding="utf-8").splitlines() == ["004", "005"]
    assert any("recorded migration 005" in a for a in actions)

    repeat = migration.run(root, home=home, registry_path=reg)
    assert any("already applied" in a for a in repeat)
    assert migration.zshenv_path(home).read_text(encoding="utf-8") == ztext


def test_run_converges_hand_hardened_block_without_duplicate(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "brain"
    home.mkdir()
    root.mkdir()
    migration.zshenv_path(home).write_text(f"{HAND_HARDENED_BLOCK}\n", encoding="utf-8")
    reg = _write_registry(home, {"api_token": "registry-token"})

    migration.run(root, home=home, registry_path=reg)

    ztext = migration.zshenv_path(home).read_text(encoding="utf-8")
    assert ztext.count(migration.BLOCK_START) == 1
    assert ztext.count(migration.BLOCK_END) == 1
    assert "python3 -c" not in ztext
    assert "/usr/bin/sed -E -n" in ztext


def test_dependency_free_block_yields_registry_token_under_virgin_env(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "brain"
    home.mkdir()
    root.mkdir()
    reg = _write_registry(
        home,
        {"api_token": "first-token", "api_token_env": "LOCAL_BRAIN_API_TOKEN"},
        trailing_api_token="second-token",
    )

    migration.run(root, home=home, registry_path=reg)
    proc = _source_zshenv(
        home,
        'test "${LOCAL_BRAIN_API_TOKEN:-}" = "first-token" && printf SET',
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "SET"


def test_dependency_free_block_ignores_unrelated_earlier_api_token(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "brain"
    home.mkdir()
    root.mkdir()
    reg = _write_registry(
        home,
        {"api_token": "local-brain-token", "api_token_env": "LOCAL_BRAIN_API_TOKEN"},
        leading_api_token="wrong-token",
    )

    migration.run(root, home=home, registry_path=reg)
    proc = _source_zshenv(
        home,
        'test "${LOCAL_BRAIN_API_TOKEN:-}" = "local-brain-token" && printf SET',
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "SET"


def test_dependency_free_block_first_service_api_token_line_wins(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "brain"
    home.mkdir()
    root.mkdir()
    reg = home / ".brain" / "registry.yaml"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(
        "\n".join(
            [
                "settings:",
                "  local_brain_service:",
                "    api_token: first-token",
                "    api_token: second-token",
                "    enabled: true",
                "",
            ]
        ),
        encoding="utf-8",
    )

    migration.run(root, home=home, registry_path=reg)
    proc = _source_zshenv(
        home,
        'test "${LOCAL_BRAIN_API_TOKEN:-}" = "first-token" && printf SET',
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "SET"


def test_dependency_free_block_unquotes_single_quoted_yaml_token(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "brain"
    home.mkdir()
    root.mkdir()
    reg = home / ".brain" / "registry.yaml"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(
        "\n".join(
            [
                "settings:",
                "  local_brain_service:",
                "    api_token: '123'",
                "",
            ]
        ),
        encoding="utf-8",
    )

    migration.run(root, home=home, registry_path=reg)
    proc = _source_zshenv(
        home,
        'test "${LOCAL_BRAIN_API_TOKEN:-}" = "123" && printf SET',
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "SET"


def test_dependency_free_block_does_not_clobber_pre_set_env(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "brain"
    home.mkdir()
    root.mkdir()
    reg = _write_registry(home, {"api_token": "registry-token"})
    migration.run(root, home=home, registry_path=reg)

    proc = subprocess.run(
        [
            "/usr/bin/env",
            "-i",
            "PATH=/usr/bin:/bin",
            f"HOME={home}",
            "LOCAL_BRAIN_API_TOKEN=pre-set-token",
            "/bin/sh",
            "-c",
            '. "$HOME/.zshenv"; printf "%s" "$LOCAL_BRAIN_API_TOKEN"',
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "pre-set-token"


def test_dependency_free_block_unsets_empty_extraction(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "brain"
    home.mkdir()
    root.mkdir()
    reg = _write_registry(home, {"enabled": True}, trailing_api_token="wrong-token")
    migration.run(root, home=home, registry_path=reg)

    proc = _source_zshenv(
        home,
        'test "${LOCAL_BRAIN_API_TOKEN+x}" = "" && printf UNSET',
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "UNSET"


def test_run_respects_api_token_env_override_and_mirrors_claude_env(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "brain"
    home.mkdir()
    root.mkdir()
    reg = _write_registry(home, {"api_token": "registry-token", "api_token_env": "BRAIN_TOKEN"})

    migration.run(root, home=home, registry_path=reg)
    proc = _source_zshenv(
        home,
        'test "${BRAIN_TOKEN:-}" = "registry-token" && '
        'test "${LOCAL_BRAIN_API_TOKEN:-}" = "registry-token" && printf BOTH',
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "BOTH"
