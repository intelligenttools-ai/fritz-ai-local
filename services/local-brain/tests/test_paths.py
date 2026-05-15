from __future__ import annotations

from pathlib import Path

from fritz_local_brain.paths import PathMapper


def test_maps_home_relative_path_to_exact_nested_container_mount() -> None:
    mapper = PathMapper("/Users/test/Notes/MyVault=/vaults/myvault")

    assert mapper.to_container("~/Notes/MyVault") == Path("/vaults/myvault")
    assert mapper.to_container("~/Notes/MyVault/knowledge/a.md") == Path("/vaults/myvault/knowledge/a.md")


def test_does_not_map_home_path_by_basename_only() -> None:
    mapper = PathMapper("/Users/test/Notes/MyVault=/vaults/myvault")

    assert mapper.to_container("~/MyVault") != Path("/vaults/myvault")


def test_does_not_preserve_parent_traversal_in_container_mapping() -> None:
    mapper = PathMapper("/Users/test/Notes/MyVault=/vaults/myvault")

    assert mapper.to_container("~/Notes/MyVault/../Other/secret.md") != Path("/vaults/myvault/../Other/secret.md")


def test_prefers_more_specific_path_mapping() -> None:
    mapper = PathMapper("/Users/test/Notes=/vaults/notes,/Users/test/Notes/MyVault=/vaults/myvault")

    assert mapper.to_container("~/Notes/MyVault/knowledge/a.md") == Path("/vaults/myvault/knowledge/a.md")
