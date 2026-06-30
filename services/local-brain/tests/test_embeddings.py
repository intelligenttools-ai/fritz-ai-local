"""Regression tests for the embedding index build defects fixed in #166.

Tests:
1. Per-document non-fatal embed: a single _embed_text failure must NOT abort the
   whole index build; successful documents are still written.
2. Input truncation: _document must cap text to embedding_max_input_chars (default
   1800), not the old 4000 hardcoded value.
3. Default for embedding_max_input_chars is 1800 (regression guard).
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from fritz_local_brain import embeddings as emb
from fritz_local_brain.config import Settings
from fritz_local_brain.embeddings import _document, _refresh_embedding_index_unlocked
from fritz_local_brain.models import EmbeddingIndexRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_stat() -> os.stat_result:
    """Return a minimal stat_result with mtime=0 and size=0."""
    return Path(__file__).stat()  # any real file's stat; fields aren't load-bearing here


def _settings(tmp_path: Path, **extra) -> Settings:
    brain_home = tmp_path / "brain"
    brain_home.mkdir(parents=True, exist_ok=True)
    return Settings(
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_EMBEDDING_ENABLED=True,
        LOCAL_BRAIN_EMBEDDING_PROTOCOL="openai-compatible",
        LOCAL_BRAIN_EMBEDDING_BASE_URL="http://localhost:9999/v1",
        **extra,
    )


# ---------------------------------------------------------------------------
# 1. Per-document non-fatal embed
# ---------------------------------------------------------------------------


def test_single_failing_document_does_not_abort_index_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A _embed_text failure for one document must be skipped; other docs are indexed."""
    settings = _settings(tmp_path)

    # Two documents: first raises, second succeeds.
    doc_fail = {
        "vault": "brain",
        "path": "fail.md",
        "title": "Fail",
        "snippet": "fail snippet",
        "source_mtime_ns": 0,
        "source_size": 5,
        "content_hash": "abc",
        "text": "fail content",
    }
    doc_ok = {
        "vault": "brain",
        "path": "ok.md",
        "title": "Ok",
        "snippet": "ok snippet",
        "source_mtime_ns": 1,
        "source_size": 8,
        "content_hash": "def",
        "text": "ok content",
    }

    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [doc_fail, doc_ok])

    call_count = 0

    async def fake_embed(settings, text: str) -> list[float]:
        nonlocal call_count
        call_count += 1
        if text == "fail content":
            raise RuntimeError("400 Bad Request: input length exceeds context length")
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(emb, "_embed_text", fake_embed)

    result = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))

    # Index must be written (not aborted).
    assert result.indexed is True, f"Expected indexed=True, got error: {result.error}"
    assert result.error is None

    # Only the successful doc is counted.
    assert result.documents_indexed == 1

    # Index file must exist on disk.
    index_file = settings.brain_home / "embeddings" / "index.json"
    assert index_file.exists(), "Index file must be written even when one document fails"

    data = json.loads(index_file.read_text(encoding="utf-8"))
    paths_in_index = [d["path"] for d in data["documents"]]
    assert "ok.md" in paths_in_index, "Successful document must be in index"
    assert "fail.md" not in paths_in_index, "Failed document must be skipped"


def test_skipped_document_is_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A skipped document due to embed failure must produce an EMBEDDINGS log entry."""
    settings = _settings(tmp_path)

    doc = {
        "vault": "brain",
        "path": "bad.md",
        "title": "Bad",
        "snippet": "s",
        "source_mtime_ns": 0,
        "source_size": 3,
        "content_hash": "xyz",
        "text": "bad",
    }
    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [doc])

    async def fake_embed(settings, text: str) -> list[float]:
        raise RuntimeError("context length exceeded")

    monkeypatch.setattr(emb, "_embed_text", fake_embed)

    log_calls: list[tuple] = []

    def fake_log(brain_home, operation, summary, dry_run):
        log_calls.append((operation, summary))

    monkeypatch.setattr(emb, "append_global_log", fake_log)

    asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))

    skip_logs = [(op, msg) for op, msg in log_calls if op == "EMBEDDINGS" and "skipped" in msg.lower()]
    assert skip_logs, f"Expected an EMBEDDINGS skip log entry; got log_calls={log_calls}"
    assert "bad.md" in skip_logs[0][1], "Skip log must mention the failing document path"


def test_all_documents_fail_preserves_existing_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When ALL docs fail and a good index already exists, the existing index is left intact."""
    settings = _settings(tmp_path)

    doc_good = {
        "vault": "brain",
        "path": "good.md",
        "title": "Good",
        "snippet": "g",
        "source_mtime_ns": 1,
        "source_size": 5,
        "content_hash": "aaa",
        "text": "good content",
    }
    doc_bad = {
        "vault": "brain",
        "path": "bad.md",
        "title": "Bad",
        "snippet": "b",
        "source_mtime_ns": 2,
        "source_size": 3,
        "content_hash": "bbb",
        "text": "bad content",
    }

    # --- First refresh: doc_good succeeds → writes a good index ---
    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [doc_good])

    async def fake_embed_ok(settings, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(emb, "_embed_text", fake_embed_ok)
    monkeypatch.setattr(emb, "append_global_log", lambda *a, **kw: None)

    result1 = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))
    assert result1.indexed is True
    assert result1.documents_indexed == 1

    index_file = settings.brain_home / "embeddings" / "index.json"
    good_mtime = index_file.stat().st_mtime_ns
    good_content = index_file.read_text(encoding="utf-8")

    # --- Second refresh: all docs fail (outage simulation) → must NOT overwrite ---
    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [doc_bad])

    async def fake_embed_fail(settings, text: str) -> list[float]:
        raise RuntimeError("endpoint unreachable")

    monkeypatch.setattr(emb, "_embed_text", fake_embed_fail)

    result2 = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))

    assert result2.error is not None, "result.error must be set when all docs fail"
    assert result2.indexed is False or result2.indexed is None, "indexed must not be True when all fail"
    # Index file must be unchanged (same content and mtime).
    assert index_file.read_text(encoding="utf-8") == good_content, "Existing index must not be overwritten on full outage"


