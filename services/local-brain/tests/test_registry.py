"""Tests for registry optional loader and ExternalTarget schema (WI10)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from fritz_local_brain.config import Settings
from fritz_local_brain.registry import (
    ExternalTarget,
    RegistryError,
    load_external_targets,
    load_registry_optional,
)


# ---------------------------------------------------------------------------
# Brain core independence — registry absent
# ---------------------------------------------------------------------------


def test_load_registry_optional_returns_empty_dict_when_absent(tmp_path: Path) -> None:
    """load_registry_optional returns {} when no registry.yaml exists."""
    result = load_registry_optional(tmp_path)
    assert result == {}


def test_load_external_targets_returns_empty_list_when_registry_absent(tmp_path: Path) -> None:
    """load_external_targets returns [] when registry.yaml is absent."""
    result = load_external_targets(tmp_path)
    assert result == []


def test_settings_resolve_brain_store_path_independent_of_registry(tmp_path: Path) -> None:
    """Settings.resolve_brain_store_path() resolves to <brain_home>/knowledge without registry."""
    settings = Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path)
    store = settings.resolve_brain_store_path()
    assert store == tmp_path / "knowledge"


def test_load_external_targets_returns_empty_list_when_no_external_targets_key(
    tmp_path: Path,
) -> None:
    """Returns [] when registry.yaml is present but has no external_targets key."""
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        yaml.dump({"version": 1, "vaults": {"my-vault": {"path": "~/Notes"}}}),
        encoding="utf-8",
    )
    result = load_external_targets(tmp_path)
    assert result == []


# ---------------------------------------------------------------------------
# Schema: valid external_targets of each kind
# ---------------------------------------------------------------------------


def _write_registry(tmp_path: Path, external_targets: dict) -> None:
    content = {"version": 1, "external_targets": external_targets}
    (tmp_path / "registry.yaml").write_text(yaml.dump(content), encoding="utf-8")


def test_local_vault_target_parses_correctly(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        {
            "team-vault": {
                "kind": "local-vault",
                "connection": "~/Notes/TeamVault",
                "mirror_mode": "full-summary",
            }
        },
    )
    targets = load_external_targets(tmp_path)
    assert len(targets) == 1
    t = targets[0]
    assert t.name == "team-vault"
    assert t.kind == "local-vault"
    assert t.connection == "~/Notes/TeamVault"
    assert t.mirror_mode == "full-summary"
    assert t.auth is None


def test_mcp_target_parses_correctly(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        {
            "team-mcp": {
                "kind": "mcp",
                "connection": "mcp://obsidian-bridge",
                "auth": "OBSIDIAN_MCP_TOKEN",
                "mirror_mode": "index-only",
            }
        },
    )
    targets = load_external_targets(tmp_path)
    assert len(targets) == 1
    t = targets[0]
    assert t.name == "team-mcp"
    assert t.kind == "mcp"
    assert t.connection == "mcp://obsidian-bridge"
    assert t.auth == "OBSIDIAN_MCP_TOKEN"
    assert t.mirror_mode == "index-only"


def test_drive_target_parses_correctly(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        {
            "shared-drive": {
                "kind": "drive",
                "connection": "/mnt/shared/knowledge",
            }
        },
    )
    targets = load_external_targets(tmp_path)
    assert len(targets) == 1
    t = targets[0]
    assert t.name == "shared-drive"
    assert t.kind == "drive"
    assert t.connection == "/mnt/shared/knowledge"
    assert t.mirror_mode == "index-only"  # default


def test_offsite_target_parses_correctly(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        {
            "offsite-affine": {
                "kind": "offsite",
                "connection": "https://affine.example.com/workspace/abc",
                "auth": "AFFINE_TOKEN",
                "mirror_mode": "full-summary",
            }
        },
    )
    targets = load_external_targets(tmp_path)
    assert len(targets) == 1
    t = targets[0]
    assert t.name == "offsite-affine"
    assert t.kind == "offsite"
    assert t.mirror_mode == "full-summary"


def test_default_mirror_mode_is_index_only(tmp_path: Path) -> None:
    """When mirror_mode is omitted, it defaults to 'index-only'."""
    _write_registry(
        tmp_path,
        {
            "no-mode": {
                "kind": "local-vault",
                "connection": "~/Notes/Vault",
            }
        },
    )
    targets = load_external_targets(tmp_path)
    assert targets[0].mirror_mode == "index-only"


def test_all_four_kinds_parse_in_one_registry(tmp_path: Path) -> None:
    """A registry with all four kinds parses into four ExternalTarget objects."""
    _write_registry(
        tmp_path,
        {
            "t-offsite": {"kind": "offsite", "connection": "https://example.com"},
            "t-mcp": {"kind": "mcp", "connection": "mcp://server"},
            "t-local-vault": {"kind": "local-vault", "connection": "~/Vault"},
            "t-drive": {"kind": "drive", "connection": "/mnt/share"},
        },
    )
    targets = load_external_targets(tmp_path)
    assert len(targets) == 4
    kinds = {t.kind for t in targets}
    assert kinds == {"local-vault", "mcp", "drive", "offsite"}


def test_results_are_sorted_by_name(tmp_path: Path) -> None:
    """load_external_targets returns targets in deterministic (name-sorted) order."""
    _write_registry(
        tmp_path,
        {
            "zzz-last": {"kind": "drive", "connection": "/mnt/z"},
            "aaa-first": {"kind": "local-vault", "connection": "~/A"},
            "mmm-mid": {"kind": "mcp", "connection": "mcp://m"},
        },
    )
    targets = load_external_targets(tmp_path)
    names = [t.name for t in targets]
    assert names == ["aaa-first", "mmm-mid", "zzz-last"]


# ---------------------------------------------------------------------------
# Schema: invalid kind / mirror_mode raises
# ---------------------------------------------------------------------------


def test_unknown_kind_raises_registry_error(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        {
            "bad-kind": {
                "kind": "nonexistent-kind",
                "connection": "somewhere",
            }
        },
    )
    with pytest.raises(RegistryError):
        load_external_targets(tmp_path)


def test_unknown_mirror_mode_raises_registry_error(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        {
            "bad-mode": {
                "kind": "local-vault",
                "connection": "~/Vault",
                "mirror_mode": "total-copy",  # invalid
            }
        },
    )
    with pytest.raises(RegistryError):
        load_external_targets(tmp_path)


# ---------------------------------------------------------------------------
# Extra fields allowed (per-kind passthrough)
# ---------------------------------------------------------------------------


def test_extra_fields_are_preserved(tmp_path: Path) -> None:
    """Extra per-kind fields are accepted (model_config extra='allow')."""
    _write_registry(
        tmp_path,
        {
            "rich-target": {
                "kind": "mcp",
                "connection": "mcp://bridge",
                "custom_timeout": 30,
                "retry_policy": "exponential",
            }
        },
    )
    targets = load_external_targets(tmp_path)
    assert len(targets) == 1
    t = targets[0]
    assert t.kind == "mcp"
    # Extra fields accessible via model's extra storage.
    assert t.model_extra is not None
    assert t.model_extra.get("custom_timeout") == 30
    assert t.model_extra.get("retry_policy") == "exponential"


# ---------------------------------------------------------------------------
# Mode-switch invariance — adding/removing registry does NOT change store
# ---------------------------------------------------------------------------


def test_mode_switch_does_not_change_store_path(tmp_path: Path) -> None:
    """Adding or removing registry.yaml never changes resolve_brain_store_path()."""
    settings = Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path)

    # Without registry.
    path_before = settings.resolve_brain_store_path()
    assert path_before == tmp_path / "knowledge"

    # Add registry with external_targets.
    _write_registry(
        tmp_path,
        {"my-vault": {"kind": "local-vault", "connection": "~/Notes"}},
    )
    path_with_registry = settings.resolve_brain_store_path()
    assert path_with_registry == path_before

    # Remove registry.
    (tmp_path / "registry.yaml").unlink()
    path_after = settings.resolve_brain_store_path()
    assert path_after == path_before


def test_mode_switch_does_not_alter_store_contents(tmp_path: Path) -> None:
    """Adding/removing registry.yaml leaves brain store files untouched."""
    store = tmp_path / "knowledge"
    store.mkdir(parents=True)
    article = store / "my-article.md"
    article.write_text("---\ntype: note\n---\n# Hello\n", encoding="utf-8")

    original_content = article.read_text(encoding="utf-8")

    # Add a registry with external_targets.
    _write_registry(
        tmp_path,
        {"ext": {"kind": "offsite", "connection": "https://example.com"}},
    )
    assert article.read_text(encoding="utf-8") == original_content

    # Validate external targets loaded without touching store.
    targets = load_external_targets(tmp_path)
    assert len(targets) == 1
    assert article.read_text(encoding="utf-8") == original_content

    # Remove the registry.
    (tmp_path / "registry.yaml").unlink()
    assert article.read_text(encoding="utf-8") == original_content
    assert load_external_targets(tmp_path) == []
