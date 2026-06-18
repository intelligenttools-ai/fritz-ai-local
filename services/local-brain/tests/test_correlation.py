"""Tests for the correlation feed (WI6): find_related_articles and its integration."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from fritz_local_brain import compile_workflow, embeddings
from fritz_local_brain.config import Settings
from fritz_local_brain.correlation import find_related_articles
from fritz_local_brain.models import ArticleWriteProposal, CompileAgentOutput, CompileRunRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(tmp_path: Path, *, embedding_enabled: bool = False) -> Settings:
    brain_home = tmp_path / "brain"
    brain_home.mkdir(parents=True, exist_ok=True)
    return Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_EMBEDDING_ENABLED=embedding_enabled)


def _write_article(store_root: Path, relpath: str, content: str) -> Path:
    path = store_root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Edge-case guards
# ---------------------------------------------------------------------------


def test_find_related_articles_returns_empty_for_missing_store_root(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    result = asyncio.run(
        find_related_articles(settings, "some query", store_root=None, top_k=5, char_budget=4000)
    )
    assert result == []


def test_find_related_articles_returns_empty_when_store_root_does_not_exist(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    result = asyncio.run(
        find_related_articles(
            settings,
            "some query",
            store_root=tmp_path / "nonexistent",
            top_k=5,
            char_budget=4000,
        )
    )
    assert result == []


def test_find_related_articles_returns_empty_when_top_k_is_zero(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store_root = tmp_path / "store"
    _write_article(store_root, "common/context/fact.md", "# Fact\n\nSome content.\n")
    result = asyncio.run(
        find_related_articles(settings, "some content", store_root=store_root, top_k=0, char_budget=4000)
    )
    assert result == []


def test_find_related_articles_returns_empty_when_char_budget_is_zero(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store_root = tmp_path / "store"
    _write_article(store_root, "common/context/fact.md", "# Fact\n\nSome content.\n")
    result = asyncio.run(
        find_related_articles(settings, "some content", store_root=store_root, top_k=5, char_budget=0)
    )
    assert result == []


def test_find_related_articles_skips_index_md(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store_root = tmp_path / "store"
    _write_article(store_root, "index.md", "# Index\n\nsome content keyword.\n")
    _write_article(store_root, "common/context/real.md", "# Real\n\nsome content keyword.\n")
    result = asyncio.run(
        find_related_articles(settings, "some content keyword", store_root=store_root, top_k=5, char_budget=4000)
    )
    paths = [r["path"] for r in result]
    assert "index.md" not in paths
    assert "common/context/real.md" in paths


def test_find_related_articles_skips_symlinked_files(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store_root = tmp_path / "store"
    store_root.mkdir()
    real = tmp_path / "outside.md"
    real.write_text("# Outside\n\nsome keyword content.\n", encoding="utf-8")
    link = store_root / "linked.md"
    link.symlink_to(real)
    result = asyncio.run(
        find_related_articles(settings, "some keyword content", store_root=store_root, top_k=5, char_budget=4000)
    )
    assert result == []


# ---------------------------------------------------------------------------
# Keyword fallback path (embeddings OFF)
# ---------------------------------------------------------------------------


def test_keyword_fallback_ranks_by_overlap_descending(tmp_path: Path) -> None:
    """Article with more token overlap ranks higher."""
    settings = _settings(tmp_path, embedding_enabled=False)
    store_root = tmp_path / "store"
    # 'alpha.md' shares 2 tokens with query; 'beta.md' shares only 1.
    _write_article(store_root, "alpha.md", "# Alpha\n\nfoo bar baz.\n")
    _write_article(store_root, "beta.md", "# Beta\n\nfoo only.\n")
    result = asyncio.run(
        find_related_articles(settings, "foo bar", store_root=store_root, top_k=5, char_budget=4000)
    )
    paths = [r["path"] for r in result]
    assert paths[0] == "alpha.md"
    assert paths[1] == "beta.md"


def test_keyword_fallback_tie_breaks_alphabetically(tmp_path: Path) -> None:
    """Equal overlap → articles sorted by relpath ascending."""
    settings = _settings(tmp_path, embedding_enabled=False)
    store_root = tmp_path / "store"
    _write_article(store_root, "b-article.md", "# B\n\nfoo bar.\n")
    _write_article(store_root, "a-article.md", "# A\n\nfoo bar.\n")
    result = asyncio.run(
        find_related_articles(settings, "foo bar", store_root=store_root, top_k=5, char_budget=4000)
    )
    paths = [r["path"] for r in result]
    assert paths == ["a-article.md", "b-article.md"]


def test_keyword_fallback_excludes_zero_overlap(tmp_path: Path) -> None:
    """Articles with no token overlap are excluded."""
    settings = _settings(tmp_path, embedding_enabled=False)
    store_root = tmp_path / "store"
    _write_article(store_root, "relevant.md", "# R\n\nfoo bar.\n")
    _write_article(store_root, "irrelevant.md", "# I\n\ncompletely unrelated.\n")
    result = asyncio.run(
        find_related_articles(settings, "foo bar", store_root=store_root, top_k=5, char_budget=4000)
    )
    paths = [r["path"] for r in result]
    assert "irrelevant.md" not in paths
    assert "relevant.md" in paths


def test_keyword_fallback_respects_top_k(tmp_path: Path) -> None:
    """At most top_k articles are returned even when more overlap."""
    settings = _settings(tmp_path, embedding_enabled=False)
    store_root = tmp_path / "store"
    for i in range(5):
        _write_article(store_root, f"art-{i}.md", f"# Art {i}\n\nfoo bar baz.\n")
    result = asyncio.run(
        find_related_articles(settings, "foo bar baz", store_root=store_root, top_k=3, char_budget=100000)
    )
    assert len(result) == 3


def test_keyword_fallback_truncates_at_char_budget_boundary(tmp_path: Path) -> None:
    """The article that would exceed the budget is included truncated; subsequent articles dropped."""
    settings = _settings(tmp_path, embedding_enabled=False)
    store_root = tmp_path / "store"
    # Two articles, each with 'needle' in them.
    content_a = "# A\n\n" + "needle " * 10 + "\n"  # ~66 chars
    content_b = "# B\n\n" + "needle " * 10 + "\n"  # ~66 chars
    _write_article(store_root, "a-art.md", content_a)
    _write_article(store_root, "b-art.md", content_b)
    # Budget allows first article fully, second only partially (but >0 budget remains after first).
    # We set budget to len(content_a) + 10 so second is truncated to 10 chars.
    budget = len(content_a) + 10
    result = asyncio.run(
        find_related_articles(settings, "needle", store_root=store_root, top_k=5, char_budget=budget)
    )
    # Two articles: first full, second truncated.
    assert len(result) == 2
    assert result[0]["path"] == "a-art.md"
    assert result[0]["content"] == content_a
    assert result[1]["path"] == "b-art.md"
    assert result[1]["content"] == content_b[:10]
    # Total content length must not exceed budget.
    total = sum(len(r["content"]) for r in result)
    assert total <= budget


def test_keyword_fallback_truncates_single_article_exceeding_full_budget(tmp_path: Path) -> None:
    """When a single article's content exceeds char_budget, it is truncated to char_budget."""
    settings = _settings(tmp_path, embedding_enabled=False)
    store_root = tmp_path / "store"
    long_content = "# Big\n\n" + "needle " * 1000 + "\n"
    _write_article(store_root, "big.md", long_content)
    budget = 100
    result = asyncio.run(
        find_related_articles(settings, "needle", store_root=store_root, top_k=5, char_budget=budget)
    )
    assert len(result) == 1
    assert len(result[0]["content"]) == budget
    assert result[0]["content"] == long_content[:budget]