def test_all_documents_fail_no_existing_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When ALL docs fail and NO index exists, no index file is created and result.error is set."""
    settings = _settings(tmp_path)

    doc = {
        "vault": "brain",
        "path": "bad.md",
        "title": "Bad",
        "snippet": "s",
        "source_mtime_ns": 0,
        "source_size": 3,
        "content_hash": "xyz",
        "text": "bad",
    }
    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [doc])

    async def fake_embed(settings, text: str) -> list[float]:
        raise RuntimeError("endpoint unreachable")

    monkeypatch.setattr(emb, "_embed_text", fake_embed)
    monkeypatch.setattr(emb, "append_global_log", lambda *a, **kw: None)

    result = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))

    assert result.error is not None, "result.error must be set when all docs fail with no existing index"
    index_file = settings.brain_home / "embeddings" / "index.json"
    assert not index_file.exists(), "No index file should be created when all docs fail"


def test_skipped_docs_retried_on_next_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A doc skipped (embed error) on one refresh must be retried on the next non-force refresh."""
    settings = _settings(tmp_path)

    doc_a = {
        "vault": "brain",
        "path": "a.md",
        "title": "A",
        "snippet": "a snippet",
        "source_mtime_ns": 10,
        "source_size": 5,
        "content_hash": "aaa",
        "text": "a content",
    }
    doc_b = {
        "vault": "brain",
        "path": "b.md",
        "title": "B",
        "snippet": "b snippet",
        "source_mtime_ns": 20,
        "source_size": 5,
        "content_hash": "bbb",
        "text": "b content",
    }

    # --- Refresh 1: doc_b fails, doc_a succeeds ---
    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [doc_a, doc_b])

    async def fake_embed_first(settings, text: str) -> list[float]:
        if text == "b content":
            raise RuntimeError("transient error")
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(emb, "_embed_text", fake_embed_first)
    monkeypatch.setattr(emb, "append_global_log", lambda *a, **kw: None)

    result1 = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))
    assert result1.indexed is True
    assert result1.documents_indexed == 1

    # --- Refresh 2 (non-force): both docs present; b should now succeed → index rebuilt ---
    async def fake_embed_second(settings, text: str) -> list[float]:
        return [0.4, 0.5, 0.6]

    monkeypatch.setattr(emb, "_embed_text", fake_embed_second)

    result2 = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=False)))
    assert result2.indexed is True, f"Expected indexed=True on second refresh, error: {result2.error}"
    assert result2.documents_indexed == 2, f"Expected 2 docs after retry, got {result2.documents_indexed}"

    index_file = settings.brain_home / "embeddings" / "index.json"
    data = json.loads(index_file.read_text(encoding="utf-8"))
    paths_in_index = [d["path"] for d in data["documents"]]
    assert "b.md" in paths_in_index, "doc B must now be in the index after retry"
    assert "a.md" in paths_in_index, "doc A must still be in the index"


# ---------------------------------------------------------------------------
# 1b. Permanently-skipped docs must not mark the index stale for search (#170)
# ---------------------------------------------------------------------------


