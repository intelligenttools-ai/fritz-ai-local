"""Acceptance tests for kb_health.compute_kb_health (#180).

Acceptance bullets:
A. articles_total and articles_by_status counts (including default-status fallback).
B. articles_by_vault (store-mode single vault "_store" / "brain").
C. growth_by_day reflects articles' updated/mtime dates.
D. embedding stats from a written index.json; and no-index case returns zeros/null.
E. compile {total, ok, error, success_rate} from telemetry; zero-compiles -> null.
F. backlog.pending_captures_by_source reflects pending captures.
G. cache: second call returns cached result; force=True / invalidate recomputes.
H. malformed article file does not crash the scan.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from fritz_local_brain.config import Settings
from fritz_local_brain import kb_health
from fritz_local_brain.kb_health import compute_kb_health, invalidate_kb_health_cache
from fritz_local_brain.telemetry import record_event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(tmp_path: Path, **extra) -> Settings:
    brain_home = tmp_path / "brain"
    brain_home.mkdir(parents=True, exist_ok=True)
    return Settings(
        _env_file=None,
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_TELEMETRY_ENABLED=True,
        **extra,
    )


def _write_article(store: Path, rel_path: str, *, status: str | None = None, updated: str | None = None) -> Path:
    """Write a minimal article under *store / rel_path*."""
    path = store / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    fm_lines = ["---"]
    fm_lines.append(f"title: {path.stem}")
    if status is not None:
        fm_lines.append(f"status: {status}")
    if updated is not None:
        fm_lines.append(f"updated: {updated}")
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append("Body text.")
    path.write_text("\n".join(fm_lines), encoding="utf-8")
    return path


def _write_index_json(brain_home: Path, *, documents: list, skipped_keys: list | None = None) -> None:
    """Write a minimal index.json into the embeddings dir."""
    embeddings_dir = brain_home / "embeddings"
    embeddings_dir.mkdir(parents=True, exist_ok=True)
    data: dict = {
        "model": "test-model",
        "provider_fingerprint": "abc",
        "updated_at": "2026-01-01T00:00:00",
        "source_fingerprint": "xyz",
        "documents": documents,
        "skipped_keys": skipped_keys if skipped_keys is not None else [],
        "skipped_entries": [],
    }
    (embeddings_dir / "index.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_capture(brain_home: Path, source: str, name: str = "cap.md") -> None:
    cap_dir = brain_home / "capture" / source
    cap_dir.mkdir(parents=True, exist_ok=True)
    (cap_dir / name).write_text("# Capture\n\nPending capture.", encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixture: a mini brain with a handful of articles + telemetry events
# ---------------------------------------------------------------------------


@pytest.fixture()
def mini_brain(tmp_path: Path):
    """
    Store layout:
      knowledge/
        common/decisions/a.md   status=active,   updated=2025-03-01
        common/decisions/b.md   status=corroborated, updated=2025-03-01
        common/lessons/c.md     status=deprecated,  updated=2025-04-10
        common/lessons/d.md     (no status → default "active")
        common/context/index.md  ← excluded
        index.md                ← excluded
      Telemetry: 2 compile events (1 ok, 1 error)
      Backlog: 1 inbox capture
    """
    settings = _settings(tmp_path)
    store = settings.resolve_brain_store_path()
    store.mkdir(parents=True, exist_ok=True)

    _write_article(store, "common/decisions/a.md", status="active", updated="2025-03-01")
    _write_article(store, "common/decisions/b.md", status="corroborated", updated="2025-03-01")
    _write_article(store, "common/lessons/c.md", status="deprecated", updated="2025-04-10")
    _write_article(store, "common/lessons/d.md")  # no status frontmatter

    # index.md files must be excluded
    (store / "index.md").write_text("# Index\n", encoding="utf-8")
    (store / "common" / "context").mkdir(parents=True, exist_ok=True)
    (store / "common" / "context" / "index.md").write_text("# Context\n", encoding="utf-8")

    # Telemetry
    record_event(settings, "compile", status="ok")
    record_event(settings, "compile", status="error")

    # Backlog: 1 inbox capture
    _write_capture(settings.brain_home, "inbox")

    # Invalidate cache so this fresh brain is computed fresh
    invalidate_kb_health_cache(settings)

    return settings


# ---------------------------------------------------------------------------
# A. articles_total and articles_by_status
# ---------------------------------------------------------------------------


def test_articles_total_and_by_status(mini_brain: Settings) -> None:
    result = compute_kb_health(mini_brain)

    # 4 articles (index.md files excluded)
    assert result["articles_total"] == 4

    by_status = result["articles_by_status"]
    # All STATUS_VALUES present (even zeros)
    from fritz_local_brain.knowledge import STATUS_VALUES
    for s in STATUS_VALUES:
        assert s in by_status, f"Missing status key: {s}"

    assert by_status["active"] == 2       # a.md (explicit) + d.md (default fallback)
    assert by_status["corroborated"] == 1
    assert by_status["deprecated"] == 1
    assert by_status["superseded"] == 0
    assert by_status["historical"] == 0


def test_default_status_fallback(mini_brain: Settings) -> None:
    """An article with no status frontmatter must count as 'active'."""
    result = compute_kb_health(mini_brain)
    # d.md has no status; 'active' count must be 2 (a.md explicit + d.md fallback)
    assert result["articles_by_status"]["active"] == 2


# ---------------------------------------------------------------------------
# B. articles_by_vault (store-mode)
# ---------------------------------------------------------------------------


def test_articles_by_vault_store_mode(mini_brain: Settings) -> None:
    result = compute_kb_health(mini_brain)
    # In store mode the vault name is "brain"
    assert "brain" in result["articles_by_vault"]
    assert result["articles_by_vault"]["brain"] == 4


# ---------------------------------------------------------------------------
# C. growth_by_day
# ---------------------------------------------------------------------------


def test_growth_by_day_reflects_updated_dates(mini_brain: Settings) -> None:
    result = compute_kb_health(mini_brain)
    gbd = result["growth_by_day"]
    # a.md and b.md have updated=2025-03-01 → count 2
    assert gbd.get("2025-03-01") == 2
    # c.md has updated=2025-04-10 → count 1
    assert gbd.get("2025-04-10") == 1
    # d.md has no updated → falls back to mtime (today or whenever written);
    # it must appear in some date key
    total_from_gbd = sum(gbd.values())
    assert total_from_gbd == 4  # all 4 articles have some date


# ---------------------------------------------------------------------------
# D. embedding stats
# ---------------------------------------------------------------------------


def test_embedding_stats_with_index(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = settings.resolve_brain_store_path()
    store.mkdir(parents=True, exist_ok=True)

    docs = [{"vault": "brain", "path": "a.md"}, {"vault": "brain", "path": "b.md"}]
    skipped = [["brain", "c.md"], ["brain", "d.md"]]
    _write_index_json(settings.brain_home, documents=docs, skipped_keys=skipped)
    invalidate_kb_health_cache(settings)

    result = compute_kb_health(settings)
    emb = result["embedding"]
    assert emb["documents_indexed"] == 2
    assert emb["skipped"] == 2
    assert isinstance(emb["index_size_bytes"], int)
    assert emb["index_size_bytes"] > 0


def test_embedding_stats_no_index(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    invalidate_kb_health_cache(settings)

    result = compute_kb_health(settings)
    emb = result["embedding"]
    assert emb["documents_indexed"] == 0
    assert emb["skipped"] == 0
    assert emb["index_size_bytes"] is None


# ---------------------------------------------------------------------------
# E. compile stats from telemetry
# ---------------------------------------------------------------------------


def test_compile_stats_from_telemetry(mini_brain: Settings) -> None:
    result = compute_kb_health(mini_brain)
    c = result["compile"]
    assert c["total"] == 2
    assert c["ok"] == 1
    assert c["error"] == 1
    assert c["success_rate"] == pytest.approx(0.5)


def test_compile_stats_zero_compiles(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    invalidate_kb_health_cache(settings)
    result = compute_kb_health(settings)
    c = result["compile"]
    assert c["total"] == 0
    assert c["ok"] == 0
    assert c["error"] == 0
    assert c["success_rate"] is None  # no ZeroDivision


# ---------------------------------------------------------------------------
# F. backlog
# ---------------------------------------------------------------------------


def test_backlog_pending_captures(mini_brain: Settings) -> None:
    result = compute_kb_health(mini_brain)
    backlog = result["backlog"]
    assert "pending_captures_by_source" in backlog
    sources = backlog["pending_captures_by_source"]
    # We wrote 1 inbox capture
    assert sources.get("inbox", 0) >= 1


# ---------------------------------------------------------------------------
# G. cache behaviour
# ---------------------------------------------------------------------------


def test_cache_returns_same_object_on_second_call(mini_brain: Settings) -> None:
    first = compute_kb_health(mini_brain)
    second = compute_kb_health(mini_brain)
    assert first is second  # exact same dict from cache


def test_force_bypasses_cache(mini_brain: Settings) -> None:
    first = compute_kb_health(mini_brain)
    second = compute_kb_health(mini_brain, force=True)
    # force=True always recomputes → different dict object
    assert first is not second
    # But values should be equivalent (same brain state)
    assert second["articles_total"] == first["articles_total"]


def test_invalidate_cache_triggers_recompute(mini_brain: Settings) -> None:
    store = mini_brain.resolve_brain_store_path()
    first = compute_kb_health(mini_brain)
    assert first["articles_total"] == 4

    # Add a new article while the cache is hot
    _write_article(store, "common/decisions/new.md", status="active", updated="2026-01-01")

    # Without invalidation the cached result is returned
    still_cached = compute_kb_health(mini_brain)
    assert still_cached is first  # same object — cache was not dropped

    # After invalidation the scan picks up the new article
    invalidate_kb_health_cache(mini_brain)
    fresh = compute_kb_health(mini_brain)
    assert fresh["articles_total"] == 5


# ---------------------------------------------------------------------------
# H. malformed article does not crash
# ---------------------------------------------------------------------------


def test_malformed_article_does_not_crash(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = settings.resolve_brain_store_path()
    store.mkdir(parents=True, exist_ok=True)

    # Malformed YAML front matter
    (store / "bad.md").write_text("---\n: bad: yaml: [[\n---\n\nBody.\n", encoding="utf-8")
    # A totally empty file
    (store / "empty.md").write_text("", encoding="utf-8")
    # A good article alongside the bad ones
    _write_article(store, "good.md", status="active", updated="2026-01-01")

    invalidate_kb_health_cache(settings)
    result = compute_kb_health(settings)

    # Must not raise, must count the good article plus the bad ones (with default status)
    assert result["articles_total"] >= 1  # at minimum the good article
    # The function must complete without exception (this line is reached)


# ---------------------------------------------------------------------------
# I. registry-mode (_scan_vaults branch) coverage
# ---------------------------------------------------------------------------


def _build_registry_vault(tmp_path: Path, *, manifest_text: str) -> Settings:
    """Build a brain_home with a registry pointing at one vault.

    The vault has ``.brain/manifest.yaml`` (content controlled by *manifest_text*)
    and a ``knowledge`` directory. Mirrors the registry+manifest fixture pattern in
    test_correlation.py.
    """
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    brain_home.mkdir(parents=True, exist_ok=True)
    (vault_path / ".brain").mkdir(parents=True)
    (vault_path / "knowledge").mkdir()
    (vault_path / ".brain" / "manifest.yaml").write_text(manifest_text, encoding="utf-8")
    (brain_home / "registry.yaml").write_text(
        f"vaults:\n  myvault:\n    path: {vault_path}\n", encoding="utf-8"
    )
    return Settings(
        _env_file=None,
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_TELEMETRY_ENABLED=True,
    )


def test_registry_mode_articles_by_vault(tmp_path: Path) -> None:
    """A registered vault with a valid manifest is scanned via _scan_vaults."""
    settings = _build_registry_vault(
        tmp_path, manifest_text="paths:\n  knowledge: knowledge\nexclude: []\n"
    )
    knowledge_root = tmp_path / "vault" / "knowledge"

    _write_article(knowledge_root, "decisions/a.md", status="active", updated="2025-05-01")
    _write_article(knowledge_root, "decisions/b.md", status="deprecated", updated="2025-05-01")
    _write_article(knowledge_root, "lessons/c.md", status="corroborated", updated="2025-06-02")
    # index.md must be excluded
    (knowledge_root / "index.md").write_text("# Index\n", encoding="utf-8")

    invalidate_kb_health_cache(settings)
    result = compute_kb_health(settings)

    assert result["articles_total"] == 3
    assert result["articles_by_vault"].get("myvault") == 3
    # store-mode vault name must NOT appear in registry mode
    assert "brain" not in result["articles_by_vault"]
    assert result["articles_by_status"]["active"] == 1
    assert result["articles_by_status"]["deprecated"] == 1
    assert result["articles_by_status"]["corroborated"] == 1
    # growth reflects the two updated dates
    assert result["growth_by_day"].get("2025-05-01") == 2
    assert result["growth_by_day"].get("2025-06-02") == 1


def test_malformed_manifest_does_not_crash(tmp_path: Path) -> None:
    """A corrupt .brain/manifest.yaml must not crash compute_kb_health (locks FIX 1).

    With the only registered vault's manifest unparseable, that vault is treated as
    'no manifest' → no manifests at all → the scan falls through to store mode and
    returns a valid result instead of raising yaml.YAMLError.
    """
    settings = _build_registry_vault(
        tmp_path, manifest_text="paths: [this: is: not: valid: yaml\n  ::::\n"
    )
    # Put an article under the vault knowledge root; since the manifest is unreadable
    # the vault contributes nothing (falls back to store mode, which is empty here).
    knowledge_root = tmp_path / "vault" / "knowledge"
    _write_article(knowledge_root, "decisions/a.md", status="active", updated="2025-05-01")

    invalidate_kb_health_cache(settings)
    # Must not raise.
    result = compute_kb_health(settings)

    # The vault with the corrupt manifest contributes 0; result is still well-formed.
    assert "myvault" not in result["articles_by_vault"]
    assert result["articles_total"] == 0
    # All sub-stats present and sane.
    assert result["compile"]["success_rate"] is None
    assert result["embedding"]["index_size_bytes"] is None
    assert "pending_captures_by_source" in result["backlog"]
