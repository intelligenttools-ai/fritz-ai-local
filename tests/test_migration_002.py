"""Tests for migrations/002-reconcile-brain-layout.py.

The live ``~/.brain`` is NEVER touched: every test builds a synthetic populated
brain under ``tmp_path`` and points the migration at it via the ``BRAIN_HOME``
env override (and ``run(root=...)`` for direct calls).
"""

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations" / "002-reconcile-brain-layout.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_002", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_brain(tmp_path: Path, registry_content: str | None = "vaults_default") -> Path:
    """Build a synthetic populated brain. Returns the brain root."""
    root = tmp_path / "brain"
    inbox = root / "capture" / "inbox"
    auto = root / "capture" / "auto"
    inbox.mkdir(parents=True)
    auto.mkdir(parents=True)

    (inbox / "2026-01-01-first.md").write_text("# First\n\nbody one\n", encoding="utf-8")
    (inbox / "2026-01-02-second.md").write_text("# Second\n\nbody two\n", encoding="utf-8")
    (auto / ".seen").write_text("seen-marker\n", encoding="utf-8")
    (root / "log.md").write_text("2026-01-01 00:00 | INGEST | seed\n", encoding="utf-8")

    if registry_content == "vaults_default":
        (root / "registry.yaml").write_text(
            yaml.safe_dump({"vaults": {"work": {"path": "/some/path"}}}, sort_keys=False),
            encoding="utf-8",
        )
    elif registry_content is not None:
        (root / "registry.yaml").write_text(registry_content, encoding="utf-8")
    # registry_content is None -> no registry.yaml created

    return root


def _capture_hashes(root: Path) -> dict[str, str]:
    files = [
        root / "capture" / "inbox" / "2026-01-01-first.md",
        root / "capture" / "inbox" / "2026-01-02-second.md",
        root / "capture" / "auto" / ".seen",
        root / "log.md",
    ]
    return {str(f.relative_to(root)): _sha(f) for f in files}


# --- brain_home override ----------------------------------------------------


def test_brain_home_uses_env(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_HOME", str(tmp_path / "brain"))
    assert migration.brain_home() == (tmp_path / "brain").resolve()


def test_brain_home_falls_back(monkeypatch):
    monkeypatch.delenv("BRAIN_HOME", raising=False)
    assert migration.brain_home() == Path.home() / ".brain"


# --- first run --------------------------------------------------------------


def test_first_run_creates_daily_and_settings(tmp_path):
    root = _make_brain(tmp_path)
    before = _capture_hashes(root)

    actions = migration.run(root)

    # daily/ now exists; inbox/auto preserved.
    assert (root / "capture" / "daily").is_dir()
    assert (root / "capture" / "inbox").is_dir()
    assert (root / "capture" / "auto").is_dir()

    # settings: {} added, existing keys preserved.
    registry = yaml.safe_load((root / "registry.yaml").read_text())
    assert registry["settings"] == {}
    assert registry["vaults"] == {"work": {"path": "/some/path"}}

    # .migrations-run records 002.
    assert (root / ".migrations-run").read_text().splitlines() == ["002"]

    # pre-existing files byte-for-byte unchanged.
    assert _capture_hashes(root) == before

    assert any("daily" in a for a in actions)


# --- idempotency ------------------------------------------------------------


def test_second_run_is_noop(tmp_path):
    root = _make_brain(tmp_path)
    migration.run(root)

    registry_before = (root / "registry.yaml").read_text()
    capture_before = _capture_hashes(root)
    daily_dir = root / "capture" / "daily"

    actions = migration.run(root)

    assert (root / "registry.yaml").read_text() == registry_before
    assert _capture_hashes(root) == capture_before
    assert daily_dir.is_dir()
    assert (root / ".migrations-run").read_text().splitlines() == ["002"]
    assert any("already applied" in a for a in actions)


# --- existing settings block preserved --------------------------------------


def test_existing_settings_block_unchanged(tmp_path):
    registry = yaml.safe_dump(
        {"vaults": {"work": {}}, "settings": {"local_brain_service": {"enabled": True}}},
        sort_keys=False,
    )
    root = _make_brain(tmp_path, registry_content=registry)
    before = (root / "registry.yaml").read_text()

    actions = migration.run(root)

    assert (root / "registry.yaml").read_text() == before
    assert any("already present" in a for a in actions)


# --- no registry.yaml -------------------------------------------------------


def test_no_registry_still_creates_dirs(tmp_path):
    root = _make_brain(tmp_path, registry_content=None)

    actions = migration.run(root)

    assert not (root / "registry.yaml").exists()
    assert (root / "capture" / "daily").is_dir()
    assert (root / ".migrations-run").read_text().splitlines() == ["002"]
    assert any("skipped registry.yaml" in a for a in actions)


# --- dry-run writes nothing -------------------------------------------------


def test_dry_run_writes_nothing(tmp_path):
    root = _make_brain(tmp_path)
    registry_before = (root / "registry.yaml").read_text()
    capture_before = _capture_hashes(root)

    actions = migration.run(root, dry_run=True)

    assert not (root / "capture" / "daily").exists()
    assert (root / "registry.yaml").read_text() == registry_before
    assert _capture_hashes(root) == capture_before
    assert not (root / ".migrations-run").exists()

    # summary still lists intended changes.
    assert any("would create" in a for a in actions)
    assert any("would add empty settings" in a for a in actions)
    assert any("would record" in a for a in actions)


# --- end-to-end via subprocess (env override + CLI flags) -------------------


def test_cli_apply_via_env(tmp_path, monkeypatch):
    root = _make_brain(tmp_path)
    env = {**_subprocess_env(), "BRAIN_HOME": str(root)}
    result = subprocess.run(
        [sys.executable, str(MIGRATION_PATH)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert (root / "capture" / "daily").is_dir()
    assert (root / ".migrations-run").read_text().splitlines() == ["002"]
    assert "applied" in result.stdout


def test_cli_dry_run_flag(tmp_path):
    root = _make_brain(tmp_path)
    env = {**_subprocess_env(), "BRAIN_HOME": str(root)}
    result = subprocess.run(
        [sys.executable, str(MIGRATION_PATH), "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert not (root / "capture" / "daily").exists()
    assert not (root / ".migrations-run").exists()
    assert "DRY-RUN" in result.stdout


def test_cli_dry_run_env(tmp_path):
    root = _make_brain(tmp_path)
    env = {**_subprocess_env(), "BRAIN_HOME": str(root), "FRITZ_MIGRATION_DRY_RUN": "1"}
    result = subprocess.run(
        [sys.executable, str(MIGRATION_PATH)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert not (root / "capture" / "daily").exists()
    assert "DRY-RUN" in result.stdout


def _subprocess_env() -> dict:
    import os

    # Preserve PATH/PYTHONPATH etc. but strip any inherited BRAIN_HOME so each
    # test sets its own.
    env = dict(os.environ)
    env.pop("BRAIN_HOME", None)
    env.pop("FRITZ_MIGRATION_DRY_RUN", None)
    return env