def test_oversize_doc_does_not_mark_index_stale_for_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ACCEPTANCE (#170): a permanently un-embeddable doc must not disable vector search."""
    settings = _settings(tmp_path)

    doc_ok = {
        "vault": "brain",
        "path": "ok.md",
        "title": "Ok",
        "snippet": "ok snippet",
        "source_mtime_ns": 1,
        "source_size": 8,
        "content_hash": "def",
        "text": "ok content",
    }
    doc_oversize = {
        "vault": "brain",
        "path": "huge.md",
        "title": "Huge",
        "snippet": "huge snippet",
        "source_mtime_ns": 2,
        "source_size": 9999,
        "content_hash": "ghi",
        "text": "oversize content",
    }

    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [doc_ok, doc_oversize])

    async def fake_embed(settings, text: str) -> list[float]:
        if text == "oversize content":
            raise RuntimeError("400: input length exceeds context length")
        # ok doc text AND the query text both get the same fixed vector → high cosine.
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(emb, "_embed_text", fake_embed)
    monkeypatch.setattr(emb, "append_global_log", lambda *a, **kw: None)

    result = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))
    assert result.indexed is True, f"Expected indexed=True, error: {result.error}"
    assert result.documents_indexed == 1

    index_file = settings.brain_home / "embeddings" / "index.json"
    data = json.loads(index_file.read_text(encoding="utf-8"))
    skipped_keys = data.get("skipped_keys")
    assert skipped_keys == [["brain", "huge.md"]], f"Expected skipped_keys for the oversize doc, got {skipped_keys}"

    # The oversize doc is permanently skipped, so search-side freshness must be OK.
    assert emb.embedding_index_unavailable_reason(settings) is None

    matches = asyncio.run(emb.search_embedding_index(settings, "query text", 3))
    assert matches, "Vector search must return the ok doc, not [] due to a stale-index verdict"
    assert any(m.path == "ok.md" for m in matches)


def test_changed_indexed_doc_still_marks_index_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuine change to an indexed doc must still invalidate the index (no skips case)."""
    settings = _settings(tmp_path)

    doc = {
        "vault": "brain",
        "path": "ok.md",
        "title": "Ok",
        "snippet": "ok snippet",
        "source_mtime_ns": 1,
        "source_size": 8,
        "content_hash": "def",
        "text": "ok content",
    }

    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [dict(doc)])

    async def fake_embed(settings, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(emb, "_embed_text", fake_embed)
    monkeypatch.setattr(emb, "append_global_log", lambda *a, **kw: None)

    result = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))
    assert result.indexed is True
    assert result.documents_indexed == 1
    assert emb.embedding_index_unavailable_reason(settings) is None

    # Now the doc's content/mtime change → must read as stale.
    changed = dict(doc)
    changed["content_hash"] = "CHANGED"
    changed["source_mtime_ns"] = 999
    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [changed])

    assert emb.embedding_index_unavailable_reason(settings) == "Embedding index is stale; waiting for compile/ingest refresh"
    assert asyncio.run(emb.search_embedding_index(settings, "query text", 3)) == []


def test_new_unskipped_doc_marks_index_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A brand-new doc (not in skipped_keys) must invalidate the index."""
    settings = _settings(tmp_path)

    doc = {
        "vault": "brain",
        "path": "ok.md",
        "title": "Ok",
        "snippet": "ok snippet",
        "source_mtime_ns": 1,
        "source_size": 8,
        "content_hash": "def",
        "text": "ok content",
    }

    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [dict(doc)])

    async def fake_embed(settings, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(emb, "_embed_text", fake_embed)
    monkeypatch.setattr(emb, "append_global_log", lambda *a, **kw: None)

    result = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))
    assert result.indexed is True
    assert emb.embedding_index_unavailable_reason(settings) is None

    # Add a brand-new doc that was never skipped → must invalidate.
    new_doc = {
        "vault": "brain",
        "path": "brand-new.md",
        "title": "New",
        "snippet": "new snippet",
        "source_mtime_ns": 5,
        "source_size": 3,
        "content_hash": "new",
        "text": "new content",
    }
    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [dict(doc), new_doc])

    assert emb.embedding_index_unavailable_reason(settings) == "Embedding index is stale; waiting for compile/ingest refresh"


# ---------------------------------------------------------------------------
# 2. Input truncation to embedding_max_input_chars
# ---------------------------------------------------------------------------


def test_document_truncates_text_to_configured_cap(tmp_path: Path) -> None:
    """_document must truncate text to embedding_max_input_chars, not 4000."""
    settings = _settings(tmp_path, LOCAL_BRAIN_EMBEDDING_MAX_INPUT_CHARS=500)
    long_text = "x" * 5000
    stat_result = _fake_stat()

    # _collect_embedding_documents calls _document with the cap; test via the public
    # _document function with the new max_input_chars param.
    doc = _document("brain", "test.md", long_text, stat_result, max_input_chars=500)

    assert len(doc["text"]) == 500, f"Expected text truncated to 500, got {len(doc['text'])}"
    assert len(doc["content_hash"]) == 64  # sha256 hex — sanity check