def test_keyword_fallback_result_dict_has_required_keys(tmp_path: Path) -> None:
    settings = _settings(tmp_path, embedding_enabled=False)
    store_root = tmp_path / "store"
    _write_article(store_root, "common/context/fact.md", "# My Fact\n\nfoo bar.\n")
    result = asyncio.run(
        find_related_articles(settings, "foo bar", store_root=store_root, top_k=5, char_budget=4000)
    )
    assert len(result) == 1
    item = result[0]
    assert item["vault"] == "brain"
    assert item["path"] == "common/context/fact.md"
    assert item["title"] == "My Fact"
    assert "foo bar" in item["content"]


# ---------------------------------------------------------------------------
# Embeddings-enabled path
# ---------------------------------------------------------------------------


def test_find_related_articles_uses_vector_search_when_index_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When embeddings enabled and index is fresh, vector search is used instead of keyword."""
    brain_home = tmp_path / "brain"
    brain_home.mkdir(parents=True)
    store_root = brain_home / "knowledge"
    (brain_home / "registry.yaml").write_text("vaults: {}\n", encoding="utf-8")

    # Two articles: 'match.md' is semantically similar to query; 'other.md' is not.
    _write_article(store_root, "common/context/match.md", "# Match\n\nvector-match-content.\n")
    _write_article(store_root, "common/context/other.md", "# Other\n\nunrelated-content-xyz.\n")

    # Fake embeddings: query → [1.0, 0.0]; match → [0.95, 0.05]; other → [0.0, 1.0]
    async def fake_embed(settings: Settings, text: str) -> list[float]:
        if "query-probe" in text:
            return [1.0, 0.0]
        if "vector-match-content" in text:
            return [0.95, 0.05]
        return [0.0, 1.0]

    monkeypatch.setattr(embeddings, "_embed_text", fake_embed)
    settings = Settings(
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_EMBEDDING_ENABLED=True,
    )
    asyncio.run(embeddings.refresh_embedding_index(settings))

    result = asyncio.run(
        find_related_articles(
            settings,
            "query-probe",
            store_root=store_root,
            top_k=5,
            char_budget=4000,
        )
    )

    paths = [r["path"] for r in result]
    assert "common/context/match.md" in paths
    assert result[0]["path"] == "common/context/match.md"
    assert result[0]["vault"] == "brain"
    assert "vector-match-content" in result[0]["content"]


# ---------------------------------------------------------------------------
# Store-mode compile integration: deps carry related_articles
# ---------------------------------------------------------------------------


class _CapturingCompileAgent:
    """Fake compile agent that records deps for assertion."""

    def __init__(self) -> None:
        self.captured_deps: list[object] = []

    async def run(self, prompt: str, *, deps: object, usage_limits: object) -> SimpleNamespace:
        self.captured_deps.append(deps)
        return SimpleNamespace(output=CompileAgentOutput())


def test_store_mode_compile_deps_carry_related_articles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """In store mode, CompileDeps.related_articles is populated from find_related_articles."""
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"
    capture_path = brain_home / "capture" / "inbox" / "cap.md"
    store_root_dir = brain_home / "knowledge"

    capture_path.parent.mkdir(parents=True)
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")
    capture_path.write_text("# Cap\n\nfoo bar baz.\n", encoding="utf-8")

    # Pre-populate a store article so correlation can find it.
    article = _write_article(store_root_dir, "common/context/existing.md", "# Existing\n\nfoo bar baz.\n")

    agent = _CapturingCompileAgent()
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

    settings = Settings(
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills",
        LOCAL_BRAIN_CORRELATION_TOP_K=3,
        LOCAL_BRAIN_CORRELATION_MAX_CHARS=4000,
    )
    asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=True, max_captures=1)))

    # #153: one agent.run per capture (single capture here, so exactly one call).
    assert len(agent.captured_deps) == 1
    deps = agent.captured_deps[0]
    assert hasattr(deps, "related_articles")
    # The existing store article shares tokens with the capture text → it must appear.
    paths = [r["path"] for r in deps.related_articles]
    assert "common/context/existing.md" in paths


def test_store_mode_compile_related_articles_empty_when_top_k_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """correlation_top_k=0 means related_articles is [] in deps."""
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"
    capture_path = brain_home / "capture" / "inbox" / "cap.md"
    store_root_dir = brain_home / "knowledge"

    capture_path.parent.mkdir(parents=True)
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")
    capture_path.write_text("# Cap\n\nfoo bar baz.\n", encoding="utf-8")
    _write_article(store_root_dir, "common/context/existing.md", "# Existing\n\nfoo bar baz.\n")

    agent = _CapturingCompileAgent()
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

    settings = Settings(
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills",
        LOCAL_BRAIN_CORRELATION_TOP_K=0,
        LOCAL_BRAIN_CORRELATION_MAX_CHARS=4000,
    )
    asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=True, max_captures=1)))

    # #153: one agent.run per capture (single capture here, so exactly one call).
    assert len(agent.captured_deps) == 1
    assert agent.captured_deps[0].related_articles == []


def test_registry_mode_compile_related_articles_not_populated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In registry mode (vault configured), related_articles stays empty."""
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"
    capture_path = brain_home / "capture" / "inbox" / "cap.md"

    capture_path.parent.mkdir(parents=True)
    (vault_path / ".brain").mkdir(parents=True)
    (vault_path / "knowledge").mkdir()
    (vault_path / ".brain" / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\nexclude: []\n", encoding="utf-8")
    brain_home.mkdir(exist_ok=True)
    (brain_home / "registry.yaml").write_text(f"vaults:\n  test:\n    path: {vault_path}\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")
    capture_path.write_text("# Cap\n\nfoo bar baz.\n", encoding="utf-8")

    agent = _CapturingCompileAgent()
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda settings, skill_text: agent)

    settings = Settings(
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills",
    )
    asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=True, max_captures=1)))

    # #153: one agent.run per capture (single capture here, so exactly one call).
    assert len(agent.captured_deps) == 1
    # Registry mode → related_articles must be empty.
    assert agent.captured_deps[0].related_articles == []