def test_document_default_cap_is_1800(tmp_path: Path) -> None:
    """Default embedding_max_input_chars is 1800 (regression against old 4000)."""
    settings = _settings(tmp_path)
    assert settings.embedding_max_input_chars == 1800

    long_text = "y" * 5000
    stat_result = _fake_stat()

    doc = _document("brain", "test.md", long_text, stat_result, max_input_chars=settings.embedding_max_input_chars)
    assert len(doc["text"]) == 1800, f"Expected 1800, got {len(doc['text'])}"


def test_document_text_shorter_than_cap_is_not_padded(tmp_path: Path) -> None:
    """Text shorter than the cap is left as-is."""
    short_text = "hello world"
    stat_result = _fake_stat()
    doc = _document("brain", "short.md", short_text, stat_result, max_input_chars=1800)
    assert doc["text"] == short_text


def test_collect_embedding_documents_uses_configured_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_collect_embedding_documents passes the settings cap, so docs are truncated."""
    settings = _settings(tmp_path, LOCAL_BRAIN_EMBEDDING_MAX_INPUT_CHARS=200)

    brain_store = settings.brain_home / "knowledge"
    brain_store.mkdir(parents=True)
    # Write an article longer than the cap.
    article = brain_store / "note.md"
    article.write_text("# Title\n\n" + "z" * 5000, encoding="utf-8")

    # No registry → store mode is used.
    (settings.brain_home / "registry.yaml").write_text("vaults: {}\n", encoding="utf-8")

    docs = emb._collect_embedding_documents(settings)
    assert docs, "Expected at least one document"
    for doc in docs:
        assert len(doc["text"]) <= 200, f"Expected text <= 200 chars, got {len(doc['text'])}"


# ---------------------------------------------------------------------------
# 3. Default value regression
# ---------------------------------------------------------------------------


def test_embedding_max_input_chars_default_is_1800() -> None:
    """Settings.embedding_max_input_chars defaults to 1800 without any override."""
    settings = Settings(LOCAL_BRAIN_HOME=Path("/tmp/test-brain-nocreate"))
    assert settings.embedding_max_input_chars == 1800


def test_embedding_max_input_chars_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """LOCAL_BRAIN_EMBEDDING_MAX_INPUT_CHARS env var sets the cap."""
    monkeypatch.setenv("LOCAL_BRAIN_EMBEDDING_MAX_INPUT_CHARS", "512")
    settings = Settings(LOCAL_BRAIN_HOME=Path("/tmp/test-brain-nocreate"))
    assert settings.embedding_max_input_chars == 512


def test_embedding_max_input_chars_short_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """EMBEDDING_MAX_INPUT_CHARS (short alias) also sets the cap."""
    monkeypatch.setenv("EMBEDDING_MAX_INPUT_CHARS", "300")
    settings = Settings(LOCAL_BRAIN_HOME=Path("/tmp/test-brain-nocreate"))
    assert settings.embedding_max_input_chars == 300


# ---------------------------------------------------------------------------
# #174 Option 2: token-aware truncation so oversize docs are indexed (truncated)
# ---------------------------------------------------------------------------


def test_document_truncates_to_token_budget(tmp_path: Path) -> None:
    """_document caps the embedded text to max_input_tokens (token estimate)."""
    dense = "a " * 1000  # token-dense; well over a 20-token budget
    stat_result = _fake_stat()

    doc = _document("brain", "d.md", dense, stat_result, max_input_chars=1800, max_input_tokens=20)

    tokens = emb._token_encoder().encode_ordinary(doc["text"])
    assert len(tokens) <= 20, f"Expected <= 20 tokens, got {len(tokens)}"


def test_document_under_token_budget_unchanged(tmp_path: Path) -> None:
    """Short text under the token budget is returned identical."""
    short_text = "hello world"
    stat_result = _fake_stat()
    doc = _document("brain", "short.md", short_text, stat_result, max_input_chars=1800, max_input_tokens=512)
    assert doc["text"] == short_text


def test_collect_embedding_documents_applies_token_cap(tmp_path: Path) -> None:
    """_collect_embedding_documents passes the token cap so docs are token-truncated."""
    settings = _settings(tmp_path, LOCAL_BRAIN_EMBEDDING_MAX_INPUT_TOKENS=8)

    brain_store = settings.brain_home / "knowledge"
    brain_store.mkdir(parents=True)
    article = brain_store / "note.md"
    article.write_text("# Title\n\n" + ("word " * 5000), encoding="utf-8")
    (settings.brain_home / "registry.yaml").write_text("vaults: {}\n", encoding="utf-8")

    docs = emb._collect_embedding_documents(settings)
    assert docs, "Expected at least one document"
    for doc in docs:
        tokens = emb._token_encoder().encode_ordinary(doc["text"])
        assert len(tokens) <= 8, f"Expected <= 8 tokens, got {len(tokens)}"


def test_embedding_max_input_tokens_default_is_512() -> None:
    """Settings.embedding_max_input_tokens defaults to 512 (mxbai window)."""
    settings = Settings(LOCAL_BRAIN_HOME=Path("/tmp/test-brain-nocreate"))
    assert settings.embedding_max_input_tokens == 512


def test_embedding_max_input_tokens_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """LOCAL_BRAIN_EMBEDDING_MAX_INPUT_TOKENS env var sets the token cap."""
    monkeypatch.setenv("LOCAL_BRAIN_EMBEDDING_MAX_INPUT_TOKENS", "256")
    settings = Settings(LOCAL_BRAIN_HOME=Path("/tmp/test-brain-nocreate"))
    assert settings.embedding_max_input_tokens == 256


def test_embedding_max_input_tokens_short_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """EMBEDDING_MAX_INPUT_TOKENS (short alias) also sets the token cap."""
    monkeypatch.setenv("EMBEDDING_MAX_INPUT_TOKENS", "128")
    settings = Settings(LOCAL_BRAIN_HOME=Path("/tmp/test-brain-nocreate"))
    assert settings.embedding_max_input_tokens == 128


# ---------------------------------------------------------------------------
# #174 Option 3: cache the known-oversize decision so unchanged oversize docs
# are not re-sent to the embed endpoint every refresh.
# ---------------------------------------------------------------------------


def test_force_rebuild_bypasses_oversize_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A forced rebuild IGNORES the oversize skip-cache and re-attempts every doc.

    This is the operator's recovery lever after a model/config change or a
    misclassification: force=True must re-send a cached-oversize doc even when its
    content is unchanged. The non-force honored-cache behavior is covered by
    test_known_oversize_not_reattempted_on_nonforce_stale_rebuild.
    """
    settings = _settings(tmp_path)

    doc_ok = {
        "vault": "brain",
        "path": "ok.md",
        "title": "Ok",
        "snippet": "ok snippet",
        "source_mtime_ns": 1,
        "source_size": 8,
        "content_hash": "okhash",
        "text": "ok content",
    }
    doc_big = {
        "vault": "brain",
        "path": "big.md",
        "title": "Big",
        "snippet": "big snippet",
        "source_mtime_ns": 2,
        "source_size": 9999,
        "content_hash": "bighash",
        "text": "big content",
    }

    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [doc_ok, doc_big])
    monkeypatch.setattr(emb, "append_global_log", lambda *a, **kw: None)

    sent_texts: list[str] = []

    async def fake_embed(settings, text: str) -> list[float]:
        sent_texts.append(text)
        if text == "big content":
            raise RuntimeError("400: input length exceeds context length")
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(emb, "_embed_text", fake_embed)

    # Build 1 (force): big skipped and recorded in skipped_entries.
    result1 = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))
    assert result1.indexed is True
    assert result1.documents_indexed == 1

    index_file = settings.brain_home / "embeddings" / "index.json"
    data = json.loads(index_file.read_text(encoding="utf-8"))
    assert data.get("skipped_entries") == [
        {"vault": "brain", "path": "big.md", "content_hash": "bighash"}
    ], f"Expected skipped_entries for big doc, got {data.get('skipped_entries')}"
    assert data.get("skipped_keys") == [["brain", "big.md"]]

    # Build 2 (force) with the SAME docs: big MUST be re-sent (forced rebuild
    # bypasses the cache), even though its content is unchanged.
    result2 = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))
    assert result2.indexed is True
    assert result2.documents_indexed == 1, "ok doc still indexed"

    assert sent_texts.count("big content") == 2, (
        f"big content must be re-sent on the forced rebuild (cache bypassed), got {sent_texts}"
    )
    data2 = json.loads(index_file.read_text(encoding="utf-8"))
    assert data2.get("skipped_entries") == [
        {"vault": "brain", "path": "big.md", "content_hash": "bighash"}
    ]
    assert [d["path"] for d in data2["documents"]] == ["ok.md"]


def test_oversize_reattempted_when_content_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When an oversize doc's content changes, the cache is bypassed and it is re-attempted."""
    settings = _settings(tmp_path)

    doc_big_v1 = {
        "vault": "brain",
        "path": "big.md",
        "title": "Big",
        "snippet": "big snippet",
        "source_mtime_ns": 2,
        "source_size": 9999,
        "content_hash": "bighash-v1",
        "text": "big content v1",
    }

    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [dict(doc_big_v1)])
    monkeypatch.setattr(emb, "append_global_log", lambda *a, **kw: None)

    async def fake_embed_fail(settings, text: str) -> list[float]:
        raise RuntimeError("400: input length exceeds context length")

    monkeypatch.setattr(emb, "_embed_text", fake_embed_fail)

    # Build 1: big fails oversize (no transient failure) → an empty index IS written
    # and big is cached in skipped_entries (FIX 2: all-permanent-skip corpora persist).
    result1 = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))
    assert result1.indexed is True
    assert result1.error is None
    assert result1.documents_indexed == 0

    # Seed a prior good index that also carries big's stale skipped_entries, so the
    # cache is populated for build 2. Build an ok doc alongside big-v1 first.
    doc_ok = {
        "vault": "brain",
        "path": "ok.md",
        "title": "Ok",
        "snippet": "ok snippet",
        "source_mtime_ns": 1,
        "source_size": 8,
        "content_hash": "okhash",
        "text": "ok content",
    }
    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [dict(doc_ok), dict(doc_big_v1)])

    sent_texts: list[str] = []

    async def fake_embed_big_fails(settings, text: str) -> list[float]:
        sent_texts.append(text)
        if text.startswith("big content"):
            raise RuntimeError("400: input length exceeds context length")
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(emb, "_embed_text", fake_embed_big_fails)
    result2 = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))
    assert result2.indexed is True
    index_file = settings.brain_home / "embeddings" / "index.json"
    data2 = json.loads(index_file.read_text(encoding="utf-8"))
    assert data2.get("skipped_entries") == [
        {"vault": "brain", "path": "big.md", "content_hash": "bighash-v1"}
    ]

    # Build 3: big's content CHANGES (new content_hash) and now succeeds → re-attempted + indexed.
    doc_big_v2 = dict(doc_big_v1)
    doc_big_v2["content_hash"] = "bighash-v2"
    doc_big_v2["text"] = "big content v2 now small"
    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [dict(doc_ok), dict(doc_big_v2)])

    sent_v3: list[str] = []

    async def fake_embed_all_ok(settings, text: str) -> list[float]:
        sent_v3.append(text)
        return [0.4, 0.5, 0.6]

    monkeypatch.setattr(emb, "_embed_text", fake_embed_all_ok)
    result3 = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))
    assert result3.indexed is True
    assert "big content v2 now small" in sent_v3, "changed content must be re-attempted"
    data3 = json.loads(index_file.read_text(encoding="utf-8"))
    assert "big.md" in [d["path"] for d in data3["documents"]], "re-attempted big doc must be indexed"
    assert data3.get("skipped_entries") == [], "no skips after successful re-attempt"


def test_skipped_entries_backward_compat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A #170-style index.json without skipped_entries → empty cache → every doc attempted."""
    settings = _settings(tmp_path)

    index_file = settings.brain_home / "embeddings" / "index.json"
    index_file.parent.mkdir(parents=True, exist_ok=True)
    # Hand-write a legacy index with only skipped_keys (no skipped_entries).
    legacy = {
        "model": settings.embedding_model,
        "provider_fingerprint": emb._provider_fingerprint(settings),
        "updated_at": "2026-01-01T00:00:00",
        "source_fingerprint": "stale",
        "skipped_keys": [["brain", "prev.md"]],
        "documents": [],
    }
    index_file.write_text(json.dumps(legacy, indent=2) + "\n", encoding="utf-8")

    doc_prev = {
        "vault": "brain",
        "path": "prev.md",
        "title": "Prev",
        "snippet": "prev snippet",
        "source_mtime_ns": 7,
        "source_size": 5,
        "content_hash": "prevhash",
        "text": "prev content",
    }
    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [dict(doc_prev)])
    monkeypatch.setattr(emb, "append_global_log", lambda *a, **kw: None)

    sent_texts: list[str] = []

    async def fake_embed(settings, text: str) -> list[float]:
        sent_texts.append(text)
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(emb, "_embed_text", fake_embed)

    result = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))
    assert result.indexed is True
    assert "prev content" in sent_texts, "previously skipped doc must be attempted (empty cache)"
    data = json.loads(index_file.read_text(encoding="utf-8"))
    assert "prev.md" in [d["path"] for d in data["documents"]]


def test_known_oversize_not_reattempted_on_nonforce_stale_rebuild(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real scheduler path: a non-force STALE rebuild must consult the cache.

    Build 1 (force) caches the oversize doc. Then `ok`'s content changes, marking
    the index stale, and a force=False refresh runs the build loop. The cached
    oversize doc must NOT be re-sent (cache hit), while the changed `ok` doc is
    re-embedded. This is the noise #174 targets that force-gating used to leak.
    """
    settings = _settings(tmp_path)

    doc_ok_v1 = {
        "vault": "brain",
        "path": "ok.md",
        "title": "Ok",
        "snippet": "ok snippet",
        "source_mtime_ns": 1,
        "source_size": 8,
        "content_hash": "okhash-v1",
        "text": "ok content v1",
    }
    doc_big = {
        "vault": "brain",
        "path": "big.md",
        "title": "Big",
        "snippet": "big snippet",
        "source_mtime_ns": 2,
        "source_size": 9999,
        "content_hash": "bighash",
        "text": "big content",
    }

    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [dict(doc_ok_v1), dict(doc_big)])
    monkeypatch.setattr(emb, "append_global_log", lambda *a, **kw: None)

    sent_texts: list[str] = []

    async def fake_embed(settings, text: str) -> list[float]:
        sent_texts.append(text)
        if text.startswith("big content"):
            raise RuntimeError("400: the input length exceeds the context length")
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(emb, "_embed_text", fake_embed)

    # Build 1 (force): big cached as oversize, ok indexed.
    result1 = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))
    assert result1.indexed is True
    assert result1.documents_indexed == 1
    index_file = settings.brain_home / "embeddings" / "index.json"
    data1 = json.loads(index_file.read_text(encoding="utf-8"))
    assert data1.get("skipped_entries") == [{"vault": "brain", "path": "big.md", "content_hash": "bighash"}]
    assert sent_texts.count("big content") == 1

    # Change ok's content → the index is now STALE → a non-force refresh must
    # actually run the build loop (not early-return) and re-embed ok.
    doc_ok_v2 = dict(doc_ok_v1)
    doc_ok_v2["content_hash"] = "okhash-v2"
    doc_ok_v2["source_mtime_ns"] = 99
    doc_ok_v2["text"] = "ok content v2"
    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [dict(doc_ok_v2), dict(doc_big)])

    result2 = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=False)))
    assert result2.indexed is True, f"non-force stale rebuild must run, error: {result2.error}"
    assert result2.documents_indexed == 1, "ok re-embedded; big stays cached"

    # big's text was sent exactly once total (build 1 only) — the non-force stale
    # rebuild hit the cache instead of re-sending it.
    assert sent_texts.count("big content") == 1, f"big must not be re-sent on non-force rebuild, got {sent_texts}"
    assert "ok content v2" in sent_texts, "changed ok doc must be re-embedded"

    data2 = json.loads(index_file.read_text(encoding="utf-8"))
    assert [d["path"] for d in data2["documents"]] == ["ok.md"]
    assert data2.get("skipped_entries") == [{"vault": "brain", "path": "big.md", "content_hash": "bighash"}]


def test_transient_failure_is_not_cached_and_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#170 at the #174 level: a TRANSIENT embed failure is NOT cached and IS retried.

    Build 1: a doc fails with a connection error (not an oversize phrasing). It must
    NOT land in skipped_entries. Build 2: the embed now succeeds, so the doc is
    attempted again and indexed.
    """
    settings = _settings(tmp_path)

    doc_ok = {
        "vault": "brain",
        "path": "ok.md",
        "title": "Ok",
        "snippet": "ok snippet",
        "source_mtime_ns": 1,
        "source_size": 8,
        "content_hash": "okhash",
        "text": "ok content",
    }
    doc_flaky = {
        "vault": "brain",
        "path": "flaky.md",
        "title": "Flaky",
        "snippet": "flaky snippet",
        "source_mtime_ns": 2,
        "source_size": 5,
        "content_hash": "flakyhash",
        "text": "flaky content",
    }

    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [dict(doc_ok), dict(doc_flaky)])
    monkeypatch.setattr(emb, "append_global_log", lambda *a, **kw: None)

    async def fake_embed_transient(settings, text: str) -> list[float]:
        if text == "flaky content":
            raise RuntimeError("connection refused")
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(emb, "_embed_text", fake_embed_transient)

    # Build 1: flaky fails transiently → must NOT be cached.
    result1 = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))
    assert result1.indexed is True
    assert result1.documents_indexed == 1
    index_file = settings.brain_home / "embeddings" / "index.json"
    data1 = json.loads(index_file.read_text(encoding="utf-8"))
    assert data1.get("skipped_entries") == [], f"transient failure must not be cached, got {data1.get('skipped_entries')}"
    assert data1.get("skipped_keys") == [["brain", "flaky.md"]]

    # Build 2 (force): flaky now succeeds → re-attempted and indexed.
    sent_texts: list[str] = []

    async def fake_embed_ok(settings, text: str) -> list[float]:
        sent_texts.append(text)
        return [0.4, 0.5, 0.6]

    monkeypatch.setattr(emb, "_embed_text", fake_embed_ok)

    result2 = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))
    assert result2.indexed is True
    assert result2.documents_indexed == 2
    assert "flaky content" in sent_texts, "transient doc must be re-attempted (not cached)"
    data2 = json.loads(index_file.read_text(encoding="utf-8"))
    assert "flaky.md" in [d["path"] for d in data2["documents"]]
    assert data2.get("skipped_entries") == []


def test_is_oversize_embed_error_discriminates() -> None:
    """_is_oversize_embed_error: oversize phrasings → True; transient → False."""
    oversize = [
        RuntimeError("400: the input length exceeds the context length"),  # Ollama/mxbai
        RuntimeError("This model's maximum context length is 8192 tokens"),  # OpenAI-style
        RuntimeError("input is too long for the context window"),
        RuntimeError("exceeds the maximum context"),
    ]
    transient = [
        RuntimeError("connection refused"),
        RuntimeError("connection reset"),
        RuntimeError("request timed out"),
        RuntimeError("service unavailable"),
        RuntimeError("timeout"),
        RuntimeError("transient error"),
        RuntimeError("endpoint unreachable"),
    ]
    for exc in oversize:
        assert emb._is_oversize_embed_error(exc) is True, f"expected oversize: {exc}"
    for exc in transient:
        assert emb._is_oversize_embed_error(exc) is False, f"expected non-oversize: {exc}"


# ---------------------------------------------------------------------------
# #174 FIX 2: an all-skipped corpus must still persist (skipped_entries + clean
# index) when the only empties are permanent (oversize/cached) — but a TRANSIENT
# full failure must still preserve the prior index.
# ---------------------------------------------------------------------------


def test_all_oversize_corpus_persists_skipped_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A corpus whose ONLY doc is permanently oversize → an index IS written with
    skipped_entries, and a second NON-FORCE refresh does NOT re-attempt the doc."""
    settings = _settings(tmp_path)

    doc_big = {
        "vault": "brain",
        "path": "big.md",
        "title": "Big",
        "snippet": "big snippet",
        "source_mtime_ns": 2,
        "source_size": 9999,
        "content_hash": "bighash",
        "text": "big content",
    }

    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [dict(doc_big)])
    monkeypatch.setattr(emb, "append_global_log", lambda *a, **kw: None)

    sent_texts: list[str] = []

    async def fake_embed(settings, text: str) -> list[float]:
        sent_texts.append(text)
        raise RuntimeError("400: input length exceeds the context length")

    monkeypatch.setattr(emb, "_embed_text", fake_embed)

    # Build 1 (force): every doc oversize (no transient) → write an empty index that
    # records the skip instead of erroring out.
    result1 = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))
    assert result1.indexed is True
    assert result1.error is None
    assert result1.documents_indexed == 0

    index_file = settings.brain_home / "embeddings" / "index.json"
    assert index_file.exists(), "an index file must be written even for an all-oversize corpus"
    data1 = json.loads(index_file.read_text(encoding="utf-8"))
    assert data1.get("documents") == []
    assert data1.get("skipped_entries") == [
        {"vault": "brain", "path": "big.md", "content_hash": "bighash"}
    ]
    assert sent_texts.count("big content") == 1

    # Build 2 (NON-force): the doc is cached → must NOT be re-sent (no repeated embed call).
    result2 = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=False)))
    assert result2.indexed is True
    assert sent_texts.count("big content") == 1, (
        f"cached oversize doc must not be re-attempted on a non-force refresh, got {sent_texts}"
    )
    data2 = json.loads(index_file.read_text(encoding="utf-8"))
    assert data2.get("skipped_entries") == [
        {"vault": "brain", "path": "big.md", "content_hash": "bighash"}
    ]


def test_all_transient_corpus_preserves_index_and_is_not_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A corpus whose only doc fails with a TRANSIENT error → NO index written /
    existing index preserved, AND the doc is NOT cached (next refresh retries it)."""
    settings = _settings(tmp_path)

    doc_flaky = {
        "vault": "brain",
        "path": "flaky.md",
        "title": "Flaky",
        "snippet": "flaky snippet",
        "source_mtime_ns": 2,
        "source_size": 5,
        "content_hash": "flakyhash",
        "text": "flaky content",
    }

    monkeypatch.setattr(emb, "_collect_embedding_documents", lambda s: [dict(doc_flaky)])
    monkeypatch.setattr(emb, "append_global_log", lambda *a, **kw: None)

    async def fake_embed_transient(settings, text: str) -> list[float]:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(emb, "_embed_text", fake_embed_transient)

    index_file = settings.brain_home / "embeddings" / "index.json"

    # Build 1 (force): the only doc fails transiently → the all-fail guard fires,
    # no index is written, nothing is cached.
    result1 = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))
    assert result1.error is not None
    assert result1.indexed is False
    assert not index_file.exists(), "a transient full failure must not write an index"

    # Build 2 (force): the embed now succeeds → the doc is retried (not cached) and indexed.
    sent_texts: list[str] = []

    async def fake_embed_ok(settings, text: str) -> list[float]:
        sent_texts.append(text)
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(emb, "_embed_text", fake_embed_ok)

    result2 = asyncio.run(_refresh_embedding_index_unlocked(settings, EmbeddingIndexRequest(force=True)))
    assert result2.indexed is True
    assert "flaky content" in sent_texts, "transient doc must be retried (never cached)"
    data2 = json.loads(index_file.read_text(encoding="utf-8"))
    assert "flaky.md" in [d["path"] for d in data2["documents"]]
    assert data2.get("skipped_entries") == []
